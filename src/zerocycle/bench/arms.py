"""Руки бенчмарка (docs/bench/PROTOCOL.md): схема ответа, задача агенту, сопоставление находок с эталоном.

Все руки отвечают в одной JSON-схеме: категория, статус, документ, страница, описание, основание. Агентские руки
(A0, A1, B) получают одну и ту же задачу; рука C — находки движка в той же схеме, как их отдаёт MCP-сервер.

Сопоставление — по протоколу, без знания правил движка:
- находка ↔ дефект: тот же документ и страница (±1), категория из допустимых проявлений дефекта;
- у дефектов всего документа (лист выброшен, наименование здания в томе, имя файла) страница не важна;
- у дефектов отсутствия (раздела или тома нет, том не в «Составе ПД», повтор GUID) — тот же раздел, обозначение
  или GUID в тексте находки.

Несопоставленное «нарушение» на засеянном пакете — ложная тревога. Если оно на месте из notes эталона, это ложная
тревога на отвлекающем содержимом; такие идут ещё и отдельной разбивкой по видам.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from zerocycle.rules.cards import FAMILY

CATEGORIES = list(FAMILY.values())  # «комплектность», «оформление», «увязка», «нормы и ссылки на них», …
VIOLATED, MANUAL = "нарушение", "проверить вручную"

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": CATEGORIES},
                    "status": {"type": "string", "enum": [VIOLATED, MANUAL]},
                    "document": {
                        "type": "string",
                        "description": "путь внутри package/: «04 КР/2026-07-КР1.pdf»",
                    },
                    "page": {"type": ["integer", "null"], "description": "страница PDF с 1; null — документ целиком"},
                    "description": {"type": "string"},
                    "basis": {"type": "string", "description": "основание: документ и пункт нормы"},
                },
                "required": ["category", "status", "document", "page", "description", "basis"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string", "description": "что проверено, что проверить не удалось и почему"},
    },
    "required": ["findings", "summary"],
    "additionalProperties": False,
}

TASK = """Ты — нормоконтролёр и ГИП. Перед подачей в экспертизу проверь пакет проектной документации (Российская
Федерация, стадия «П») на формальные дефекты. Пакет лежит в папке package/ текущей рабочей папки: тома в PDF по папкам
разделов, «Состав проектной документации», info.xml электронного пакета.

Что искать:
- комплектность: разделы и подразделы по Положению о составе разделов проектной документации (постановление
  Правительства РФ № 87); «Состав проектной документации» ↔ тома в пакете;
- оформление по ГОСТ Р 21.101-2020: основные надписи (обозначение в графе 1, стадия, «Лист» и «Листов», подписи и
  даты, формат листа), нумерация листов, ведомости и содержания томов;
- увязка между томами: наименование объекта и здания, технико-экономические показатели;
- ссылки на нормативные документы, которые не действуют или заменены;
- формат подачи электронных документов: info.xml (GUID, имена файлов), имена файлов по обозначениям.

Как отвечать:
- в findings — только то, что ты подтвердил по файлам пакета: документ (путь внутри package/), страница PDF с 1
  (null — документ целиком), что не так и основание — документ и пункт нормы;
- статус «нарушение» — дефект подтверждён; «проверить вручную» — нужен человек;
- не выдумывай ссылки на нормы и числа; чего проверить не удалось — напиши в summary.

