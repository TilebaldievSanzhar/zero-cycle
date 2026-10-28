"""Правила пакета `kz` (PLAN, шаг 0.7k): что подаётся на экспертизу вместе с ПСД и на какие нормы ссылается проект.

- `kz.completeness.ird` — исходно-разрешительные и сопроводительные документы по Правилам КВЭ (Приказ № 299,
  прил. 2–4): самая частая категория замечаний РК — 27 из 49 заключений (docs/bench/REMARKS_KZ.md).
- `kz.norms.agsk1` — ссылки на нормы: заменённые редакции и документы, которых нет в АГСК-1.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from functools import cache
from importlib import resources

from zerocycle.model import Anchor, Document, Family, Severity, Status
from zerocycle.norms.designation import NormDesignation, twins_to_cyrillic
from zerocycle.norms.index import NormIndex
from zerocycle.rules.base import Applies, Fixture, Outcome, rule
from zerocycle.rules.context import Context
from zerocycle.rules.fixtures import case, doc, package, page

MAX_ANCHORS = 3
KZ = Applies(jurisdictions=("kz",))
RULES_BASE = "Правила КВЭ (Приказ № 299)"
INDEX_BASE = "АГСК-1 (июнь 2025)"


def _read(name: str) -> str:
    return resources.files("zerocycle.packs.kz").joinpath(name).read_text(encoding="utf-8")


@dataclass(frozen=True)
class IrdItem:
    id: str
    title: str
    basis: dict[str, str]  # вид работ → пункт приложения
    names: tuple[re.Pattern[str], ...]
    heads: tuple[re.Pattern[str], ...]
    weak: tuple[re.Pattern[str], ...] = ()
    condition: str | None = None
    condition_for: dict[str, str] = field(default_factory=dict)

    def condition_of(self, work: str) -> str | None:
        return self.condition_for.get(work, self.condition)


@cache
def ird_items() -> tuple[IrdItem, ...]:
    d = tomllib.loads(_read("ird.toml"))

    def rx(xs: list[str]) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(x, re.IGNORECASE) for x in xs)

    return tuple(
        IrdItem(
            id=i["id"],
            title=i["title"],
            basis=dict(i["basis"]),
            names=rx(i.get("names", [])),
            heads=rx(i.get("heads", [])),
            weak=rx(i.get("weak", [])),
            condition=i.get("condition"),
            condition_for=dict(i.get("condition_for", {})),
        )
        for i in d["item"]
    )


@cache
def agsk1() -> NormIndex:
    return NormIndex.model_validate(json.loads(_read("agsk1.json")))


def _norm(text: str) -> str:
    return " ".join(twins_to_cyrillic(text.replace("_", " ")).lower().replace("ё", "е").split())


@dataclass
class _Hit:
    doc: Document
    page: int | None
    quote: str
    by: str  # «имя файла» или «заголовок страницы»


_CONCLUSION = re.compile(r"заключени\w*\s*(?:экспертиз|гэ\b|госэксп)|заключени\w*\s+(?:№|n)|экспертн\w+\s+заключ")


def _is_conclusion(ctx: Context, d: Document) -> bool:
    """Заключение экспертизы — результат, а не часть подачи: в пакете из закупки оно лежит рядом с ПСД."""
    if _CONCLUSION.search(_norm(d.path)):
        return True
    first = min(ctx.pages_of(d).values(), key=lambda p: p.number, default=None)
    return bool(first and re.search(r"заключени", _norm(first.title)) and "экспертиз" in _norm(first.text[:2000]))


def _search(ctx: Context, item_names: tuple[re.Pattern[str], ...], heads: tuple[re.Pattern[str], ...]) -> list[_Hit]:
    """Документ по имени файла, затем — по заголовку страницы (первые строки без пунктов перечня)."""
    docs = [d for d in ctx.package.documents if not _is_conclusion(ctx, d)]
    hits: list[_Hit] = []
    for d in docs:
        name = d.path.rsplit("/", 1)[-1]
        if any(p.search(_norm(name)) for p in item_names):
            hits.append(_Hit(d, None, name, "имя файла"))
    named = {h.doc.path for h in hits}
    for d in docs if heads else ():
        if d.path in named:
            continue
        stamped = {p.number for p in d.pages if p.title_block is not None}
        for pt in sorted(ctx.pages_of(d).values(), key=lambda p: p.number):
            if pt.number in stamped:
                continue  # лист с основной надписью — страница проекта, а не документ ИРД: там только упоминания
            title = _norm(pt.title)
            if m := next((m for p in heads if (m := p.search(title))), None):
                hits.append(_Hit(d, pt.number, title[max(0, m.start() - 20) : m.end() + 40], "заголовок страницы"))
                break
    return hits


def _anchors(ctx: Context, hits: list[_Hit]) -> tuple[Anchor, ...]:
    return tuple(ctx.anchor(h.doc, h.page, quote=h.quote) for h in hits[:MAX_ANCHORS])


def _where(hits: list[_Hit]) -> str:
    parts = [f"«{h.doc.path}»" + (f", с. {h.page}" if h.page else "") for h in hits[:MAX_ANCHORS]]
    return "; ".join(parts) + (f" и ещё {len(hits) - MAX_ANCHORS}" if len(hits) > MAX_ANCHORS else "")


def _hit_values(hits: list[_Hit], key: str) -> dict:
    """Значения находки — всё, что названо в тексте: документы, страницы, сколько ещё не показано."""
    return {key: [h.doc.path for h in hits], "pages": [h.page for h in hits], "more": max(0, len(hits) - MAX_ANCHORS)}


# --- фикстуры комплектности ---------------------------------------------------------------------------------------

_FULL_NEW = [
    "1. Решение об инвестировании проекта.pdf",
    "2. АПЗ.pdf",
    "3. Госакт на земельный участок.pdf",
    "Геологический отчет.pdf",
    "Топографическая съемка.pdf",
    "12. ТУ электроснабжение.pdf",
    "Задание на проектирование.pdf",
    "Согласование эскизного проекта.pdf",
    "Лицензия ГСЛ на проектную деятельность.pdf",
    "Расчет стоимости ПИР.pdf",
    "Перечень цен по прейскурантам.pdf",
    "Казахстанское содержание.pdf",
]


def _ird_case(names: list[str], texts: dict[tuple[str, int], str] | None = None, scans: int = 0, **params):
    def build():
        # ОПЗ: лист 1 — со штампом записки, листы 2–3 — приложения без основной надписи
        docs = [doc(n, 1) for n in names] + [
            doc("Том 1. ОПЗ.pdf", [page(1, {1: "311-2026-ОПЗ", 2: "Строительство ФАП"}), page(2), page(3)])
        ]
        if scans:
            docs.append(doc("Приложения.pdf", [page(i, has_text=False) for i in range(1, scans + 1)]))
        return case(package(*docs, work_kind=params.pop("work", "new")), texts, **params)

    return build


def _without(*drop: str) -> list[str]:
    return [n for n in _FULL_NEW if not any(d in n for d in drop)]


@rule(
    "kz.completeness.ird",
    version="0.1.0",
    title="Исходно-разрешительная документация на экспертизу — по Правилам КВЭ",
    family=Family.completeness,
    kind="presence",
    severity=Severity.blocker,
    basis=(f"{RULES_BASE}, прил. 2–4",),
    applies=KZ,
    why=(
        "Без исходно-разрешительной документации экспертиза не рассматривает проект по существу; "
        "в заключениях РК это самая частая формальная правка: «представлено АПЗ», «предоставлен отчёт по ИГИ», "
        "«представлены ТУ»."
    ),
    remark="Представить {документ} ({основание}).",
    evidence=(
        "REMARKS_KZ: ИРД и согласования — 69 пунктов в 27 заключениях из 49",
        "REMARKS_KZ: документы сметы (казахстанское содержание, прайс-листы, расчёт ПИР) — 33 пункта в 20 заключениях",
    ),
    fixtures=(
        Fixture("всё на месте", _ird_case(_FULL_NEW), {Status.ok: 12, Status.violated: 0, Status.manual: 0}),
        Fixture(
            "нет АПЗ и госакта",
            _ird_case(_without("АПЗ", "Госакт")),
            {Status.violated: 2},
            items={"apz": Status.violated, "land-title": Status.violated, "surveys-geology": Status.ok},
        ),
        Fixture(
            "АПЗ — приложением к ОПЗ, а в тексте записки — только упоминание госакта",
            _ird_case(
                _without("АПЗ", "Госакт"),
                {
                    ("Том 1. ОПЗ.pdf", 2): "АРХИТЕКТУРНО-ПЛАНИРОВОЧНОЕ ЗАДАНИЕ (АПЗ) на проектирование № KZ12",
                    ("Том 1. ОПЗ.pdf", 3): "1. Общие данные. "
                    + "Исходные данные. " * 30
                    + "госакт на земельный участок",
                },
            ),
            {},
            items={"apz": Status.ok, "land-title": Status.violated},
        ),
        Fixture(
            "задание упомянуто на листе со штампом — не документ",
            _ird_case(
                _without("Задание"),
                {("Том 1. ОПЗ.pdf", 1): "ЗАДАНИЕ НА ПРОЕКТИРОВАНИЕ утверждено заказчиком 12.01.2026"},
            ),
            {},
            items={"design-assignment": Status.violated},
        ),
        Fixture(
            "бюджетная программа вместо решения об инвестировании",
            _ird_case([*_without("Решение об инвестировании"), "4. Бюджетная программа.pdf"]),
            {},
            items={"investment-decision": Status.manual},
        ),
        Fixture(
            "не найдено, но есть сканы без текста",
            _ird_case(_without("АПЗ"), scans=3),
            {},
            items={"apz": Status.manual},
        ),
        Fixture(
            "капремонт: свой перечень",
            _ird_case(["Дефектный акт.pdf", "Задание на проектирование.pdf"], work="capital_repair"),
            {},
            items={"defect-acts": Status.ok, "design-assignment": Status.ok, "investment-decision": Status.violated},
        ),
        Fixture("вид работ не определён", lambda: case(package(doc("АПЗ.pdf"))), {Status.cannot_check: 1}),
    ),
)
def completeness_ird(ctx: Context) -> Iterator[Outcome]:
    work = ctx.work_kind
    if work is None:
        yield Outcome(
            Status.cannot_check,
            "Не определён вид работ — строительство, реконструкция или капитальный ремонт: перечень Правил КВЭ "
            "зависит от него",
        )
        return
    sheets = {(d.path, p.number): p for d in ctx.pdfs() for p in d.pages}
    scans = [(d, n) for d, n in ctx.textless() if sheets[(d.path, n)].title_block is None]  # сканы приложений
    scan_docs = sorted({d.path for d, _ in scans})
    searched = {"files": len(ctx.package.documents), "text_pages": sum(1 for _ in ctx.text_pages())}
    for item in ird_items():
        if work not in item.basis:
            continue
        basis = (f"{RULES_BASE}, {item.basis[work]}",)
        strong = _search(ctx, item.names, item.heads)
        if strong:
            yield Outcome(
                Status.ok,
                f"{item.title}: {_where(strong)}",
                anchors=_anchors(ctx, strong),
                values=_hit_values(strong, "found") | {"by": strong[0].by},
                basis=basis,
                item=item.id,
            )
            continue
        weak = _search(ctx, item.weak, item.weak)
        if weak:
            yield Outcome(
                Status.manual,
                f"{item.title}: не найден, есть похожий документ — {_where(weak)}; проверить, подходит ли он",
                anchors=_anchors(ctx, weak),
                values=_hit_values(weak, "similar"),
                basis=basis,
                item=item.id,
            )
            continue
        condition = item.condition_of(work)
        if condition:
            yield Outcome(
                Status.manual,
                f"{item.title}: не найден — проверить: {condition}",
                values={"searched": searched},
                basis=basis,
                item=item.id,
            )
        elif scans:
            yield Outcome(
                Status.manual,
                f"{item.title}: не найден ни по именам файлов, ни в начале страниц; {len(scans)} листов без текстового "
                f"слоя и без основной надписи — документ может быть среди сканов: {', '.join(scan_docs[:MAX_ANCHORS])}",
                values={"searched": searched, "scan_pages": len(scans), "scan_documents": scan_docs},
                basis=basis,
                item=item.id,
            )
        else:
            yield Outcome(
                Status.violated,
                f"{item.title}: не найден ни по именам файлов, ни в начале страниц",
                values={"searched": searched},
                basis=basis,
                item=item.id,
            )


# --- ссылки на нормы по АГСК-1 ------------------------------------------------------------------------------------


def _with_rk(ref: NormDesignation) -> NormDesignation | None:
    """«СН 2.02-02-2023», «СП 2.04-107-2022» — номер в системе РК, но без «РК» в обозначении."""
    if ref.kind in ("СН", "СП") and re.fullmatch(r"\d\.\d{2}-\d{2,3}", ref.number) and (ref.year or 0) >= 2000:
        return replace(ref, kind=f"{ref.kind} РК", issues=(*ref.issues, "без «РК» в обозначении"))
    return None


def _norm_case(*texts: str):
    def build():
        docs = [doc(f"Документ {i}.pdf", 1) for i in range(1, len(texts) + 1)]
        return case(package(*docs), {(f"Документ {i}.pdf", 1): t for i, t in enumerate(texts, 1)})

    return build


@rule(
    "kz.norms.agsk1",
    version="0.1.0",
    title="Ссылки на нормы — на действующие редакции по АГСК-1",
    family=Family.norms,
    kind="reference",
    severity=Severity.major,
    basis=(INDEX_BASE,),
    applies=KZ,
    why=(
        "Проект должен опираться на действующие нормы; ссылку на заменённую редакцию эксперт возвращает "
        "с формулировкой «ссылки на нормативные документы актуализировать»."
    ),
    remark="Заменить ссылку на {было} действующим {стало}.",
    evidence=(
        "REMARKS_KZ: ссылки на нормы — 34 пункта в 16 заключениях из 49",
        "REMARKS_KZ: «Вместо СНиП 3.05.04-85, утратившего силу, указан действующий СН РК 4.01-02-2013»",
    ),
    fixtures=(
        Fixture("заменённая редакция", _norm_case("согласно СН РК 1.02-03-2011*"), {Status.violated: 1, Status.ok: 0}),
        Fixture("действующая редакция", _norm_case("климат по СП РК 2.04-01-2017"), {Status.ok: 1, Status.violated: 0}),
        Fixture("документа нет в АГСК-1", _norm_case("по СНиП 3.05.04-85"), {Status.manual: 1}),
        Fixture("ГОСТ — вне АГСК-1", _norm_case("оформлено по ГОСТ 21.101-97"), {Status.cannot_check: 1, Status.ok: 0}),
        Fixture("нет текста", lambda: case(package(doc("АР.pdf", 2))), {Status.cannot_check: 1}),
    ),
)
def norms_agsk1(ctx: Context) -> Iterator[Outcome]:
    index = agsk1()
    ctx.sources[INDEX_BASE] = f"sha256 {index.sha256[:12]}, по состоянию на {index.as_of.isoformat()}"
    hits = ctx.norm_refs()
    if not hits:
        yield Outcome(Status.cannot_check, "В тексте пакета нет ссылок на нормы или нет текстового слоя")
        return
    by_code: dict[str, list] = defaultdict(list)
    for h in hits:
        by_code[h.designation.code].append(h)
    current_refs = current_docs = 0
    uncovered: Counter[str] = Counter()
    for code, group in sorted(by_code.items()):
        ref = group[0].designation
        v = index.verdict(ref)
        if v.status == "not_listed" and (alt := _with_rk(ref)) and index.verdict(alt).status != "not_listed":
            ref, v = alt, index.verdict(alt)  # «СН 2.02-02-2023» в пакете РК — «СН РК 2.02-02-2023» без «РК»
            code = ref.code
        anchors = tuple(h.anchor for h in group[:MAX_ANCHORS])
        if v.status in ("outdated_edition", "replaced") and v.current:
            cur = v.current
            title = f" «{cur.title}»" if cur.title else ""
            since = f", введён с {cur.since}" if cur.since else ""
            yield Outcome(
                Status.violated,
                f"Ссылка на {code} ({len(group)} раз): в АГСК-1 действует {cur.code}{title}{since}",
                anchors=anchors,
                values={
                    "ref": code,
                    "occurrences": len(group),
                    "current": cur.code,
                    "title": cur.title,
                    "since": cur.since,
                    "verdict": v.status,
                },
                basis=(f"{INDEX_BASE}, с. {cur.page}",) if cur.page else (INDEX_BASE,),
                item=code,
            )
        elif v.status == "not_listed":
            as_of = f"{index.as_of:%d.%m.%Y}"
            near = f"; в АГСК-1 есть {v.current.code} — проверить год в ссылке" if v.current else ""
            yield Outcome(
                Status.manual,
                f"{code} ({len(group)} раз) нет в АГСК-1 на {as_of}{near}: проверить, действует ли документ",
                anchors=anchors,
                values={
                    "ref": code,
                    "occurrences": len(group),
                    "as_of": as_of,
                    "near": v.current.code if v.current else None,
                },
                item=code,
            )
        elif v.status == "not_covered":
            uncovered[ref.kind] += len(group)
        else:
            current_refs += len(group)
            current_docs += 1
    if current_docs:
        yield Outcome(
            Status.ok,
            f"{current_refs} ссылок на {current_docs} документов, действующих по АГСК-1",
            values={"refs": current_refs, "documents": current_docs},
        )
    if uncovered:
        kinds = ", ".join(f"{k} — {n}" for k, n in uncovered.most_common())
        yield Outcome(
            Status.cannot_check,
            f"Ссылки не проверены — АГСК-1 не перечисляет эти виды документов: {kinds}",
            values={"kinds": dict(uncovered)},
        )
