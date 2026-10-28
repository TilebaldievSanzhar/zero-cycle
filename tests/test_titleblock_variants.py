"""Основные надписи не по ГОСТ: варианты, найденные в реальных ПСД госзакупок РК (23.10.2026).

Реальные документы в репозиторий не кладём — здесь их геометрия, воспроизведённая рисованием:
двойные рамки ячеек из Word, шапка ЕСКД без «Кол.уч», шапка верхней строкой без линии над ней,
обозначение «-ОВ» в одной ячейке с заказчиком, таблица вплотную над основной надписью, штамп в середине листа.
Пакеты казахстанские — разбор идёт в контексте юрисдикции kz. Последний лист — корпоративная шапка «Рев.»
вместо «Изм.»: её подключает слой организации, без правки кода.
"""

import pytest

from zerocycle.bench.draw import FONT, new_canvas
from zerocycle.extract.designation import parse
from zerocycle.extract.titleblock import _designation_in, find_title_block
from zerocycle.ingest.pdf import read_pdf
from zerocycle.packs import context
from zerocycle.units import mm, normalize_format, pt

KZ = context("kz")


class Pen:
    """Рисование в мм от левого верхнего угла листа."""

    def __init__(self, c, w_mm, h_mm):
        self.c, self.h = c, h_mm
        c.setPageSize((pt(w_mm), pt(h_mm)))

    def line(self, x0, y0, x1, y1):
        self.c.setLineWidth(0.6)
        self.c.line(pt(x0), pt(self.h - y0), pt(x1), pt(self.h - y1))

    def rect(self, x0, y0, x1, y1, gap=0.0):
        """Ячейка; gap — зазор, как у таблиц Word, где у каждой ячейки своя рамка."""
        g = gap / 2
        a, b, c_, d = x0 + g, y0 + g, x1 - g, y1 - g
        for p in ((a, b, c_, b), (c_, b, c_, d), (c_, d, a, d), (a, d, a, b)):
            self.line(*p)

    def text(self, x, y, s, size=7):
        self.c.setFont(FONT, size)
        self.c.drawString(pt(x), pt(self.h - y), s)


def _grid(pen, x0, y0, widths, heights, gap=0.0, skip_top=False):
    xs = [x0]
    for w in widths:
        xs.append(xs[-1] + w)
    ys = [y0]
    for h in heights:
        ys.append(ys[-1] + h)
    for i in range(len(xs) - 1):
        for j in range(len(ys) - 1):
            if gap:
                pen.rect(xs[i], ys[j], xs[i + 1], ys[j + 1], gap)
    if not gap:
        for k, y in enumerate(ys):
            if not (skip_top and k == 0):
                pen.line(xs[0], y, xs[-1], y)
        for x in xs:
            pen.line(x, ys[0], x, ys[-1])
    return xs, ys


def _header(pen, xs, y, labels):
    for x, label in zip(xs, labels, strict=False):
        pen.text(x + 0.6, y, label, 6)


