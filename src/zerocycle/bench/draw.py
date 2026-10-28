"""Листы по ГОСТ Р 21.101-2020 для засеянных пакетов: рамка, основная надпись, текст, таблицы (reportlab, BSD).

Координаты — миллиметры от левого верхнего угла листа, как в профиле основных надписей; reportlab считает
от левого нижнего угла в пунктах — перевод только здесь. Геометрия основной надписи берётся из того же
профиля, по которому её читает парсер, а сам профиль сверен с рисунками прил. Ж; устойчивость парсера
к чужим PDF проверяет реальный публичный слой (PLAN, шаг 0.11).

Генератор v1 рисует и штампы «организаций» (`StampStyle`): строки 4,4–6,8 мм вместо 5, колонки шире или уже,
шапка ЕСКД без «Кол.уч», таблица Word с рамкой у каждой ячейки — варианты из реальных ПСД (docs/bench/REAL_KZ.md).
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from zerocycle.packs import FormProfile, TitleBlockProfile, titleblock_profile
from zerocycle.units import format_size, pt

FONT = "ZC"
THICK = 0.7  # мм: сплошная толстая основная линия
THIN = 0.25

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


@cache
def register_font() -> str:
    """Шрифт с кириллицей: ZEROCYCLE_FONT или первый найденный из системных."""
    for p in [os.environ.get("ZEROCYCLE_FONT", ""), *_FONT_CANDIDATES]:
        if p and Path(p).is_file():
            pdfmetrics.registerFont(TTFont(FONT, p))
            return p
    raise RuntimeError("не найден TTF-шрифт с кириллицей: укажите путь в ZEROCYCLE_FONT")


@dataclass(frozen=True)
class StampStyle:
    """Как «организация» рисует основную надпись. По умолчанию — как в прил. Ж ГОСТ Р 21.101-2020."""

    row: float = 5.0  # высота строки, мм
    columns: tuple[float, ...] = (10, 10, 10, 10, 15, 10)  # колонки таблицы изменений; пять — шапка ЕСКД
    header: tuple[str, ...] = ("Изм.", "Кол.уч", "Лист", "№ док.", "Подп.", "Дата")
    gap: float = 0.0  # таблица Word: у каждой ячейки своя рамка с зазором, каждая граница — двойная
    grid: str | None = None  # «cells» | «long»; None — как у пакета


GOST = StampStyle()


@cache
def styled_profile(style: StampStyle) -> TitleBlockProfile:
    """Профиль основных надписей со строками и колонками стиля: правая часть сдвигается за левой."""
    base = titleblock_profile("2020")
    if style == GOST:
        return base
    ky = style.row / 5.0
    xs = [0.0]
    for w in style.columns:
        xs.append(xs[-1] + w)
    graphs = (14, 15, 16, 17, 18, 19) if len(style.columns) == 6 else (14, 16, 17, 18, 19)  # ЕСКД — без «Кол.уч»
    changes = {g: (xs[i], xs[i + 1]) for i, g in enumerate(graphs)}
    n = len(xs) - 1
    signs = {10: (xs[0], xs[2]), 11: (xs[2], xs[n - 2]), 12: (xs[n - 2], xs[n - 1]), 13: (xs[n - 1], xs[n])}
    left = base.left_width
    dx = xs[-1] - left

    def cell(c):
        x0, x1, y0, y1 = c
        return (x0 + dx if x0 >= left else x0, x1 + dx, y0 * ky, y1 * ky)

    forms = {
        k: dataclasses.replace(
            f,
            width=f.width + dx,
            height=f.height * ky,
            header=(f.header[0] * ky, f.header[1] * ky),
            changes=tuple((a * ky, b * ky) for a, b in f.changes),
            signatures=tuple((a * ky, b * ky) for a, b in f.signatures),
            graphs={g: cell(c) for g, c in f.graphs.items()},
            labels={name: cell(c) for name, c in f.labels.items()},
        )
        for k, f in base.forms.items()
    }
    return dataclasses.replace(
        base,
        width=base.width + dx,
        change_columns=changes,
        sign_columns=signs,
        header_labels=style.header,
        forms=forms,
    )


@dataclass
class TitleValues:
    """Что вписать в основную надпись. Пустая строка — графа пустая."""

    form: int
    graphs: dict[int, str] = field(default_factory=dict)
    signatures: list[tuple[str, str, str]] = field(default_factory=list)  # графы 10, 11, 13
    changes: list[tuple[str, str, str, str, str]] = field(default_factory=list)  # графы 14, 15, 16, 17, 19


class Sheet:
    """Один лист: рамка, основная надпись, тексты и таблицы в мм от левого верхнего угла."""

    def __init__(self, canvas: Canvas, paper: str, landscape: bool, style: StampStyle | None = None):
        short, long_ = format_size(paper)
        self.w, self.h = (long_, short) if landscape else (short, long_)
        self.c = canvas
        self.c.setPageSize((pt(self.w), pt(self.h)))
        self.style = style or GOST
        # листы засеянного корпуса — ГОСТ Р 21.101-2020; стиль организации двигает строки и колонки
        self.profile: TitleBlockProfile = styled_profile(self.style)

    # --- примитивы -------------------------------------------------------------------------------
    def line(self, x0: float, y0: float, x1: float, y1: float, width: float = THIN, dash: bool = False) -> None:
        self.c.setLineWidth(pt(width))
        self.c.setDash(pt(2), pt(1)) if dash else self.c.setDash()
        self.c.line(pt(x0), pt(self.h - y0), pt(x1), pt(self.h - y1))

    def text(self, x: float, y: float, s: str, size: float = 8, align: str = "left") -> None:
        """y — базовая линия, мм от верха листа."""
        self.c.setFont(FONT, size)
        X, Y = pt(x), pt(self.h - y)
        {"left": self.c.drawString, "center": self.c.drawCentredString, "right": self.c.drawRightString}[align](X, Y, s)

    def vtext(self, x: float, y: float, s: str, size: float = 6) -> None:
        """Текст снизу вверх (поле для подшивки): x — базовая линия, y — начало строки, мм от верха листа."""
        self.c.saveState()
        self.c.translate(pt(x), pt(self.h - y))
        self.c.rotate(90)
        self.c.setFont(FONT, size)
        self.c.drawString(0, 0, s)
        self.c.restoreState()

    def text_in(self, box: tuple[float, float, float, float], s: str, size: float = 8, align: str = "center") -> None:
        """Текст в ячейке [x0, x1, y0, y1]: переносится по словам, при нехватке места — мельче."""
        x0, x1, y0, y1 = box
        if not s:
            return
        while True:
            lines = simpleSplit(s, FONT, size, pt(x1 - x0 - 1.5))
            line_h = size * 0.42  # мм на строку: кегль в пт × ~1,2 межстрочный
            if len(lines) * line_h <= (y1 - y0) - 0.6 or size <= 4:
                break
            size -= 0.5
        total = len(lines) * line_h
        base = y0 + ((y1 - y0) - total) / 2 + line_h * 0.78
        for i, ln in enumerate(lines):
            y = base + i * line_h
            if align == "center":
                self.text((x0 + x1) / 2, y, ln, size, "center")
            else:
                self.text(x0 + 1.0, y, ln, size, "left")

    def rect(self, x0: float, y0: float, x1: float, y1: float, width: float = THIN, inset: float = 0.0) -> None:
        a, b, c, d = x0 + inset, y0 + inset, x1 - inset, y1 - inset
        for p in ((a, b, c, b), (c, b, c, d), (c, d, a, d), (a, d, a, b)):
            self.line(*p, width)

    # --- рамка и основная надпись -----------------------------------------------------------------
    def frame(self) -> tuple[float, float, float, float]:
        f = self.profile.frame
        x0, y0, x1, y1 = f["left"], f["top"], self.w - f["right"], self.h - f["bottom"]
        for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0)):
            self.line(a, b, c, d, THICK)
        return x0, y0, x1, y1

    def title_block(self, v: TitleValues, grid: str = "cells", with_lines: bool = True) -> tuple[float, float]:
        """Нарисовать основную надпись; вернуть левый верхний угол (мм). grid: «cells» — отрезками ячеек,
        «long» — длинными линиями (как из разных САПР); with_lines=False — только текст (штамп «напечатан»)."""
        form: FormProfile = self.profile.forms[v.form]
        f = self.profile.frame
        left = self.w - f["right"] - form.width
        top = self.h - f["bottom"] - form.height
        if with_lines and self.style.gap:
            self._title_cells(form, left, top)
        elif with_lines:
            self._title_lines(form, left, top, self.style.grid or grid, with27=bool(v.graphs.get(27)))
        cell = lambda c: (left + c[0], left + c[1], top + c[2], top + c[3])  # noqa: E731

        # подписи граф
        for label, c in form.labels.items():
            self.text_in(cell(c), label, 6)
        y0, y1 = form.header
        for label, (x0, x1) in zip(self.profile.header_labels, self.profile.change_columns.values(), strict=True):
            self.text_in(cell((x0, x1, y0, y1)), label, 6)
        # графы
        for g, s in v.graphs.items():
            if g in form.graphs and s:
                box = cell(form.graphs[g])
                if g == 26:
                    self.text_in(box, f"Формат {s}", 7)
                elif v.form == 4 and g in (7, 8):
                    self.text_in(box, f"{'Лист' if g == 7 else 'Листов'} {s}", 7)
                else:
                    self.text_in(box, s, 10 if g in (1,) else 8)
        # подписи и изменения
        cols = self.profile.sign_columns
        for (y0, y1), (role, name, date) in zip(form.signatures, v.signatures, strict=False):
            self.text_in(cell((*cols[10], y0, y1)), role, 6.5, "left")
            self.text_in(cell((*cols[11], y0, y1)), name, 6.5, "left")
            self.text_in(cell((*cols[13], y0, y1)), date, 6)
        ccols = self.profile.change_columns
        for (y0, y1), row in zip(reversed(form.changes), v.changes, strict=False):
            for g, s in zip((14, 15, 16, 17, 19), row, strict=True):
                if g in ccols:
                    self.text_in(cell((*ccols[g], y0, y1)), s, 6)
        return left, top

    def _title_lines(self, form: FormProfile, left: float, top: float, grid: str, with27: bool) -> None:
        W, H = form.width, form.height
        L = self.profile.left_width  # левая часть: таблица изменений и подписи
        row = self.style.row
        # верх основной надписи: одной линией или кусками (правую часть дорисуют контуры граф)
        self.line(left, top, left + (W if grid == "long" else L), top, THICK)
        self.line(left, top, left, top + H, THICK)
        self.line(left + L, top, left + L, top + H, THICK)
        # строки левой части; шапка и её нижняя граница толстые
        hy0, hy1 = form.header
        for k in range(1, round(H / row)):
            y = k * row
            w = THICK if min(abs(y - hy0), abs(y - hy1)) < 1e-6 else THIN
            if grid == "long":
                self.line(left, top + y, left + L, top + y, w)
            else:
                for x0, x1 in self.profile.change_columns.values():
                    self.line(left + x0, top + y, left + x1, top + y, w)
        # колонки левой части: 14–19 над шапкой и в ней, 10–13 — ниже
        for x0, _x1 in list(self.profile.change_columns.values())[1:]:
            self.line(left + x0, top, left + x0, top + hy1, THICK)
        for x0, _x1 in list(self.profile.sign_columns.values())[1:]:
            self.line(left + x0, top + hy1, left + x0, top + H, THICK)
        # ячейки правой части — по контурам граф
        for g, (x0, x1, y0, y1) in form.graphs.items():
            if g in (26, 27):
                continue
            for a, b, c, d in ((x0, y0, x1, y0), (x1, y0, x1, y1), (x0, y1, x1, y1), (x0, y0, x0, y1)):
                self.line(left + a, top + b, left + c, top + d, THICK if x0 >= L else THIN)
        for _label, (x0, x1, y0, y1) in form.labels.items():
            self.line(left + x0, top + y0, left + x1, top + y0, THIN)
            self.line(left + x0, top + y1, left + x1, top + y1, THIN)
            self.line(left + x0, top + y0, left + x0, top + y1, THIN)
            self.line(left + x1, top + y0, left + x1, top + y1, THIN)
        if with27 and 27 in form.graphs:  # графа 27 — штриховая строка над основной надписью, по необходимости
            x0, x1, y0, y1 = form.graphs[27]
            self.line(left + x0, top + y0, left + x1, top + y0, THIN, dash=True)
            self.line(left + x0, top + y0, left + x0, top + y1, THIN, dash=True)

    def _title_cells(self, form: FormProfile, left: float, top: float) -> None:
        """Штамп таблицей Word: у каждой ячейки своя рамка, между рамками — зазор стиля."""
        g = self.style.gap / 2
        row = self.style.row
        hy1 = form.header[1]
        for k in range(round(form.height / row)):
            y0, y1 = k * row, (k + 1) * row
            cols = self.profile.change_columns if y1 <= hy1 + 1e-6 else self.profile.sign_columns
            for x0, x1 in cols.values():
                self.rect(left + x0, top + y0, left + x1, top + y1, THIN, g)
        cells = [c for n, c in form.graphs.items() if n not in (26, 27)] + list(form.labels.values())
        for x0, x1, y0, y1 in cells:
            self.rect(left + x0, top + y0, left + x1, top + y1, THIN, g)

    def margin_graphs(self, inventory: str, date: str = "") -> None:
        """Дополнительные графы 20–22 на поле для подшивки (прил. Ж): «Инв. № подл.», «Подп. и дата»,
        «Взам. инв. №» — колонки 5 и 7 мм, текст снизу вверх, от нижней линии рамки."""
        bottom = self.h - self.profile.frame["bottom"]
        x0, x1, x2 = 8.0, 13.0, 20.0
        y = bottom
        for label, value, height in (
            ("Инв. № подл.", inventory, 25.0),
            ("Подп. и дата", date, 35.0),
            ("Взам. инв. №", "", 25.0),
        ):
            self.rect(x0, y - height, x1, y, THIN)
            self.rect(x1, y - height, x2, y, THIN)
            self.vtext(x0 + 3.6, y - 2.0, label, 5.5)
            if value:
                self.vtext(x1 + 4.6, y - 2.0, value, 7)
            y -= height

    # --- таблицы ----------------------------------------------------------------------------------
    def table(
        self, x: float, y: float, widths: list[float], header: list[str], rows: list[list[str]], row_h: float = 8.0
    ) -> float:
        """Таблица с линиями; вернуть y низа таблицы."""
        xs = [x]
        for w in widths:
            xs.append(xs[-1] + w)
        all_rows = [header, *rows]
        yy = y
        self.line(xs[0], yy, xs[-1], yy, THICK)
        for i, r in enumerate(all_rows):
            for j, s in enumerate(r):
                self.text_in((xs[j], xs[j + 1], yy, yy + row_h), s, 7 if i == 0 else 8, "center" if i == 0 else "left")
            yy += row_h
            self.line(xs[0], yy, xs[-1], yy, THICK if i == 0 else THIN)
        for xv in xs:
            self.line(xv, y, xv, yy, THICK)
        return yy

    def paragraphs(self, x: float, y: float, width: float, bottom: float, texts: list[str], size: float = 10):
        """Абзацы с переносом; вернуть (y, остаток абзацев), если место на листе кончилось."""
        line_h = size * 0.45
        rest = list(texts)
        while rest:
            lines = simpleSplit(rest[0], FONT, size, pt(width))
            need = len(lines) * line_h + 2.0
            if y + need > bottom:
                return y, rest
            for ln in lines:
                y += line_h
                self.text(x, y, ln, size)
            y += 2.0
            rest.pop(0)
        return y, []


def new_canvas(path: Path, title: str, author: str) -> Canvas:
    register_font()
    c = Canvas(str(path), invariant=1)  # invariant: одинаковый сид → одинаковые байты
    c.setTitle(title)
    c.setAuthor(author)
    c.setCreator("zerocycle bench")
    return c
