"""Правила `core`: один объект — одно наименование, один пакет — одна стадия.

Наименование объекта и стадия записаны в основных надписях всех листов. Расхождение — типичный след копирования:
привязанный типовой проект сохраняет в штампах чужое место строительства (пакет Тауыш, docs/bench/REAL_KZ.md),
лист из другого комплекта приходит со своей стадией (пакет Байгабыл: «ЭП» на листе ВК рабочего проекта).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterator

from zerocycle.model import Document, Family, Page, Severity, Status, TitleBlock
from zerocycle.norms.designation import to_cyrillic
from zerocycle.rules.base import Fixture, Outcome, rule
from zerocycle.rules.context import Context
from zerocycle.rules.fixtures import case, doc, package, page

MAX_ANCHORS = 5
SAME_NAME = 0.6  # доля общих основ слов, при которой два наименования — одно и то же с разной записью
_PUNCT = re.compile(r"[«»\"'“”„()\[\],.;:!?№/\\-]+")


def _stems(name: str) -> frozenset[str]:
    """Основы значимых слов: «в селе Тауыш Джангельдинского района» → {«селе», «тауыш», «джанг», «район»}."""
    words = _PUNCT.sub(" ", name.lower().replace("ё", "е")).split()
    return frozenset(w[:5] for w in words if len(w) >= 3)


def _places(name: str) -> frozenset[str]:
    """Основы имён собственных — места и названия: слова с прописной не в начале, кроме аббревиатур («ФАП», «РК»)."""
    words = _PUNCT.sub(" ", name.replace("ё", "е")).split()[1:]
    return frozenset(w.lower()[:5] for w in words if len(w) >= 3 and w[0].isupper() and not w.isupper())


def _same(a: str, b: str) -> bool:
    """Одно ли это наименование: совпадают имена собственные или (если их нет) большая часть основ слов.

    «ФАП в с. Тауыш» и «Строительство фельдшерско-акушерского пункта в селе Тауыш» — одно; «… в селе Кына
    Аккольского района» — другое: места не пересекаются.
    """
    pa, pb = _places(a), _places(b)
    if pa and pb:
        small, big = sorted((pa, pb), key=len)
        return len(small & big) / len(small) >= 0.5
    sa, sb = _stems(a), _stems(b)
    return len(sa & sb) / len(sa | sb) >= SAME_NAME if sa | sb else True


def _groups(values: list[tuple]) -> list[list[tuple]]:
    """Наименования по группам «одно и то же наименование»: самая частая запись — представитель группы."""
    reps: list[tuple[str, list]] = []
    by_text: dict[str, list] = {}
    for text, _ in Counter(v[0] for v in values).most_common():
        group = next((g for rep, g in reps if _same(rep, text)), None)
        if group is None:
            group = []
            reps.append((text, group))
        by_text[text] = group
    for v in values:
        by_text[v[0]].append(v)
    return sorted((g for _, g in reps), key=len, reverse=True)


UNREADABLE = "(cid:"  # pdfplumber: глиф без Unicode — текст графы не читается, судить по нему нельзя
# наименование объекта начинается с вида работ: «Строительство …», «Капитальный ремонт …»
_OBJECT_START = re.compile(
    r"^[«\"“(]*\s*(?:строительств|реконструкц|капитальн\w*\s+ремонт|расширени|техническ\w*\s+перевооруж|модернизац)",
    re.IGNORECASE,
)
_DECLARED = re.compile(
    r"[«\"“]\s*((?:строительств|реконструкц|капитальн\w*\s+ремонт|расширени|модернизац)[^«»\"“”]{10,220})",
    re.IGNORECASE,
)
_TITLE_DOC = re.compile(r"пояснительн|\bопз\b|\bпз\b|паспорт\w*\s+(?:рабочего\s+)?проект", re.IGNORECASE)


def _sheets(ctx: Context, graph: int) -> Iterator[tuple[str, Document, Page, TitleBlock]]:
    for d, p, tb in ctx.title_blocks():
        value = " ".join(tb.get(graph).split())
        if value and UNREADABLE not in value:
            yield value, d, p, tb


def _stamp_names(ctx: Context) -> list[tuple[str, Document, Page, TitleBlock, int]]:
    """Наименование объекта в основной надписи: графа 2, а если её нет и в графе 1 вместо обозначения стоит
    наименование («Строительство …») — графа 1 (так в книге ПБ пакета Тауыш)."""
    out = []
    for d, p, tb in ctx.title_blocks():
        for graph in (2, 1):
            value = " ".join(tb.get(graph).split())
            if not value or UNREADABLE in value or (graph == 1 and not _OBJECT_START.match(value)):
                continue
            out.append((value, d, p, tb, graph))
            break
    return out


def _declared_name(ctx: Context) -> tuple[str, Document, int] | None:
    """Наименование объекта, заявленное на первых страницах ОПЗ или паспорта проекта: «… «Строительство …» …»."""
    found: list[tuple[str, Document, int]] = []
    for d in ctx.pdfs():
        if not _TITLE_DOC.search(d.path):
            continue
        for pt in sorted(ctx.pages_of(d).values(), key=lambda x: x.number)[:3]:
            found += [(" ".join(m.group(1).split()).strip(" .,»"), d, pt.number) for m in _DECLARED.finditer(pt.text)]
    if not found:
        return None
    groups = _groups([(name, d, None, None) for name, d, _ in found])  # самое частое наименование среди найденных
    best = Counter(v[0] for v in groups[0]).most_common(1)[0][0]
    return next(f for f in found if f[0] == best)


_TAUYSH = "Строительство фельдшерско-акушерского пункта в с. Тауыш Джангельдинского района"
_KYNA = "Строительство фельдшерско-акушерского пункта в селе Кына Аккольского района Акмолинской области"


def _name_case(*names: str, opz: str | None = None, pb_graph1: str | None = None):
    def build():
        docs = [doc(f"Альбом {i}.pdf", [page(1, {1: f"311-2026-{i}", 2: n, 6: "РП"})]) for i, n in enumerate(names, 1)]
        texts = {}
        if opz:
            docs.append(doc("Том 1. ОПЗ.pdf", [page(1, {1: "311-2026-ОПЗ", 6: "РП"})]))
            texts[("Том 1. ОПЗ.pdf", 1)] = f"ОБЩАЯ ПОЯСНИТЕЛЬНАЯ ЗАПИСКА\nРабочий проект «{opz}» (привязка)"
        if pb_graph1:
            docs.append(doc("Книга ПБ.pdf", [page(1, {1: pb_graph1, 6: "РП"})]))
        return case(package(*docs), texts)

    return build


@rule(
    "core.object-name.consistent",
    version="0.2.0",
    title="Наименование объекта в основных надписях — одно и то, что заявлено в ОПЗ",
    family=Family.consistency,
    kind="consistency",
    severity=Severity.major,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 2)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 2)",),
    },
    why=(
        "Наименование объекта — одно на весь проект. Чужое наименование в штампе — след привязки типового проекта "
        "или копирования из другого объекта; эксперт пишет «название объекта в штампах откорректировать согласно АПЗ»."
    ),
    remark="Наименование объекта в основных надписях листов {листы} не соответствует наименованию проекта: «{было}».",
    evidence=(
        "REMARKS_KZ: «Название объекта в штампах … откорректировано согласно АПЗ и задания» (4 пункта, 3 заключения)",
        "REAL_KZ: пакет Тауыш прошёл экспертизу с местом строительства «село Кына» в штампах АР, ВК, ОВ, ПБ",
    ),
    fixtures=(
        Fixture(
            "одно наименование, разная запись",
            _name_case(_TAUYSH, _TAUYSH.replace("с.", "селе"), "ФАП в с. Тауыш", opz=_TAUYSH),
            {Status.ok: 1, Status.violated: 0},
        ),
        Fixture("чужое место строительства", _name_case(_TAUYSH, _TAUYSH, _KYNA), {Status.violated: 1, Status.ok: 0}),
        Fixture(
            "все штампы — от типового проекта, в ОПЗ — свой объект",
            _name_case(_KYNA, _KYNA, opz=_TAUYSH),
            {Status.violated: 1, Status.ok: 0},
        ),
        Fixture(
            "наименование в графе 1 вместо обозначения",
            _name_case(_TAUYSH, opz=_TAUYSH, pb_graph1=_KYNA.replace("в селе", "по адресу: Аккольский район, село")),
            {Status.violated: 1},
        ),
        Fixture("нет основных надписей", lambda: case(package(doc("ОПЗ.pdf", 2))), {Status.cannot_check: 1}),
    ),
)
def object_name_consistent(ctx: Context) -> Iterator[Outcome]:
    values = _stamp_names(ctx)
    if not values:
        yield Outcome(
            Status.cannot_check,
            "Нет основных надписей с читаемым наименованием объекта (графы 2 и 1)",
            values={"graphs": [2, 1]},
        )
        return
    groups = _groups(values)
    declared = _declared_name(ctx)
    if declared:
        ref_name, ref_doc, ref_page = declared
        odd = [g for g in groups if not _same(ref_name, Counter(v[0] for v in g).most_common(1)[0][0])]
        ref_text = f"в «{ref_doc.path}» (с. {ref_page})"
    else:
        ref_name = Counter(v[0] for v in groups[0]).most_common(1)[0][0]
        odd, ref_text, ref_page = groups[1:], "на остальных листах", None
    if not odd:
        yield Outcome(
            Status.ok,
            f"Наименование объекта одинаково на {len(values)} листах" + (" и совпадает с ОПЗ" if declared else ""),
            values={"sheets": len(values), "declared": ref_name if declared else None},
        )
        return
    for group in odd:
        name = Counter(v[0] for v in group).most_common(1)[0][0]
        anchors = [ctx.graph_anchor(d, p, tb, g) for _, d, p, tb, g in group[:MAX_ANCHORS]]
        if declared:
            anchors.append(ctx.anchor(ref_doc, ref_page, quote=ref_name))
        yield Outcome(
            Status.violated,
            f"Наименование объекта в основных надписях {len(group)} листов — «{name}», а {ref_text} — «{ref_name}»",
            anchors=tuple(anchors),
            values={
                "sheets": len(group),
                "name": name,
                "reference": ref_name,
                "reference_page": ref_page,
                "documents": sorted({v[1].path for v in group}),
                "graphs": sorted({v[4] for v in group}),
            },
            item=name,
        )


def _norm_stage(value: str) -> str:
    return re.sub(r"[\s.]", "", to_cyrillic(value).upper())


# эскизный проект в составе подачи: «Альбом Эскизный проект», «ЭП_Строительство …» — своя стадия законна
_SKETCH = re.compile(r"эскиз|(?:^|[\s_./(-])эп(?=[\s_./)-]|$)", re.IGNORECASE)


def _stage_case(*docs_stages: tuple[str, list[str]]):
    def build():
        docs = [
            doc(path, [page(i, {1: "311-2026", 2: _TAUYSH, 6: s}) for i, s in enumerate(stages, 1)])
            for path, stages in docs_stages
        ]
        return case(package(*docs))

    return build


@rule(
    "core.stage.consistent",
    version="0.2.0",
    title="Стадия одна во всём документе и пакете",
    family=Family.formatting,
    kind="consistency",
    severity=Severity.minor,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 6)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 6)",),
    },
    why=(
        "Стадия в графе 6 — одна для всех листов документа; лист с другой стадией пришёл из другого комплекта и, "
        "значит, мог не пройти корректировку вместе с остальными."
    ),
    remark="На листе {лист} указана стадия «{было}», на остальных листах документа — «{стало}».",
    evidence=("REAL_KZ: пакет Байгабыл — стадия «ЭП» на листе ВК рабочего проекта, на остальных — «РП»",),
    fixtures=(
        Fixture("одна стадия", _stage_case(("АР.pdf", ["РП", "РП"]), ("ВК.pdf", ["РП", "P.П"])), {Status.ok: 1}),
        Fixture("чужая стадия на листе", _stage_case(("ВК.pdf", ["РП", "ЭП", "РП"])), {Status.violated: 1}),
        Fixture(
            "поровну листов в документе — чужая та, что не как у пакета",
            _stage_case(("АР.pdf", ["П", "П"]), ("ТБЭ.pdf", ["П", "Р"])),
            {Status.violated: 1, Status.manual: 0},
            items={"ТБЭ.pdf:Р": Status.violated},
        ),
        Fixture(
            "эскизный проект целиком — не ошибка",
            _stage_case(
                ("АР.pdf", ["РП", "РП"]), ("Эскизный проект.pdf", ["ЭП", "ЭП"]), ("ЭП_Строительство ФАП.pdf", ["ЭП"])
            ),
            {Status.ok: 1, Status.violated: 0, Status.manual: 0},
        ),
        Fixture(
            "нечитаемая стадия — не судим",
            _stage_case(("ЭС.pdf", ["РП", "(cid:475)П", "РП"])),
            {Status.ok: 1, Status.violated: 0},
        ),
        Fixture(
            "документ целиком в другой стадии",
            _stage_case(("АР.pdf", ["РП", "РП"]), ("ОВ.pdf", ["РП"]), ("ВК.pdf", ["П", "П"])),
            {Status.manual: 1, Status.violated: 0},
        ),
    ),
)
def stage_consistent(ctx: Context) -> Iterator[Outcome]:
    by_doc: dict[str, list[tuple[str, Document, Page, TitleBlock]]] = defaultdict(list)
    for value, d, p, tb in _sheets(ctx, 6):
        stage = _norm_stage(value)
        if stage:
            by_doc[d.path].append((stage, d, p, tb))
    if not by_doc:
        yield Outcome(Status.cannot_check, "Нет основных надписей со стадией (графа 6)", values={"graph": 6})
        return
    overall = Counter(s for sheets in by_doc.values() for s, *_ in sheets)
    doc_stage: dict[str, str] = {}
    bad = 0
    for path, sheets in by_doc.items():
        counts = Counter(s for s, *_ in sheets)
        # поровну листов — стадия документа та, что у пакета: «П» и «Р» на двух листах тома ПД — лист «Р» чужой
        major, n_major = max(counts.items(), key=lambda kv: (kv[1], overall[kv[0]]))
        doc_stage[path] = major
        for stage, n in counts.items():
            if stage == major:
                continue
            bad += 1
            odd = [x for x in sheets if x[0] == stage]
            yield Outcome(
                Status.violated,
                f"«{path}»: стадия «{stage}» на {n} листах, на остальных {n_major} листах — «{major}»",
                anchors=tuple(ctx.graph_anchor(d, p, tb, 6) for _, d, p, tb in odd[:MAX_ANCHORS]),
                values={"document": path, "stage": stage, "sheets": n, "main_stage": major, "main_sheets": n_major},
                item=f"{path}:{stage}",
            )

    def sketch(path: str) -> bool:  # эскизный проект в составе пакета — своя стадия законна
        return bool(_SKETCH.search(path.rsplit("/", 1)[-1]) or _SKETCH.search(by_doc[path][0][1].title or ""))

    main_docs = [s for p, s in doc_stage.items() if not sketch(p)] or list(doc_stage.values())
    package_stage, _ = Counter(main_docs).most_common(1)[0]
    for path, stage in doc_stage.items():
        sheets = by_doc[path]
        if stage == package_stage or sketch(path):
            continue
        bad += 1
        yield Outcome(
            Status.manual,
            f"«{path}» выполнен в стадии «{stage}», остальные документы пакета — в стадии «{package_stage}»",
            anchors=tuple(ctx.graph_anchor(d, p, tb, 6) for _, d, p, tb in sheets[:2]),
            values={"document": path, "stage": stage, "package_stage": package_stage},
            item=f"{path}:{stage}",
        )
    if not bad:
        yield Outcome(
            Status.ok,
            f"Стадия «{package_stage}» одна во всех документах",
            values={"stage": package_stage, "documents": len(doc_stage)},
        )


# --- наименование здания (графа 3) -------------------------------------------------------------------------------


def _plain(name: str) -> str:
    return " ".join(_PUNCT.sub(" ", to_cyrillic(name).lower().replace("ё", "е")).split())


def _near(a: str, b: str) -> bool:
    """Почти одно наименование — отличается номером или приписанной частью: след правки копированием.

    «Жилой дом № 3» и «Жилой дом № 4», «Жилой дом № 3» и «Жилой дом № 3 (корп. 1)» — почти одно; «Котельная»
    и «Жилой дом № 3» — разные здания одного пакета, это не ошибка.
    """
    pa, pb = _plain(a), _plain(b)
    if re.sub(r"\d+", "#", pa) == re.sub(r"\d+", "#", pb):
        return True
    short, long_ = sorted((pa, pb), key=len)
    return long_.startswith(short) and len(long_) - len(short) <= 20


def _building_case(*docs_names: tuple[str, str]):
    def build():
        docs = [
            doc(path, [page(i, {1: f"311-2026-{i}", 2: _TAUYSH, 3: name}) for i in range(1, 3)])
            for path, name in docs_names
        ]
        return case(package(*docs))

    return build


_HOUSE = "Многоквартирный жилой дом № 3 со встроенными помещениями"


@rule(
    "core.building-name.consistent",
    version="0.1.0",
    title="Наименование здания в графе 3 одно во всех томах",
    family=Family.consistency,
    kind="consistency",
    severity=Severity.major,
    basis={
        "ГОСТ 21.101-97": ("ГОСТ 21.101-97, прил. Д (графа 3)",),
        "ГОСТ Р 21.101-2020": ("ГОСТ Р 21.101-2020, прил. Ж (графа 3)",),
    },
    why=(
        "Том с другим номером дома или корпуса в графе 3 — том от соседнего объекта или не исправленная копия: "
        "эксперт не может принять, что решения тома относятся к этому зданию."
    ),
    remark="В основной надписи тома {том} указать наименование здания «{стало}» вместо «{было}».",
    evidence=("Каталог дефектов засеянного корпуса: F04 — наименование здания в томе отличается",),
    fixtures=(
        Fixture("одно", _building_case(("АР.pdf", _HOUSE), ("КР.pdf", _HOUSE), ("ОВ.pdf", _HOUSE)), {Status.ok: 1}),
        Fixture(
            "другой номер дома в одном томе",
            _building_case(("АР.pdf", _HOUSE), ("КР.pdf", _HOUSE), ("ОВ.pdf", _HOUSE.replace("№ 3", "№ 4"))),
            {Status.violated: 1},
            items={"ОВ.pdf": Status.violated},
        ),
        Fixture(
            "приписан корпус",
            _building_case(("АР.pdf", _HOUSE), ("КР.pdf", _HOUSE), ("ОВ.pdf", f"{_HOUSE} (корп. 1)")),
            {Status.violated: 1},
        ),
        Fixture(
            "та же запись без сокращения — проверить",
            _building_case(
                ("АР.pdf", "Фельдшерско-акушерский пункт (ФАП)"),
                ("КР.pdf", "Фельдшерско-акушерский пункт (ФАП)"),
                ("ВК.pdf", "Фельдшерско -акушерский пункт"),
            ),
            {Status.violated: 0, Status.manual: 1},
        ),
        Fixture(
            "другое здание того же пакета — не ошибка",
            _building_case(("АР.pdf", _HOUSE), ("КР.pdf", _HOUSE), ("ТС.pdf", "Котельная")),
            {Status.violated: 0, Status.ok: 1},
        ),
        Fixture("нет графы 3", lambda: case(package(doc("АР.pdf", 2))), {Status.cannot_check: 1}),
    ),
)
def building_name_consistent(ctx: Context) -> Iterator[Outcome]:
    by_doc: dict[str, Counter[str]] = defaultdict(Counter)
    sheets: dict[tuple[str, str], list[tuple[Document, Page, TitleBlock]]] = defaultdict(list)
    for value, d, p, tb in _sheets(ctx, 3):
        by_doc[d.path][value] += 1
        sheets[(d.path, value)].append((d, p, tb))
    if not by_doc:
        yield Outcome(
            Status.cannot_check, "Нет основных надписей с наименованием здания (графа 3)", values={"graph": 3}
        )
        return
    main = {path: counts.most_common(1)[0][0] for path, counts in by_doc.items()}
    votes = Counter(_plain(v) for v in main.values())
    top, n_top = votes.most_common(1)[0]
    reference = next(v for v in main.values() if _plain(v) == top)
    bad = 0
    for path, value in sorted(main.items()):
        if _plain(value) == top or n_top < 2 or not _near(value, reference):
            continue
        bad += 1
        odd = sheets[(path, value)]
        # другой номер дома или корпуса — другое здание; разница только в записи («… (ФАП)») — проверить
        other = re.findall(r"\d+", _plain(value)) != re.findall(r"\d+", _plain(reference))
        yield Outcome(
            Status.violated if other else Status.manual,
            f"«{path}»: в графе 3 «{value}», в остальных {n_top} документах — «{reference}»",
            anchors=tuple(ctx.graph_anchor(d, p, tb, 3) for d, p, tb in odd[:MAX_ANCHORS]),
            values={"document": path, "graph3": value, "expected": reference, "documents": n_top},
            severity=None if other else Severity.minor,
            item=path,
        )
    if not bad:
        yield Outcome(
            Status.ok,
            f"Наименование здания согласовано в {n_top} документах",
            values={"documents": n_top, "name": reference},
        )