@pytest.fixture(scope="module")
def sheets(tmp_path_factory):
    path = tmp_path_factory.mktemp("variants") / "variants.pdf"
    c = new_canvas(path, "variants", "test")

    # 1. Word: у каждой ячейки своя рамка с зазором 0,5 мм; форма 6; колонка «Лист» — отдельными отрезками
    pen = Pen(c, 210, 297)
    pen.rect(20, 5, 205, 292)
    xs, _ = _grid(pen, 20, 277, [10, 10, 10, 10, 15, 10], [5, 5, 5], gap=0.5)
    _header(pen, xs, 291, ["Изм.", "Кол.уч", "Лист", "№док", "Подпись", "Дата"])
    pen.rect(85, 277, 193, 292, gap=0.5)
    pen.rect(193, 277, 205, 282, gap=0.5)
    pen.rect(193, 282, 205, 292, gap=0.5)
    pen.text(115, 286, "311-2026-ПЗ.СП", 12)
    pen.text(195, 281, "Лист", 6)
    pen.text(197.5, 289, "3", 10)
    c.showPage()

    # 2. шапка верхней строкой, линии над ней нет; ниже две пустые строки; обозначение разорвано пробелом
    pen = Pen(c, 420, 297)
    pen.rect(20, 5, 415, 292)
    xs, _ = _grid(pen, 230, 277, [10, 10, 10, 10, 15, 10], [5, 5, 5], skip_top=True)
    _header(pen, xs, 281, ["Изм.", "Кол.уч.", "Лист", "Nдок.", "Подпись", "Дата"])
    pen.line(295, 282, 415, 282)
    pen.line(295, 277, 295, 292)
    pen.line(405, 277, 405, 292)
    pen.line(405, 284, 415, 284)
    pen.text(310, 287, "39-2024", 9)
    pen.text(360, 287, "ОВ.СО", 9)
    pen.text(406, 281, "Лист", 6)
    pen.text(409, 290, "2", 9)
    c.showPage()

    # 3. ЕСКД, форма 2а: «Изм | Лист | № докум. | Подп. | Дата», без «Кол.уч»; обозначение — один шифр
    pen = Pen(c, 210, 297)
    pen.rect(20, 5, 205, 292)
    xs, _ = _grid(pen, 20, 277, [7, 10, 23, 15, 10], [5, 5, 5])
    _header(pen, xs, 291, ["Изм", "Лист", "№ докум.", "Подп.", "Дата"])
    pen.rect(85, 277, 195, 292)
    pen.rect(195, 277, 205, 283)
    pen.rect(195, 283, 205, 292)
    pen.text(135, 286, "ПОС", 12)
    pen.text(196, 281, "Лист", 6)
    pen.text(198.5, 289, "2", 10)
    c.showPage()

    # 4. форма 3: заказчик и «-ОВ» в одной ячейке, таблица со строками 8,5 мм вплотную над основной надписью
    pen = Pen(c, 420, 297)
    pen.rect(20, 5, 415, 292)
    _grid(pen, 230, 194, [125, 60], [8.5] * 5)  # таблица нагрузок
    pen.text(232, 200, "Коэффициент мощности, cos ф", 7)
    pen.text(360, 200, "0.95", 7)
    left = 230
    xs, _ = _grid(pen, left, 236.5, [10, 10, 10, 10, 15, 10], [5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5.5])
    _header(pen, xs, 255.5, ["Изм.", "Кол.уч", "Лист", "№док.", "Подп.", "Дата"])
    signs = [("ГИП", "Хамит Ж"), ("Исполнитель", "Рустем Д."), ("Норм. контр.", "Сисенбеков")]
    for i, (role, name) in enumerate(signs):
        pen.text(left + 0.5, 260.5 + 5 * i, role, 5.5)
        pen.text(left + 20.5, 260.5 + 5 * i, name, 5.5)
    R = left + 65
    for y in (236.5, 246.5, 261.5, 276.5, 292):
        pen.line(R, y, 415, y)
    pen.line(R, 236.5, R, 292)
    pen.line(365, 261.5, 365, 292)
    for x in (380, 395):
        pen.line(x, 261.5, x, 276.5)
    pen.line(365, 266.5, 415, 266.5)
    pen.text(R + 2, 240, 'Заказчик: ГУ "Управление строительства, архитектуры и', 7)
    pen.text(R + 2, 244.5, 'градостроительства акимата Костанайской области"', 7)
    pen.text(400, 242, "-ОВ", 10)
    pen.text(R + 2, 254, "Строительство фельдшерско-акушерского пункта в селе Байгабыл", 7)
    pen.text(R + 2, 285, "Отопление. План на отм. 0,000.", 7)
    pen.text(366, 265.5, "Стадия", 6)
    pen.text(381, 265.5, "Лист", 6)
    pen.text(396, 265.5, "Листов", 6)
    pen.text(370, 273, "РП", 9)
    pen.text(386, 273, "2", 9)
    pen.text(367, 285, "ТОО «ГорСервисПроект» г.Кызылорда", 6)
    c.showPage()

    # 5. лист вписан в чужой формат: рамка и штамп в середине страницы А2
    pen = Pen(c, 594, 420)
    pen.rect(40, 100, 560, 330)
    left = 560 - 185
    xs, _ = _grid(pen, left, 290, [10, 10, 10, 10, 15, 10], [5] * 8)
    _header(pen, xs, 304, ["Изм.", "Кол.уч", "Лист", "№док.", "Подпись", "Дата"])
    R = left + 65
    for y in (290, 300, 310, 315, 330):
        pen.line(R, y, 560, y)
    pen.line(R, 290, R, 330)
    pen.line(510, 310, 510, 330)
    for x in (525, 540):
        pen.line(x, 310, x, 320)
    pen.line(510, 320, 560, 320)
    pen.text(R + 5, 297, "51-2022-ВК", 10)
    pen.text(R + 5, 306, "Фельдшерско-акушерский пункт", 7)
    pen.text(511, 314, "Стадия", 6)
    pen.text(526, 314, "Лист", 6)
    pen.text(541, 314, "Листов", 6)
    pen.text(514, 318.5, "РП", 7)
    pen.text(530, 318.5, "1", 7)
    pen.text(545, 318.5, "5", 7)
    pen.text(512, 326, 'НАО "Turar Healthcare"', 6)
    c.showPage()

    # 6. корпоративная шапка: «Рев.» вместо «Изм.»; штамп не у угла рамки — найти его можно только по шапке
    pen = Pen(c, 420, 297)
    pen.rect(20, 5, 415, 292)
    left = 150
    xs, _ = _grid(pen, left, 200, [10, 10, 10, 10, 15, 10], [5] * 8)
    _header(pen, xs, 214, ["Рев.", "Кол.уч", "Лист", "№док.", "Подп.", "Дата"])
    R = left + 65
    for y in (200, 210, 220, 225, 240):
        pen.line(R, y, 335, y)
    pen.line(R, 200, R, 240)
    pen.line(285, 220, 285, 240)
    for x in (300, 315):
        pen.line(x, 220, x, 230)
    pen.line(285, 230, 335, 230)
    pen.text(R + 5, 207, "2024-08-ОПЗ", 10)
    pen.text(286, 224, "Стадия", 6)
    pen.text(301, 224, "Лист", 6)
    pen.text(316, 224, "Листов", 6)
    pen.text(289, 228.5, "РП", 7)
    pen.text(305, 228.5, "1", 7)
    pen.text(320, 228.5, "9", 7)
    pen.text(287, 236, 'ТОО "Проект-Астана"', 6)
    c.showPage()

    # 7. под основной надписью «Копировал» и формат, как в шаблонах ЕСКД и ГОСТ 21.101-97
    pen = Pen(c, 420, 297)
    pen.rect(20, 5, 415, 292)
    xs, _ = _grid(pen, left, 200, [10, 10, 10, 10, 15, 10], [5] * 8)
    _header(pen, xs, 214, ["Изм.", "Кол.уч", "Лист", "№док.", "Подп.", "Дата"])
    for y in (200, 210, 220, 225, 240):
        pen.line(R, y, 335, y)
    pen.line(R, 200, R, 240)
    pen.line(285, 220, 285, 240)
    for x in (300, 315):
        pen.line(x, 220, x, 230)
    pen.line(285, 230, 335, 230)
    pen.text(R + 5, 207, "2024-08-ВК", 10)
    pen.text(286, 224, "Стадия", 6)
    pen.text(301, 224, "Лист", 6)
    pen.text(316, 224, "Листов", 6)
    pen.text(289, 228.5, "РП", 7)
    pen.text(305, 228.5, "1", 7)
    pen.text(320, 228.5, "4", 7)
    pen.text(R + 5, 244, "Копировал Иванов", 6)
    pen.text(300, 244, "Формат А3", 6)
    c.showPage()

    # 8. лист типового проекта без текста: поля рамки 10 и 15 мм (ГОСТ 21.101-97, прил. Е), штамп только линиями
    pen = Pen(c, 420, 297)
    pen.rect(20, 10, 415, 282)
    _grid(pen, 230, 227, [10, 10, 10, 10, 15, 10], [5] * 11)
    pen.line(230, 227, 415, 227)
    for y in (237, 252, 257, 267):
        pen.line(295, y, 415, y)
    pen.line(365, 252, 365, 282)
    for x in (380, 395):
        pen.line(x, 252, x, 267)
    c.showPage()
    c.save()
    return read_pdf(path)


