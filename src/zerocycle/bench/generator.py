"""Засеянные пакеты ПД (PLAN, шаг 0.4): ПДЭ с info.xml, тома PDF с основными надписями, «Состав ПД»,
содержания томов, ПЗ с ТЭП и ссылками на нормы — и подсаженные дефекты с эталоном.

Порядок: `build(seed)` собирает чистое описание пакета → `defects.inject` портит его и пишет эталон →
`render` рисует файлы. Один сид — один и тот же пакет байт в байт (reportlab invariant, GUID от сида).

Система обозначений «организации» (ГОСТ Р 21.101-2020, п. 4.1.3.3 оставляет её стандарту организации):
том — «<база>-<шифр><номера>», текстовая часть — «<том>.ТЧ», графическая — «<том>.ГЧ», содержание —
«<том>-С», состав ПД — «<база>-СП». Один том — один PDF в папке раздела.

Генератор v1 (`bench/v1.py`, шаг 0.4b) собирает описание того же вида с полями v1. Рисует его тот же `render`,
и v0 от этого не меняется ни на байт. Что добавляет v1:
- штампы организаций;
- печать с уменьшением, /Rotate и листы боком;
- таблицы над штампом, примечания на чертежах, дополнительные графы на поле для подшивки;
- приложения и сметные формы без основной надписи;
- строки «Не разрабатывается» в «Составе ПД»;
- notes эталона — места, где правильный ответ не «нарушено».
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from lxml import etree
from reportlab.lib.utils import ImageReader

from zerocycle import __version__
from zerocycle.bench import content as C
from zerocycle.bench.draw import FONT, Sheet, StampStyle, TitleValues, new_canvas, register_font, styled_profile
from zerocycle.packs import codebook
from zerocycle.units import format_size, pt

A4_TEXT = (25.0, 200.0)  # x текста на листе А4 с рамкой 20/5
TOP = 15.0
BOTTOM_FIRST = 297 - 5 - 40 - 5  # над формой 5
BOTTOM_NEXT = 297 - 5 - 15 - 5  # над формой 6
PARA_SIZE = 10.0
ROW_H = 8.0
# недопустимые в имени файла символы обозначения заменяют на «_» (ГОСТ Р 21.101-2020, прил. А к п. 8.2.6):
# «ЖК-44/2026-ООС» → «ЖК-44_2026-ООС.pdf»
FILE_UNSAFE = str.maketrans(dict.fromkeys('\\/:*?"<>|', "_"))
ROLES = ("Разраб.", "Пров.", "Н. контр.", "ГИП")


@dataclass
class SheetSpec:
    kind: str  # title | contents | text | drawing | composition | appendix | estimate
    paper: str  # формат листа (что записано в графе 26)
    landscape: bool
    form: int | None  # None — лист без основной надписи: титульный, приложение, сметная форма
    graphs: dict[int, str] = field(default_factory=dict)
    signatures: list[tuple[str, str, str]] = field(default_factory=list)
    changes: list[tuple[str, str, str, str, str]] = field(default_factory=list)
    blocks: list[tuple] = field(
        default_factory=list
    )  # ("p", текст[, метка]) | ("table", ширины, шапка, строки, метка…)
    drawing: str | None = None  # наименование изображения на листе
    register: list[list[str]] | None = None  # ведомость графической части (её часть на этом листе)
    keep: bool = True  # False — лист выброшен дефектом
    tags: list[str] = field(default_factory=list)  # метки дефектов: при рисовании превращаются в номер страницы
    # --- генератор v1 ---
    style: StampStyle | None = None  # основная надпись организации; None — по прил. Ж
    page: str | None = None  # формат страницы, если лист напечатан с уменьшением; None — как paper
    scale: float = 1.0  # масштаб печати листа на странице page
    rotate: int = 0  # /Rotate: страница хранится повёрнутой, при показе лист стоит ровно
    sideways: int = 0  # содержимое повёрнуто без /Rotate — лист показывается боком (90 — против часовой)
    margin: str | None = None  # инвентарный номер подлинника в дополнительных графах на поле для подшивки
    above: tuple | None = None  # таблица вплотную над основной надписью: (заголовок, ширины, шапка, строки)
    footnotes: list[str] = field(default_factory=list)  # «Примечания» на чертеже
    lines: list[str] = field(default_factory=list)  # строки листа без рамки (приложение, шапка сметы)
    image: int | None = None  # приложение-скан без текстового слоя: вариант картинки
    truth: list[dict] = field(default_factory=list)  # записи notes эталона: отвлекающее содержимое листа
    register_more: bool = False  # ведомость продолжается с предыдущего листа


@dataclass
class TomSpec:
    number: str  # номер тома: «1», «4.2», «5.3»
    section: int
    code: str
    numbers: tuple[int, ...]
    title: str
    designation: str
    folder: str
    guid: str
    sheets: list[SheetSpec] = field(default_factory=list)
    present: bool = True  # файл есть в пакете
    in_composition: bool = True  # указан в «Составе ПД»
    file_name: str = ""
    developed: bool = True  # False — в «Составе ПД» строка «Не разрабатывается», тома нет и быть не должно
    truth: list[dict] = field(default_factory=list)  # notes эталона на весь файл тома (v1)

    @property
    def path(self) -> str:
        return f"{self.folder}/{self.file_name or self.designation.translate(FILE_UNSAFE) + '.pdf'}"


@dataclass
class PackageSpec:
    seed: int
    base: str
    org: str
    org_short: str
    customer: str
    complex_name: str
    building: str
    people: dict[str, str]
    date: str
    tep: dict[str, str]
    grid: str
    project_guid: str
    toms: list[TomSpec] = field(default_factory=list)
    composition: TomSpec | None = None
    # --- генератор v1 ---
    version: int = 0
    template: str | None = None  # шаблон организации (bench/templates.py)
    split: str | None = None  # dev | heldout
    roles: tuple[str, str, str, str] | None = None  # подписи ролей шаблона
    sign_dates: tuple[str, str, str, str] | None = None  # даты подписей: разработал, проверил, н. контр., ГИП
    text_style: StampStyle | None = None  # основная надпись текстовых документов, в том числе «Состава ПД»
    notes: list[dict] = field(default_factory=list)  # notes эталона уровня пакета
    profile: dict = field(default_factory=dict)  # что записать в эталон: шаблон, выборка, объём

    def tom(self, code: str, numbers: tuple[int, ...] = ()) -> TomSpec | None:
        return next(
            (t for t in self.toms if t.code == code and t.numbers[: len(numbers)] == numbers and t.developed), None
        )


def _num(x: float, decimals: int = 1) -> str:
    """12456.7 → «12 456,7» — как пишут в документах."""
    s = f"{x:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s


def _guid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _tep(rng: random.Random) -> tuple[dict[str, str], int]:
    floors = rng.choice([9, 12, 14, 16, 17, 19, 21, 25])
    footprint = rng.uniform(900, 2400)
    total = footprint * floors * rng.uniform(0.78, 0.86)
    volume = total * rng.uniform(3.3, 3.8)
    under = footprint * rng.uniform(3.0, 3.6)
    plot = footprint * rng.uniform(4.5, 8.0)
    tep = {
        "Площадь земельного участка": _num(plot) + " м²",
        "Площадь застройки": _num(footprint) + " м²",
        "Общая площадь здания": _num(total) + " м²",
        "Строительный объём": _num(volume + under, 0) + " м³",
        "в том числе подземной части": _num(under, 0) + " м³",
        "Количество этажей": f"{floors + 1} шт.",
        "в том числе подземных": "1 шт.",
        "Количество квартир": f"{int(total / 62)} шт.",
    }
    return tep, floors


TEP_KEYS = {
    "full": [
        "Площадь застройки",
        "Общая площадь здания",
        "Строительный объём",
        "в том числе подземной части",
        "Количество этажей",
        "в том числе подземных",
        "Количество квартир",
    ],
    "plot": ["Площадь земельного участка", "Площадь застройки"],
    "building": ["Площадь застройки", "Общая площадь здания", "Строительный объём", "Количество этажей"],
}


def build(seed: int) -> PackageSpec:
    """Чистое описание пакета типового жилого дома (раздел 13 «Иная документация» не нужен)."""
    rng = random.Random(seed)
    org, org_short = rng.choice(C.ORGS)
    n = rng.randint(10, 99)
    names = rng.sample(C.SURNAMES, 6)
    day = rng.randint(1, 28)
    tep, floors = _tep(rng)
    spec = PackageSpec(
        seed=seed,
        base=rng.choice(C.BASES).format(n=n),
        org=org,
        org_short=org_short,
        customer=rng.choice(C.CUSTOMERS),
        complex_name=rng.choice(C.COMPLEXES),
        building=rng.choice(C.BUILDINGS).format(n=rng.randint(1, 9)),
        people={
            "dev": names[0],
            "check": names[1],
            "nk": names[2],
            "gip": names[3],
            "chief": names[4],
            "extra": names[5],
        },
        date=f"{day:02d}.09.26",
        tep=tep,
        grid=rng.choice(["cells", "long"]),
        project_guid=_guid(rng),
    )
    fill = {
        "building": spec.building,
        "complex": spec.complex_name,
        "floors": floors,
        "power": _num(rng.uniform(250, 900), 0),
        "water": _num(rng.uniform(60, 240), 1),
        "months": rng.randint(18, 36),
    }
    for sec in C.SECTIONS:
        parts = sec.get("split")
        variants = [(p + 1, title) for p, title in enumerate(parts)] if parts and rng.random() < 0.6 else [(None, None)]
        for part, part_title in variants:
            numbers = (sec["sub"],) if "sub" in sec else ((part,) if part else ())
            code_tok = sec["code"] + ".".join(str(x) for x in numbers)
            number = f"{sec['section']}" + (f".{numbers[0]}" if numbers else "")
            title = sec["title"] + (f". Часть {part}. {part_title}" if part else "")
            tom = TomSpec(
                number=number,
                section=sec["section"],
                code=sec["code"],
                numbers=numbers,
                title=title,
                designation=f"{spec.base}-{code_tok}",
                folder=sec["folder"],
                guid=_guid(rng),
            )
            tom.sheets = _tom_sheets(spec, tom, sec, fill, rng)
            spec.toms.append(tom)
    spec.composition = _composition(spec, rng)
    return spec


def _signs(
    spec: PackageSpec,
    form: int,
    people: dict[str, str] | None = None,
    roles: tuple[str, str, str, str] | None = None,
) -> list[tuple[str, str, str]]:
    """Подписи формы 5 или 3: разработал, проверил, пустые строки, нормоконтроль, ГИП."""
    p = people or spec.people
    r = roles or spec.roles or ROLES
    d = spec.sign_dates or (spec.date,) * 4
    blank = [("", "", "")] * (1 if form == 5 else 2)
    return [(r[0], p["dev"], d[0]), (r[1], p["check"], d[1]), *blank, (r[2], p["nk"], d[2]), (r[3], p["gip"], d[3])]


def _norm_rows(sec: dict, rng: random.Random) -> list[list[str]]:
    refs = [r for g in sec.get("norms", []) for r in C.CURRENT[g]]
    if sec["section"] == 1:
        refs += C.VOLUNTARY
    rows = [[str(i + 1), law] for i, law in enumerate(C.LAWS)]
    rows += [[str(len(rows) + i + 1), ref] for i, ref in enumerate(refs)]
    return rows


def _blocks(spec: PackageSpec, sec: dict, fill: dict, rng: random.Random) -> list[tuple]:
    blocks: list[tuple] = [("p", t.format(**fill)) for t in sec["text"]]
    if sec.get("tep"):
        rows = [[k, spec.tep[k]] for k in TEP_KEYS[sec["tep"]]]
        blocks.insert(min(3, len(blocks)), ("p", "Технико-экономические показатели приведены в таблице."))
        blocks.insert(
            min(4, len(blocks)), ("table", [95.0, 50.0], ["Наименование показателя", "Значение"], rows, "tep")
        )
    if sec.get("norms"):
        blocks.append(("p", "Проектная документация разработана в соответствии с требованиями следующих документов:"))
        blocks.append(("table", [15.0, 160.0], ["№", "Документ"], _norm_rows(sec, rng), "norms"))
    return blocks


def paginate(
    blocks: list[tuple],
    width: float = A4_TEXT[1] - A4_TEXT[0],
    bottoms: tuple[float, float] = (BOTTOM_FIRST, BOTTOM_NEXT),
) -> list[list[tuple]]:
    """Разбить блоки по листам А4 так же, как их нарисует render: первый лист — над формой 5, дальше — над 6.

    bottoms — нижняя граница текста над формами 5 и 6; у штампа организации выше строки — выше и граница."""
    from reportlab.lib.utils import simpleSplit

    register_font()
    pages: list[list[tuple]] = [[]]
    y, bottom = TOP, bottoms[0]
    for b in blocks:
        if b[0] == "p":
            h = len(simpleSplit(b[1], FONT, PARA_SIZE, width * 72 / 25.4)) * PARA_SIZE * 0.45 + 2.0
        else:
            h = (len(b[3]) + 1) * ROW_H + 4.0
        if y + h > bottom and pages[-1]:
            pages.append([])
            y, bottom = TOP, bottoms[1]
        pages[-1].append(b)
        y += h
    return pages


def _tom_sheets(spec: PackageSpec, tom: TomSpec, sec: dict, fill: dict, rng: random.Random) -> list[SheetSpec]:
    sheets = [SheetSpec(kind="title", paper="A4", landscape=False, form=None)]
    tch, gch = f"{tom.designation}.ТЧ", f"{tom.designation}.ГЧ"
    text_pages = paginate(_blocks(spec, sec, fill, rng))
    drawings = list(sec.get("drawings", []))
    if sec.get("split") and tom.numbers:  # части КР: листы делятся пополам
        half = len(drawings) // 2
        drawings = drawings[:half] if tom.numbers[0] == 1 else drawings[half:]

    contents = [[f"{tom.designation}-С", "Содержание тома", ""], [tch, "Текстовая часть", f"{len(text_pages)} л."]]
    if drawings:
        contents.append([gch, "Графическая часть", f"{len(drawings)} л."])
    sheets.append(
        SheetSpec(
            kind="contents",
            paper="A4",
            landscape=False,
            form=5,
            graphs={
                1: f"{tom.designation}-С",
                5: f"Содержание тома {tom.number}",
                6: "П",
                8: "1",
                9: spec.org_short,
                26: "А4",
            },
            signatures=_signs(spec, 5),
            blocks=[("table", [60.0, 95.0, 30.0], ["Обозначение", "Наименование", "Примечание"], contents, "contents")],
        )
    )
    for i, page_blocks in enumerate(text_pages, start=1):
        first = i == 1
        graphs = {1: tch, 7: str(i), 26: "А4"}
        if first:
            graphs.update(
                {
                    5: f"{tom.title.split('. ', 1)[1]}. Текстовая часть",
                    6: "П",
                    8: str(len(text_pages)),
                    9: spec.org_short,
                }
            )
            if len(text_pages) == 1:
                del graphs[7]
        sheets.append(
            SheetSpec(
                kind="text",
                paper="A4",
                landscape=False,
                form=5 if first else 6,
                graphs=graphs,
                signatures=_signs(spec, 5) if first else [],
                blocks=page_blocks,
            )
        )
    for i, name in enumerate(drawings, start=1):
        paper, landscape = rng.choice([("A3", True), ("A3", True), ("A3", True), ("A2", True), ("A4x3", True)])
        graphs = {
            1: gch,
            2: spec.complex_name,
            3: spec.building,
            4: name,
            6: "П",
            7: str(i),
            9: spec.org_short,
            26: _paper_text(paper),
        }
        if i == 1:
            graphs[8] = str(len(drawings))
        if rng.random() < 0.5:
            graphs[27] = spec.customer
        sheets.append(
            SheetSpec(
                kind="drawing",
                paper=paper,
                landscape=landscape,
                form=3,
                graphs=graphs,
                signatures=_signs(spec, 3),
                drawing=name,
                register=[[str(k), d, ""] for k, d in enumerate(drawings, start=1)] if i == 1 else None,
            )
        )
    return sheets


def text_bottoms(style: StampStyle | None) -> tuple[float, float]:
    """Нижняя граница текста на листе А4 над формами 5 и 6 основной надписи стиля, мм от верха листа."""
    if style is None:
        return BOTTOM_FIRST, BOTTOM_NEXT
    forms = styled_profile(style).forms
    return 297 - 5 - forms[5].height - 5, 297 - 5 - forms[6].height - 5


def _paper_text(paper: str) -> str:
    return paper.replace("A", "А").replace("x", "х")  # в графе 26 пишут кириллицей: «А3», «А4х3»


def _composition(spec: PackageSpec, rng: random.Random) -> TomSpec:
    tom = TomSpec(
        number="",
        section=0,
        code="",
        numbers=(),
        title="Состав проектной документации",
        designation=f"{spec.base}-СП",
        folder="00 СП",
        guid=_guid(rng),
    )
    tom.sheets = []  # строки — в render: зависят от дефектов
    return tom


def composition_rows(spec: PackageSpec) -> list[list[str]]:
    return [
        [t.number, t.designation, t.title, "" if t.developed else "Не разрабатывается"]
        for t in spec.toms
        if t.in_composition
    ]


# --- рисование ------------------------------------------------------------------------------------


def render(spec: PackageSpec, out: Path, defects: list[dict]) -> Path:
    """out/package — сам пакет (его видят руки бенчмарка), рядом — эталон: ground_truth.json и pages.jsonl."""
    pkg = out / "package"
    pkg.mkdir(parents=True, exist_ok=True)
    expected: list[dict] = []
    notes: list[dict] = []
    for tom in spec.toms:
        if tom.present:
            expected += _render_tom(spec, tom, pkg, notes)
    expected += _render_composition(spec, pkg, notes)
    _write_info_xml(spec, pkg)
    pages = {(row["file"], tag): row["page"] for row in expected for tag in row.pop("tags", [])}
    for d in defects:
        if tag := d.pop("tag", None):
            d["page"] = pages[(d["file"], tag)]
    gt = {
        "package": out.name,
        "seed": spec.seed,
        "generator": f"zerocycle {__version__}",
        "base": spec.base,
        "clean": not defects,
        "defects": defects,
        "toms": [
            {
                "number": t.number,
                "designation": t.designation,
                "path": t.path,
                "present": t.present,
                "in_composition": t.in_composition,
                **({} if t.developed else {"developed": False}),
            }
            for t in spec.toms
        ],
    }
    if spec.version:
        sheets = len(expected)
        gt["profile"] = {"version": spec.version, **spec.profile, "sheets": sheets}
        gt["notes"] = notes
    (out / "ground_truth.json").write_text(json.dumps(gt, ensure_ascii=False, indent=1), encoding="utf-8")
    with (out / "pages.jsonl").open("w", encoding="utf-8") as f:
        for row in expected:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return out


def _render_tom(spec: PackageSpec, tom: TomSpec, out: Path, notes: list[dict]) -> list[dict]:
    path = out / tom.path
    path.parent.mkdir(parents=True, exist_ok=True)
    c = new_canvas(path, tom.title, spec.org)
    rows = []
    page_no = 0
    for sh in tom.sheets:
        if not sh.keep:
            continue
        page_no += 1
        s = Sheet(c, sh.paper, sh.landscape, sh.style)
        placed = _place(s, sh)
        if sh.kind in ("appendix", "estimate"):
            _loose(s, sh)
        else:
            s.frame()
            if sh.margin:
                s.margin_graphs(sh.margin)
            if sh.kind == "title":
                _title_page(s, spec, tom)
            else:
                s.title_block(TitleValues(sh.form, sh.graphs, sh.signatures, sh.changes), grid=spec.grid)
                _body(s, sh)
        if placed:
            c.restoreState()
        c.showPage()
        if sh.rotate:
            c.setPageRotation(0)  # поворот reportlab держит и на следующих страницах
        row = {
            "file": tom.path,
            "page": page_no,
            "kind": sh.kind,
            "form": sh.form,
            "paper": sh.page or sh.paper,
            "graphs": {str(k): v for k, v in sh.graphs.items()},
            "signatures": [list(x) for x in sh.signatures if any(x)],
            "tags": sh.tags,
        }
        if spec.version:
            row.update(_layout(sh))
            notes += [{**n, "file": tom.path, "page": page_no} for n in sh.truth]
        rows.append(row)
    c.save()
    notes += [{**n, "file": tom.path, "page": None} for n in tom.truth]
    return rows


def _layout(sh: SheetSpec) -> dict:
    """Что эталону описи нужно знать о раскладке листа v1: формат листа при уменьшении, повороты."""
    out: dict = {}
    if sh.page:
        out.update({"sheet": sh.paper, "scale": round(sh.scale, 4)})
    if sh.rotate:
        out["rotate"] = sh.rotate
    if sh.sideways:
        out["sideways"] = sh.sideways
    return out


def _place(s: Sheet, sh: SheetSpec) -> bool:
    """Положить лист на страницу: с уменьшением на страницу другого формата, с /Rotate или боком.

    Координаты листа остаются миллиметрами листа: переход к странице — преобразованием холста.
    """
    c = s.c
    if sh.page:
        short, long_ = format_size(sh.page)
        pw, ph = (long_, short) if sh.landscape else (short, long_)
        c.setPageSize((pt(pw), pt(ph)))
        c.saveState()
        c.translate(pt((pw - s.w * sh.scale) / 2), pt((ph - s.h * sh.scale) / 2))  # по центру страницы
        c.scale(sh.scale, sh.scale)
        return True
    angle = sh.rotate or sh.sideways
    if not angle:
        return False
    if sh.rotate:
        # reportlab считает размер страницы видимым и при /Rotate 90 и 270 сам пишет MediaBox книжным:
        # хранится h × w, при показе — w × h, как у листов, выгруженных САПР с поворотом
        c.setPageRotation(sh.rotate)
    else:
        c.setPageSize((pt(s.h), pt(s.w)))  # лист боком: страница книжная, /Rotate нет
    c.saveState()
    if angle == 90:  # точка листа (x, y) ложится на страницу в (h - y, x): поворот против часовой
        c.translate(pt(s.h), 0)
        c.rotate(90)
    else:  # 270: (y, w - x)
        c.translate(0, pt(s.w))
        c.rotate(-90)
    return True


def _render_composition(spec: PackageSpec, out: Path, notes: list[dict]) -> list[dict]:
    tom = spec.composition
    path = out / tom.path
    path.parent.mkdir(parents=True, exist_ok=True)
    rows_all = composition_rows(spec)
    header = ["Номер тома", "Обозначение", "Наименование", "Примечание"]
    widths = [15.0, 55.0, 95.0, 20.0]
    # первый лист — над формой 5, следующие — над формой 6
    first, following = text_bottoms(spec.text_style)
    per_first = int((first - TOP) // ROW_H) - 2
    per_next = int((following - TOP) // ROW_H) - 2
    chunks = [rows_all[:per_first]]
    rest = rows_all[per_first:]
    while rest:
        chunks.append(rest[:per_next])
        rest = rest[per_next:]
    c = new_canvas(path, tom.title, spec.org)
    expected = []
    for i, chunk in enumerate(chunks, start=1):
        s = Sheet(c, "A4", False, spec.text_style)
        s.frame()
        if i == 1:
            graphs = {1: tom.designation, 5: tom.title, 6: "П", 8: str(len(chunks)), 9: spec.org_short, 26: "А4"}
            if len(chunks) > 1:
                graphs[7] = "1"
            signs = _signs(spec, 5)
            form = 5
        else:
            graphs, signs, form = {1: tom.designation, 7: str(i), 26: "А4"}, [], 6
        s.title_block(TitleValues(form, graphs, signs), grid=spec.grid)
        s.table(20.0, TOP, widths, header, chunk, ROW_H)
        c.showPage()
        expected.append(
            {
                "file": tom.path,
                "page": i,
                "kind": "composition",
                "form": form,
                "paper": "A4",
                "graphs": {str(k): v for k, v in graphs.items()},
                "signatures": [list(x) for x in signs if any(x)],
            }
        )
        for t in spec.toms:
            if not t.developed and [t.number, t.designation, t.title, "Не разрабатывается"] in chunk:
                notes.append(
                    {
                        "kind": "not_developed",
                        "file": tom.path,
                        "page": i,
                        "designation": t.designation,
                        "section": t.section,
                        "code": t.code,
                        "subsection": t.numbers[0] if t.code == "ИОС" else None,
                        "expected": "manual",
                        "why": "раздел условный: «Не разрабатывается» в «Составе ПД», тома нет — это не дефект",
                    }
                )
    c.save()
    return expected


def _title_page(s: Sheet, spec: PackageSpec, tom: TomSpec) -> None:
    w = s.w
    s.text_in((25, w - 10, 15, 25), spec.org, 11)
    s.text_in((25, w - 10, 30, 38), f"Заказчик — {spec.customer}", 9)
    s.text_in((25, w - 10, 70, 90), spec.complex_name, 12)
    s.text_in((25, w - 10, 92, 108), spec.building, 12)
    s.text_in((25, w - 10, 120, 130), "ПРОЕКТНАЯ ДОКУМЕНТАЦИЯ", 14)
    s.text_in((25, w - 10, 135, 160), tom.title, 12)
    s.text_in((25, w - 10, 165, 175), tom.designation, 14)
    s.text_in((25, w - 10, 180, 190), f"Том {tom.number}", 13)
    s.text_in((30, 100, 220, 228), "Главный инженер", 9, "left")
    s.text_in((140, w - 10, 220, 228), spec.people["chief"], 9, "left")
    s.text_in((30, 100, 232, 240), "Главный инженер проекта", 9, "left")
    s.text_in((140, w - 10, 232, 240), spec.people["gip"], 9, "left")
    s.text_in((25, w - 10, 270, 280), "2026", 11)


def _body(s: Sheet, sh: SheetSpec) -> None:
    if sh.kind in ("contents", "text"):
        x0, x1 = A4_TEXT
        y = TOP
        for b in sh.blocks:
            if b[0] == "p":
                y, _ = s.paragraphs(x0, y, x1 - x0, 290, [b[1]], PARA_SIZE)
            else:
                _kind, widths, header = b[:3]
                y = s.table(x0, y + 2, widths, header, _styled_rows(b), ROW_H) + 2
    elif sh.kind == "drawing":
        _drawing(s, sh)


def _styled_rows(b: tuple) -> list[list[str]]:
    """Строки таблицы, как их пишет организация: обозначение нормы или название показателя ТЭП (v1).

    В описании пакета остаются канонические значения: по ним дефекты находят, что портить.
    """
    rows = b[3]
    if len(b) < 6:
        return rows
    if b[4] == "norms":
        return [[r[0], b[5].format(code=r[1]) if r[1].startswith(("СП", "ГОСТ")) else r[1], *r[2:]] for r in rows]
    if b[4] == "tep":
        return [[b[5].get(r[0], r[0]), *r[1:]] for r in rows]
    return rows


def _drawing(s: Sheet, sh: SheetSpec) -> None:
    """Условный чертёж: координационные оси, контур здания, помещения; ведомость — на первом листе."""
    form = s.profile.forms[3]
    stamp_left = s.w - s.profile.frame["right"] - form.width
    x_right = s.w - 5 - 185 - 10 if sh.register else s.w - 15
    if sh.above:
        x_right = min(x_right, stamp_left - 10)
    x0, y0, x1, y1 = 35.0, 30.0, max(x_right, 120.0), s.h - s.profile.frame["bottom"] - form.height - 15
    s.text_in((x0, x1, 15, 24), sh.drawing or "", 11)
    nx, ny = 6, 4
    for i in range(nx):
        x = x0 + (x1 - x0) * i / (nx - 1)
        s.line(x, y0, x, y1, 0.18, dash=True)
        s.c.setDash()
        s.c.circle(*_xy(s, x, y0 - 4), 2.8 * 72 / 25.4)
        s.text_in((x - 3, x + 3, y0 - 7, y0 - 1), str(i + 1), 7)
    letters = "АБВГДЕЖИК"
    for j in range(ny):
        y = y0 + (y1 - y0) * j / (ny - 1)
        s.line(x0, y, x1, y, 0.18, dash=True)
        s.c.setDash()
        s.c.circle(*_xy(s, x0 - 6, y), 2.8 * 72 / 25.4)
        s.text_in((x0 - 9, x0 - 3, y - 3, y + 3), letters[ny - 1 - j], 7)
    for a, b, c_, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
        s.line(a, b, c_, d, 0.7)
    if sh.register:
        rx = s.w - 5 - 185
        title = "Ведомость графической части" + (" (продолжение)" if sh.register_more else "")
        s.text_in((rx, s.w - 5, 12, 20), title, 9)
        s.table(rx, 22.0, [15.0, 140.0, 30.0], ["Лист", "Наименование", "Примечание"], sh.register, 7.0)
    if sh.above:  # таблица вплотную над основной надписью (и над графой 27, если она есть)
        title, widths, header, rows = sh.above
        top = s.h - s.profile.frame["bottom"] - form.height
        if sh.graphs.get(27):
            top += form.graphs[27][2]
        height = (len(rows) + 1) * 6.0
        x = s.w - s.profile.frame["right"] - sum(widths)
        s.text_in((x, x + sum(widths), top - height - 7, top - height - 1), title, 8)
        s.table(x, top - height, widths, header, rows, 6.0)
    if sh.footnotes:  # «Примечания» — внизу слева, под осями, не заходя на основную надпись
        from reportlab.lib.utils import simpleSplit

        y = y1 + 8
        for note in sh.footnotes:
            for ln in simpleSplit(note, FONT, 7.5, pt(stamp_left - 40)):
                if y > s.h - 8:
                    return
                s.text(30.0, y, ln, 7.5)
                y += 3.6


def _loose(s: Sheet, sh: SheetSpec) -> None:
    """Лист без рамки и основной надписи: приложение ИРД (текст или скан) или сметная форма."""
    if sh.image is not None:
        from zerocycle.bench.bulk import scan_image

        s.c.drawImage(ImageReader(_jpeg(sh.image, scan_image)), 0, 0, pt(s.w), pt(s.h))
        return
    y = 20.0
    for i, ln in enumerate(sh.lines):
        s.text(20.0 if i else s.w / 2, y, ln, 11 if i == 0 else 9.5, "left" if i else "center")
        y += 9.0 if i == 0 else 5.6
    for b in sh.blocks:
        _kind, widths, header, rows, _tag = b[:5]
        y = s.table(10.0, y + 2, widths, header, rows, ROW_H) + 2
    if sh.kind == "estimate" and sh.drawing:
        s.text(s.w - 10, s.h - 6, sh.drawing, 8, "right")  # колонтитул сметной программы


def _jpeg(variant: int, make) -> object:
    import io

    return io.BytesIO(make(variant))


def _xy(s: Sheet, x: float, y: float) -> tuple[float, float]:
    return x * 72 / 25.4, (s.h - y) * 72 / 25.4


def _write_info_xml(spec: PackageSpec, out: Path) -> None:
    """info.xml по ГОСТ Р 21.101-2020, прил. Ф: папки разделов и подразделов, документы с GUID."""
    cb = codebook("ru")  # засеянный корпус — пакеты РФ

    def attrs(el, **kw: str) -> None:
        box = etree.SubElement(el, "Attributes")
        for k, v in kw.items():
            etree.SubElement(box, "Attribute", Name=k, Value=v)

    created = "2026-09-" + spec.date[:2] + "T10:00:00"
    root = etree.Element("Package", Created=created, Organization=spec.org)
    project = etree.SubElement(root, "Project", Id=spec.project_guid)
    attrs(project, ProjectName=spec.building, Code=spec.base)
    children = etree.SubElement(project, "Children")
    rng = random.Random(spec.seed + 1)

    def document(parent, tom: TomSpec) -> None:
        d = etree.SubElement(parent, "Document", Created=created, Id=tom.guid, FileName=tom.path.replace("/", "\\"))
        attrs(d, Code=tom.designation, DocumentName=tom.title)

    document(children, spec.composition)
    sections: dict[int, etree._Element] = {}
    subsections: dict[tuple[int, int], etree._Element] = {}
    for tom in spec.toms:
        if not tom.present:
            continue
        if tom.section not in sections:
            info = next(s for s in cb.nonlinear if s.number == tom.section)
            folder = etree.SubElement(children, "Folder", Id=_guid(rng), Type="Section")
            attrs(folder, Number=str(tom.section), Code=tom.code, Title=info.title)
            sections[tom.section] = etree.SubElement(folder, "Children")
        parent = sections[tom.section]
        if tom.code == "ИОС":
            key = (tom.section, tom.numbers[0])
            if key not in subsections:
                info = next(s for s in cb.nonlinear if s.number == tom.section)
                sub = etree.SubElement(parent, "Folder", Id=_guid(rng), Type="Subsection")
                attrs(sub, Code=f"ИОС{tom.numbers[0]}", Title=info.subsections[tom.numbers[0]])
                subsections[key] = etree.SubElement(sub, "Children")
            parent = subsections[key]
        document(parent, tom)
    etree.ElementTree(root).write(str(out / "info.xml"), encoding="utf-8", xml_declaration=True, pretty_print=True)


def spec_dict(spec: PackageSpec) -> dict:
    """Описание пакета для отладки (без листов)."""
    d = asdict(spec)
    for t in d["toms"]:
        t.pop("sheets")
    d["composition"].pop("sheets")
    return d


def paper_size(paper: str) -> tuple[float, float]:
    return format_size(paper)
