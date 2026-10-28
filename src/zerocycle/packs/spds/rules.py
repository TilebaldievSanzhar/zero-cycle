"""Правила семьи СПДС: основная надпись, листы документа, «Состав проектной документации», ПДЭ.

- графа 1 — обозначение без ошибок записи и одно на всех листах документа; подписи — фамилии в строках ролей;
- листы — нумерация подряд (графа 7), «Листов» (графа 8), формат против размера листа (графа 26);
- «Состав проектной документации» против томов пакета; ПДЭ — GUID и имена файлов (только РФ).

Редакция стандарта — по юрисдикции: ГОСТ 21.101-97 в РК, ГОСТ Р 21.101-2020 в РФ (дополнение к ADR 0003);
основание находки берётся по редакции контекста проверки.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import PurePosixPath

from zerocycle.extract.designation import belongs_to, interpret, parse
from zerocycle.model import Anchor, Designation, Document, Family, Page, PdeInfo, PdeNode, Severity, Status, TitleBlock
from zerocycle.norms.designation import to_cyrillic
from zerocycle.packs import Codebook, codebook
from zerocycle.rules.base import Applies, Fixture, Outcome, rule
from zerocycle.rules.context import Context
from zerocycle.rules.fixtures import case, doc, package, page, title_block
from zerocycle.units import format_size, normalize_format, paper_format, pt

MAX_ANCHORS = 5
_DIGIT = re.compile(r"\d")
_SHEET_NO = re.compile(r"\d{1,4}")  # графы 7 и 8: «3а», «3.1» (добавленные листы, 7.3.9) — не судим
_SERIOUS = ("нет базового обозначения", "в базовом обозначении нет цифр")  # остальное — запись: тире, пробел, латиница
UNREADABLE = "(cid:"  # pdfplumber: глиф без Unicode — текст графы не читается
_TRIVIAL = re.compile(r"[\W\d_]{0,2}")  # «№», «-», «1» — обрывок соседней ячейки, а не содержание графы


def _in_text(value: str, cb: Codebook) -> Designation | None:
    """Шифр среди текста графы 1: «Заказчик: ГУ "Управление …" ВК …» — шаблон штампа РК без базового обозначения."""
    for token in value.split():
        d = parse(token.strip('«»"(),;:'), cb)
        if d and d.code in cb.all_codes:
            return d
    return None


def _project_document(d: Document, values: list[str], cb: Codebook) -> bool:
    """Документ проекта: обозначение есть в графе 1 хотя бы одного листа или в имени файла («311-2026. Том 1 …»).

    Отчёт изысканий или эскиз сторонней организации со своим штампом — не документ проекта: его графу 1 не судим.
    """
    if any(parse(v, cb) or _in_text(v, cb) for v in values):
        return True
    return parse(d.path.rsplit("/", 1)[-1].split(" ")[0].rstrip("."), cb) is not None


def _graph1_case(*values: str):
    def build():
        sheets = [page(i, {1: v, 2: "Строительство ФАП в с. Тауыш", 6: "РП"}) for i, v in enumerate(values, 1)]
        return case(package(doc("ОВ.pdf", sheets)))

    return build


@rule(
    "spds.titleblock.designation",
    version="0.1.0",
    title="В графе 1 основной надписи — обозначение документа без ошибок записи",
    family=Family.formatting,
    kind="format",
    severity=Severity.minor,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 1)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 1)",),
    },
    why=(
        "По обозначению документ находят в составе проекта, в ссылках и в изменениях. Обозначение без базовой части "
        "(«-ОВ») или наименование объекта вместо обозначения не позволяют отличить документы разных объектов."
    ),
    remark="В графе 1 основной надписи листов {листы} указать обозначение документа: «{было}».",
    evidence=(
        "REAL_KZ: пакет Байгабыл — ни на одном листе нет базового обозначения («-ОВ», «-АС»); в ОПЗ двух пакетов "
        "в графе 1 — наименование объекта",
        "REMARKS_KZ: «в графе 1 указаны обозначение документа согласно п. 4.1.3 ГОСТ 21.101-97»",
    ),
    fixtures=(
        Fixture("обозначение на месте", _graph1_case("311-2026-ОВ", "311-2026-ОВ"), {Status.ok: 1, Status.violated: 0}),
        Fixture("нет базового обозначения", _graph1_case("-ОВ", "-ОВ"), {Status.violated: 1}),
        Fixture(
            "наименование вместо обозначения на одном листе",
            _graph1_case("311-2026-ОВ", "Строительство фельдшерско-акушерского пункта"),
            {Status.violated: 1, Status.ok: 1},
        ),
        Fixture("тире вместо дефиса", _graph1_case("311–2026–ОВ"), {Status.violated: 1}),
        Fixture(
            "шифр среди текста заказчика",
            _graph1_case('Заказчик: ГУ "Управление строительства" ВК градостроительства'),
            {Status.violated: 1},
            items={'ОВ.pdf:Заказчик: ГУ "Управление строительства" ВК градостроительства': Status.violated},
        ),
        Fixture(
            "нечитаемый шрифт и обрывок «№» — не судим",
            _graph1_case("311-2026-ОВ", "(cid:476)(cid:509)рои", "№"),
            {Status.ok: 1, Status.violated: 0},
        ),
        Fixture(
            "отчёт изысканий со своим штампом — не документ проекта",
            lambda: case(package(doc("Геологический отчет.pdf", [page(1, {1: "«Строительство ФАП в с. Тауыш»"})]))),
            {Status.cannot_check: 1, Status.violated: 0},
        ),
        Fixture("нет основных надписей", lambda: case(package(doc("ОПЗ.pdf", 2))), {Status.cannot_check: 1}),
    ),
)
def titleblock_designation(ctx: Context) -> Iterator[Outcome]:
    cb = codebook(ctx.jurisdiction)
    groups: dict[tuple[str, str], list[tuple[Document, Page, TitleBlock]]] = defaultdict(list)
    by_doc: dict[str, list[str]] = defaultdict(list)
    for d, p, tb in ctx.title_blocks():
        value = " ".join(tb.get(1).split())
        if not value or UNREADABLE in value or _TRIVIAL.fullmatch(value):
            continue  # нечитаемый шрифт или обрывок ячейки («№») — не обозначение и не ошибка
        groups[(d.path, value)].append((d, p, tb))
        by_doc[d.path].append(value)
    project = {d.path for d in ctx.package.documents if d.path in by_doc and _project_document(d, by_doc[d.path], cb)}
    groups = {k: v for k, v in groups.items() if k[0] in project}
    if not groups:
        yield Outcome(Status.cannot_check, "Нет основных надписей документов проекта с читаемой графой 1")
        return
    good = 0
    for (path, value), sheets in sorted(groups.items()):
        designation = parse(value, cb)
        issues = list(designation.issues) if designation else []
        if designation is None and (designation := _in_text(value, cb)):
            issues = [*designation.issues, "в графе 1 кроме шифра — посторонний текст"]
        elif designation is None:
            issues = ["не обозначение: в графе 1 текст, а не шифр документа"]
        if not issues:
            good += len(sheets)
            continue
        serious = designation is None or any(i in _SERIOUS for i in issues)
        yield Outcome(
            Status.violated,
            f"«{path}», графа 1 «{value}» на {len(sheets)} листах: {'; '.join(issues)}",
            anchors=tuple(ctx.graph_anchor(d, p, tb, 1) for d, p, tb in sheets[:MAX_ANCHORS]),
            values={"document": path, "graph1": value, "sheets": len(sheets), "issues": issues},
            severity=Severity.major if serious else Severity.minor,
            item=f"{path}:{value}",
        )
    if good:
        yield Outcome(Status.ok, f"Обозначение в графе 1 без замечаний на {good} листах", values={"sheets": good})


_ROLE = re.compile(r"[\s.]+")
# роли строк подписей, как их пишут в штампах; «m конmр» (шрифт САПР без кодировки) — не роль, а нечитаемый текст
_KNOWN_ROLE = re.compile(
    r"^(?:разраб|выполн|исполн|составил|чертил|провер|н\s*контр|норм|т\s*контр|гип|гап|гл\s*(?:спец|инж|архит)|"
    r"глав|нач|зав|рук|утв|соглас|инж|техн|эксперт|консульт|архит|констр|гл\s*констр)",
    re.IGNORECASE,
)


def _sign_case(*rows: tuple[str, ...]):
    def build():
        tb = title_block({1: "311-2026-ПБ", 2: "Строительство ФАП в с. Тауыш", 6: "РП"}, form=5, signatures=rows)
        return case(package(doc("ПБ.pdf", [page(1, tb), page(2, tb)])))

    return build


@rule(
    "spds.titleblock.signatures",
    version="0.2.0",
    title="В строках подписей основной надписи у каждой роли есть фамилия и дата",
    family=Family.formatting,
    kind="format",
    severity=Severity.major,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графы 10–11)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графы 10–11)",),
    },
    why=(
        "Строка подписи без фамилии — документ без ответственного: эксперт возвращает с замечанием «поставить "
        "подписи и фамилии исполнителей и ГИПа». Дата подписания — графа 13; если даты в штампе пишут, строка "
        "без даты — подпись, которую не поставили."
    ),
    remark="В основной надписи листов {листы} указать фамилию и дату в строке «{роль}».",
    evidence=(
        "REAL_KZ: пакет Тауыш — в книге ПБ у «ГИП» и «Выполнил» нет фамилий",
        "REMARKS_KZ: основная надпись, подписи, титульные листы — 37 пунктов в 14 заключениях из 49",
    ),
    fixtures=(
        Fixture("фамилии на месте", _sign_case(("Разраб.", "Иванов"), ("ГИП", "Петров")), {Status.ok: 1}),
        Fixture(
            "ГИП без фамилии",
            _sign_case(("Выполнил", "Иванов"), ("ГИП", "")),
            {Status.violated: 1, Status.ok: 0},
        ),
        Fixture("нет строк подписей", _sign_case(), {Status.cannot_check: 1}),
        Fixture(
            "роль не читается — не судим",
            _sign_case(("Разраб.", "Иванов"), ("m конmр", ""), ("Н контроль", "")),
            {Status.violated: 1},
            items={"ПБ.pdf:Н контроль": Status.violated},
        ),
        Fixture(
            "нет даты, у остальных строк есть",
            _sign_case(("Разраб.", "Иванов", ""), ("Пров.", "Петров", "25.09.26"), ("ГИП", "Сидоров", "25.09.26")),
            {Status.violated: 1},
            items={"ПБ.pdf:Разраб:дата": Status.violated},
        ),
        Fixture(
            "даты не пишут во всём штампе — не судим",
            _sign_case(("Разраб.", "Иванов", ""), ("ГИП", "Сидоров", "")),
            {Status.violated: 0, Status.ok: 1},
        ),
    ),
)
def titleblock_signatures(ctx: Context) -> Iterator[Outcome]:
    missing: dict[tuple[str, str, str], list[tuple[Document, Page, TitleBlock]]] = defaultdict(list)
    signed = 0
    for d, p, tb in ctx.title_blocks():
        rows = []
        for row in tb.signatures:
            role = _ROLE.sub(" ", row.role).strip()
            if not role or UNREADABLE in role or not _KNOWN_ROLE.match(to_cyrillic(role).lower()):
                continue
            rows.append((role, row))
        dated = any(row.date.strip() for _, row in rows)  # даты не пишут нигде в штампе — так оформлен шаблон
        for role, row in rows:
            if not row.name.strip():
                missing[(d.path, role, "фамилии")].append((d, p, tb))
            elif dated and not row.date.strip():
                missing[(d.path, role, "даты")].append((d, p, tb))
            else:
                signed += 1
    if not missing and not signed:
        yield Outcome(Status.cannot_check, "В основных надписях не прочитаны строки подписей")
        return
    for (path, role, what), sheets in sorted(missing.items()):
        yield Outcome(
            Status.violated,
            f"«{path}»: в строке «{role}» нет {what} на {len(sheets)} листах",
            anchors=tuple(ctx.anchor(d, p.number, tb.bbox, role) for d, p, tb in sheets[:MAX_ANCHORS]),
            values={"document": path, "role": role, "missing": what, "sheets": len(sheets)},
            basis=(f"{ctx.edition}, прил. {'Ж' if ctx.edition == 'ГОСТ Р 21.101-2020' else 'Д'} (графа 13)",)
            if what == "даты" and ctx.edition
            else (),
            item=f"{path}:{role}" + (":дата" if what == "даты" else ""),
        )
    if not missing:
        yield Outcome(Status.ok, f"Фамилии и даты есть во всех {signed} строках подписей", values={"rows": signed})


# --- листы документа: нумерация (графа 7) и «Листов» (графа 8) ------------------------------------------------------


@dataclass
class Sheet:
    doc: Document
    page: Page
    tb: TitleBlock
    number: int | None  # графа 7
    total: int | None  # графа 8 — только на первом листе документа


@dataclass
class Run:
    """Документ по листам: подряд идущие листы одного обозначения в графе 1; лист с графой 8 начинает новый."""

    key: str
    sheets: list[Sheet] = field(default_factory=list)
    keys: set[str] = field(default_factory=set)  # записи графы 1 на листах документа
    unstamped: int = 0  # страницы без найденной основной надписи между листами документа
    trailing: int = 0  # такие же страницы после последнего листа — до следующего документа или конца файла

    @property
    def first(self) -> Sheet:
        return self.sheets[0]

    def continues(self, sheet: Sheet, key: str, next_key: str | None) -> bool:
        """Лист — продолжение документа.

        Лист без графы 8 продолжает документ, если у него та же запись графы 1, если она вернётся на следующем листе
        (один лист с чужим обозначением) или если номер листа идёт подряд. Лист с графой 8 — первый лист нового
        документа, кроме шаблонов, где «Листов» повторяют на каждом листе (пакеты РК): тот же «Листов» и номер
        не меньше прежнего — тот же документ; номер 1 или меньше прежнего — новый.
        """
        last = self.sheets[-1].number
        follows = sheet.number is not None and last is not None and sheet.number == last + 1
        same = key in self.keys or next_key in self.keys or follows
        if sheet.total is not None:
            later = sheet.number is None or last is None or (sheet.number >= last and sheet.number != 1)
            return same and later and sheet.total == self.first.total
        return same


def _int(value: str) -> int | None:
    value = value.strip()
    return int(value) if _SHEET_NO.fullmatch(value) else None


def _key(tb: TitleBlock) -> str | None:
    value = " ".join(tb.get(1).split())
    if not value or UNREADABLE in value or _TRIVIAL.fullmatch(value):
        return None
    return to_cyrillic(value.replace(" ", "")).upper()


def runs(ctx: Context) -> list[Run]:
    """Документы пакета по листам. Продолжение документа в следующем файле (без графы 8) — тот же документ.

    Один лист с чужим обозначением посреди документа документ не разрывает: это ошибка графы 1 одного листа
    (её показывает `spds.titleblock.consistent`), а не начало другого документа.
    """
    out: list[Run] = []
    for d in ctx.pdfs():
        entries = [(p, p.title_block, _key(p.title_block) if p.title_block else None) for p in d.pages]
        keys_after = [next((k for _, _, k in entries[i + 1 :] if k), None) for i in range(len(entries))]
        current: Run | None = None
        pending = 0
        for i, (p, tb, key) in enumerate(entries):
            if key is None:
                pending += 1 if current else 0
                continue
            sheet = Sheet(d, p, tb, _int(tb.get(7)), _int(tb.get(8)))
            if current is not None and current.continues(sheet, key, keys_after[i]):
                current.sheets.append(sheet)
                current.keys.add(key)
                current.unstamped += pending
            else:
                if current is not None:
                    current.trailing = pending
                current = Run(key, [sheet], {key})
                out.append(current)
            pending = 0
        if current is not None:
            current.trailing = pending
    merged: list[Run] = []
    for r in out:
        head = next((m for m in reversed(merged) if r.key in m.keys and m.first.total is not None), None)
        if r.first.total is None and head is not None and head.sheets[-1].doc is not r.first.doc:
            head.sheets += r.sheets
            head.keys |= r.keys
            head.unstamped += head.trailing + r.unstamped
            head.trailing = r.trailing
        else:
            merged.append(r)
    return merged


def _changed(run: Run) -> bool:
    return any(s.tb.changes for s in run.sheets)


def _sheets_case(*docs_: tuple[str, list[tuple[str, str, str]]]):
    """Документы: (файл, [(графа 1, графа 7, графа 8)…]); пустая графа 1 — страница без основной надписи."""

    def build():
        out = []
        for path, sheets in docs_:
            pages = []
            for i, (g1, g7, g8) in enumerate(sheets, 1):
                graphs = {k: v for k, v in {1: g1, 7: g7, 8: g8}.items() if v}
                pages.append(page(i, graphs if g1 else None))
            out.append(doc(path, pages))
        return case(package(*out, jurisdiction="ru"))

    return build


_TCH = "2345-ПЗ.ТЧ"
_GCH = "2345-АР.ГЧ"


@rule(
    "spds.sheets.numbering",
    version="0.1.0",
    title="Листы документа пронумерованы подряд, без пропусков и повторов",
    family=Family.formatting,
    kind="format",
    severity=Severity.minor,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 7)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 7)",),
    },
    why=(
        "Пропуск в нумерации — признак потерянного или выброшенного листа: эксперт сверяет листы с ведомостью "
        "и возвращает документ «доукомплектовать». Аннулированный лист номер не освобождает (7.3.9), но такой "
        "лист видно по таблице изменений."
    ),
    remark="Проверить комплектность и нумерацию листов документа {документ}: после листа {лист} — лист {следующий}.",
    evidence=(
        "Каталог дефектов засеянного корпуса: F01 — пропуск в нумерации листов; D02 — лист выброшен, а номер остался",
        "REMARKS_KZ: листы — нумерация, ведомости, «Листов» — среди частых формальных пунктов",
    ),
    fixtures=(
        Fixture(
            "подряд", _sheets_case(("ПЗ.pdf", [(_TCH, "1", "3"), (_TCH, "2", ""), (_TCH, "3", "")])), {Status.ok: 1}
        ),
        Fixture(
            "пропуск номера",
            _sheets_case(("ПЗ.pdf", [(_TCH, "1", "4"), (_TCH, "2", ""), (_TCH, "4", ""), (_TCH, "5", "")])),
            {Status.violated: 1, Status.ok: 0},
        ),
        Fixture(
            "страница без штампа между листами — не пропуск",
            _sheets_case(("ПЗ.pdf", [(_TCH, "1", "3"), ("", "", ""), (_TCH, "3", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "новый документ в том же файле начинается с 1",
            _sheets_case(("АР.pdf", [(_TCH, "1", "2"), (_TCH, "2", ""), (_GCH, "1", "2"), (_GCH, "2", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "повтор номера",
            _sheets_case(("ПЗ.pdf", [(_TCH, "1", "3"), (_TCH, "2", ""), (_TCH, "2", "")])),
            {Status.manual: 1, Status.violated: 0},
        ),
        Fixture("нет номеров листов", _sheets_case(("ПЗ.pdf", [(_TCH, "", "1")])), {Status.cannot_check: 1}),
    ),
)
def sheets_numbering(ctx: Context) -> Iterator[Outcome]:
    checked = good = 0
    for run in runs(ctx):
        numbered = [s for s in run.sheets if s.number is not None]
        if len(numbered) < 2:
            continue
        checked += 1
        problems = 0
        for a, b in pairwise(numbered):
            if b.number == 1:
                break  # нумерация заново — другой документ с тем же обозначением в графе 1
            pages = b.page.number - a.page.number if a.doc is b.doc else 1
            step = b.number - a.number
            if step <= 0:
                status, what = Status.manual, "номер повторяется или идёт назад"
            elif step > pages:
                status = Status.manual if _changed(run) else Status.violated
                what = f"пропущено номеров: {step - pages}"
            else:
                continue
            problems += 1
            yield Outcome(
                status,
                f"«{b.doc.path}», {run.key}: после листа {a.number} — лист {b.number} ({what})",
                anchors=(ctx.graph_anchor(a.doc, a.page, a.tb, 7), ctx.graph_anchor(b.doc, b.page, b.tb, 7)),
                values={
                    "document": b.doc.path,
                    "graph1": run.key,
                    "after": a.number,
                    "next": b.number,
                    "missing": max(step - pages, 0),
                    "changes": _changed(run),
                },
                item=f"{b.doc.path}:{run.key}:{a.number}",
            )
        good += not problems
    if not checked:
        yield Outcome(
            Status.cannot_check, "Нет документов с прочитанными номерами листов (графа 7)", values={"graph": 7}
        )
    elif good:
        yield Outcome(Status.ok, f"Листы пронумерованы подряд в {good} документах", values={"documents": good})


@rule(
    "spds.sheets.count",
    version="0.1.0",
    title="«Листов» на первом листе равно числу листов документа",
    family=Family.formatting,
    kind="format",
    severity=Severity.minor,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 8)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 8)",),
    },
    why=(
        "Графа 8 — общее количество листов документа: по ней проверяют, что документ пришёл целиком. Расхождение "
        "значит, что лист потерян, лишний или графа не обновлена после изменения."
    ),
    remark="В графе 8 первого листа документа {документ} указать {листов} — по числу листов.",
    evidence=("Каталог дефектов засеянного корпуса: F02 — «Листов» не равно числу листов документа",),
    fixtures=(
        Fixture("совпадает", _sheets_case(("ПЗ.pdf", [(_TCH, "1", "2"), (_TCH, "2", "")])), {Status.ok: 1}),
        Fixture(
            "меньше листов",
            _sheets_case(("ПЗ.pdf", [(_TCH, "1", "1"), (_TCH, "2", "")])),
            {Status.violated: 1, Status.ok: 0},
        ),
        Fixture(
            "титул в общем счёте: последний лист — 3",
            _sheets_case(("ПЗ.pdf", [("", "", ""), (_TCH, "2", "3"), (_TCH, "3", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "документ в двух файлах",
            _sheets_case(
                ("АР ч1.pdf", [("2345-АР", "1", "4"), ("2345-АР", "2", "")]),
                ("АР ч2.pdf", [("2345-АР", "3", ""), ("2345-АР", "4", "")]),
            ),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "лист с чужим обозначением посреди документа — не другой документ",
            _sheets_case(("ПЗ.pdf", [(_TCH, "1", "3"), ("2354-ПЗ.ТЧ", "2", ""), (_TCH, "3", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "последний лист с чужим обозначением, нумерация продолжается",
            _sheets_case(("ПЗ.pdf", [(_TCH, "1", "2"), ("2345-ПБ.ТЧ", "2", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "«Листов» на каждом листе (шаблон РК) и добавленный лист «3.1»",
            _sheets_case(("АР.pdf", [(_GCH, "1", "4"), (_GCH, "2", "4"), (_GCH, "3", "4"), (_GCH, "3.1", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "«Листов» на каждом листе, лист с нечитаемым номером посередине",
            _sheets_case(("ЭС.pdf", [(_GCH, "1", "4"), (_GCH, "2", "4"), ("", "", ""), (_GCH, "4", "4")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "последние листы без читаемой основной надписи",
            _sheets_case(("СС.pdf", [(_GCH, "1", "3"), ("", "", ""), ("", "", "")])),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture("нет графы 8", _sheets_case(("ПЗ.pdf", [(_TCH, "1", ""), (_TCH, "2", "")])), {Status.cannot_check: 1}),
    ),
)
def sheets_count(ctx: Context) -> Iterator[Outcome]:
    checked = good = 0
    for run in runs(ctx):
        total = run.first.total
        if total is None:
            continue
        checked += 1
        stamped = len(run.sheets)
        numbers = [s.number for s in run.sheets if s.number is not None]
        last = max(numbers) if numbers else stamped
        # листы без прочитанной основной надписи внутри и после документа — тоже могут быть его листами
        spans = (stamped, stamped + run.unstamped, stamped + run.unstamped + run.trailing, last, last + run.trailing)
        if total in spans:
            good += 1
            continue
        first = run.first
        yield Outcome(
            Status.violated,
            f"«{first.doc.path}», {run.key}: в графе 8 — {total}, листов в документе {stamped}"
            + (f", последний номер {last}" if last != stamped else ""),
            anchors=(ctx.graph_anchor(first.doc, first.page, first.tb, 8),),
            values={"document": first.doc.path, "graph1": run.key, "graph8": total, "sheets": stamped, "last": last},
            item=f"{first.doc.path}:{run.key}",
        )
    if not checked:
        yield Outcome(Status.cannot_check, "Нет документов с прочитанной графой 8 «Листов»", values={"graph": 8})
    elif good:
        yield Outcome(Status.ok, f"«Листов» совпадает с числом листов в {good} документах", values={"documents": good})


# --- формат листа (графа 26 / надпись «Формат») ---------------------------------------------------------------------


SAME_SCALE = 0.07  # отклонение ширины левой части штампа от 65 мм, при котором печать считаем 1:1


def _written_format(tb: TitleBlock, edition: str | None) -> str | None:
    text = tb.format_text or (tb.get(26) if edition == "ГОСТ Р 21.101-2020" else "")
    return normalize_format(text) if text else None


def _format_case(*sheets: tuple[str, str, float]):
    """Листы: (запись формата, фактический формат страницы, масштаб штампа)."""

    def build():
        pages = []
        for i, (written, paper, scale) in enumerate(sheets, 1):
            short, long_ = format_size(paper)
            tb = title_block({1: "2345-АР", **({26: written} if written else {})})
            tb = tb.model_copy(update={"format_text": written or None, "scale": scale})
            p = page(i, tb).model_copy(update={"width": pt(long_), "height": pt(short), "paper": paper})
            pages.append(p)
        return case(package(doc("АР.pdf", pages), jurisdiction="ru"))

    return build


@rule(
    "spds.sheets.format",
    version="0.1.0",
    title="Формат, записанный у основной надписи, совпадает с размером листа",
    family=Family.formatting,
    kind="format",
    severity=Severity.minor,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (формат листа)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 26)",),
    },
    why=(
        "По графе 26 печатают и проверяют масштаб: для электронного документа указывают формат, на котором "
        "изображение соответствует масштабу. Если лист другого размера, масштаб чертежа не тот, что подписан."
    ),
    remark="На листе {лист} документа {документ} формат в графе 26 «{было}» не совпадает с размером листа {стало}.",
    evidence=("Каталог дефектов засеянного корпуса: F08 — формат в графе 26 не совпадает с размером листа",),
    fixtures=(
        Fixture("совпадает", _format_case(("А3", "A3", 1.0), ("А4х3", "A4x3", 1.0)), {Status.ok: 1}),
        Fixture("лист больше записанного", _format_case(("А3", "A3", 1.0), ("А3", "A2", 1.0)), {Status.violated: 1}),
        Fixture(
            "печать с уменьшением: штамп тоже уменьшен",
            _format_case(("А1", "A3", 0.5)),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture(
            "нестандартный штамп и чужой формат — проверить",
            _format_case(("А1", "A3", 0.8)),
            {Status.violated: 0, Status.manual: 1},
        ),
        Fixture("формат не записан", _format_case(("", "A3", 1.0)), {Status.cannot_check: 1}),
    ),
)
def sheets_format(ctx: Context) -> Iterator[Outcome]:
    groups: dict[tuple[str, str, str, Status], list[tuple[Document, Page, TitleBlock]]] = defaultdict(list)
    good = 0
    for d, p, tb in ctx.title_blocks():
        written = _written_format(tb, ctx.edition)
        if written is None or not p.width or not p.height:
            continue
        if abs(tb.scale - 1) <= SAME_SCALE:
            actual, status = p.paper, Status.violated
        else:  # штамп не 1:1 — печать с уменьшением или шаблон не по ГОСТ: формат по размеру с поправкой
            true_size = paper_format(p.width / tb.scale, p.height / tb.scale)
            actual = written if written in (true_size, p.paper) else p.paper
            status = Status.manual
        if actual is None:
            continue  # нестандартный размер страницы: обрезка, скан — не судим
        if actual == written:
            good += 1
            continue
        groups[(d.path, written, actual, status)].append((d, p, tb))
    if not groups and not good:
        yield Outcome(Status.cannot_check, "У основных надписей не прочитана запись формата листа")
        return
    for (path, written, actual, status), sheets in sorted(groups.items()):
        scaled = status == Status.manual
        yield Outcome(
            status,
            f"«{path}», листов {len(sheets)}: записан формат {written}, размер листа — {actual}"
            + (", штамп не в масштабе 1:1 — проверить масштаб печати" if scaled else ""),
            anchors=tuple(ctx.graph_anchor(d, p, tb, 26) for d, p, tb in sheets[:MAX_ANCHORS]),
            values={
                "document": path,
                "written": written,
                "actual": actual,
                "sheets": len(sheets),
                "pages": [p.number for _, p, _ in sheets],
                "scale": [round(tb.scale, 2) for _, _, tb in sheets[:MAX_ANCHORS]],
            },
            item=f"{path}:{written}:{actual}",
        )
    if good:
        yield Outcome(Status.ok, f"Формат совпадает с размером на {good} листах", values={"sheets": good})


# --- «Состав проектной документации» ↔ тома пакета ------------------------------------------------------------------


def _volumes(ctx: Context, base: str) -> list[Document]:
    """Тома пакета: документы с обозначением раздела (шифр, базовая часть пакета), кроме ведомостей и содержаний."""
    cb = ctx.codebook
    out = []
    for d in ctx.package.documents:
        des = d.designation
        if des is None or des.suffix or des.code is None or des.base != base:
            continue
        sec = interpret(des, cb)
        if sec is not None and sec.number is not None:
            out.append(d)
    return out


def _matches(a: Designation, b: Designation) -> bool:
    return a.core == b.core or belongs_to(a, b) or belongs_to(b, a)


def _composition_case(listed: list[str], present: list[str]):
    def build():
        cb = codebook("ru")
        docs = []
        for code in present:
            d = doc(f"{code}/2345-{code}.pdf", 2)
            d.designation = parse(f"2345-{code}", cb)
            docs.append(d)
        sp = doc("СП/2345-СП.pdf", 1)
        sp.designation = parse("2345-СП", cb)
        docs.append(sp)
        text = "Состав проектной документации\n" + "\n".join(f"{i} 2345-{c} Раздел" for i, c in enumerate(listed, 1))
        return case(package(*docs, jurisdiction="ru"), {(sp.path, 1): text})

    return build


@rule(
    "spds.composition.volumes",
    version="0.1.0",
    title="«Состав проектной документации» и тома пакета совпадают",
    family=Family.completeness,
    kind="consistency",
    severity=Severity.major,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Р (форма 16)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, 8.1.6, прил. Т",),
    },
    why=(
        "Эксперт принимает пакет по ведомости «Состав проектной документации»: том из ведомости, которого нет, — "
        "некомплект; том, которого нет в ведомости, — неучтённый документ."
    ),
    remark="Привести в соответствие «Состав проектной документации» и тома: {том}.",
    evidence=(
        "Каталог дефектов засеянного корпуса: F09 — том из «Состава ПД» отсутствует; F05 — том не указан "
        "в «Составе ПД»",
    ),
    fixtures=(
        Fixture("совпадают", _composition_case(["ПЗ", "АР", "ИОС1"], ["ПЗ", "АР", "ИОС1"]), {Status.ok: 1}),
        Fixture(
            "тома из ведомости нет в пакете",
            _composition_case(["ПЗ", "АР", "ИОС1"], ["ПЗ", "АР"]),
            {Status.violated: 1},
            items={"указан, нет в пакете: 2345-ИОС1": Status.violated},
        ),
        Fixture(
            "тома нет в ведомости",
            _composition_case(["ПЗ", "АР"], ["ПЗ", "АР", "ОДИ"]),
            {Status.violated: 1},
            items={"нет в составе: 2345-ОДИ": Status.violated},
        ),
        Fixture(
            "книги тома записаны томом",
            _composition_case(["ПЗ", "ИОС4"], ["ПЗ", "ИОС4.1", "ИОС4.2"]),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture("нет ведомости", lambda: case(package(doc("АР.pdf", 2), jurisdiction="ru")), {Status.cannot_check: 1}),
    ),
)
def composition_volumes(ctx: Context) -> Iterator[Outcome]:
    listed = ctx.composition()
    if len(listed) < 2:
        yield Outcome(Status.cannot_check, "Ведомость «Состав проектной документации» не найдена или не прочитана")
        return
    base = Counter(item.designation.base for item in listed).most_common(1)[0][0]
    volumes = _volumes(ctx, base)
    matched = 0
    for item in listed:
        if item.designation.base != base:
            continue
        if any(_matches(d.designation, item.designation) for d in volumes):
            matched += 1
            continue
        yield Outcome(
            Status.violated,
            f"Том {item.designation.text} указан в «Составе проектной документации», а в пакете его нет",
            anchors=(item.anchor,),
            values={"designation": item.designation.text, "line": item.line},
            item=f"указан, нет в пакете: {item.designation.text}",
        )
    for d in volumes:
        if any(_matches(d.designation, item.designation) for item in listed):
            continue
        yield Outcome(
            Status.violated,
            f"Том {d.designation.text} («{d.path}») есть в пакете, но не указан в «Составе проектной документации»",
            anchors=(ctx.anchor(d, 1, quote=d.designation.text),),
            values={"designation": d.designation.text, "document": d.path},
            item=f"нет в составе: {d.designation.text}",
        )
    if matched:
        yield Outcome(
            Status.ok,
            f"Томов из «Состава проектной документации» на месте: {matched}",
            values={"volumes": matched},
        )


# --- ПДЭ: GUID и имена файлов (ГОСТ Р 21.101-2020, 8.2) -------------------------------------------------------------


PDE = Applies(jurisdictions=("ru",))


def _pde_case(nodes: list[tuple[str, str, str]]):
    """Документы info.xml: (GUID, Code, FileName); файлы пакета — по FileName."""

    def build():
        cb = codebook("ru")
        docs = []
        for _guid, code, name in nodes:
            d = doc(name, 1)
            d.designation = parse(code, cb)
            docs.append(d)
        pkg = package(*docs, jurisdiction="ru")
        pkg.pde = PdeInfo(
            file="info.xml",
            sha256="0" * 64,
            nodes=[
                PdeNode(kind="document", guid=g, attrs={"Code": c}, file_name=n, xpath=f"/Package/Document[{i}]")
                for i, (g, c, n) in enumerate(nodes, 1)
            ],
        )
        return case(pkg)

    return build


_G1, _G2 = "8a9a021e-a648-47dd-8683-9eb905b6e6e3", "619699cf-e198-4ad9-b06c-144a025b413f"


@rule(
    "spds.pde.guid",
    version="0.1.0",
    title="У каждого документа ПДЭ свой GUID",
    family=Family.submission_format,
    kind="format",
    severity=Severity.major,
    basis=("ГОСТ Р 21.101-2020, 8.2.4",),
    applies=PDE,
    why=(
        "По GUID система экспертизы отличает документы и их версии при повторной подаче: два документа с одним "
        "GUID — один из них потеряется или заменит другой."
    ),
    remark="Назначить документам {документы} разные GUID в info.xml.",
    evidence=("Каталог дефектов засеянного корпуса: F07 — повтор GUID в info.xml",),
    fixtures=(
        Fixture(
            "разные",
            _pde_case([(_G1, "2345-ПЗ", "01/2345-ПЗ.pdf"), (_G2, "2345-АР", "03/2345-АР.pdf")]),
            {Status.ok: 1},
        ),
        Fixture(
            "повтор",
            _pde_case([(_G1, "2345-ПЗ", "01/2345-ПЗ.pdf"), (_G1, "2345-АР", "03/2345-АР.pdf")]),
            {Status.violated: 1, Status.ok: 0},
        ),
        Fixture("нет info.xml", lambda: case(package(doc("АР.pdf", 1), jurisdiction="ru")), {Status.cannot_check: 1}),
    ),
)
def pde_guid(ctx: Context) -> Iterator[Outcome]:
    pde = ctx.package.pde
    if pde is None:
        yield Outcome(Status.cannot_check, "В пакете нет info.xml — пакет не в форме ПДЭ")
        return
    by_guid: dict[str, list[PdeNode]] = defaultdict(list)
    for n in pde.nodes:
        if n.kind == "document" and n.guid:
            by_guid[n.guid.lower()].append(n)
    dupes = {g: ns for g, ns in by_guid.items() if len(ns) > 1}
    for guid, ns in sorted(dupes.items()):
        names = [n.attrs.get("Code") or n.file_name or n.xpath for n in ns]
        yield Outcome(
            Status.violated,
            f"GUID {guid} у {len(ns)} документов: {', '.join(names)}",
            anchors=tuple(
                Anchor(file=pde.file, sha256=pde.sha256, xpath=n.xpath, quote=n.guid) for n in ns[:MAX_ANCHORS]
            ),
            values={"guid": guid, "documents": names, "count": len(ns)},
            item=guid,
        )
    unique = sum(1 for ns in by_guid.values() if len(ns) == 1)
    if unique and not dupes:
        yield Outcome(Status.ok, f"GUID уникальны у {unique} документов", values={"documents": unique})


_FORBIDDEN = re.compile(r'[\\/:*?"<>|]')  # недопустимые в имени файла символы — заменяют на «_» (8.2.6)


def _stem(path: str, whole: bool = False) -> str:
    """Имя файла без расширения для сравнения с обозначением; whole — весь путь без расширения."""
    p = PurePosixPath(path)
    name = str(p.with_suffix("")) if whole else p.stem
    return to_cyrillic(name.replace(" ", "")).upper()


@rule(
    "spds.pde.file-names",
    version="0.1.0",
    title="Имя файла документа ПДЭ начинается с его обозначения",
    family=Family.submission_format,
    kind="format",
    severity=Severity.minor,
    basis=("ГОСТ Р 21.101-2020, 8.2.6",),
    applies=PDE,
    why=(
        "Имя файла ПДЭ составляют из обозначения документа, номера листа и наименования (8.2.6): по нему документ "
        "находят в пакете без info.xml. «ПЗ1_финал.pdf» не говорит, какой это документ и какая версия."
    ),
    remark="Переименовать файл {файл} по обозначению документа: «{обозначение}».",
    evidence=("Каталог дефектов засеянного корпуса: F06 — имя файла не по обозначению",),
    fixtures=(
        Fixture(
            "по обозначению",
            _pde_case([(_G1, "2345-ПЗ", "01/2345-ПЗ.pdf"), (_G2, "2345-АР", "03/2345-АР_Изм1.pdf")]),
            {Status.ok: 1, Status.violated: 0},
        ),
        Fixture(
            "имя не по обозначению",
            _pde_case([(_G1, "2345-ПЗ", "01/ПЗ1_финал.pdf"), (_G2, "2345-АР", "03/2345-АР.pdf")]),
            {Status.violated: 1},
            items={"01/ПЗ1_финал.pdf": Status.violated},
        ),
        Fixture(
            "«/» в обозначении: «_» в имени — верно, папка — нет",
            _pde_case([(_G1, "ЖК-4/2026-ПЗ", "01/ЖК-4_2026-ПЗ.pdf"), (_G2, "ЖК-4/2026-АР", "03/ЖК-4/2026-АР.pdf")]),
            {Status.violated: 1, Status.ok: 1},
            items={"«/» в обозначении": Status.violated},
        ),
        Fixture("нет info.xml", lambda: case(package(doc("АР.pdf", 1), jurisdiction="ru")), {Status.cannot_check: 1}),
    ),
)
def pde_file_names(ctx: Context) -> Iterator[Outcome]:
    if ctx.package.pde is None:
        yield Outcome(Status.cannot_check, "В пакете нет info.xml — пакет не в форме ПДЭ")
        return
    good = 0
    folders: list[Document] = []
    for d in ctx.pdfs():
        des = d.designation
        if des is None:
            continue
        expected = to_cyrillic(_FORBIDDEN.sub("_", des.text).replace(" ", "")).upper()
        if _stem(d.path).startswith(expected):
            good += 1
            continue
        if "/" in des.text and _stem(d.path, whole=True).endswith(to_cyrillic(des.text.replace(" ", "")).upper()):
            folders.append(d)  # «ЖК-44/2026-ООС.pdf» — «/» из обозначения стал папкой
            continue
        yield Outcome(
            Status.violated,
            f"Файл «{d.path}»: имя не начинается с обозначения документа {des.text}",
            anchors=(ctx.anchor(d, 1, quote=des.text),),
            values={"document": d.path, "designation": des.text},
            item=d.path,
        )
    if folders:
        yield Outcome(
            Status.violated,
            f"Файлов {len(folders)}: «/» из обозначения стал папкой («{folders[0].path}») — недопустимые в имени "
            f"файла символы заменяют на «_»",
            anchors=tuple(ctx.anchor(d, 1, quote=d.designation.text) for d in folders[:MAX_ANCHORS]),
            values={"files": [d.path for d in folders], "count": len(folders)},
            item="«/» в обозначении",
        )
    if good:
        yield Outcome(Status.ok, f"Имена {good} файлов начинаются с обозначения документа", values={"files": good})


# --- графа 1: одно обозначение на всех листах документа -------------------------------------------------------------


def _major_designation(parsed: list[tuple[Sheet, Designation]]) -> str | None:
    """Обозначение документа по листам: у явного большинства; поровну между «X» и «X.ТЧ» — полная запись части
    (лист без хвоста внутри части — отклонение); иначе судить не по чему."""
    counts = Counter(des.text for _, des in parsed).most_common()
    if not counts:
        return None
    (top, n), rest = counts[0], counts[1:]
    if n * 2 > len(parsed) and n >= 2:
        return top
    if len(counts) == 2 and rest[0][1] == n:
        by_text = {des.text: des for _, des in parsed}
        a, b = by_text[top], by_text[rest[0][0]]
        if b.tail and not a.tail and belongs_to(b, a):
            return b.text
        if a.tail and not b.tail and belongs_to(a, b):
            return a.text
    return None


def _block_case(*values: str, totals: dict[int, str] | None = None, designation: str | None = "2345-ПЗ"):
    """Листы одного файла: графа 1 по листам; графа 8 — на первых листах документов, нумерация — от них."""

    def build():
        cb = codebook("ru")
        pages = []
        number = 0
        for i, v in enumerate(values, 1):
            total = (totals or {}).get(i, "")
            number = 1 if total else number + 1
            graphs = {k: x for k, x in {1: v, 7: str(number), 8: total}.items() if x}
            pages.append(page(i, graphs if v else None))
        d = doc("ПЗ.pdf", pages)
        d.designation = parse(designation, cb) if designation else None
        return case(package(d, jurisdiction="ru"))

    return build


@rule(
    "spds.titleblock.consistent",
    version="0.2.0",
    title="На всех листах документа — его обозначение",
    family=Family.formatting,
    kind="consistency",
    severity=Severity.major,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 1)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 1)",),
    },
    why=(
        "Лист с чужим обозначением — лист другого документа или объекта: при изменениях и замене листов его "
        "не найдут, а эксперт считает документ некомплектным."
    ),
    remark="На листе {лист} документа {документ} в графе 1 указать «{стало}» вместо «{было}».",
    evidence=("Каталог дефектов засеянного корпуса: D03 — неверное обозначение в графе 1 (цифры, шифр, хвост)",),
    fixtures=(
        Fixture("одно обозначение", _block_case(_TCH, _TCH, _TCH, totals={1: "3"}), {Status.ok: 1}),
        Fixture(
            "переставлены цифры базовой части",
            _block_case(_TCH, "2354-ПЗ.ТЧ", _TCH, totals={1: "3"}),
            {Status.violated: 1},
            items={"ПЗ.pdf:2": Status.violated},
        ),
        Fixture(
            "первый лист без хвоста документа",
            _block_case("2345-ПЗ", _TCH, _TCH, totals={1: "3"}),
            {Status.violated: 1},
            items={"ПЗ.pdf:1": Status.violated},
        ),
        Fixture(
            "часть из двух листов: на втором снят хвост",
            _block_case(_TCH, "2345-ПЗ", totals={1: "2"}),
            {Status.violated: 1},
            items={"ПЗ.pdf:2": Status.violated},
        ),
        Fixture(
            "часть из двух листов: на первом снят хвост",
            _block_case("2345-ПЗ", _TCH, totals={1: "2"}),
            {Status.violated: 1},
            items={"ПЗ.pdf:1": Status.violated},
        ),
        Fixture(
            "чужой шифр — не обозначение этого документа",
            _block_case("2345-АР.ТЧ", totals={1: "1"}),
            {Status.violated: 1},
        ),
        Fixture(
            "содержание и части тома — свои обозначения",
            _block_case("2345-ПЗ-С", _TCH, _TCH, "2345-ПЗ.ГЧ", totals={1: "1", 2: "2", 4: "1"}),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture("нет обозначений", _block_case("", designation=None), {Status.cannot_check: 1}),
    ),
)
def titleblock_consistent(ctx: Context) -> Iterator[Outcome]:
    cb = codebook(ctx.jurisdiction)
    checked: set[str] = set()
    good = 0
    for run in runs(ctx):
        # сравниваем только то, что разбирается как обозначение: текст заказчика или наименование в графе 1 —
        # забота `spds.titleblock.designation`, а не этого правила
        parsed = []
        for s in run.sheets:
            des = parse(s.tb.get(1), cb)
            if des is not None and des.code and _DIGIT.search(des.base):
                parsed.append((s, des))
        major = _major_designation(parsed)
        for s, des in parsed:
            checked.add(s.doc.path)
            own = s.doc.designation
            if own is not None and not _matches(des.model_copy(update={"suffix": None}), own):
                problem = f"не обозначение документа {own.text}"
            elif major and des.text != major:
                problem = f"на остальных листах — «{major}»"
            else:
                good += 1
                continue
            value = " ".join(s.tb.get(1).split())
            yield Outcome(
                Status.violated,
                f"«{s.doc.path}», лист {s.page.number}: в графе 1 «{value}» — {problem}",
                anchors=(ctx.graph_anchor(s.doc, s.page, s.tb, 1),),
                values={
                    "document": s.doc.path,
                    "page": s.page.number,
                    "graph1": value,
                    "expected": own.text if own else major,
                    "major": major,
                },
                item=f"{s.doc.path}:{s.page.number}",
            )
    if not checked:
        yield Outcome(Status.cannot_check, "Нет листов с обозначением в графе 1", values={"graph": 1})
    elif good:
        yield Outcome(Status.ok, f"Обозначение согласовано на {good} листах", values={"sheets": good})