@pytest.fixture(scope="module")
def pages(sheets):
    return [find_title_block(p, KZ) for p in sheets]


def test_word_double_borders(pages):
    tb = pages[0]
    assert tb is not None and tb.method == "grid" and tb.form == 6
    assert (tb.get(1), tb.get(7)) == ("311-2026-ПЗ.СП", "3")


def test_header_on_top_without_line(pages):
    tb = pages[1]
    assert tb is not None and tb.form == 6
    assert tb.get(1) == "39-2024 ОВ.СО" and tb.get(7) == "2"
    assert parse(tb.get(1), KZ.codebook).text == "39-2024-ОВ.СО"


def test_eskd_header_without_kol_uch(pages):
    tb = pages[2]
    assert tb is not None and (tb.get(1), tb.get(7)) == ("ПОС", "2")
    assert "нет базового обозначения" in parse(tb.get(1), KZ.codebook).issues


def test_designation_inside_customer_cell_and_table_above(pages):
    tb = pages[3]
    assert tb is not None and tb.form == 3
    assert tb.get(1) == "-ОВ"
    assert (tb.get(6), tb.get(7)) == ("РП", "2")
    assert tb.get(9).startswith("ТОО")
    assert any(e.startswith("Заказчик") for e in tb.extra)
    assert "0.95" not in str(tb.graphs) and not any("0.95" in e for e in tb.extra)  # таблица сверху не протекла
    assert [s.role for s in tb.signatures] == ["ГИП", "Исполнитель", "Норм. контр."]


