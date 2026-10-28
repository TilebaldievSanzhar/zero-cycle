"""Контекст пакета: юрисдикция → редакция СПДС → профиль основных надписей и справочник шифров; слой организации."""

import pytest

from zerocycle.extract.designation import parse
from zerocycle.packs import available, context, merge, titleblock_profile


def test_jurisdictions_and_editions():
    assert {"kz", "ru"} <= set(available()) and not {"spds", "core"} & set(available())
    ru, kz = context("ru"), context("kz")
    assert (ru.edition, ru.standard, ru.titleblocks.verified) == ("2020", "ГОСТ Р 21.101-2020", True)
    assert (kz.edition, kz.standard, kz.titleblocks.verified) == ("97", "ГОСТ 21.101-97", True)
    assert ru.chain == ("core", "spds", "ru") and kz.chain == ("core", "spds", "kz")


def test_unknown_jurisdiction():
    with pytest.raises(ValueError, match="нет пакета юрисдикции"):
        context("uz")


def test_edition_97_same_geometry_as_2020():
    """ГОСТ 21.101-97, прил. Д и Е: формы 3–6, подписи граф и поля рамки — как в 2020; нет только графы 27."""
    p97, p2020 = titleblock_profile("97"), titleblock_profile("2020")
    for n, f in p2020.forms.items():
        assert {g: c for g, c in f.graphs.items() if g != 27} == p97.forms[n].graphs
        assert (f.height, f.header, f.changes, f.signatures, f.labels) == (
            p97.forms[n].height,
            p97.forms[n].header,
            p97.forms[n].changes,
            p97.forms[n].signatures,
            p97.forms[n].labels,
        )
    assert p97.header_roles == p2020.header_roles and p97.label_graphs == p2020.label_graphs
    assert p97.frame == p2020.frame
    assert p97.customer_row == 0 and p2020.customer_row == 10


def test_edition_97_differences():
    p97, p2020 = titleblock_profile("97"), titleblock_profile("2020")
    # графа 26: формат в 2020, «Копировал» в 97 — формат там графой не нумеруется
    assert (p2020.format_graph, p2020.copier_graph) == (26, None)
    assert (p97.format_graph, p97.copier_graph, p97.copier_labels) == (None, 26, ("Копировал",))
    # рамка типовой проектной документации: сверху 10, снизу 15 мм (прил. Е)
    assert p2020.frames == (p2020.frame,)
    assert p97.frames[1] == {**p97.frame, "top": 10.0, "bottom": 15.0}
    # стадии в графе 6 и предел листов в томе
    assert set(p2020.stages) == {"П", "Р", "И"} and set(p97.stages) == {"П", "Р"}
    assert p2020.max_sheets == {"A4": 300}
    assert p97.max_sheets == {"A4": 250, "A3": 150, "A2": 75, "A1": 50}


def test_edition_97_marks():
    """Прил. А ГОСТ 21.101-97: есть ГТ и СС, нет ЭОМ, ГГ, КМ, КДД, ЭВ, ЭК; ЭОМ и КМ — практика РК (sections.toml)."""
    kz, ru = context("kz").codebook, context("ru").codebook
    assert {"ГТ", "СС"} <= set(kz.marks) and not {"ГТ", "СС"} & set(ru.marks)
    assert not {"ЭОМ", "ГГ", "КМ", "КДД", "ЭВ", "ЭК"} & set(kz.marks)
    assert {"ЭОМ", "КМ"} <= kz.all_codes  # шифры из пакетов и заключений РК
    assert len(kz.marks) == 39  # 40 строк таблицы А.1 без «Автоматизация …»


def test_geometry_derived_from_profile():
    """Числа, которые раньше жили константами в коде поиска основной надписи, выводятся из профиля."""
    p = titleblock_profile("2020")
    assert p.width == 185 and p.left_width == 65
    assert p.left_columns == (10, 20, 30, 40, 55, 65)
    assert p.heights == {55.0: 3, 40.0: 5, 15.0: 6}
    assert p.header_from_bottom == {3: 32.5, 5: 27.5, 6: 2.5}
    assert (p.max_change_rows, p.max_sign_rows, p.customer_row) == (4, 6, 10)
    assert p.label_graphs == {"Стадия": 6, "Лист": 7, "Листов": 8, "Масса": 24, "Масштаб": 25}


def test_codebooks():
    ru, kz = context("ru").codebook, context("kz").codebook
    assert {"ОПЗ", "СС", "СД"} <= kz.all_codes
    assert not {"ОПЗ", "СС", "СД"} & ru.all_codes
    assert "ИОС" in ru.all_codes and "ИОС" not in kz.all_codes  # подразделы ИОС — ПП 87
    assert {"АР", "ОВ", "ВК", "ЭОМ"} <= ru.all_codes & kz.all_codes  # марки — от семьи СПДС
    assert "шифр не из справочника юрисдикции" not in parse("311-2026-ОПЗ", kz).issues
    assert "шифр не из справочника юрисдикции" in parse("311-2026-ОПЗ", ru).issues


def test_org_forms_by_jurisdiction():
    assert context("kz").org.search("ТОО Проект")
    assert context("ru").org.search("ООО Проект")
    assert not context("ru").org.search("ТОО Проект")
    assert context("ru").org.search("«Проект»")  # кавычки — признак организации всегда


def test_merge_tables_and_replace_lists():
    base = {"a": {"x": 1, "y": [1, 2]}, "b": 2}
    assert merge(base, {"a": {"y": [3]}, "c": 4}) == {"a": {"x": 1, "y": [3]}, "b": 2, "c": 4}
    assert merge(base, {"a": {"x": False}, "b": False}) == {"a": {"y": [1, 2]}}  # false убирает унаследованное
    assert base == {"a": {"x": 1, "y": [1, 2]}, "b": 2}  # родитель не меняется


def test_organization_layer(tmp_path):
    org = tmp_path / "proekt.toml"
    org.write_text(
        'name = "ООО «Проект»"\n'
        'org_forms = ["ТОО"]\n\n'
        '[sections.codes]\n"ЗП" = "Задание на проектирование"\n\n'
        '[spds.tails]\n"ДОП" = "Дополнение"\n\n'
        '[spds.header.roles]\n14 = ["изм*", "рев*"]\n',
        encoding="utf-8",
    )
    ctx = context("ru", org)
    assert ctx.organization == "ООО «Проект»"
    assert parse("2345-ЗП", ctx.codebook).issues == ()
    assert parse("2345-АР-ДОП", ctx.codebook).tail == "ДОП"
    assert ctx.titleblocks.header_roles[14] == ("изм*", "рев*")
    assert ctx.titleblocks.header_roles[15] == context("ru").titleblocks.header_roles[15]  # остальное — от редакции
    assert ctx.org.search("ТОО Проект")
    assert context("ru").organization is None
