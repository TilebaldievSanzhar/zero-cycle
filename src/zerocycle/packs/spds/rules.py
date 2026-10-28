"""Правила семьи СПДС: графы основной надписи — обозначение документа и подписи.

Редакция стандарта — по юрисдикции: ГОСТ 21.101-97 в РК, ГОСТ Р 21.101-2020 в РФ (дополнение к ADR 0003);
основание находки берётся по редакции контекста проверки.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator

from zerocycle.extract.designation import parse
from zerocycle.model import Designation, Document, Family, Page, Severity, Status, TitleBlock
from zerocycle.norms.designation import to_cyrillic
from zerocycle.packs import Codebook, codebook
from zerocycle.rules.base import Fixture, Outcome, rule
from zerocycle.rules.context import Context
from zerocycle.rules.fixtures import case, doc, package, page, title_block

MAX_ANCHORS = 5
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


def _sign_case(*rows: tuple[str, str]):
    def build():
        tb = title_block({1: "311-2026-ПБ", 2: "Строительство ФАП в с. Тауыш", 6: "РП"}, form=5, signatures=rows)
        return case(package(doc("ПБ.pdf", [page(1, tb), page(2, tb)])))

    return build


@rule(
    "spds.titleblock.signatures",
    version="0.1.0",
    title="В строках подписей основной надписи у каждой роли есть фамилия",
    family=Family.formatting,
    kind="format",
    severity=Severity.major,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графы 10–11)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графы 10–11)",),
    },
    why=(
        "Строка подписи без фамилии — документ без ответственного: эксперт возвращает с замечанием «поставить "
        "подписи и фамилии исполнителей и ГИПа»."
    ),
    remark="В основной надписи листов {листы} указать фамилию в строке «{роль}».",
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
    ),
)
def titleblock_signatures(ctx: Context) -> Iterator[Outcome]:
    missing: dict[tuple[str, str], list[tuple[Document, Page, TitleBlock]]] = defaultdict(list)
    signed = 0
    for d, p, tb in ctx.title_blocks():
        for row in tb.signatures:
            role = _ROLE.sub(" ", row.role).strip()
            if not role or UNREADABLE in role or not _KNOWN_ROLE.match(to_cyrillic(role).lower()):
                continue
            if row.name.strip():
                signed += 1
            else:
                missing[(d.path, role)].append((d, p, tb))
    if not missing and not signed:
        yield Outcome(Status.cannot_check, "В основных надписях не прочитаны строки подписей")
        return
    for (path, role), sheets in sorted(missing.items()):
        yield Outcome(
            Status.violated,
            f"«{path}»: в строке «{role}» нет фамилии на {len(sheets)} листах",
            anchors=tuple(ctx.anchor(d, p.number, tb.bbox, role) for d, p, tb in sheets[:MAX_ANCHORS]),
            values={"document": path, "role": role, "sheets": len(sheets)},
            item=f"{path}:{role}",
        )
    if not missing:
        yield Outcome(Status.ok, f"Фамилии есть во всех {signed} строках подписей", values={"rows": signed})