def test_block_in_the_middle_of_page(pages):
    tb = pages[4]
    assert tb is not None and tb.method == "grid"
    assert (tb.get(1), tb.get(6), tb.get(7), tb.get(8)) == ("51-2022-ВК", "РП", "1", "5")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('Заказчик: ГУ "Управление" -ОВ', "-ОВ"),
        ("Заказ: - ОВ.СО", "-ОВ.СО"),
        ("№2023-17 АР", "№2023-17 АР"),
        ("311-2026 -ПЗ", "311-2026 -ПЗ"),
        ("311-2026-ОПЗ", "311-2026-ОПЗ"),
        ("2023-17-СС", "2023-17-СС"),
        ("Альбом АР", None),
        ("Отопление и вентиляция ОВ", None),
    ],
)
def test_designation_in_cell(text, expected):
    assert _designation_in(text, KZ.codebook) == expected


@pytest.mark.parametrize("text", ["311-2026-ОПЗ", "2023-17-СС", "311-2026-СД"])
def test_kz_codes_unknown_to_ru(text):
    """Шифры РК есть только в справочнике kz: с российским справочником графа 1 их не узнаёт."""
    assert _designation_in(text, context("ru").codebook) is None


def test_corporate_header_needs_organization_layer(sheets, tmp_path):
    page = sheets[5]
    assert find_title_block(page, KZ) is None
    org = tmp_path / "proekt-astana.toml"
    org.write_text(
        'name = "ТОО «Проект-Астана»"\n\n[spds.header.roles]\n14 = ["изм*", "рев*"]\n',
        encoding="utf-8",
    )
    tb = find_title_block(page, context("kz", org))
    assert tb is not None and tb.method == "grid"
    assert (tb.get(1), tb.get(6), tb.get(7), tb.get(8)) == ("2024-08-ОПЗ", "РП", "1", "9")
    assert tb.get(9).startswith("ТОО")


def test_below_block_by_edition(sheets):
    """Под основной надписью «Копировал» и формат: графа 26 — «Копировал» в ГОСТ 21.101-97, формат — в 2020."""
    page = sheets[6]
    kz = find_title_block(page, KZ)
    assert (kz.get(1), kz.get(26), kz.format_text) == ("2024-08-ВК", "Иванов", "А3")
    ru = find_title_block(page, context("ru"))
    assert (ru.get(26), ru.format_text) == ("А3", "А3")


def test_no_customer_graph_in_97(sheets):
    """Графы 27 в ГОСТ 21.101-97 нет: таблица над штампом не читается как заказчик."""
    assert all(27 not in tb.graphs for tb in (find_title_block(p, KZ) for p in sheets) if tb)


def test_typical_project_frame(sheets):
    """Лист типового проекта без текста: поля рамки 10 и 15 мм (ГОСТ 21.101-97, прил. Е).

    Профиль 97 ставит основную надпись в угол рамки (низ — 282 мм); профиль 2020 ждёт поле 5 мм и находит её
    на 10 мм ниже — за нижней линией рамки, со сдвинутыми ячейками.
    """
    page = sheets[7]
    tb = find_title_block(page, KZ)
    assert tb is not None and (tb.method, tb.form) == ("frame", 3)
    assert abs(mm(tb.bbox[3]) - 282) < 1 and abs(mm(tb.bbox[1]) - 227) < 1
    ru = find_title_block(page, context("ru"))
    assert ru is None or abs(mm(ru.bbox[3]) - 282) > 5


def test_format_written_with_cad_fonts():
    assert normalize_format("Формаm АЗ") == "A3"
    assert normalize_format("А4х3") == "A4x3"
