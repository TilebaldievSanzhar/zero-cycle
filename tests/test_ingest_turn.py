"""Поворот листа: /Rotate учитывает pdfplumber, лист боком без /Rotate опись поворачивает сама.

Лист боком — альбомный чертёж, «напечатанный в PDF» на книжную страницу. Повёрнутый вариант опись берёт, только если
основная надпись находится лишь на нём: вертикальный текст бывает и на ровном листе. Например, у чертежа с текстом
в кривых остаются надписи поля для подшивки — так на реальном листе ОВ из закупок РК.
"""

import pytest

from zerocycle.bench.draw import Sheet, TitleValues, new_canvas
from zerocycle.ingest.pdf import read_pdf
from zerocycle.packs import context
from zerocycle.scan import read_sheet
from zerocycle.units import pt

RU = context("ru")
DESIGNATION = "2026-07-АР.ГЧ"
VALUES = TitleValues(
    3,
    {1: DESIGNATION, 2: "Жилой комплекс", 3: "Дом № 1", 4: "План 1-го этажа", 6: "П", 7: "2", 9: "ООО «Проект»"},
    [("Разраб.", "Иванов", "01.09.26"), ("Пров.", "Петров", "01.09.26")],
)


def _sheet(c, sideways: int = 0, rotate: int = 0, text: bool = True) -> None:
    s = Sheet(c, "A3", True)
    if rotate:  # размер страницы у reportlab — видимый: MediaBox при /Rotate 90 и 270 он пишет книжным сам
        c.setPageRotation(rotate)
    elif sideways:
        c.setPageSize((pt(s.h), pt(s.w)))
    c.saveState()
    if (rotate or sideways) == 90:
        c.translate(pt(s.h), 0)
        c.rotate(90)
    elif (rotate or sideways) == 270:
        c.translate(0, pt(s.w))
        c.rotate(-90)
    s.frame()
    s.margin_graphs("1234", "01.09.26")
    if text:
        s.title_block(VALUES)
    else:  # текст САПР в кривых: от основной надписи остались только линии
        s._title_lines(s.profile.forms[3], s.w - 5 - 185, s.h - 5 - 55, "cells", with27=False)
    c.restoreState()
    c.showPage()
    c.setPageRotation(0)


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    path = tmp_path_factory.mktemp("turn") / "turn.pdf"
    c = new_canvas(path, "turn", "test")
    _sheet(c, sideways=90)  # текст снизу вверх
    _sheet(c, sideways=270)  # текст сверху вниз
    _sheet(c, rotate=90)
    _sheet(c, rotate=270)
    _sheet(c, text=False)
    c.save()
    return read_pdf(path)


@pytest.mark.parametrize(("i", "turn"), [(0, 90), (1, 270)])
def test_sideways_sheet_is_turned(pages, i, turn):
    p = pages[i]
    assert p.turn == 0 and p.width < p.height and p.turned is not None and p.turned.turn == turn
    sheet, tb = read_sheet(p, RU)
    assert sheet.turn == turn and sheet.width > sheet.height
    assert tb is not None and tb.form == 3 and (tb.get(1), tb.get(7)) == (DESIGNATION, "2")


@pytest.mark.parametrize("i", [2, 3])
def test_rotate_is_shown_upright(pages, i):
    """/Rotate 90 и 270: размеры и координаты — как при показе, поворачивать нечего."""
    p = pages[i]
    assert p.turned is None and p.width > p.height
    sheet, tb = read_sheet(p, RU)
    assert sheet.turn == 0 and tb is not None and tb.get(1) == DESIGNATION


def test_upright_sheet_with_vertical_margin_text_stays(pages):
    p = pages[4]
    assert p.turned is not None  # почти весь текст — вертикальные надписи поля для подшивки
    sheet, tb = read_sheet(p, RU)
    assert sheet is p and sheet.turn == 0
    assert tb is not None and tb.method == "frame" and tb.form == 3
