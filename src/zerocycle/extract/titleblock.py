"""Поиск и разбор основной надписи листа (ГОСТ Р 21.101-2020, прил. Ж).

Основная надпись стоит в правом нижнем углу внутренней рамки: правый край — в 5 мм от края листа, нижний —
в 5 мм. Ширина 185 мм, высота задаёт форму: 55 мм — формы 3 и 4, 40 мм — форма 5, 15 мм — форма 6.

Как ищем:
1. «frame» — по линиям: горизонталь шириной 185 мм на высоте 55, 40 или 15 мм над низом рамки и вертикали
   колонок таблицы изменений под ней. Форму 4 отличаем от 3 по надписям «Масса» и «Масштаб».
2. «labels» — без линий (штамп напечатан текстом): по шапке «Изм. … Дата» в правом нижнем углу; форма —
   по высоте шапки над низом рамки.

Затем слова раскладываются по ячейкам профиля формы (`packs/spds/titleblocks.toml`): графы 1–9, 23–27,
строки подписей (10–13) под шапкой и строки изменений (14–19) над ней.

Ограничение v0: масштаб 1:1. Лист, напечатанный в PDF с уменьшением, не разбирается — возвращаем None,
а не угадываем.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from zerocycle.ingest.pdf import PdfPage, Segment, Word
from zerocycle.model import BBox, ChangeRow, SignRow, TitleBlock
from zerocycle.packs import FormProfile, TitleBlockProfile, titleblock_profile
from zerocycle.units import pt

TOL_MM = 3.0  # допуск положения рамки и линий
WIDTH_MM = 185.0
HEIGHTS_MM = (55.0, 40.0, 15.0)  # от высокой к низкой: у формы 3 есть и короткие горизонтали
LEFT_COLUMNS_MM = (10.0, 20.0, 30.0, 40.0, 55.0, 65.0)  # границы колонок таблицы изменений
MIN_COLUMNS = 4  # сколько из них должно найтись, чтобы поверить рамке
# центр строки шапки «Изм. … Дата» над низом основной надписи, мм → форма
HEADER_FROM_BOTTOM = {3: 32.5, 5: 27.5, 6: 2.5}


@dataclass(frozen=True)
class _Frame:
    left: float
    top: float
    right: float
    bottom: float


def _key(text: str) -> str:
    """Подпись графы для сравнения: «Кол. уч.» → «колуч», «Н. контр.» → «нконтр»."""
    return re.sub(r"[\s.]", "", text).lower()


def find_title_block(page: PdfPage, profile: TitleBlockProfile | None = None) -> TitleBlock | None:
    profile = profile or titleblock_profile()
    right, bottom = _corner(page, profile)
    found = _by_frame(page, right, bottom) or _by_labels(page, profile, right, bottom)
    if found is None:
        return None
    form, frame, method = found
    if form == 3 and _has_words(page, frame, {"масса", "масштаб"}):
        form = 4
    return _read(page, profile.forms[form], profile, frame, method)


def _corner(page: PdfPage, profile: TitleBlockProfile) -> tuple[float, float]:
    """Правый нижний угол внутренней рамки: ожидаемый по полям, уточнённый по линиям рядом."""
    right = page.width - pt(profile.frame["right"])
    bottom = page.height - pt(profile.frame["bottom"])
    tol = pt(TOL_MM)
    vs = [
        s.x0 for s in page.vsegs if abs(s.x0 - right) <= tol and s.bottom >= bottom - tol and s.bottom - s.top >= pt(10)
    ]
    hs = [s.top for s in page.hsegs if abs(s.top - bottom) <= tol and s.x1 >= right - tol and s.x1 - s.x0 >= pt(50)]
    if vs:
        right = min(vs, key=lambda x: abs(x - right))
    if hs:
        bottom = min(hs, key=lambda y: abs(y - bottom))
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


def _columns(vsegs: list[Segment], left: float, top: float) -> int:
    """Сколько границ колонок таблицы изменений начинаются от верха основной надписи."""
    n = 0
    for x in LEFT_COLUMNS_MM:
        cx = left + pt(x)
        if any(abs(s.x0 - cx) <= 1.5 and s.top <= top + pt(2) and s.bottom >= top + pt(4) for s in vsegs):
            n += 1
    return n


def _by_frame(page: PdfPage, right: float, bottom: float) -> tuple[int, _Frame, str] | None:
    left = right - pt(WIDTH_MM)
    for h in HEIGHTS_MM:
        top = bottom - pt(h)
        if _coverage(page.hsegs, top, left, right) < 0.95:
            continue
        if _columns(page.vsegs, left, top) < MIN_COLUMNS:
            continue
        form = {55.0: 3, 40.0: 5, 15.0: 6}[h]
        return form, _Frame(left, top, right, bottom), "frame"
    return None


def _by_labels(
    page: PdfPage, profile: TitleBlockProfile, right: float, bottom: float
) -> tuple[int, _Frame, str] | None:
    left = right - pt(WIDTH_MM)
    zone = [
        w for w in page.words if w.cx >= left - pt(5) and w.cx <= right and w.cy >= bottom - pt(70) and w.cy <= bottom
    ]
    heads = [w for w in zone if _key(w.text) == "изм" and w.cx <= left + pt(12)]
    for head in heads:
        row = [w for w in zone if abs(w.cy - head.cy) <= pt(1.5)]
        labels = {_key(w.text) for w in row}
        if len(labels & {_key(x) for x in profile.header_labels}) < 3:
            continue
        from_bottom = (bottom - head.cy) / pt(1)
        for form, expected in HEADER_FROM_BOTTOM.items():
            if abs(from_bottom - expected) <= 2.0:
                height = profile.forms[form].height
                return form, _Frame(left, bottom - pt(height), right, bottom), "labels"
    return None


def _has_words(page: PdfPage, frame: _Frame, keys: set[str]) -> bool:
    return any(_key(w.text) in keys for w in _words_in(page.words, (frame.left, frame.top, frame.right, frame.bottom)))


def _cell(frame: _Frame, c: tuple[float, float, float, float]) -> BBox:
    x0, x1, y0, y1 = c
    return (frame.left + pt(x0), frame.top + pt(y0), frame.left + pt(x1), frame.top + pt(y1))


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


def _read(page: PdfPage, form: FormProfile, profile: TitleBlockProfile, frame: _Frame, method: str) -> TitleBlock:
    drop = {_key(x) for x in (*form.labels, *profile.header_labels, "Формат")}
    near = _words_in(page.words, (frame.left - pt(2), frame.top - pt(12), frame.right + pt(2), frame.bottom + pt(6)))
    graphs: dict[int, str] = {}
    cells: dict[int, BBox] = {}
    for g, c in form.graphs.items():
        box = _cell(frame, c)
        text = _join(_words_in(near, box), drop)
        cells[g] = box
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
    return TitleBlock(
        form=form.form,
        bbox=(frame.left, frame.top, frame.right, frame.bottom),
        graphs=graphs,
        cells=cells,
        signatures=signatures,
        changes=changes,
        method=method,
        confidence=round(confidence, 2),
    )
