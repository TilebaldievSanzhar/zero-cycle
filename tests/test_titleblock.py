"""Основная надпись: рисуем по ГОСТ Р 21.101-2020 (прил. Ж) и читаем обратно."""

import pytest

from zerocycle.bench.draw import Sheet, TitleValues, new_canvas
from zerocycle.extract.titleblock import find_title_block
from zerocycle.ingest.pdf import read_pdf
from zerocycle.units import paper_format

SIGNS = [
    ("Разраб.", "Иванов", "12.10.26"),
    ("Пров.", "Петрова", "12.10.26"),
    ("", "", ""),
    ("", "", ""),
    ("Н. контр.", "Сидоров", "13.10.26"),
    ("ГИП", "Кузнецов", "14.10.26"),
]

PAGES = [
    # (формат, альбомная, значения, сетка, линии)
    (
        "A3",
        True,
        TitleValues(
            3,
            {
                1: "2026-017-АР.ГЧ",
                2: "Жилой комплекс «Северный». 2-й этап",
                3: "Многоквартирный жилой дом № 3",
                4: "План 1-го этажа",
                6: "П",
                7: "3",
                8: "12",
                9: "ООО «Проект-Строй»",
                26: "А3",
                27: "АО «Застройщик»",
            },
            SIGNS,
            [("1", "2", "3", "15-26", "20.10.26")],
        ),
        "cells",
        True,
    ),
    (
        "A4",
        False,
        TitleValues(
            5,
            {
                1: "2026-017-ПЗ.ТЧ",
                5: "Пояснительная записка. Текстовая часть",
                6: "П",
                7: "1",
                8: "24",
                9: "ООО «Проект-Строй»",
                26: "А4",
            },
            [*SIGNS[:2], ("", "", ""), SIGNS[4], SIGNS[5]],
        ),
        "long",
        True,
    ),
    ("A4", False, TitleValues(6, {1: "2026-017-ПЗ.ТЧ", 7: "2", 26: "А4"}), "cells", True),
    (
        "A2",
        True,
        TitleValues(
            3,
            {
                1: "2026-017-КР.ГЧ",
                2: "Жилой комплекс «Северный»",
                3: "Дом № 3",
                4: "Схема расположения плит",
                6: "П",
                7: "1",
                8: "5",
                9: "ООО «Проект-Строй»",
            },
            SIGNS,
        ),
        "long",
        False,
    ),
]


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    path = tmp_path_factory.mktemp("tb") / "sheets.pdf"
    c = new_canvas(path, "test", "test")
    for paper, landscape, values, grid, lines in PAGES:
        s = Sheet(c, paper, landscape)
        s.frame()
        s.title_block(values, grid=grid, with_lines=lines)
        c.showPage()
    c.save()
    return [(p, find_title_block(p)) for p in read_pdf(path)]


@pytest.mark.parametrize("i", range(len(PAGES)))
def test_form_and_graphs(parsed, i):
    page, tb = parsed[i]
    paper, _landscape, values, _grid, lines = PAGES[i]
    assert paper_format(page.width, page.height) == paper
    assert tb is not None
    assert tb.form == values.form
    assert tb.method == ("grid" if lines else "labels")
    for g, s in values.graphs.items():
        assert tb.get(g) == s, f"графа {g}"


def test_signatures_and_changes(parsed):
    _page, tb = parsed[0]
    rows = [(r.role, r.name, r.date) for r in tb.signatures]
    assert rows == [r for r in SIGNS if any(r)]
    assert [(r.change, r.count, r.sheet, r.doc, r.date) for r in tb.changes] == [("1", "2", "3", "15-26", "20.10.26")]


def test_cells_are_anchors_inside_block(parsed):
    _page, tb = parsed[0]
    x0, top, x1, bottom = tb.bbox
    cx0, ctop, cx1, cbottom = tb.cells[1]
    assert x0 <= cx0 < cx1 <= x1 and top <= ctop < cbottom <= bottom