Работай только с папкой package/: файлы вне текущей рабочей папки не открывай и не ищи, черновики складывай только в
текущую папку. Python 3 с pdfplumber, pypdfium2, lxml и Pillow доступен командой python; PDF можно читать и
инструментом Read."""

# категории, в которых засеянный дефект может проявиться (PROTOCOL: «одно из допустимых проявлений»)
ALLOWED = {
    "D01": {"увязка"},
    "D02": {"комплектность", "оформление"},
    "D03": {"оформление"},
    "D07": {"нормы и ссылки на них"},
    "D07R": {"нормы и ссылки на них"},
    "D09": {"комплектность"},
    "D10": {"оформление"},
    "F01": {"оформление", "комплектность"},
    "F02": {"оформление", "комплектность"},
    "F03": {"оформление"},
    "F04": {"увязка", "оформление"},
    "F05": {"комплектность"},
    "F06": {"формат подачи", "оформление"},
    "F07": {"формат подачи"},
    "F08": {"оформление"},
    "F09": {"комплектность"},
}
WHOLE_DOCUMENT = {"D02", "F04", "F06"}  # дефект документа целиком: страница находки не важна
PAGE_TOLERANCE = 1


def norm_doc(doc: str | None) -> str:
    """Путь документа из ответа руки → путь внутри package/: «.\\package\\04 КР\\x.pdf» → «04 КР/x.pdf»."""
    s = (doc or "").strip().replace("\\", "/")
    if "package/" in s:
        s = s.split("package/", 1)[1]
    return s.lstrip("./")


def _same_document(f: dict, path: str | None, designation: str | None = None) -> bool:
    doc = norm_doc(f.get("document"))
    if not doc:
        return False
    if path and (doc == path or PurePosixPath(doc).name == PurePosixPath(path).name):
        return True
    return bool(designation) and designation in doc


def _text(f: dict) -> str:
    return " ".join(str(f.get(k) or "") for k in ("document", "description", "basis"))


def _mentions_designation(f: dict, designation: str) -> bool:
    """Обозначение тома в тексте находки: целиком или без базовой части («ОДИ», «ИОС6»)."""
    text = _text(f)
    if designation in text:
        return True
    tail = designation.rsplit("-", 1)[-1]
    return bool(re.search(rf"(?<![А-ЯA-Z]){re.escape(tail)}(?![А-ЯA-Z0-9])", text))


def _mentions_section(f: dict, section: int, code: str) -> bool:
    text = _text(f).lower()
    return bool(re.search(rf"раздел\w*\s*№?\s*{section}(?!\d)", text)) or code.lower() in text


def matches(defect: dict, f: dict) -> bool:
    """Находка указывает на засеянный дефект: место и категория из допустимых проявлений."""
    code = defect["id"]
    if f.get("category") not in ALLOWED.get(code, set()):
        return False
    if code == "D09":
        section = _mentions_section(f, defect["section"], defect["code"])
        return section or _mentions_designation(f, defect["designation"])
    if code in ("F05", "F09"):
        return _mentions_designation(f, defect["designation"])
    if code == "F07":
        text = _text(f).lower()
        return defect["guid"].lower() in text or any(_mentions_designation(f, d) for d in defect["designations"])
    if code == "F06":
        return _same_document(f, defect["file"]) or PurePosixPath(defect["file"]).name in _text(f)
    if code == "D01" and defect.get("reference") and _same_document(f, defect["reference"]):
        return _mentions_designation(f, defect["designation"])  # расхождение увидено со стороны ПЗ
    if not _same_document(f, defect.get("file"), defect.get("designation")):
        return False
    if code in WHOLE_DOCUMENT or not defect.get("page"):
        return True
    page = f.get("page")
    return isinstance(page, int) and abs(page - defect["page"]) <= PAGE_TOLERANCE


def _on_note(f: dict, notes: list[dict]) -> str | None:
    """Вид отвлекающего места, на которое указывает находка: тот же файл и страница (или файл целиком)."""
    doc = norm_doc(f.get("document"))
    for n in notes:
        same = n.get("file") and (doc == n["file"] or PurePosixPath(doc).name == PurePosixPath(n["file"]).name)
        if same and (n.get("page") is None or f.get("page") == n["page"]):
            return n["kind"]
        if n["kind"] == "not_developed" and n.get("designation") and _mentions_designation(f, n["designation"]):
            return n["kind"]
    return None


@dataclass
class Score:
    seeded: int = 0
    found: dict[int, list[int]] = field(default_factory=dict)  # номер дефекта → номера «нарушений», что его нашли
    manual: dict[int, list[int]] = field(default_factory=dict)  # номер дефекта → «проверить вручную» на месте
    violated: int = 0  # всего «нарушений» в ответе
    false_alarms: list[int] = field(default_factory=list)  # «нарушения», не сопоставленные ни с одним дефектом
    on_notes: dict[str, int] = field(default_factory=dict)  # из них на отвлекающих местах эталона — по видам
    with_evidence: int = 0  # находок со страницей или документом целиком и основанием
    findings: int = 0
    keys: set[str] = field(default_factory=set)  # для коэффициента Жаккара между прогонами

    @property
    def recall(self) -> float:
        return len(self.found) / self.seeded if self.seeded else 0.0


def score(truth: dict, findings: list[dict]) -> tuple[Score, dict[str, float]]:
    """Сопоставить ответ руки с эталоном пакета; вернуть счёт и метрики (recall, точность, ложные тревоги)."""
    defects = truth.get("defects", [])
    notes = truth.get("notes", [])
    s = Score(seeded=len(defects), findings=len(findings))
    used: set[int] = set()
    for k, f in enumerate(findings):
        if f.get("document") and (f.get("basis") or "").strip():
            s.with_evidence += 1
        hit = [i for i, d in enumerate(defects) if matches(d, f)]
        if f.get("status") == VIOLATED:
            s.violated += 1
            for i in hit:
                s.found.setdefault(i, []).append(k)
            if hit:
                used.add(k)
                s.keys.add("defect:" + ",".join(defects[i]["id"] + str(i) for i in hit))
                continue
            s.false_alarms.append(k)
            if kind := _on_note(f, notes):
                s.on_notes[kind] = s.on_notes.get(kind, 0) + 1
            s.keys.add(f"finding:{norm_doc(f.get('document'))}:{f.get('page')}:{f.get('category')}")
        else:
            for i in hit:
                s.manual.setdefault(i, []).append(k)
    for i in s.found:
        s.manual.pop(i, None)
    true_hits = len(used)
    metrics = {
        "recall": round(s.recall, 3),
        "precision": round(true_hits / s.violated, 3) if s.violated else 1.0,
        "false_alarms": len(s.false_alarms),
        "on_notes": sum(s.on_notes.values()),
        "manual_at_defects": len(s.manual),
        "evidence": round(s.with_evidence / s.findings, 3) if s.findings else 1.0,
    }
    return s, metrics


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def engine_answer(report) -> dict:
    """Ответ руки C: находки движка «нарушено» и «проверить вручную» в схеме ответа (как их отдаёт MCP)."""
    from zerocycle.mcp.tools import finding_view
    from zerocycle.model import Status

    out = []
    for k, f in enumerate(report.findings, start=1):
        if f.status not in (Status.violated, Status.manual):
            continue
        v = finding_view(f"F{k}", f)
        out.append(
            {
                "category": v["category"],
                "status": VIOLATED if f.status == Status.violated else MANUAL,
                "document": v["document"] or "",
                "page": v["page"],
                "description": v["description"],
                "basis": "; ".join(v["basis"]) if isinstance(v["basis"], list | tuple) else str(v["basis"] or ""),
            }
        )
    return {"findings": out, "summary": f"движок: {len(out)} находок «нарушено» и «проверить вручную»"}


# --- скилы руки A1 ------------------------------------------------------------------------------------------------

# Публичные источники из bim-data — те же, на которых стоят пакеты правил spds и ru. Курируемой таблицы замен,
# правил и кода «Нулевого цикла» здесь нет (PROTOCOL, рука A1).
A1_SOURCES = [
    "norms/ru/GOST_R_21.101-2020_SPDS_osnovnye_trebovaniya.pdf",
    "norms/ru/PP_87_sostav_razdelov_PD_consultant.txt",
    "norms/ru/PP_145_gosekspertiza_polozhenie_consultant.txt",
    "norms/ru/Prikaz_Minstroy_783pr_format_elektronnyh_dokumentov_consultant.txt",
    "norms/ru/Pismo_Minstroy_16187-OG14_2025_XML_consultant.txt",
    "norms/ru/Pismo_Minstroy_25724-IF00_2023_format_consultant.txt",
    "datasets/stroykompleks/ntd_documents.json",
]

A1_AUTHOR_TASK = """Ты — сильный пользователь Claude Code. Подготовь скилы Claude Code, с которыми агент проверит пакет
проектной документации перед экспертизой на формальные дефекты. Агент получит такую задачу:

---
{task}
---

Источники — только файлы в папке sources/:
- ГОСТ Р 21.101-2020 (PDF);
- постановления Правительства РФ № 87 (состав разделов ПД) и № 145 (экспертиза);
- приказ Минстроя № 783/пр и письма Минстроя о форматах электронных документов;
- выгрузка реестра документов по стандартизации «Стройкомплекс.РФ» — ntd_documents.json.

Других источников нет: ни веба, ни пакетов-примеров.

Что сдать — папку .claude/skills/ в текущей папке:
- один или несколько скилов: SKILL.md с описанием, когда скил применять, и инструкциями;
- если нужно — вспомогательные скрипты на Python 3 и справочные файлы, выписанные из источников. Для скриптов
  доступны pdfplumber, pypdfium2, lxml и Pillow, команда python;
- короткий README.md о том, что делают скилы.

Скрипты проверь на тестовых PDF, которые сделаешь сам. Работай только в текущей папке: файлы вне неё не открывай и
не ищи. Бюджет ограничен — уложись примерно в час работы."""
