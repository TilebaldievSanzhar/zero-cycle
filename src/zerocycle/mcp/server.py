"""Обвязка FastMCP над инструментами сессии (`tools.Session`); запуск по stdio — `zerocycle mcp`.

Подключение к Claude Code (из папки, где лежит пакет; `--project` не меняет рабочую папку, и `--root .` — она):

    claude mcp add zerocycle -- uv run --project <путь к zero-cycle> python -m zerocycle mcp --root .

Через stdio в stdout идут только сообщения протокола: правила и опись ничего не печатают, предупреждения
библиотек PDF уходят в stderr.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP, Image

from zerocycle.mcp.tools import Session

WAIT = 50.0  # сколько ждать проверку в одном вызове: меньше таймаута вызова инструмента у клиентов
MAX_WAIT = 600.0

INSTRUCTIONS = """«Нулевой цикл» проверяет пакет проектной документации перед экспертизой: комплектность разделов,
оформление основных надписей и листов, увязку документов (наименования, обозначения, ТЭП) и ссылки на нормы.
Пакеты правил юрисдикции: ru — ГОСТ Р 21.101-2020, ПП РФ № 87, реестр «Стройкомплекс.РФ»; kz — ГОСТ 21.101-97,
Правила КВЭ (Приказ № 299), АГСК-1.

Порядок работы:
1. check_package(path) — опись и проверка; большой пакет идёт минуты: если state = running, вызывай run_status.
   В сводке coverage — что движок проверил и чего не проверяет.
2. list_findings(status=...) — находки; explain_finding(id) — основание, как сверено, как сформулирует эксперт;
   render_fragment(id) — лист с красной рамкой находки, чтобы проверить глазами.
3. expertise() (kz) — какие документы эксперты РК чаще всего заставляли добавить для объектов такого вида: частоты
   из заключений, не норма.
4. list_rules — что движок проверяет; всё остальное (содержание решений, расчёты, графику) проверяй сам.
5. norm_status("СП 54.13330.2016") — действует ли документ по индексу юрисдикции.

Статусы: violated — нарушение с листом и основанием; manual — нужен человек; cannot_check — движок не смог
проверить (нет текстового слоя, нет данных), это НЕ «норма»; ok — проверено, замечаний нет.

В РК (kz): «нарушение» дают только правила с измеренной точностью. Оформление и увязка (core, spds) приходят как manual
с пометкой «точность … не измерена» — это подсказки: посмотри лист (render_fragment), прежде чем писать. Пропущенные
документы — kz.completeness.ird (Правила КВЭ) и kz.completeness.expertise (что эксперты РК часто требуют для объектов
такого вида); manual_by_rule в сводке показывает, где сколько подсказок. Подсказку kz.completeness.expertise, которую ты
не опроверг по файлам (документа нет и среди сканов), включи в ответ как «проверить вручную» с основанием из её basis
(«Практика экспертизы РК, N из M заключений»): пункта нормы у неё нет — так и должно быть.
Находка — доказательство с якорем (файл, страница, рамка в пунктах PDF от левого верхнего угла, цитата) и
основанием из списка проверенных документов. Не добавляй к находке пунктов норм, которых нет в её basis."""


def build(session: Session) -> FastMCP:
    mcp = FastMCP("zerocycle", instructions=INSTRUCTIONS, log_level="WARNING")  # без строки на каждый вызов

    async def wait(run_id: str, seconds: float) -> None:
        deadline = anyio.current_time() + max(0.0, min(seconds, MAX_WAIT))
        while not session.wait(run_id, timeout=0) and anyio.current_time() < deadline:
            await anyio.sleep(0.25)

    @mcp.tool()
    async def check_package(
        path: str, jurisdiction: str | None = None, work_kind: str | None = None, wait_seconds: float = WAIT
    ) -> dict[str, Any]:
        """Проверить пакет проектной документации — папку или zip — правилами юрисдикции (ru или kz; по умолчанию —
        юрисдикция сервера).

        work_kind — new, reconstruction или capital_repair, если вид работ не следует из наименования объекта.
        Возвращает сводку: число находок по статусам, нарушения, что движок проверил и чего не проверял. Если
        проверка не успела за wait_seconds, state = running — вызови run_status(run_id).
        """
        run_id = session.start(path, jurisdiction, work_kind)
        await wait(run_id, wait_seconds)
        return session.summary(run_id)

    @mcp.tool()
    async def run_status(run_id: str | None = None, wait_seconds: float = WAIT) -> dict[str, Any]:
        """Статус и сводка прогона (по умолчанию — последнего); ждёт окончания до wait_seconds."""
        rec = session._run(run_id)
        await wait(rec.id, wait_seconds)
        return session.summary(rec.id)

    @mcp.tool()
    def list_findings(
        run_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
        rule: str | None = None,
        document: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Находки прогона в схеме ответа: id, status, severity, category, document, page, description, basis, rule.

        Фильтры: status (violated, manual, cannot_check, ok), category («оформление» или completeness, formatting,
        consistency, norms, submission_format), rule (начало id: «spds.sheets»), document (часть пути файла).
        """
        return session.list_findings(run_id, status, category, rule, document, offset, limit)

    @mcp.tool()
    def explain_finding(finding_id: str, run_id: str | None = None) -> dict[str, Any]:
        """Находка целиком: сравниваемые значения, все якоря, основание и как оно сверено, карточка правила —
        почему эксперт это ловит и как сформулирует замечание."""
        return session.explain_finding(finding_id, run_id)

    @mcp.tool()
    def render_fragment(
        finding_id: str | None = None,
        run_id: str | None = None,
        file: str | None = None,
        page: int | None = None,
        anchor: int = 0,
    ) -> Image:
        """Картинка листа: рамка находки красным с полем вокруг (anchor — номер якоря, если их несколько).
        Без finding_id — лист целиком по file и page."""
        png, _ = session.render_fragment(finding_id, run_id, file, page, anchor)
        return Image(data=png, format="png")

    @mcp.tool()
    def expertise(
        object_class: str | None = None, work_kind: str | None = None, run_id: str | None = None
    ) -> dict[str, Any]:
        """Знание экспертизы РК: какие документы эксперты чаще всего заставляли добавить по замечаниям — для вида
        объекта (building, linear, landscape) и вида работ (new, reconstruction, capital_repair). Без аргументов —
        по пакету последнего прогона. Частота из заключений, а не норма; engine_checks — ищет ли их движок сам."""
        return session.expertise(object_class, work_kind, run_id)

    @mcp.tool()
    def list_rules(jurisdiction: str | None = None) -> dict[str, Any]:
        """Правила юрисдикции: что проверяется, основание, применимость — и чего движок не проверяет."""
        return session.list_rules(jurisdiction or session.jurisdiction)

    @mcp.tool()
    def norm_status(designation: str, jurisdiction: str | None = None) -> dict[str, Any]:
        """Действует ли нормативный документ по индексу юрисдикции: «СП 54.13330.2016», «СН РК 1.02-03-2011».
        В строке может быть несколько обозначений — ответ по каждому."""
        return session.norm_status(designation, jurisdiction or session.jurisdiction)

    return mcp


def serve(root: Path | None = None, runs_dir: Path | None = None, jurisdiction: str = "ru") -> None:
    session = Session(root=root, runs_dir=runs_dir, jurisdiction=jurisdiction)
    try:
        build(session).run("stdio")
    finally:
        session.close()
