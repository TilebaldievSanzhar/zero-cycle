"""Генератор v1 (PLAN, шаг 0.4b): пакеты ПД объёмом как реальные — 300–1 500 листов — на шаблонах организаций.

Что добавлено к v0 (bench/generator.py):
- объём:
  - текстовые части в десятки листов, графические — до сотни листов;
  - части КР и книги ИОС4;
  - приложения ИРД к ПЗ, часть из них — сканы без текстового слоя;
  - сметные формы раздела 12 без основных надписей;
- шаблоны организаций (bench/templates.py):
  - основные надписи, подписи ролей, хвосты обозначений и нумерация, формулировки;
  - в одном-двух подразделах ИОС — субподрядчик со своим штампом и подписями;
- раскладка листов:
  - печать с уменьшением: А1 и А2 на странице А3;
  - вписывание с полями и нестандартным масштабом;
  - /Rotate 90 и 270;
  - лист боком без /Rotate;
- отвлекающее содержимое (bench/bulk.py):
  - таблицы вплотную над основной надписью;
  - примечания со ссылками на нормы;
  - дополнительные графы на поле для подшивки;
  - СНиП в наименованиях СП, «площадь застройки» ТП;
  - строки «Не разрабатывается» в «Составе ПД».

Эталон — те же defects, плюс profile и notes. profile — шаблон, выборка, объём. notes — места, где правильный ответ
не «нарушено» (docs/bench/PROTOCOL.md). Выборка `heldout` берёт только отложенные шаблоны и формулировки. Сид и
выборка задают пакет целиком.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

from zerocycle.bench import bulk as B
from zerocycle.bench import content as C
from zerocycle.bench.generator import (
    TEP_KEYS,
    PackageSpec,
    SheetSpec,
    TomSpec,
    _guid,
    _num,
    _paper_text,
    _signs,
    _tep,
    paginate,
    text_bottoms,
)
from zerocycle.bench.templates import Template, templates

SIZES = {"full": (340, 1400), "small": (60, 90)}  # целевое число листов; фактическое — с титулами и содержаниями
RATES = {
    "rotate": 0.05,  # лист графической части с /Rotate 90 или 270 — как выгружают САПР
    "sideways": 0.02,  # лист боком без /Rotate — «печать в PDF» альбомного листа на книжную страницу
    "scaled": 0.06,  # А1 или А2, напечатанный на странице А3 с уменьшением; в графе 26 — формат листа
    "fit": 0.02,  # А1, вписанный в А3 с полями: масштаб нестандартный
    "above": 0.3,  # таблица вплотную над основной надписью (если нет графы 27)
    "footnotes": 0.5,  # «Примечания» на чертеже
    "customer": 0.5,  # графа 27 — заказчик
    "subcontractor": 0.7,  # один-два подраздела ИОС выполнил субподрядчик
    "no_gas": 0.3,  # газоснабжение не предусмотрено: ИОС6 «Не разрабатывается»
    "no_estimate": 0.5,  # частный застройщик: раздел 12 «Не разрабатывается»
}
# доли листов по разделам: текстовая часть, графическая часть, прочее (приложения ПЗ, сметные формы)
WEIGHTS = {
    "ПЗ": (0.050, 0.0, 0.025),
    "ПЗУ": (0.012, 0.022, 0.0),
    "АР": (0.025, 0.085, 0.0),
    "КР": (0.035, 0.130, 0.0),
    "ИОС1": (0.020, 0.050, 0.0),
    "ИОС2": (0.013, 0.030, 0.0),
    "ИОС3": (0.012, 0.030, 0.0),
    "ИОС4": (0.025, 0.060, 0.0),
    "ИОС5": (0.012, 0.030, 0.0),
    "ИОС6": (0.006, 0.012, 0.0),
    "ТХ": (0.008, 0.015, 0.0),
    "ПОС": (0.035, 0.010, 0.0),
    "ООС": (0.060, 0.006, 0.0),
    "ПБ": (0.040, 0.020, 0.0),
    "ТБЭ": (0.015, 0.0, 0.0),
    "ОДИ": (0.012, 0.010, 0.0),
    "СМ": (0.004, 0.0, 0.150),
}
KR_PARTS = [
    "Конструктивные решения ниже отметки 0.000",
    "Конструктивные решения выше отметки 0.000",
    "Конструктивные решения подземной автостоянки",
    "Конструкции кровли и технических помещений",
]
IOS4_BOOKS = ["Отопление, вентиляция и кондиционирование воздуха", "Тепловые сети", "Индивидуальный тепловой пункт"]
SUB_ORGS = [
    ("ООО «Инженерные системы»", "ООО «Инженерные системы»"),
    ("ООО «ТеплоПроект»", "ООО «ТеплоПроект»"),
    ("АО «Связьпроект»", "АО «Связьпроект»"),
]
PER_REGISTER = 26  # строк ведомости графической части на листе А3
PER_ESTIMATE = 18  # строк сметной формы на странице
PER_APPENDIX = 38  # строк текста приложения на странице

NOTE = {
    "snip_in_title": "СНиП здесь — часть наименования действующего СП, а не ссылка на заменённый документ",
    "tep_distractor": "площадь другого объекта или другой показатель — не ТЭП здания",
    "appendix": "приложение ИРД — документ другой организации: основной надписи нет и не должно быть",
    "estimate": "сметная форма из сметной программы: основной надписи нет и не должно быть",
    "scaled_print": "печать с уменьшением: в графе 26 — формат листа, страница меньше",
    "fit_print": "лист вписан в страницу с полями: масштаб нестандартный, формат не определить",
    "rotated": "/Rotate: при показе лист стоит ровно",
    "sideways": "лист повёрнут без /Rotate: опись должна повернуть его сама",
    "subcontractor": "подраздел выполнил субподрядчик: свой штамп и подписи, в графе 9 — его организация",
}


def _note(kind: str, expected: str = "ok", **kw) -> dict:
    return {"kind": kind, "expected": expected, "why": NOTE[kind], **kw}


def build(seed: int, split: str = "dev", size: str = "full", rates: dict | None = None) -> PackageSpec:
    """Чистое описание пакета v1: шаблон организации из выборки split, объём по size («full» | «small»)."""
    r = {**RATES, **(rates or {})}
    rng = random.Random(f"zerocycle-v1:{split}:{seed}")
    tpl = rng.choice(templates(split))
    org, org_short = rng.choice(C.ORGS)
    names = rng.sample(C.SURNAMES, 9)
    day = rng.randint(1, 24)
    tep, floors = _tep(rng)
    d0 = date(2026, 9, day)
    shifts = (0, 1, 3, 4) if tpl.staggered else (0, 0, 0, 0)
    spec = PackageSpec(
        seed=seed,
        base=rng.choice(C.BASES).format(n=rng.randint(10, 99)),
        org=org,
        org_short=org_short,
        customer=rng.choice(C.CUSTOMERS),
        complex_name=rng.choice(C.COMPLEXES),
        building=rng.choice(C.BUILDINGS).format(n=rng.randint(1, 9)),
        people=dict(zip(("dev", "check", "nk", "gip", "chief", "extra"), names[:6], strict=True)),
        date=f"{day:02d}.09.26",
        tep=tep,
        grid=rng.choice(["cells", "long"]),
        project_guid=_guid(rng),
        version=1,
        template=tpl.id,
        split=split,
        roles=tpl.roles,
        sign_dates=tuple((d0 + timedelta(days=k)).strftime("%d.%m.%y") for k in shifts),
        text_style=tpl.text_stamp,
    )
    sub = None
    if rng.random() < r["subcontractor"]:
        others = [t for t in templates(split) if t.id != tpl.id] or [tpl]
        sorg, sorg_short = rng.choice(SUB_ORGS)
        sub = {
            "template": rng.choice(others),
            "org": sorg,
            "org_short": sorg_short,
            "people": {"dev": names[6], "check": names[7], "nk": names[8], "gip": names[3]},
            "subs": sorted(rng.sample([1, 2, 3, 4, 5], rng.randint(1, 2))),
        }
    lo, hi = SIZES[size]
    target = math.exp(rng.uniform(math.log(lo), math.log(hi)))
    k = target / sum(sum(w) for w in WEIGHTS.values())
    no_gas = rng.random() < r["no_gas"]
    no_estimate = not spec.customer.startswith("ГКУ") and rng.random() < r["no_estimate"]
    fill = _fill(spec, floors, rng)

    for sec in C.SECTIONS:
        key = B.section_key(sec)
        wt, wd, wx = WEIGHTS[key]
        n_text, n_draw, n_extra = max(1, round(wt * k)), (max(1, round(wd * k)) if wd else 0), round(wx * k)
        folder = tpl.folder(sec["section"], sec["code"], sec.get("sub"))
        if (key == "ИОС6" and no_gas) or (key == "СМ" and no_estimate):
            spec.toms.append(_not_developed(spec, sec, folder, rng))
            continue
        parts = _parts(sec, key, n_draw, rng)
        for i, (numbers, part_title) in enumerate(parts):
            share = 1 / len(parts)
            code_tok = sec["code"] + ".".join(str(x) for x in numbers)
            tom = TomSpec(
                number=".".join(str(x) for x in (sec["section"], *numbers)),
                section=sec["section"],
                code=sec["code"],
                numbers=numbers,
                title=sec["title"] + (f". Часть {numbers[-1]}. {part_title}" if part_title else ""),
                designation=f"{spec.base}-{code_tok}",
                folder=folder,
                guid=_guid(rng),
            )
            who = sub if sub and sec.get("sub") in sub["subs"] else None
            budget = (max(1, round(n_text * share)), max(1, round(n_draw * share)) if n_draw else 0, n_extra)
            tom.sheets = _sheets(spec, tpl, tom, sec, key, budget, fill, who, r, rng, first_part=i == 0)
            if who:
                tom.truth.append(_note("subcontractor", organization=who["org_short"]))
            spec.toms.append(tom)
    spec.composition = TomSpec(
        number="",
        section=0,
        code="",
        numbers=(),
        title="Состав проектной документации",
        designation=f"{spec.base}-СП",
        folder=tpl.folder(0, "СП"),
        guid=_guid(rng),
    )
    spec.profile = {
        "template": tpl.id,
        "split": split,
        "size": size,
        "target": round(target),
        "subcontractor": {"template": sub["template"].id, "organization": sub["org_short"], "subsections": sub["subs"]}
        if sub
        else None,
    }
    return spec


def _fill(spec: PackageSpec, floors: int, rng: random.Random) -> dict:
    """Значения для формулировок: из ТЭП пакета и случайные, но правдоподобные."""

    def value(key: str) -> float:  # «12 345,6 м²» → 12345.6
        return float(spec.tep[key].rsplit(" ", 1)[0].replace(" ", "").replace(",", "."))

    total, plot = value("Общая площадь здания"), value("Площадь земельного участка")
    return {
        "building": spec.building,
        "complex": spec.complex_name,
        "floors": floors,
        "power": _num(rng.uniform(250, 900), 0),
        "water": _num(rng.uniform(60, 240), 1),
        "months": rng.randint(18, 36),
        "years": rng.choice([50, 100]),
        "abs_mark": _num(rng.uniform(120, 190), 2),
        "t": rng.randint(24, 36),
        "survey": f"{rng.randint(100, 999)}-26-{rng.choice(B.SURVEYS)}",
        "tu": f"{rng.randint(10, 999)}/26",
        "date": f"{rng.randint(1, 28):02d}.0{rng.randint(1, 6)}.2026",
        "tp": _num(rng.uniform(28, 48), 1),
        "tbo": _num(rng.uniform(12, 30), 1),
        "temp": _num(rng.uniform(120, 260), 1),
        "flats_area": _num(total * rng.uniform(0.62, 0.72), 1),
        "green": _num(plot * rng.uniform(0.2, 0.3), 1),
        "paved": _num(plot * rng.uniform(0.3, 0.4), 1),
        "yards": _num(plot * rng.uniform(0.05, 0.1), 1),
        "parking": rng.randint(20, 160),
        "load": rng.choice(["1,5", "2,0"]),
        "workers": rng.randint(60, 180),
        "minutes": rng.choice([6, 8, 10]),
        "cost": _num(rng.uniform(900_000, 4_500_000), 2),
    }


def _parts(sec: dict, key: str, n_draw: int, rng: random.Random) -> list[tuple[tuple[int, ...], str | None]]:
    """Тома раздела: КР — до четырёх частей, ИОС4 — до трёх книг, остальные — один том."""
    if key == "КР" and n_draw >= 12:
        m = 2 if n_draw < 40 else rng.randint(2, 4)
        return [((p,), KR_PARTS[p - 1]) for p in range(1, m + 1)]
    if key == "ИОС4" and n_draw >= 30:
        m = rng.randint(2, 3)
        return [((4, b), IOS4_BOOKS[b - 1]) for b in range(1, m + 1)]
    return [((sec["sub"],) if "sub" in sec else (), None)]


def _not_developed(spec: PackageSpec, sec: dict, folder: str, rng: random.Random) -> TomSpec:
    numbers = (sec["sub"],) if "sub" in sec else ()
    return TomSpec(
        number=".".join(str(x) for x in (sec["section"], *numbers)),
        section=sec["section"],
        code=sec["code"],
        numbers=numbers,
        title=sec["title"],
        designation=f"{spec.base}-{sec['code']}{''.join(map(str, numbers))}",
        folder=folder,
        guid=_guid(rng),
        present=False,
        developed=False,
    )


def _norm_rows(sec: dict) -> list[list[str]]:
    """Перечень норм: законы и своды правил с наименованиями (наименования — по реестру)."""
    refs = [r for g in sec.get("norms", []) for r in C.CURRENT[g]]
    if sec["section"] == 1:
        refs += C.VOLUNTARY
    rows = []
    for law in C.LAWS:
        code, _, title = law.partition(" «")
        rows.append([str(len(rows) + 1), code, title.rstrip("»")])
    rows += [[str(len(rows) + i + 1), ref, B.NORM_TITLES.get(ref, "")] for i, ref in enumerate(refs)]
    return rows


def _text_pages(
    spec: PackageSpec,
    tpl: Template,
    own: Template,
    sec: dict,
    key: str,
    target: int,
    fill: dict,
    rng: random.Random,
) -> list[list[tuple]]:
    """Текстовая часть: абзацы раздела из v0, таблицы, ТЭП, наполнение до целевого числа листов, перечень норм."""
    wording = tpl.wording
    head: list[tuple] = [("p", t.format(**fill)) for t in sec["text"]]
    if sec.get("tep"):
        rows = [[k, spec.tep[k]] for k in TEP_KEYS[sec["tep"]]]
        head.insert(min(3, len(head)), ("p", "Технико-экономические показатели приведены в таблице."))
        table = ("table", [95.0, 50.0], ["Наименование показателя", "Значение"], rows, "tep", tpl.tep_labels)
        head.insert(min(4, len(head)), table)
    if table := B.section_table(key, fill, rng):
        head.append(table)
    tail: list[tuple] = []
    norms = [r for g in sec.get("norms", []) for r in C.CURRENT[g]]
    if norms:
        intro = "Проектная документация разработана в соответствии с требованиями следующих документов:"
        header = list(tpl.norms_header)
        tail = [("p", intro), ("table", [12.0, 48.0, 115.0], header, _norm_rows(sec), "norms", tpl.norms_code)]

    def filler() -> tuple:
        text, kind = B.paragraph(key, wording, rng, fill, norms)
        return ("p", text, _note(kind)) if kind else ("p", text)

    body: list[tuple] = []
    if text := B.distractor(key, wording, fill):
        body.append(("p", text, _note("tep_distractor")))
    bottoms = text_bottoms(own.text_stamp)  # штамп выше — текста на листе меньше
    pages = paginate(head + body + tail, bottoms=bottoms)
    while len(pages) < target:
        body += [filler() for _ in range((target - len(pages)) * 14)]
        rng.shuffle(body)
        pages = paginate(head + body + tail, bottoms=bottoms)
    return pages


def _block_notes(blocks: list[tuple]) -> list[dict]:
    out = [b[2] for b in blocks if b[0] == "p" and len(b) > 2]
    if any(b[0] == "table" and b[4] == "norms" and any("СНиП" in row[2] for row in b[3]) for b in blocks):
        out.append(_note("snip_in_title", where="перечень норм"))
    return out


def _sheets(
    spec: PackageSpec,
    tpl: Template,
    tom: TomSpec,
    sec: dict,
    key: str,
    budget: tuple[int, int, int],
    fill: dict,
    who: dict | None,
    r: dict,
    rng: random.Random,
    first_part: bool,
) -> list[SheetSpec]:
    n_text, n_draw, n_extra = budget
    own = who["template"] if who else tpl  # штамп и подписи — того, кто разработал подраздел
    people = who["people"] if who else spec.people
    org9 = who["org_short"] if who else spec.org_short
    margin = str(rng.randint(1000, 9999)) if own.margin else None
    des = tom.designation
    tch, gch = (des + tpl.tails[0], des + tpl.tails[1]) if tpl.tails else (des, des)

    pages = _text_pages(spec, tpl, own, sec, key, n_text, fill, rng)
    n_t = len(pages)
    names = B.drawing_names(sec, key, n_draw) if n_draw else []
    n_d = len(names)
    extra = _extras(spec, key, n_extra, fill, rng) if first_part else []
    total = n_t if tpl.tails else n_t + n_d  # «Листов» на первом листе текстовой части

    contents = [[f"{des}-С", "Содержание тома", ""]]
    if tpl.tails:
        contents.append([tch, "Текстовая часть", f"{n_t} л."])
        if n_d:
            contents.append([gch, "Графическая часть", f"{n_d} л."])
    else:
        contents.append([des, "Текстовая часть", _range(1, n_t)])
        if n_d:
            contents.append([des, "Графическая часть", _range(n_t + 1, n_t + n_d)])
    for title, count in _extra_titles(extra):
        contents.append(["", title, f"{count} л."])

    sheets = [SheetSpec(kind="title", paper="A4", landscape=False, form=None, margin=margin)]
    sheets.append(
        SheetSpec(
            kind="contents",
            paper="A4",
            landscape=False,
            form=5,
            graphs={1: f"{des}-С", 5: f"Содержание тома {tom.number}", 6: "П", 8: "1", 9: org9, 26: "А4"},
            signatures=_signs(spec, 5, people, own.roles),
            blocks=[("table", [60.0, 95.0, 30.0], ["Обозначение", "Наименование", "Примечание"], contents, "contents")],
            style=own.text_stamp,
            margin=margin,
        )
    )
    for i, page_blocks in enumerate(pages, start=1):
        graphs = {1: tch, 7: str(i), 26: "А4"}
        if i == 1:
            graphs.update({5: f"{tom.title.split('. ', 1)[1]}. Текстовая часть", 6: "П", 8: str(total), 9: org9})
            if total == 1:
                del graphs[7]
        sheets.append(
            SheetSpec(
                kind="text",
                paper="A4",
                landscape=False,
                form=5 if i == 1 else 6,
                graphs=graphs,
                signatures=_signs(spec, 5, people, own.roles) if i == 1 else [],
                blocks=page_blocks,
                style=own.text_stamp,
                margin=margin,
                truth=_block_notes(page_blocks),
            )
        )
    sheets += [s for _, _, group in extra for s in group]
    first_no = 1 if tpl.tails else n_t + 1
    register = [[str(first_no + i), name, ""] for i, name in enumerate(names)]
    norms = [x for g in sec.get("norms", []) for x in C.CURRENT[g]]
    for i, name in enumerate(names):
        sheets.append(
            _drawing(spec, tpl, own, key, name, first_no + i, i, gch, n_d, org9, people, margin, norms, r, rng)
        )
        reg = register[i * PER_REGISTER : (i + 1) * PER_REGISTER]
        if reg:
            sheets[-1].register, sheets[-1].register_more, sheets[-1].above = reg, i > 0, None
    return sheets


def _range(a: int, b: int) -> str:
    return f"л. {a}" if a == b else f"л. {a}–{b}"


def _drawing(
    spec: PackageSpec,
    tpl: Template,
    own: Template,
    key: str,
    name: str,
    number: int,
    index: int,
    gch: str,
    n_d: int,
    org9: str,
    people: dict,
    margin: str | None,
    norms: list[str],
    r: dict,
    rng: random.Random,
) -> SheetSpec:
    paper = rng.choice(["A3", "A3", "A3", "A2", "A4x3"])
    page, scale, rotate, sideways, truth = None, 1.0, 0, 0, []
    x = rng.random()
    if x < r["scaled"]:
        paper, page = rng.choice(["A1", "A2"]), "A3"
        scale = 420 / (841 if paper == "A1" else 594)
        truth.append(_note("scaled_print", sheet=paper, scale=round(scale, 4)))
    elif x < r["scaled"] + r["fit"]:
        paper, page, scale = "A1", "A3", 0.47
        truth.append(_note("fit_print", "manual", sheet=paper, scale=scale))
    elif paper != "A4x3" and x < r["scaled"] + r["fit"] + r["rotate"]:
        rotate = rng.choice([90, 270])
        truth.append(_note("rotated", rotate=rotate))
    elif paper != "A4x3" and x < r["scaled"] + r["fit"] + r["rotate"] + r["sideways"]:
        sideways = rng.choice([90, 270])
        truth.append(_note("sideways", sideways=sideways))
    graphs = {
        1: gch,
        2: spec.complex_name,
        3: spec.building,
        4: name,
        6: "П",
        7: str(number),
        9: org9,
        26: _paper_text(paper),
    }
    if index == 0 and tpl.tails:
        graphs[8] = str(n_d)
    if rng.random() < r["customer"]:
        graphs[27] = spec.customer
    above = None
    if 27 not in graphs and rng.random() < r["above"]:
        make = {"АР": B.room_schedule, "ИОС1": B.loads_table}.get(key, B.specification)
        above = make(rng)
    footnotes: list[str] = []
    if rng.random() < r["footnotes"]:
        footnotes, snip = B.notes_block(norms, tpl.wording, rng)
        if snip:
            truth.append(_note("snip_in_title", where="примечания на чертеже"))
    return SheetSpec(
        kind="drawing",
        paper=paper,
        landscape=True,
        form=3,
        graphs=graphs,
        signatures=_signs(spec, 3, people, own.roles),
        drawing=name,
        style=own.drawing_stamp,
        page=page,
        scale=scale,
        rotate=rotate,
        sideways=sideways,
        margin=margin,
        above=above,
        footnotes=footnotes,
        truth=truth,
    )


def _extras(spec: PackageSpec, key: str, n: int, fill: dict, rng: random.Random) -> list[tuple[str, str, list]]:
    """Листы без основной надписи: приложения ИРД к ПЗ, сметные формы раздела 12. (заголовок, вид, листы)."""
    if n <= 0:
        return []
    if key == "ПЗ":
        chosen = B.APPENDICES[: max(1, min(len(B.APPENDICES), n // 2))]
        counts = _split(n, len(chosen), rng)
        out = []
        for i, ((title, kind), count) in enumerate(zip(chosen, counts, strict=True), start=1):
            sheets = []
            for p in range(count):
                if kind == "scan":
                    sh = SheetSpec(kind="appendix", paper="A4", landscape=False, form=None, image=rng.randint(0, 5))
                else:
                    make = B.assignment_lines if i == 1 else (lambda s, g, n_, t=title: B.tu_lines(s, g, t, n_))
                    lines = make(spec, rng, PER_APPENDIX) if p == 0 else _continuation(spec, rng)
                    sh = SheetSpec(kind="appendix", paper="A4", landscape=False, form=None, lines=lines)
                sh.truth.append(_note("appendix", appendix=title))
                sheets.append(sh)
            out.append((f"Приложение {i}. {title}", "appendix", sheets))
        return out
    if key == "СМ":
        groups = [("Сводный сметный расчёт стоимости строительства", max(1, n // 12))]
        groups.append(("Объектный сметный расчёт № ОС-02-01", max(1, n // 10)))
        rest = n - sum(c for _, c in groups)
        k = 1
        while rest > 0:
            c = min(rest, rng.randint(2, 9))
            groups.append((f"Локальный сметный расчёт № 02-01-{k:02d}", c))
            rest -= c
            k += 1
        out = []
        page = 0
        for title, count in groups:
            sheets = []
            for p in range(count):
                page += 1
                lines = [title.upper(), f"Объект: {spec.building}", "Составлен ресурсно-индексным методом"]
                block = (
                    "table",
                    B.ESTIMATE_WIDTHS,
                    B.ESTIMATE_HEADER,
                    B.estimate_rows(rng, PER_ESTIMATE),
                    "estimate",
                )
                sh = SheetSpec(
                    kind="estimate",
                    paper="A4",
                    landscape=True,
                    form=None,
                    lines=lines if p == 0 else lines[:1],
                    blocks=[block],
                    drawing=f"Страница {page}",  # колонтитул сметной программы
                )
                sh.truth.append(_note("estimate", estimate=title))
                sheets.append(sh)
            out.append((title, "estimate", sheets))
        return out
    return []


def _extra_titles(extra: list[tuple[str, str, list]]) -> list[tuple[str, int]]:
    return [(title, len(sheets)) for title, _kind, sheets in extra]


def _split(n: int, parts: int, rng: random.Random) -> list[int]:
    """n листов на parts документов, каждому не меньше одного."""
    counts = [1] * parts
    for _ in range(max(0, n - parts)):
        counts[rng.randrange(parts)] += 1
    return counts


def _continuation(spec: PackageSpec, rng: random.Random) -> list[str]:
    items = [
        "Прочие требования — в соответствии с действующим законодательством Российской Федерации.",
        f"Ответственный представитель застройщика — {rng.choice(C.SURNAMES)}.",
        "Срок выполнения работ — по календарному плану договора.",
        "Исходные данные передаются застройщиком в электронном виде.",
    ]
    return ["", *[f"— {t}" for t in B.cycle(items, PER_APPENDIX - 4, rng)]]
