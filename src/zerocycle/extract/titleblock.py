"""Поиск и разбор основной надписи листа: ГОСТ Р 21.101-2020 (прил. Ж) и шаблоны, которые от него отходят.

Реальные пакеты рисуют основную надпись по-разному: другие поля рамки, строки 4,4–6,8 мм вместо 5, колонки
шире или уже, штамп ЕСКД (ГОСТ 2.104: «Изм. | Лист | № докум. | Подп. | Дата»), штамп в теле Word-документа,
лист с уменьшением вписан в чужой формат, рядом — штамп привязки типового проекта. Поэтому три способа:

1. «grid» — от строки шапки «Изм. … Дата» в любом месте листа (берём самую нижнюю): границы колонок и строк —
   по фактическим линиям рядом, правый край — по горизонталям правой части. Графы правой части — по взаимному
   расположению ячеек: подписи «Стадия», «Лист», «Листов» и значения под ними (6, 7, 8); ячейка с обозначением
   (графа 1); ячейка организации под значениями (9); слева от подписей — графа 3, слева от организации — 4,
   между обозначением и подписями — 2; у формы 5 одна ячейка слева тянется через обе полосы — графа 5.
2. «frame» — по геометрии ГОСТ у правого нижнего угла рамки: горизонталь шириной 185 мм на высоте 55, 40 или
   15 мм и вертикали колонок. Нужен там, где текст переведён в кривые: рамку видно, слов нет.
3. «labels» — по геометрии ГОСТ от шапки, когда линий нет (штамп напечатан текстом).

Во 2-м и 3-м способах графы берутся по ячейкам профиля формы.

Геометрия форм, подписи граф и их образцы, формы юрлиц и справочник шифров приходят из контекста пакета
(`packs.context`): профиль редакции СПДС, пакет юрисдикции, слой организации. В коде — допуски поиска и смысл
номеров граф, общий для семьи СПДС.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from zerocycle.extract.designation import parse
from zerocycle.ingest.pdf import PdfPage, Segment, Word
from zerocycle.model import BBox, ChangeRow, SignRow, TitleBlock
from zerocycle.packs import Codebook, Context, FormProfile, TitleBlockProfile
from zerocycle.units import normalize_format, pt

TOL_MM = 3.0  # допуск положения рамки и линий
MIN_COLUMNS = 4  # сколько границ колонок таблицы изменений должно найтись, чтобы поверить рамке
EXTRA_SIGN_ROWS = 2  # в реальных штампах бывает на пару строк подписей больше, чем в форме
DOUBLE = pt(1.2)  # таблицы Word рисуют у каждой ячейки свою рамку с зазором ~0,5 мм: две линии — одна граница


@dataclass(frozen=True)
class _Frame:
    left: float
    top: float
    right: float
    bottom: float


def _key(text: str) -> str:
    """Подпись графы для сравнения: «Кол. уч.» → «колуч», «Н. контр.» → «нконтр»."""
    return re.sub(r"[\s.]", "", text).lower()


def find_title_block(page: PdfPage, ctx: Context) -> TitleBlock | None:
    profile = ctx.titleblocks
    if tb := _by_grid(page, ctx):
        return tb
    right, bottom = _corner(page, profile)
    found = _by_frame(page, profile, right, bottom) or _by_labels(page, profile, right, bottom)
    if found is None:
        return None
    form, frame, method = found
    if form == 3 and _has_words(page, frame, _form4_labels(profile)):
        form = 4
    return _read(page, profile.forms[form], profile, frame, method)


def _form4_labels(profile: TitleBlockProfile) -> set[str]:
    """Подписи, которые есть у формы 4 и нет у формы 3: «Масса», «Масштаб»."""
    f3, f4 = profile.forms.get(3), profile.forms.get(4)
    if f3 is None or f4 is None:
        return set()
    return {_key(x) for x in f4.labels} - {_key(x) for x in f3.labels}


# --- общие помощники ------------------------------------------------------------------------------


def _words_in(words: list[Word], box: BBox) -> list[Word]:
    x0, top, x1, bottom = box
    return [w for w in words if x0 <= w.cx <= x1 and top <= w.cy <= bottom]


def _join(words: list[Word], drop: set[str] = frozenset()) -> str:
    """Слова ячейки → текст: строки сверху вниз, слова слева направо; подписи граф выбрасываются."""
    words = [w for w in words if _key(w.text) not in drop]
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.cy, w.x0)):
        if lines and abs(lines[-1][0].cy - w.cy) <= 2.0:
            lines[-1].append(w)
        else:
            lines.append([w])
    return " ".join(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)) for line in lines).strip()


def _merge(values: list[float], tol: float = 1.0) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


# --- 1. от шапки по фактическим линиям --------------------------------------------------------------


Roles = dict[int, tuple[str, ...]]  # графа таблицы изменений → образцы подписи шапки (fnmatch, как `_key`)


def _roles(profile: TitleBlockProfile) -> Roles:
    return {g: tuple(_key(p) for p in patterns) for g, patterns in profile.header_roles.items()}


def _header_role(key: str, roles: Roles) -> int | None:
    """Графа по подписи шапки: «колуч» → 15, «nдок» → 17."""
    return next((g for g, patterns in roles.items() if any(fnmatchcase(key, p) for p in patterns)), None)


def _stems(patterns: tuple[str, ...]) -> tuple[str, ...]:
    """Образцы без «*» — для поиска в склеенной строке шапки: «Изм.Кол.уч» → «измколуч»."""
    return tuple(p.replace("*", "") for p in patterns)


def _find_header(page: PdfPage, roles: Roles) -> list[Word] | None:
    """Строка шапки «Изм. … Дата»: самая нижняя на листе, при равенстве — самая правая."""
    first, last = roles[min(roles)], _stems(roles[max(roles)])
    groups = [_stems(patterns) for patterns in roles.values()]
    best: tuple[float, float, list[Word]] | None = None
    for w in page.words:
        if not any(fnmatchcase(_key(w.text), p) for p in first):
            continue
        tol = max(2.0, (w.bottom - w.top) * 0.6)
        row = sorted(
            (v for v in page.words if abs(v.cy - w.cy) <= tol and w.x0 - 1 <= v.x0 <= w.x0 + pt(120)),
            key=lambda v: v.x0,
        )
        joined = "".join(_key(v.text) for v in row)
        if not any(s in joined for s in last):
            continue
        hits = sum(any(s in joined for s in group) for group in groups)
        end = next((i for i, v in enumerate(row) if any(s in _key(v.text) for s in last)), None)
        if hits < 4 or end is None:
            continue
        cand = (w.cy, w.cx, row[: end + 1])
        if best is None or (cand[0], cand[1]) > (best[0], best[1]):
            best = cand
    return best[2] if best else None


@dataclass
class _Cell:
    box: BBox
    words: list[Word] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _join(self.words)

    @property
    def x0(self) -> float:
        return self.box[0]

    @property
    def top(self) -> float:
        return self.box[1]

    @property
    def x1(self) -> float:
        return self.box[2]

    @property
    def bottom(self) -> float:
        return self.box[3]


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def _by_grid(page: PdfPage, ctx: Context) -> TitleBlock | None:
    profile = ctx.titleblocks
    roles = _roles(profile)
    head = _find_header(page, roles)
    if head is None:
        return None
    first, last = head[0], head[-1]
    hy = sum(w.cy for w in head) / len(head)
    word_h = max(w.bottom - w.top for w in head)

    # левая часть: границы колонок — вертикали, пересекающие строку шапки; строки — горизонтали
    vx = _merge(
        [s.x0 for s in page.vsegs if s.top <= hy <= s.bottom and first.x0 - pt(25) <= s.x0 <= last.x1 + pt(25)], DOUBLE
    )
    left_lines = [x for x in vx if x <= first.x0 + 1.0]
    right_lines = [x for x in vx if x >= last.x1 - 1.0]
    # строки левой части: горизонтали, пересекающие колонку «Изм»
    probe = (first.x0 + first.x1) / 2
    edge = page.height - pt(1)  # линии у самого края — граница листа, не строки основной надписи
    ys = _merge(
        [s.top for s in page.hsegs if s.x0 - 1 <= probe <= s.x1 + 1 and abs(s.top - hy) <= pt(100) and s.top < edge],
        DOUBLE,
    )
    above, below = [y for y in ys if y < hy], [y for y in ys if y > hy]
    if not (left_lines and right_lines and below):
        return None  # без линий сетку не восстановить — дальше способы по геометрии ГОСТ
    xl, xm = max(left_lines), min(right_lines)
    cols = [x for x in vx if xl <= x <= xm]
    scale = (xm - xl) / pt(profile.left_width)
    h_bot = below[0]
    # верх шапки: ближайшая линия выше; нет её рядом (шапка — верхняя строка, а линия над ней не нарисована) —
    # шапка той же высоты, что строка под ней
    row_below = below[1] - below[0] if len(below) > 1 else None
    if above and h_bot - above[-1] <= 4 * word_h:
        h_top = above[-1]
    elif row_below and row_below <= 4 * word_h:
        h_top = h_bot - row_below
    else:
        return None  # между линиями не строка шапки, а что-то крупнее — это не основная надпись
    rh = h_bot - h_top
    # строки изменений над шапкой и подписей под ней — той же высоты (0,6–1,4 строки шапки) и не больше, чем
    # бывает у форм: иначе в основную надпись попадёт таблица, стоящая на ней вплотную
    top, changes = h_top, []
    for y in reversed([y for y in above if y < h_top - 1]):
        if len(changes) >= profile.max_change_rows or not 0.6 * rh <= top - y <= 1.4 * rh:
            break
        changes.insert(0, (y, top))
        top = y
    bottom, signs = h_bot, []
    for y in below[1:]:
        if len(signs) >= profile.max_sign_rows + EXTRA_SIGN_ROWS or not 0.6 * rh <= y - bottom <= 1.4 * rh:
            break
        signs.append((bottom, y))
        bottom = y

    # правый край: горизонтали правой части, начатые у границы частей, и примыкающие к ним справа
    # (колонка «Лист» у формы 6 и в таблицах Word нарисована отдельными отрезками)
    block_h = [s for s in page.hsegs if top - 1 <= s.top <= bottom + 1]
    spans = [s.x1 for s in block_h if s.x0 <= xm + 2 and s.x1 > xm + pt(20)]
    if spans:
        xr = max(spans)
        grown = True
        while grown:
            grown = False
            for s in block_h:
                if s.x0 <= xr + DOUBLE and s.x1 > xr + 1:
                    xr, grown = s.x1, True
    else:
        cover = [s.x0 for s in page.vsegs if s.x0 > xm + pt(20) and s.top <= top + pt(2) and s.bottom >= bottom - pt(2)]
        xr = min(cover) if cover else xl + (xm - xl) * profile.width / profile.left_width

    # ячейки правой части: для каждого слова — ближайшие линии со всех сторон
    vsegs = [s for s in page.vsegs if xm - 2 <= s.x0 <= xr + 2 and s.bottom >= top - 2 and s.top <= bottom + 2]
    hsegs = [s for s in page.hsegs if top - 2 <= s.top <= bottom + 2 and s.x1 >= xm - 2 and s.x0 <= xr + 2]
    cells: dict[tuple[int, int, int, int], _Cell] = {}
    for w in _words_in(page.words, (xm + 0.5, top, xr, bottom)):
        box = _enclosing(w, vsegs, hsegs, (xm, top, xr, bottom))
        cells.setdefault(tuple(round(v) for v in box), _Cell(box)).words.append(w)
    tb = _assign(list(cells.values()), xm, top, xr, bottom, ctx)

    # графа 27 — строка над основной надписью, если её верх отчерчен линией и графа 27 в редакции есть;
    # под основной надписью — запись формата и «Копировал»
    if profile.customer_row:
        row27 = pt(profile.customer_row) * scale
        lined = any(abs(s.top - (top - row27)) <= pt(2) and s.x1 > xm and s.x0 < xr for s in page.hsegs) and any(
            abs(s.x0 - xm) <= pt(2) and s.top <= top - row27 + pt(2) and s.bottom >= top - pt(2) for s in page.vsegs
        )
        above_words = _words_in(page.words, (xm, top - row27, xr, top - 0.5))
        if lined and above_words and (text := _join(above_words)) and ctx.org.search(text):  # наименование заказчика
            tb.graphs[27] = text
    below_words = _words_in(page.words, (xm, bottom + 0.5, xr, bottom + pt(8) * scale))
    _put_below(tb, _join(below_words), profile)

    # левая часть: подписи под шапкой, изменения над ней
    change_cols = _column_roles(head, cols, roles)
    for y0, y1 in signs:
        row = _words_in(page.words, (xl, y0, xm, y1))
        if not row:
            continue
        parts = _split_signature(row, xl, xm, page, y0, y1, profile)
        tb.signatures.append(SignRow(role=parts[0], name=parts[1], date=parts[2], bbox=(xl, y0, xm, y1)))
    for y0, y1 in changes:
        row = _words_in(page.words, (xl, y0, xm, y1))
        if not row:
            continue
        by_role = {r: _join([w for w in row if a <= w.cx <= b]) for r, (a, b) in change_cols.items()}
        tb.changes.append(
            ChangeRow(
                change=by_role.get(14, ""),
                count=by_role.get(15, ""),
                sheet=by_role.get(16, ""),
                doc=by_role.get(17, ""),
                date=by_role.get(19, ""),
                bbox=(xl, y0, xm, y1),
            )
        )
    tb.bbox = (xl, top, xr, bottom)
    tb.scale = round(scale, 2)
    tb.confidence = round((0.9 if spans else 0.7) - (0.2 if not tb.get(1) else 0.0), 2)
    return tb


def _below(text: str, profile: TitleBlockProfile) -> tuple[bool, dict[str, str]]:
    """Подписи под основной надписью → (нашлась ли подпись, значения после подписей).

    «Копировал Иванов Формат А3» → {"copier": "Иванов", "format": "А3"}: значение — до следующей подписи,
    у формата — первое слово.
    """
    marks = sorted(
        (m.start(), m.end(), kind)
        for kind, labels in (("format", profile.format_labels), ("copier", profile.copier_labels))
        for label in labels
        for m in re.finditer(re.escape(label), text)
    )
    values: dict[str, str] = {}
    for i, (_start, end, kind) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        if (value := text[end:stop].strip()) and kind not in values:
            values[kind] = value.split()[0] if kind == "format" else value
    return bool(marks), values


def _put_below(tb: TitleBlock, text: str, profile: TitleBlockProfile) -> bool:
    """Значения из-под основной надписи — по смыслу граф редакции: формат — графа 26 в ГОСТ Р 21.101-2020,
    «Копировал» — графа 26 в ГОСТ 21.101-97. False — подписей в тексте нет."""
    found, values = _below(text, profile)
    if fmt := values.get("format"):
        tb.format_text = fmt
        if profile.format_graph:
            tb.graphs[profile.format_graph] = fmt
    if (copier := values.get("copier")) and profile.copier_graph:
        tb.graphs[profile.copier_graph] = copier
    return found


def _enclosing(w: Word, vsegs: list[Segment], hsegs: list[Segment], area: BBox) -> BBox:
    x_min, y_min, x_max, y_max = area
    left = max((s.x0 for s in vsegs if s.x0 <= w.cx and s.top - 1 <= w.cy <= s.bottom + 1), default=x_min)
    right = min((s.x0 for s in vsegs if s.x0 >= w.cx and s.top - 1 <= w.cy <= s.bottom + 1), default=x_max)
    up = max((s.top for s in hsegs if s.top <= w.cy and s.x0 - 1 <= w.cx <= s.x1 + 1), default=y_min)
    down = min((s.top for s in hsegs if s.top >= w.cy and s.x0 - 1 <= w.cx <= s.x1 + 1), default=y_max)
    return (max(left, x_min), max(up, y_min), min(right, x_max), min(down, y_max))


def _column_roles(head: list[Word], cols: list[float], roles: Roles) -> dict[int, tuple[float, float]]:
    """Колонки таблицы изменений по словам шапки: графа → (x0, x1); склеенные слова — по порядку ГОСТ."""
    out: dict[int, tuple[float, float]] = {}
    for a, b in itertools.pairwise(cols):
        keys = [_key(w.text) for w in head if a <= w.cx <= b]
        for k in keys:
            if (r := _header_role(k, roles)) is not None and r not in out:
                out[r] = (a, b)
    return out


def _split_signature(
    row: list[Word], xl: float, xm: float, page: PdfPage, y0: float, y1: float, profile: TitleBlockProfile
) -> list[str]:
    """Строка подписей → должность, фамилия, дата. Колонки — по линиям строки, иначе по долям профиля (графы 10–13)."""
    yc = (y0 + y1) / 2
    xs = _merge([s.x0 for s in page.vsegs if s.top <= yc <= s.bottom and xl - 1 <= s.x0 <= xm + 1])
    inner = [x for x in xs if xl + 2 < x < xm - 2]
    columns = list(profile.sign_columns.values())
    if len(inner) == len(columns) - 1:  # линии между всеми колонками: 10, 11, 12, 13
        b = [xl, *inner, xm]
    else:
        w = xm - xl
        b = [xl, *(xl + w * x1 / profile.left_width for _x0, x1 in columns[:-1]), xm]
    role = _join([v for v in row if b[0] <= v.cx < b[1]])
    name = _join([v for v in row if b[1] <= v.cx < b[2]])
    date = _join([v for v in row if b[3] <= v.cx <= b[4]])
    return [role, name, date]


def _designation_in(text: str, cb: Codebook) -> str | None:
    """Обозначение в тексте ячейки: вся ячейка или отдельное слово с шифром из справочника юрисдикции.

    Без базового обозначения («-ОВ», «ПОС») принимаем, только если в ячейке больше ничего нет: иначе
    «Альбом АР» стал бы обозначением.
    """
    known = cb.all_codes
    text = re.sub(r"(^|\s)[-–]\s+(?=[А-ЯЁA-Z])", r"\1-", text)  # «Заказ: - ОВ.СО» → «-ОВ.СО»
    words = text.split()
    single = len(words) == 1
    for cand in [text, *words]:
        d = parse(cand, cb)
        if not d or not (d.code in known or d.suffix == "СП") or "шифр не из справочника юрисдикции" in d.issues:
            continue
        # базовое обозначение — код договора или объекта: с цифрами и коротко; иначе это проза
        if d.base and re.search(r"\d", d.base) and len(cand) <= 40:
            return cand
        # без базового: ячейка из одного шифра («ПОС») или шифр с дефисом среди текста («Заказчик: … -ОВ»)
        if not d.base and (single or (cand in words and cand.startswith(("-", "–")))):
            return cand
    return None


class _Slots:
    """Раскладка ячеек правой части по графам: что занято и где."""

    def __init__(self, cells: list[_Cell]):
        self.cells = cells
        self.graphs: dict[int, str] = {}
        self.boxes: dict[int, BBox] = {}
        self.used: set[int] = set()
        self.rest: list[str] = []  # остаток текста ячеек, из которых взяли часть

    def free(self) -> list[int]:
        return [i for i in range(len(self.cells)) if i not in self.used]

    def take(self, g: int, i: int, text: str | None = None) -> None:
        if g in self.graphs:
            return
        self.graphs[g] = self.cells[i].text if text is None else text
        self.boxes[g] = self.cells[i].box
        self.used.add(i)


def _assign(cells: list[_Cell], xm: float, top: float, xr: float, bottom: float, ctx: Context) -> TitleBlock:
    """Графы правой части по взаимному расположению ячеек (см. описание модуля)."""
    s = _Slots(cells)
    graph_of = {_key(label): g for label, g in ctx.titleblocks.label_graphs.items()}

    # подписи «Стадия», «Лист», «Листов», «Масса», «Масштаб»; «Лист 3» в одной ячейке (графы 7 и 8) — форма 4
    labels: dict[int, _Cell] = {}
    for i, c in enumerate(cells):
        k = _key(c.text)
        if k in graph_of:
            labels[graph_of[k]] = c
            s.used.add(i)
        elif c.words and graph_of.get(_key(c.words[0].text)) in (7, 8) and len(c.words) > 1:
            s.take(graph_of[_key(c.words[0].text)], i, _join(c.words[1:]))

    # значения под подписями: та же колонка (не шире полутора подписей), ближайшая ячейка ниже. Пустую
    # ячейку значения не видно — тогда соседняя широкая (организация) не должна выдать себя за значение
    band = list(labels.values())
    for g, lab in labels.items():
        lab_w, lab_h = lab.x1 - lab.x0, lab.bottom - lab.top
        below = [
            i
            for i in s.free()
            if cells[i].top >= lab.bottom - 2
            and cells[i].x1 - cells[i].x0 <= 1.6 * lab_w + 2
            and _overlap(cells[i].x0, cells[i].x1, lab.x0, lab.x1) >= 0.5 * min(cells[i].x1 - cells[i].x0, lab_w)
        ]
        if below:
            i = min(below, key=lambda j: cells[j].top)
            if cells[i].top - lab.bottom < 1.5 * lab_h + 4:
                s.take(g, i)
                band.append(cells[i])

    # обозначение — самая верхняя ячейка с обозначением; нет — самая верхняя широкая ячейка (что там написано)
    des = [i for i in s.free() if _designation_in(cells[i].text, ctx.codebook)]
    g1_bottom = top
    if des:
        i = min(des, key=lambda j: (cells[j].top, -len(cells[j].text)))
        found = _designation_in(cells[i].text, ctx.codebook)
        s.take(1, i, found)
        if found != cells[i].text and (rest := cells[i].text.replace(found, " ").strip()):
            s.rest.append(rest)  # «Заказчик: …» в одной ячейке с обозначением — не терять
        g1_bottom = cells[i].bottom
    elif wide := [i for i in s.free() if cells[i].x1 - cells[i].x0 >= 0.5 * (xr - xm)]:
        i = min(wide, key=lambda j: cells[j].top)
        s.take(1, i)
        g1_bottom = cells[i].bottom

    first_sheet = 6 in labels or 8 in labels or 6 in s.graphs or 8 in s.graphs  # у последующих листов только «Лист»
    if not first_sheet or not band:
        form = 6
    else:
        lab_top = min(c.top for c in band)
        lab_bottom = max(c.bottom for c in band)
        lab_x0 = min(c.x0 for c in band)
        org = [i for i in s.free() if cells[i].top >= lab_bottom - 2 and cells[i].x0 >= lab_x0 - 2]
        org_top, org_bottom = lab_bottom, bottom
        if org:
            i = max(org, key=lambda j: (bool(ctx.org.search(cells[j].text)), cells[j].bottom))
            s.take(9, i)
            org_top, org_bottom = cells[i].top, cells[i].bottom
        for i in s.free():  # между обозначением и полосой подписей — графа 2
            if cells[i].top >= g1_bottom - 2 and cells[i].bottom <= lab_top + 2:
                s.take(2, i)
        for i in sorted((i for i in s.free() if cells[i].x1 <= lab_x0 + 2), key=lambda j: cells[j].top):
            c = cells[i]
            in_lab = _overlap(c.top, c.bottom, lab_top, lab_bottom) > 2
            in_org = _overlap(c.top, c.bottom, org_top, org_bottom) > 2
            if in_lab and in_org:
                s.take(5, i)  # форма 5: наименование документа тянется через обе полосы
            elif in_lab:
                s.take(3 if 3 not in s.graphs else 5, i)
            elif in_org:
                s.take(4 if (3 in s.graphs or 2 in s.graphs) else 5, i)
        if 24 in s.graphs or 25 in s.graphs:
            form = 4
        elif 5 in s.graphs and 3 not in s.graphs:
            form = 5
        else:
            form = 3

    return TitleBlock(
        form=form,
        bbox=(xm, top, xr, bottom),
        graphs={g: v for g, v in s.graphs.items() if v},
        cells=s.boxes,
        extra=[*s.rest, *(cells[i].text for i in s.free() if cells[i].text)],
        method="grid",
        confidence=0.0,
    )


# --- 2. по геометрии ГОСТ у угла рамки ----------------------------------------------------------------


def _corner(page: PdfPage, profile: TitleBlockProfile) -> tuple[float, float]:
    """Правый нижний угол внутренней рамки: ожидаемый по полям, уточнённый по линиям рядом.

    Полей бывает двое — обычные и рамки типовой проектной документации (ГОСТ 21.101-97, прил. Е: снизу 15 мм
    вместо 5): берём те, у которых нашлась нижняя линия рамки, иначе — правая, иначе — обычные.
    """
    tol = pt(TOL_MM)
    found: list[tuple[bool, bool, float, float]] = []
    for frame in profile.frames:
        right = page.width - pt(frame["right"])
        bottom = page.height - pt(frame["bottom"])
        vs = [
            s.x0
            for s in page.vsegs
            if abs(s.x0 - right) <= tol and s.bottom >= bottom - tol and s.bottom - s.top >= pt(10)
        ]
        hs = [s.top for s in page.hsegs if abs(s.top - bottom) <= tol and s.x1 >= right - tol and s.x1 - s.x0 >= pt(50)]
        if vs:
            right = min(vs, key=lambda x: abs(x - right))
        if hs:
            bottom = min(hs, key=lambda y: abs(y - bottom))
        found.append((bool(hs), bool(vs), right, bottom))
    by_bottom = next((c for c in found if c[0]), None)
    by_right = next((c for c in found if c[1]), None)
    _h, _v, right, bottom = by_bottom or by_right or found[0]
    return right, bottom


def _coverage(segs: list[Segment], y: float, x0: float, x1: float) -> float:
    """Доля отрезка [x0, x1] на высоте y, покрытая горизонталями (линия бывает из нескольких кусков)."""
    spans = sorted((max(s.x0, x0), min(s.x1, x1)) for s in segs if abs(s.top - y) <= 1.5 and s.x1 > x0 and s.x0 < x1)
    covered, cur_a, cur_b = 0.0, None, None
    for a, b in spans:
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                covered += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    if cur_b is not None:
        covered += cur_b - cur_a
    return covered / (x1 - x0) if x1 > x0 else 0.0


def _columns(vsegs: list[Segment], left: float, top: float, columns: tuple[float, ...]) -> int:
    """Сколько границ колонок таблицы изменений начинаются от верха основной надписи."""
    n = 0
    for x in columns:
        cx = left + pt(x)
        if any(abs(s.x0 - cx) <= 1.5 and s.top <= top + pt(2) and s.bottom >= top + pt(4) for s in vsegs):
            n += 1
    return n


def _by_frame(page: PdfPage, profile: TitleBlockProfile, right: float, bottom: float) -> tuple[int, _Frame, str] | None:
    left = right - pt(profile.width)
    for h, form in profile.heights.items():
        top = bottom - pt(h)
        if _coverage(page.hsegs, top, left, right) < 0.95:
            continue
        if _columns(page.vsegs, left, top, profile.left_columns) < MIN_COLUMNS:
            continue
        return form, _Frame(left, top, right, bottom), "frame"
    return None


# --- 3. по геометрии ГОСТ от шапки без линий -------------------------------------------------------------


def _by_labels(
    page: PdfPage, profile: TitleBlockProfile, right: float, bottom: float
) -> tuple[int, _Frame, str] | None:
    left = right - pt(profile.width)
    zone = [
        w for w in page.words if w.cx >= left - pt(5) and w.cx <= right and w.cy >= bottom - pt(70) and w.cy <= bottom
    ]
    first = _key(profile.header_labels[0])  # «Изм.»
    heads = [w for w in zone if _key(w.text) == first and w.cx <= left + pt(12)]
    for head in heads:
        row = [w for w in zone if abs(w.cy - head.cy) <= pt(1.5)]
        labels = {_key(w.text) for w in row}
        if len(labels & {_key(x) for x in profile.header_labels}) < 3:
            continue
        from_bottom = (bottom - head.cy) / pt(1)
        for form, expected in profile.header_from_bottom.items():
            if abs(from_bottom - expected) <= 2.0:
                height = profile.forms[form].height
                return form, _Frame(left, bottom - pt(height), right, bottom), "labels"
    return None


def _has_words(page: PdfPage, frame: _Frame, keys: set[str]) -> bool:
    return any(_key(w.text) in keys for w in _words_in(page.words, (frame.left, frame.top, frame.right, frame.bottom)))


def _cell(frame: _Frame, c: tuple[float, float, float, float]) -> BBox:
    x0, x1, y0, y1 = c
    return (frame.left + pt(x0), frame.top + pt(y0), frame.left + pt(x1), frame.top + pt(y1))


def _read(page: PdfPage, form: FormProfile, profile: TitleBlockProfile, frame: _Frame, method: str) -> TitleBlock:
    drop = {_key(x) for x in (*form.labels, *profile.header_labels, *profile.format_labels, *profile.copier_labels)}
    near = _words_in(page.words, (frame.left - pt(2), frame.top - pt(12), frame.right + pt(2), frame.bottom + pt(6)))
    below = profile.format_graph or profile.copier_graph  # ячейка под основной надписью: графа 26
    graphs: dict[int, str] = {}
    cells: dict[int, BBox] = {}
    for g, c in form.graphs.items():
        box = _cell(frame, c)
        cells[g] = box
        if g == below:
            continue
        text = _join(_words_in(near, box), drop)
        if text:
            graphs[g] = text

    signatures = []
    for y0, y1 in form.signatures:
        row = {
            g: _join(_words_in(near, _cell(frame, (x0, x1, y0, y1)))) for g, (x0, x1) in profile.sign_columns.items()
        }
        if any(row.values()):
            signatures.append(SignRow(role=row[10], name=row[11], date=row[13], bbox=_cell(frame, (0, 65, y0, y1))))

    changes = []
    for y0, y1 in form.changes:
        row = {
            g: _join(_words_in(near, _cell(frame, (x0, x1, y0, y1)))) for g, (x0, x1) in profile.change_columns.items()
        }
        if any(row.values()):
            changes.append(
                ChangeRow(
                    change=row[14],
                    count=row[15],
                    sheet=row[16],
                    doc=row[17],
                    date=row[19],
                    bbox=_cell(frame, (0, 65, y0, y1)),
                )
            )

    confidence = 0.95 if method == "frame" else 0.75
    if not graphs.get(1):
        confidence -= 0.2
    tb = TitleBlock(
        form=form.form,
        bbox=(frame.left, frame.top, frame.right, frame.bottom),
        graphs=graphs,
        cells=cells,
        signatures=signatures,
        changes=changes,
        method=method,
        confidence=round(confidence, 2),
    )
    if below in cells:
        words = _words_in(near, cells[below])
        if not _put_below(tb, _join(words), profile) and (value := _join(words, drop)):
            # значение без подписи: похоже на формат — запись формата, иначе — графа ячейки
            if normalize_format(value):
                tb.format_text = value
            if profile.format_graph or not normalize_format(value):
                tb.graphs[below] = value
    return tb
