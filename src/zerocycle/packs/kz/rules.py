"""Правила пакета `kz` (PLAN, шаг 0.7k): что подаётся на экспертизу вместе с ПСД и на какие нормы ссылается проект.

- `kz.completeness.ird` — исходно-разрешительные и сопроводительные документы по Правилам КВЭ (Приказ № 299,
  прил. 2–4): самая частая категория замечаний РК — 27 из 49 заключений (docs/bench/REMARKS_KZ.md).
- `kz.norms.agsk1` — ссылки на нормы: заменённые редакции и документы, которых нет в АГСК-1.
- `kz.completeness.expertise` — движок v2 (docs/bench/PROTOCOL_V3.md): документы, которые эксперты РК часто
  заставляли добавить для объектов такого вида (знание из заключений — `expertise.py`), но которых нет в Правилах КВЭ.
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
from zerocycle.rules.context import TITLE_LINES, Context
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
            # заголовок — строка, а не предложение: «Исходя из предоставленного Технического отчёта по
            # инженерно-геологическим изысканиям…» в альбоме расчётов — упоминание (Тауыш, движок v2)
            line = _norm(" ".join(_heading_lines(pt.text)))
            if m := next((m for p in heads if (m := p.search(line))), None):
                hits.append(_Hit(d, pt.number, line[max(0, m.start() - 20) : m.end() + 40], "заголовок страницы"))
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
    version="0.3.0",
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
        if v.status in ("outdated_edition", "replaced") and v.current:
            # в наименовании действующего документа — не ссылка
            group = [h for h in group if not h.in_title_of(v.current.kind, v.current.number)]
            if not group:
                continue
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


# --- знание экспертизы (движок v2) ------------------------------------------------------------------------------

PRACTICE_BASE = "Практика экспертизы РК"
# вид документа знания → пункт ird.toml, если имена разные
IRD_ID = {
    "defect-act": "defect-acts",
    "survey-report": "survey-acts",
    "approvals": "other-approvals",
    "license": "designer-license",
}
PRACTICE_SHARE = 0.08  # проверяем документ, если его требовали хотя бы в каждом двенадцатом заключении такого вида
PRACTICE_MIN = 3  # и не меньше чем в трёх заключениях
MIN_POPULATION = 15  # заключений в группе меньше — частота шумная, берём группу шире


def practice(ctx: Context) -> dict:
    """Знание экспертизы: из параметров (фикстуры) или собранное (packs/kz/expertise.json)."""
    from zerocycle.packs.kz.expertise import knowledge

    return ctx.params.get("expertise_knowledge") or knowledge()


def practice_stats(k: dict, doc_id: str, obj: str | None, work: str | None) -> tuple[int, int, str]:
    """(в скольких заключениях требовали, сколько заключений, группа): вид объекта и работ, если заключений
    хватает, иначе вид объекта, иначе все."""
    pop, stats = k.get("population", {}), k.get("doc_types", {}).get(doc_id, {})
    for key in (f"{obj}/{work}" if obj and work else None, obj, "all"):
        if key and pop.get(key, 0) >= MIN_POPULATION:
            return stats.get(key, 0), pop[key], key
    return 0, 0, "all"


def package_object(ctx: Context) -> str | None:
    """Вид объекта: задан явно или по наименованию объекта в основных надписях, иначе — по первым листам томов."""
    from zerocycle.packs.kz.expertise import object_class

    if ctx.params.get("object_class"):
        return str(ctx.params["object_class"])
    votes: Counter[str] = Counter()
    for _, _, tb in ctx.title_blocks():
        if cls := object_class(" ".join((tb.get(2), tb.get(3)))):
            votes[cls] += 1
    if not votes:
        for d in ctx.pdfs()[:30]:
            first = ctx.pages_of(d).get(1)
            if first and (cls := object_class(first.head)):
                votes[cls] += 1
    return votes.most_common(1)[0][0] if votes else None


def group_name(key: str) -> str:
    from zerocycle.packs.kz.expertise import CLASS_RU, WORK_RU

    if key == "all":
        return "все объекты"
    obj, _, work = key.partition("/")
    return CLASS_RU.get(obj, obj) + (f", {WORK_RU.get(work, work)}" if work else "")


# листы-перечни: «Состав проекта», содержание тома, ведомости ссылочных документов — там названия чужих документов
_LISTING = re.compile(
    r"состав\w*\s+(?:рабочего\s+)?проект|содержани\w*\s+(?:тома|альбома|книги)|^содержание|ведомост\w*\s+(?:ссылочн|основн\w*\s+комплект)"
)


def _caps(text: str) -> str:
    return " ".join(text.replace("ё", "е").replace("Ё", "Е").split())


# строка-предложение, а не заголовок: «энергетический паспорт разработан отдельным томом», «Исходя из
# предоставленного Технического отчёта по инженерно-геологическим изысканиям…»
_SENTENCE = re.compile(
    r"(?:разработа|выполн[еяи]|предусмотр|представл|предоставл|прин[яи]т|привед[её]н|согласно|см\.|указан|исходя"
    r"|на\s+основании|по\s+данным|в\s+соответствии)",
    re.I,
)
_NUMBERED = re.compile(r"^\d{1,2}(?:\.\d{1,2})*\.?\s+")  # «5. расчет продолжительности строительства»
HEADING_MAX = 120  # заголовок короче; длинная строка — абзац текста


def _heading_lines(text: str) -> list[str]:
    """Первые строки листа, похожие на заголовок: короткие и без глаголов описания."""
    lines = [_norm(s) for s in text.splitlines() if s.strip()][:TITLE_LINES]
    return [s for s in lines if len(s) <= HEADING_MAX and not _SENTENCE.search(s)]


def _inner_headings(text: str) -> list[str]:
    """Строки-заголовки где угодно на листе, без номера раздела: «5. Расчёт продолжительности строительства»."""
    lines = (_norm(s) for s in text.splitlines() if s.strip())
    return [_NUMBERED.sub("", s) for s in lines if len(s) <= HEADING_MAX and not _SENTENCE.search(s)]


def _similar(ctx: Context, heads: tuple[re.Pattern[str], ...], listing_ok: bool = False) -> list[_Hit]:
    """Слабые следы документа — подсказка агенту, а не находка: заголовок раздела посреди листа (расчёт — раздел
    тома ПОС) или упоминание в тексте («выполнен энергетический паспорт здания»). По одному месту на документ."""
    hits: list[_Hit] = []
    for d in ctx.package.documents:
        if _is_conclusion(ctx, d):
            continue
        for pt in sorted(ctx.pages_of(d).values(), key=lambda p: p.number):
            if not listing_ok and _LISTING.search(_norm(pt.title)):
                continue
            inner = next(((ln, m) for ln in _inner_headings(pt.text) for p in heads if (m := p.match(ln))), None)
            if inner:
                ln, m = inner
                hits.append(_Hit(d, pt.number, ln[: m.end() + 40], "заголовок раздела"))
                break
            text = _norm(pt.text)
            if m := next((m for p in heads if (m := p.search(text))), None):
                hits.append(_Hit(d, pt.number, text[max(0, m.start() - 40) : m.end() + 40], "упоминание"))
                break
        if len(hits) >= MAX_ANCHORS:
            break
    return hits


def _find_doc(
    ctx: Context,
    names: tuple[re.Pattern[str], ...],
    heads: tuple[re.Pattern[str], ...],
    body: tuple[re.Pattern[str], ...] = (),
    listing_ok: bool = False,
) -> list[_Hit]:
    """Документ по имени файла или папки, по заголовку любой страницы и по графам основной надписи (расчёты и
    паспорта часто — листы внутри тома, со штампом), затем — по заголовку прописными где угодно на листе.
    listing_ok — сам документ и есть перечень («Состав проекта»): листы-перечни не пропускаем."""
    hits: list[_Hit] = []
    docs = [d for d in ctx.package.documents if not _is_conclusion(ctx, d)]
    for d in docs:
        if any(p.search(_norm(d.path)) for p in names):
            hits.append(_Hit(d, None, d.path.rsplit("/", 1)[-1], "имя файла"))
    named = {h.doc.path for h in hits}
    for d in docs:
        if d.path in named or not heads:
            continue
        stamps = {p.number: p.title_block for p in d.pages if p.title_block is not None}
        texts = ctx.pages_of(d)
        for number in sorted(set(texts) | set(stamps)):
            if not listing_ok and number in texts and _LISTING.search(_norm(texts[number].title)):
                continue  # «Состав проекта», содержание тома: там названия других документов, а не они сами
            lines = _heading_lines(texts[number].text) if number in texts else []
            if number in stamps:
                lines += [_norm(stamps[number].get(g)) for g in (1, 3, 4, 5, 6) if stamps[number].get(g)]
            found = next(((ln, m) for ln in lines for p in heads if (m := p.search(ln))), None)
            if found:
                ln, m = found
                hits.append(_Hit(d, number, ln[max(0, m.start() - 20) : m.end() + 40], "заголовок или штамп"))
                break
    if hits or not body:
        return hits
    for d in docs:
        for pt in sorted(ctx.pages_of(d).values(), key=lambda p: p.number):
            if not listing_ok and _LISTING.search(_norm(pt.title)):
                continue
            text = _caps(pt.text)
            if m := next((m for p in body if (m := p.search(text))), None):
                hits.append(_Hit(d, pt.number, text[max(0, m.start() - 20) : m.end() + 40], "заголовок в тексте"))
                break
    return hits


def _practice_case(names: list[str], obj: str, work: str, texts: dict[tuple[str, int], str] | None = None):
    k = {
        "population": {"all": 100, obj: 40, f"{obj}/{work}": 20},
        "doc_types": {
            "energy-passport": {"all": 10, obj: 8, f"{obj}/{work}": 6},
            "thermal-calc": {"all": 9, obj: 7, f"{obj}/{work}": 5},
            "duration-calc": {"all": 2, obj: 1, f"{obj}/{work}": 1},  # редко — не проверяем
            "local-content": {"all": 30, obj: 12, f"{obj}/{work}": 8},  # проверяет kz.completeness.ird
        },
    }

    def build():
        docs = [doc(n, 1) for n in names] + [doc("Том 2. АР.pdf", [page(1, {2: "Капитальный ремонт школы"}), page(2)])]
        return case(package(*docs, work_kind=work), texts, expertise_knowledge=k, object_class=obj)

    return build


@rule(
    "kz.completeness.expertise",
    version="0.1.0",
    title="Документы, которые экспертиза РК часто требует для объектов такого вида",
    family=Family.completeness,
    kind="presence",
    severity=Severity.major,
    basis=(PRACTICE_BASE,),
    applies=KZ,
    why=(
        "Правила КВЭ перечисляют исходно-разрешительные документы, но не расчёты, паспорта и ведомости сметы, "
        "которые эксперты требуют по виду объекта: в заключениях РК их добавляют по замечаниям чаще всего — "
        "сводную ведомость ресурсов, пояснительную записку к смете, расчёт продолжительности строительства, "
        "теплотехнический расчёт и энергопаспорт здания."
    ),
    remark="Представить {документ}.",
    evidence=(
        "Разметка пунктов заключений РК (docs/bench/LABELING.md): «представлен документ» — 2 571 пункт в 481 "
        "заключении без отложенной части V3; частоты — src/zerocycle/packs/kz/expertise.json",
    ),
    fixtures=(
        Fixture(
            "энергопаспорт и теплотехника на месте",
            _practice_case(["Энергетический паспорт.pdf", "Теплотехнический расчет.pdf"], "building", "capital_repair"),
            {Status.ok: 2, Status.manual: 0},
        ),
        Fixture(
            "энергопаспорта нет — проверить вручную с частотой из практики",
            _practice_case(["Теплотехнический расчет.pdf"], "building", "capital_repair"),
            {Status.manual: 1},
            items={"energy-passport": Status.manual, "thermal-calc": Status.ok},
        ),
        Fixture(
            "теплотехнический расчёт — лист внутри тома АР",
            _practice_case(
                ["Энергетический паспорт.pdf"],
                "building",
                "capital_repair",
                {("Том 2. АР.pdf", 2): "ТЕПЛОТЕХНИЧЕСКИЙ РАСЧЕТ наружной стены\nИсходные данные"},
            ),
            {Status.manual: 0},
            items={"thermal-calc": Status.ok},
        ),
        Fixture(
            "энергопаспорт — заголовок прописными посреди листа тома; в составе проекта — только строка перечня",
            _practice_case(
                ["Теплотехнический расчет.pdf"],
                "building",
                "capital_repair",
                {
                    ("Том 2. АР.pdf", 1): "СОСТАВ ПРОЕКТА\nТом 5. ЭНЕРГЕТИЧЕСКИЙ ПАСПОРТ",
                    ("Том 2. АР.pdf", 2): "Общие данные\nУтверждаю\nЭНЕРГЕТИЧЕСКИЙ ПАСПОРТ здания школы",
                },
            ),
            {Status.manual: 0},
            items={"energy-passport": Status.ok},
        ),
        Fixture(
            "только строка «Состав проекта» и упоминание строчными — документа нет",
            _practice_case(
                ["Теплотехнический расчет.pdf"],
                "building",
                "capital_repair",
                {
                    ("Том 2. АР.pdf", 1): "СОСТАВ ПРОЕКТА\nТом 5. ЭНЕРГЕТИЧЕСКИЙ ПАСПОРТ",
                    ("Том 2. АР.pdf", 2): "Общие данные\nэнергетический паспорт разработан отдельным томом",
                },
            ),
            {Status.manual: 1},
            items={"energy-passport": Status.manual},
        ),
        Fixture(
            "в записке сказано, что энергопаспорт выполнен, а самого нет — проверить, с подсказкой места",
            _practice_case(
                ["Теплотехнический расчет.pdf"],
                "building",
                "capital_repair",
                {("Том 2. АР.pdf", 2): "Общие данные\n" * 12 + "Согласно СН РК выполнен энергетический паспорт здания"},
            ),
            {Status.manual: 1},
            items={"energy-passport": Status.manual},
        ),
        Fixture(
            "сети: энергопаспорт и теплотехника неприменимы",
            _practice_case([], "linear", "reconstruction"),
            {Status.manual: 0, Status.ok: 0},
        ),
        Fixture(
            "вид объекта не определён",
            lambda: case(package(doc("Том 1.pdf"), work_kind="new"), expertise_knowledge={"population": {}}),
            {Status.cannot_check: 1},
        ),
    ),
)
def completeness_expertise(ctx: Context) -> Iterator[Outcome]:
    from zerocycle.packs.kz.expertise import doc_types

    k = practice(ctx)
    obj, work = package_object(ctx), ctx.work_kind
    if obj is None:
        yield Outcome(
            Status.cannot_check,
            "Не определён вид объекта — здание, сеть или благоустройство: от него зависит, какие документы "
            "эксперты требуют чаще",
        )
        return
    scans = [(d, n) for d, n in ctx.textless() if not any(p.number == n and p.title_block for p in d.pages)]
    scan_docs = sorted({d.path for d, _ in scans})
    searched = {"files": len(ctx.package.documents), "text_pages": sum(1 for _ in ctx.text_pages())}
    ird = {i.id: i for i in ird_items()}
    rows = []
    for dt in doc_types():
        if not dt.applies(obj, work):
            continue
        item = ird.get(IRD_ID.get(dt.id, dt.id)) if dt.ird else None
        if item is not None and work in item.basis:
            continue  # проверяет kz.completeness.ird по Правилам КВЭ; иначе — по практике (ИГИ при капремонте)
        c, n, key = practice_stats(k, dt.id, obj, work)
        if n and c >= PRACTICE_MIN and c / n >= PRACTICE_SHARE:
            rows.append((c / n, dt, c, n, key))
    for _, dt, c, n, key in sorted(rows, key=lambda r: -r[0]):
        group = group_name(key)
        basis = (f"{PRACTICE_BASE}, {c} из {n} заключений: {group}",)
        values = {"conclusions": c, "population": n, "group": key}
        listing_ok = dt.id == "composition-sheet"
        hits = _find_doc(ctx, dt.names, dt.heads, dt.body, listing_ok)
        if hits:
            yield Outcome(
                Status.ok,
                f"{dt.title}: {_where(hits)}",
                anchors=_anchors(ctx, hits),
                values=values | _hit_values(hits, "found"),
                basis=basis,
                item=dt.id,
            )
            continue
        tail = (
            f"; {len(scans)} листов без текстового слоя и без основной надписи — документ может быть среди сканов: "
            f"{', '.join(scan_docs[:MAX_ANCHORS])}"
            if scans
            else ""
        )
        practice_note = f"Эксперты РК требовали его в {c} из {n} заключений ({group}){tail}"
        similar = _similar(ctx, dt.heads, listing_ok)
        if similar:
            yield Outcome(
                Status.manual,
                f"{dt.title}: отдельным документом не найден; похожие места — {_where(similar)}: проверить, это ли "
                f"он или только упоминание. {practice_note}",
                anchors=_anchors(ctx, similar),
                values=values
                | _hit_values(similar, "similar")
                | {"scan_pages": len(scans), "scan_documents": scan_docs},
                basis=basis,
                item=dt.id,
            )
            continue
        yield Outcome(
            Status.manual,
            f"{dt.title}: в пакете не найден ни по именам файлов, ни по заголовкам страниц и основным надписям. "
            f"{practice_note}",
            values=values | {"searched": searched, "scan_pages": len(scans), "scan_documents": scan_docs},
            basis=basis,
            item=dt.id,
        )
