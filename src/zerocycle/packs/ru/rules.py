"""Правила пакета `ru` (PLAN, шаг 0.7): состав разделов проектной документации и ссылки на нормы.

- `ru.completeness.pp87` — обязательные разделы по ПП РФ № 87, п. 3(1)–3(4): раздела нет ни в пакете, ни
  в ведомости «Состав проектной документации» — нарушение; условный раздел или реконструкция — проверить вручную.
- `ru.norms.registry` — ссылки на нормы по реестру «Стройкомплекс.РФ»: заменённые редакции сводов правил,
  актуализированные СНиП и заменённые добровольные стандарты из курируемой таблицы (`replaced.toml`).
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from importlib import resources

from zerocycle.extract.designation import interpret, parse
from zerocycle.model import Document, Family, ObjectKind, Severity, Status
from zerocycle.norms.designation import parse_designation as parse_norm
from zerocycle.norms.index import NormIndex
from zerocycle.packs import codebook
from zerocycle.rules.base import Applies, Fixture, Outcome, rule
from zerocycle.rules.context import Context
from zerocycle.rules.fixtures import case, doc, package

MAX_ANCHORS = 3
RU = Applies(jurisdictions=("ru",))
PP87 = "ПП РФ № 87"
INDEX_BASE = "Реестр «Стройкомплекс.РФ»"
_SECTION_WORD = re.compile(r"\bраздел\w*\s+(\d{1,2})\b", re.IGNORECASE)
_SUBSECTION = re.compile(r"\bраздел\w*\s+5\W+подраздел\w*\s+(\d)\b", re.IGNORECASE)


def _read(name: str) -> str:
    return resources.files("zerocycle.packs.ru").joinpath(name).read_text(encoding="utf-8")


# --- состав разделов по ПП 87 -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Required:
    number: int
    basis: str  # пункт ПП 87: «п. 3(1) к»
    condition: str | None = None
    subsections: tuple[int, ...] = ()
    subsections_basis: str | None = None


@cache
def required(linear: bool) -> tuple[Required, ...]:
    d = tomllib.loads(_read("pp87.toml"))
    return tuple(
        Required(
            number=s["number"],
            basis=s["basis"],
            condition=s.get("condition"),
            subsections=tuple(s.get("subsections", ())),
            subsections_basis=s.get("subsections_basis"),
        )
        for s in d["linear" if linear else "nonlinear"]
    )


def _is_linear(ctx: Context) -> bool:
    """Линейный объект: так записано в пакете или в пакете есть разделы, которые бывают только у линейных."""
    if ctx.package.object_kind is not None:
        return ctx.package.object_kind == ObjectKind.linear
    cb = ctx.codebook
    only_linear = {c for s in cb.linear for c in s.codes} - {c for s in cb.nonlinear for c in s.codes}
    return any(d.designation and d.designation.code in only_linear for d in ctx.package.documents)


def _sections(ctx: Context, linear: bool) -> dict[tuple[int, int | None], list[Document]]:
    """Разделы и подразделы, которые в пакете есть: (номер, подраздел) → документы; подраздел None — весь раздел."""
    out: dict[tuple[int, int | None], list[Document]] = defaultdict(list)
    for d in ctx.package.documents:
        if d.designation is None or d.designation.suffix:
            continue
        sec = interpret(d.designation, ctx.codebook, linear)
        if sec is None or sec.number is None:
            continue
        out[(sec.number, None)].append(d)
        if sec.subsection is not None:
            out[(sec.number, sec.subsection)].append(d)
    return out


def _listed_sections(ctx: Context, linear: bool) -> tuple[set[tuple[int, int | None]], bool]:
    """Что записано в ведомости «Состав проектной документации»: разделы и подразделы; есть ли ведомость вообще."""
    listed = ctx.composition()
    out: set[tuple[int, int | None]] = set()
    for item in listed:
        sec = interpret(item.designation, ctx.codebook, linear)
        if sec and sec.number is not None:
            out.add((sec.number, None))
            if sec.subsection is not None:
                out.add((sec.number, sec.subsection))
        for m in _SECTION_WORD.finditer(item.line):  # «Раздел 10. Требования…» — номер раздела словами ведомости
            out.add((int(m.group(1)), None))
        if m := _SUBSECTION.search(item.line):
            out.add((5, int(m.group(1))))
    return out, len(listed) >= 2


def _section_title(linear: bool, number: int, subsection: int | None = None) -> tuple[str, str]:
    cb = codebook("ru")
    sec = next((s for s in (cb.linear if linear else cb.nonlinear) if s.number == number), None)
    if sec is None:
        return f"раздел {number}", ""
    if subsection is not None:
        return sec.subsections.get(subsection, f"подраздел {subsection}"), f"{sec.codes[0]}{subsection}"
    return sec.title, sec.codes[0]


def _volume(code: str, base: str = "П-1-26") -> Document:
    d = doc(f"{code}/{base}-{code}.pdf", 2)
    d.designation = parse(f"{base}-{code}", codebook("ru"))
    return d


_ALL = ("ПЗ", "ПЗУ", "АР", "КР", "ИОС1", "ИОС2", "ИОС3", "ИОС4", "ИОС5", "ТХ", "ПОС", "ООС", "ПБ", "ТБЭ", "ОДИ", "СМ")


def _pp87_case(*codes: str, listed: tuple[str, ...] | None = None, stage: str = "П", work: str | None = "new"):
    def build():
        docs = [_volume(c) for c in codes]
        texts = {}
        if listed is not None:
            sp = doc("СП/П-1-26-СП.pdf", 1)
            sp.designation = parse("П-1-26-СП", codebook("ru"))
            docs.append(sp)
            texts[(sp.path, 1)] = "\n".join(f"{i} П-1-26-{c} Том" for i, c in enumerate(listed, 1))
        pkg = package(*docs, jurisdiction="ru", work_kind=work)
        pkg.stage = stage
        return case(pkg, texts)

    return build


def _without(*drop: str) -> tuple[str, ...]:
    return tuple(c for c in _ALL if c not in drop)


@rule(
    "ru.completeness.pp87",
    version="0.1.0",
    title="Обязательные разделы проектной документации по ПП 87",
    family=Family.completeness,
    kind="presence",
    severity=Severity.blocker,
    basis=(f"{PP87}, п. 3(1)",),
    applies=RU,
    why=(
        "Без обязательного раздела документацию не принимают на экспертизу: состав разделов для строительства "
        "установлен п. 3(1) ПП 87, смета — п. 3(4), а при реконструкции и капремонте состав задаёт задание."
    ),
    remark="Представить раздел {раздел} «{наименование}» (ПП РФ № 87, {пункт}).",
    evidence=(
        "ПП РФ № 87, п. 3(1): для строительства «в обязательном порядке включаются следующие разделы»",
        "Каталог дефектов засеянного корпуса: D09 — «обязательный раздел не представлен»",
    ),
    fixtures=(
        Fixture("все разделы на месте", _pp87_case(*_ALL), {Status.ok: 1, Status.violated: 0, Status.manual: 0}),
        Fixture(
            "нет раздела 10 ни в пакете, ни в составе",
            _pp87_case(*_without("ТБЭ"), listed=_without("ТБЭ")),
            {Status.violated: 1},
            items={"раздел 10": Status.violated},
        ),
        Fixture(
            "раздел 11 указан в составе, но тома нет",
            _pp87_case(*_without("ОДИ"), listed=_ALL),
            {Status.violated: 1},
            items={"раздел 11": Status.violated},
        ),
        Fixture(
            "смета — условный раздел",
            _pp87_case(*_without("СМ")),
            {Status.violated: 0, Status.manual: 1},
            items={"раздел 12": Status.manual},
        ),
        Fixture(
            "реконструкция: состав — по заданию",
            _pp87_case(*_without("ООС"), work="reconstruction"),
            {Status.violated: 0, Status.manual: 1},
            items={"раздел 8": Status.manual},
        ),
        Fixture(
            "нет подраздела водоотведения",
            _pp87_case(*_without("ИОС3")),
            {Status.violated: 0, Status.manual: 1},
            items={"раздел 5.3": Status.manual},
        ),
        Fixture(
            "вид работ не указан — строительство",
            _pp87_case(*_without("ПБ"), work=None),
            {Status.violated: 1},
            items={"раздел 9": Status.violated},
        ),
        Fixture("рабочая документация", _pp87_case(*_ALL, stage="Р"), {Status.cannot_check: 1, Status.ok: 0}),
    ),
)
def completeness_pp87(ctx: Context) -> Iterator[Outcome]:
    if ctx.package.stage == "Р":
        yield Outcome(Status.cannot_check, "Пакет рабочей документации: состав разделов по ПП 87 не применяется")
        return
    linear = _is_linear(ctx)
    present = _sections(ctx, linear)
    listed, has_composition = _listed_sections(ctx, linear)
    work = ctx.work_kind
    by_assignment = work in ("reconstruction", "capital_repair")
    found: list[int] = []
    for req in required(linear):
        title, code = _section_title(linear, req.number)
        basis = (f"{PP87}, {req.basis}",)
        if (req.number, None) not in present:
            if by_assignment:
                status, why = Status.manual, "при реконструкции и капремонте состав разделов задаёт задание (п. 3(3))"
                basis = (*basis, f"{PP87}, п. 3(3)")
            elif req.condition:
                status, why = Status.manual, f"обязателен {req.condition}"
            elif work is None:  # реконструкцию и капремонт называют в наименовании объекта; иначе — строительство
                status, why = Status.violated, "обязателен для строительства (вид работ в наименовании не указан)"
            else:
                status, why = Status.violated, "обязателен для строительства"
            if not has_composition:
                where = "ведомости «Состав проектной документации» в пакете нет"
            elif (req.number, None) in listed:
                where = "в «Составе проектной документации» раздел указан, но тома нет"
            else:
                where = "в «Составе проектной документации» его тоже нет"
            yield Outcome(
                status,
                f"Раздел {req.number} «{title}» ({code}) не найден в пакете: {why}; {where}",
                values={
                    "section": req.number,
                    "code": code,
                    "listed": (req.number, None) in listed,
                    "work": work,
                    "condition": req.condition,
                },
                basis=basis,
                item=f"раздел {req.number}",
            )
            continue
        found.append(req.number)
        for sub in req.subsections:
            if (req.number, sub) in present:
                continue
            sub_title, sub_code = _section_title(linear, req.number, sub)
            yield Outcome(
                Status.manual,
                f"Подраздел {req.number}.{sub} «{sub_title}» ({sub_code}) не найден: если системы нет, это "
                f"указывают в пояснительной записке",
                values={"section": req.number, "subsection": sub, "code": sub_code},
                basis=(f"{PP87}, {req.subsections_basis or req.basis}",),
                item=f"раздел {req.number}.{sub}",
            )
    if found:
        yield Outcome(
            Status.ok,
            f"Разделы на месте: {', '.join(map(str, found))}",
            values={"sections": found, "object": "linear" if linear else "nonlinear"},
        )


# --- ссылки на нормы по реестру «Стройкомплекс.РФ» ----------------------------------------------------------------


@cache
def registry() -> NormIndex:
    """Индекс реестра и курируемые замены добровольных стандартов (`replaced.toml`) — одним индексом."""
    data = json.loads(_read("stroykompleks.json"))
    for s in tomllib.loads(_read("replaced.toml"))["standard"]:
        d = parse_norm(s["code"])
        if d is None or d.code != s["code"]:
            raise ValueError(f"replaced.toml: обозначение не в канонической записи: {s['code']}")
        data["records"].append(
            {
                "code": d.code,
                "kind": d.kind,
                "number": d.number,
                "year": d.year,
                "title": s["title"],
                "replaces": s["replaces"],
                "since": s["since"],
                "basis": s["basis"],
            }
        )
        if d.kind not in data["covers"]:
            data["covers"].append(d.kind)
    return NormIndex.model_validate(data)


def _as_of(ctx: Context, index: NormIndex) -> date:
    """На какую дату судим о действии редакции: задана явно, дата подачи пакета или дата выгрузки реестра."""
    value = ctx.params.get("as_of") or ctx.package.submitted
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value or index.as_of


def _since(value: str | None) -> date | None:
    return datetime.strptime(value, "%d.%m.%Y").date() if value else None


def _norm_case(*texts: str, **params):
    def build():
        docs = [doc(f"Документ {i}.pdf", 1) for i in range(1, len(texts) + 1)]
        pkg = package(*docs, jurisdiction="ru")
        return case(pkg, {(f"Документ {i}.pdf", 1): t for i, t in enumerate(texts, 1)}, **params)

    return build


@rule(
    "ru.norms.registry",
    version="0.1.0",
    title="Ссылки на нормы — на действующие редакции по реестру «Стройкомплекс.РФ»",
    family=Family.norms,
    kind="reference",
    severity=Severity.major,
    basis=(INDEX_BASE,),
    applies=RU,
    why=(
        "С 01.03.2026 экспертиза проверяет требования по реестру «Стройкомплекс.РФ»: ссылка на прежнюю редакцию "
        "свода правил или на СНиП, который актуализирован сводом правил, — замечание «актуализировать ссылки»."
    ),
    remark="Заменить ссылку на {было} действующим {стало}.",
    evidence=(
        "Реестр «Стройкомплекс.РФ», 21.10.2026: 9 сводов правил со свежей заменой редакции (СП 6.13130.2021 → 2025, "
        "СП 3.13130.2009 → 2026, СП 131.13330.2020 → 2025), 106 актуализированных СНиП в наименованиях сводов правил",
        "Каталог дефектов засеянного корпуса: D07 — устаревшая редакция по реестру; D07R — заменённый добровольный "
        "стандарт вне реестра (ГОСТ Р 21.1101-2013)",
    ),
    fixtures=(
        Fixture("прежняя редакция СП", _norm_case("согласно СП 54.13330.2016"), {Status.violated: 1, Status.ok: 0}),
        Fixture("актуализированный СНиП", _norm_case("по СНиП 2.04.02-84*"), {Status.violated: 1}),
        Fixture("заменённый стандарт СПДС", _norm_case("оформлено по ГОСТ Р 21.1101-2013"), {Status.violated: 1}),
        Fixture(
            "действующие редакции",
            _norm_case("СП 54.13330.2022; ГОСТ Р 21.101-2020; СП 256.1325800.2016"),
            {Status.ok: 1, Status.violated: 0, Status.manual: 0},
        ),
        Fixture(
            "новая редакция ещё не вступила на дату подачи",
            _norm_case("по СП 6.13130.2021", as_of="2026-03-01"),
            {Status.manual: 1, Status.violated: 0},
        ),
        Fixture(
            "нет в реестре обязательных требований",
            _norm_case("ГОСТ 21.002-2014, СП 31-110-2003"),
            {Status.cannot_check: 1, Status.manual: 0, Status.violated: 0},
        ),
        Fixture("ссылка новее реестра", _norm_case("по СП 54.13330.2030"), {Status.manual: 1}),
        Fixture("нет текста", lambda: case(package(doc("АР.pdf", 2), jurisdiction="ru")), {Status.cannot_check: 1}),
    ),
)
def norms_registry(ctx: Context) -> Iterator[Outcome]:
    index = registry()
    ctx.sources[INDEX_BASE] = f"sha256 {index.sha256[:12]}, выгрузка {index.as_of.isoformat()}"
    hits = ctx.norm_refs()
    if not hits:
        yield Outcome(Status.cannot_check, "В тексте пакета нет ссылок на нормы или нет текстового слоя")
        return
    as_of = _as_of(ctx, index)
    fetched = f"{index.as_of:%d.%m.%Y}"
    by_code: dict[str, list] = defaultdict(list)
    for h in hits:
        by_code[h.designation.code].append(h)
    current_refs = current_docs = 0
    unlisted: dict[str, list[str]] = defaultdict(list)
    uncovered: Counter[str] = Counter()
    for code, group in sorted(by_code.items()):
        ref = group[0].designation
        v = index.verdict(ref)
        anchors = tuple(h.anchor for h in group[:MAX_ANCHORS])
        cur = v.current
        if v.status in ("outdated_edition", "replaced") and cur:
            title = f" «{cur.title}»" if cur.title else ""
            values = {
                "ref": code,
                "occurrences": len(group),
                "current": cur.code,
                "title": cur.title,
                "since": cur.since,
                "verdict": v.status,
            }
            basis = (cur.basis,) if cur.basis else (f"{INDEX_BASE}, выгрузка {fetched}, запись {cur.source_id}",)
            since = _since(cur.since)
            if since and since > as_of:
                yield Outcome(
                    Status.manual,
                    f"{code} ({len(group)} раз): {cur.code} введён с {cur.since}, позже даты проверки "
                    f"{as_of:%d.%m.%Y} — проверить, по какой редакции подаётся документация",
                    anchors=anchors,
                    values={**values, "as_of": f"{as_of:%d.%m.%Y}"},
                    basis=basis,
                    item=code,
                )
                continue
            yield Outcome(
                Status.violated,
                f"Ссылка на {code} ({len(group)} раз): действует {cur.code}{title}"
                + (f", введён с {cur.since}" if cur.since else ""),
                anchors=anchors,
                values=values,
                basis=basis,
                item=code,
            )
        elif v.status == "not_listed" and cur:  # номер есть, а такого года нет: ссылка новее реестра или опечатка
            yield Outcome(
                Status.manual,
                f"{code} ({len(group)} раз): в реестре на {fetched} — {cur.code}; проверить год в ссылке",
                anchors=anchors,
                values={"ref": code, "occurrences": len(group), "near": cur.code, "fetched": fetched},
                item=code,
            )
        elif v.status == "not_listed":
            unlisted[ref.kind].append(code)
        elif v.status == "not_covered":
            uncovered[ref.kind] += len(group)
        else:
            current_refs += len(group)
            current_docs += 1
    if current_docs:
        yield Outcome(
            Status.ok,
            f"{current_refs} ссылок на {current_docs} документов, действующих по реестру",
            values={"refs": current_refs, "documents": current_docs},
        )
    if unlisted:
        kinds = ", ".join(f"{k} — {len(v)}" for k, v in sorted(unlisted.items(), key=lambda kv: -len(kv[1])))
        yield Outcome(
            Status.cannot_check,
            f"Нет в реестре обязательных требований, редакция не проверена: {kinds}",
            values={
                "unlisted": {k: sorted(v) for k, v in unlisted.items()},
                "counts": {k: len(v) for k, v in unlisted.items()},
            },
        )
    if uncovered:
        kinds = ", ".join(f"{k} — {n}" for k, n in uncovered.most_common())
        yield Outcome(
            Status.cannot_check,
            f"Ссылки не проверены — реестр не перечисляет эти виды документов: {kinds}",
            values={"kinds": dict(uncovered)},
        )


# --- ТЭП: пояснительная записка ↔ тома -----------------------------------------------------------------------------


# показатели ПП 87, п. 10 «м», и площадь участка из раздела 2; «общая площадь квартир» — другой показатель
TEP = {
    "Площадь застройки": r"площадь\s+застройки",
    "Общая площадь здания": r"общая\s+площадь(?:\s+здания)?(?!\s*(?:квартир|помещен|жил|нежил|кварт))",
    "Строительный объём": r"строительный\s+объ[её]м(?!\s*(?:подземн|надземн))",
    "Количество этажей": r"количество\s+этажей|этажность",
    "Площадь участка": r"площадь\s+(?:земельного\s+)?участка",
}
_TEP_LINE = re.compile(
    r"^\s*(?:\d{1,2}[.)]?\s+)?(?P<label>" + "|".join(f"(?:{rx})" for rx in TEP.values()) + r")"
    r"[^\d\n]{0,14}?(?P<value>\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
_TEP_KEYS = [(name, re.compile(rf"^(?:{rx})$", re.IGNORECASE)) for name, rx in TEP.items()]


@dataclass(frozen=True)
class TepFact:
    name: str
    value: float
    line: str
    doc: Document
    page: int


def _number(raw: str) -> float:
    return float(raw.replace(" ", "").replace(" ", "").replace(",", "."))


def tep_facts(ctx: Context) -> list[TepFact]:
    """Показатели из текста страниц: строка «Площадь застройки 1 608,4 м²» — показатель, число и якорь."""
    out = []
    for d, pt in ctx.text_pages():
        for line in pt.text.splitlines():
            m = _TEP_LINE.match(line)
            if not m:
                continue
            label = " ".join(m.group("label").split())
            name = next((n for n, rx in _TEP_KEYS if rx.match(label)), None)
            if name:
                out.append(TepFact(name, _number(m.group("value")), " ".join(line.split()), d, pt.number))
    return out


def _tep_case(pz: str, **volumes: str):
    def build():
        cb = codebook("ru")
        docs, texts = [], {}
        for code, text in {"ПЗ": pz, **volumes}.items():
            d = doc(f"{code}/2345-{code}.pdf", 1)
            d.designation = parse(f"2345-{code}", cb)
            docs.append(d)
            texts[(d.path, 1)] = text
        return case(package(*docs, jurisdiction="ru"), texts)

    return build


_PZ_TEP = "Площадь застройки 1 608,4 м²\nОбщая площадь здания 32 584,3 м²\nКоличество этажей 26 шт."


@rule(
    "ru.tep.consistent",
    version="0.1.0",
    title="ТЭП в томах совпадают с пояснительной запиской",
    family=Family.consistency,
    kind="consistency",
    severity=Severity.major,
    basis=(f"{PP87}, п. 10 м", f"{PP87}, п. 3"),
    applies=RU,
    why=(
        "Технико-экономические показатели приводят в пояснительной записке (п. 10 «м»), а решения разделов должны "
        "быть совместимы (п. 3): площадь застройки в схеме участка или общая площадь в архитектурных решениях, "
        "отличная от ПЗ, — замечание «увязать ТЭП»."
    ),
    remark="Увязать {показатель}: в ПЗ — {было}, в томе {том} — {стало}.",
    evidence=("Каталог дефектов засеянного корпуса: D01 — ТЭП в разных томах не совпадают",),
    fixtures=(
        Fixture(
            "совпадают",
            _tep_case(_PZ_TEP, АР="Площадь застройки 1 608,4 м²\nОбщая площадь здания 32 584,3 м²"),
            {Status.ok: 1, Status.violated: 0},
        ),
        Fixture(
            "переставлены цифры",
            _tep_case(_PZ_TEP, ПЗУ="Площадь застройки 1 806,4 м²"),
            {Status.violated: 1},
            items={"ПЗУ/2345-ПЗУ.pdf:Площадь застройки": Status.violated},
        ),
        Fixture(
            "общая площадь квартир — другой показатель",
            _tep_case(_PZ_TEP, АР="Общая площадь квартир 21 000,0 м²"),
            {Status.violated: 0},
        ),
        Fixture(
            "нет ТЭП в ПЗ", _tep_case("Пояснительная записка", АР="Площадь застройки 10 м²"), {Status.cannot_check: 1}
        ),
    ),
)
def tep_consistent(ctx: Context) -> Iterator[Outcome]:
    facts = tep_facts(ctx)
    cb = ctx.codebook

    def is_pz(d: Document) -> bool:
        sec = interpret(d.designation, cb) if d.designation and not d.designation.suffix else None
        return sec is not None and sec.number == 1

    reference: dict[str, list[TepFact]] = defaultdict(list)
    others: dict[tuple[str, str], list[TepFact]] = defaultdict(list)
    for f in facts:
        if is_pz(f.doc):
            reference[f.name].append(f)
        else:
            others[(f.doc.path, f.name)].append(f)
    if not reference:
        yield Outcome(Status.cannot_check, "В пояснительной записке не найдены ТЭП (площадь застройки, общая площадь…)")
        return
    agreed = 0
    for (path, name), got in sorted(others.items()):
        ref = reference.get(name, [])
        if len({f.value for f in ref}) != 1 or len({f.value for f in got}) != 1:
            continue  # несколько зданий или этапов — по одному числу не сравнить
        r, g = ref[0], got[0]
        if r.value == g.value:
            agreed += 1
            continue
        yield Outcome(
            Status.violated,
            f"{name}: в ПЗ — «{r.line}», в «{path}» — «{g.line}»",
            anchors=(ctx.anchor(r.doc, r.page, quote=r.line), ctx.anchor(g.doc, g.page, quote=g.line)),
            values={"indicator": name, "reference": r.line, "actual": g.line, "document": path},
            item=f"{path}:{name}",
        )
    if agreed:
        yield Outcome(Status.ok, f"ТЭП совпадают с ПЗ: {agreed} сравнений", values={"compared": agreed})
