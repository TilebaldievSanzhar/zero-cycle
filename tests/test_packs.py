"""Контекст пакета: юрисдикция → редакция СПДС → профиль основных надписей и справочник шифров; слой организации."""

import pytest

from zerocycle.extract.designation import parse
from zerocycle.packs import available, context, merge, titleblock_profile


def test_jurisdictions_and_editions():
    assert {"kz", "ru"} <= set(available()) and not {"spds", "core"} & set(available())
    ru, kz = context("ru"), context("kz")
    assert (ru.edition, ru.standard, ru.titleblocks.verified) == ("2020", "ГОСТ Р 21.101-2020", True)
    assert (kz.edition, kz.standard, kz.titleblocks.verified) == ("97", "ГОСТ 21.101-97", False)
    assert ru.chain == ("core", "spds", "ru") and kz.chain == ("core", "spds", "kz")


def test_unknown_jurisdiction():
    with pytest.raises(ValueError, match="нет пакета юрисдикции"):
        context("uz")


def test_edition_97_inherits_2020_until_verified():
    """Текста ГОСТ 21.101-97 в bim-data нет: формы и подписи граф унаследованы от редакции 2020."""
    p97, p2020 = titleblock_profile("97"), titleblock_profile("2020")
    assert p97.forms == p2020.forms
    assert p97.header_roles == p2020.header_roles
    assert context("kz").codebook.marks == context("ru").codebook.marks


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
