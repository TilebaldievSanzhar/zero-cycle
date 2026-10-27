"""Разбор обозначений на примерах ГОСТ Р 21.101-2020 и типичных ошибках записи."""

import pytest

from zerocycle.extract.designation import belongs_to, interpret, parse
from zerocycle.packs import codebook

RU = codebook("ru")


@pytest.mark.parametrize(
    ("raw", "base", "code", "numbers", "doc", "suffix"),
    [
        # проектная документация, п. 4.1.3
        ("2345-ПЗ", "2345", "ПЗ", (), None, None),
        ("2345-ПЗУ1", "2345", "ПЗУ", (1,), None, None),
        ("2345-ПЗУ2", "2345", "ПЗУ", (2,), None, None),
        ("2345-ИОС4.1.1", "2345", "ИОС", (4, 1, 1), None, None),
        ("2345-ИОС4.1.2", "2345", "ИОС", (4, 1, 2), None, None),
        ("2345-ИОС4.2", "2345", "ИОС", (4, 2), None, None),
        # содержание тома и состав ПД, п. 8.1.5–8.1.6
        ("2345-ПЗУ2-С", "2345", "ПЗУ", (2,), None, "С"),
        ("2345-ИОС4.1.2-С", "2345", "ИОС", (4, 1, 2), None, "С"),
        ("2345-СП", "2345", None, (), None, "СП"),
        # рабочая документация, п. 4.2.3–4.2.5
        ("2345-12-АР", "2345-12", "АР", (), None, None),
        ("2345-12-КЖ2", "2345-12", "КЖ", (2,), None, None),
        ("2345-12-ЭО.1", "2345-12", "ЭО", (), 1, None),
        ("2345-12-ЭО.11", "2345-12", "ЭО", (), 11, None),
        ("2345-12-ЭО.01", "2345-12", "ЭО", (), 1, None),
    ],
)
def test_gost_examples(raw, base, code, numbers, doc, suffix):
    d = parse(raw, RU)
    assert d is not None
    assert (d.base, d.code, d.numbers, d.doc_number, d.suffix) == (base, code, numbers, doc, suffix)
    assert d.issues == ()


def test_normalized_text_round_trip():
    for raw in ["2345-ИОС4.1.2-С", "2345-СП", "2345-12-ЭО.1", "2345-ПЗУ1"]:
        assert parse(raw, RU).text == raw


def test_base_with_letters_dots_and_hyphens():
    d = parse("ПР.01-2026-17-ПЗ", RU)
    assert (d.base, d.code) == ("ПР.01-2026-17", "ПЗ")


def test_org_tails_with_dot_and_hyphen():
    assert parse("2345-АР.ГЧ", RU).tail == "ГЧ"
    d = parse("2345-ИОС4.1-ТЧ", RU)
    assert (d.code, d.numbers, d.tail, d.issues) == ("ИОС", (4, 1), "ТЧ", ())


@pytest.mark.parametrize(
    ("raw", "code", "issue"),
    [
        ("2345-AP", "АР", "латиница в шифре"),  # латинские A и P
        ("2345-AР", "АР", "в шифре смешаны латиница и кириллица"),
        ("2345-П3", "ПЗ", "цифра вместо буквы в шифре"),  # цифра 3 вместо буквы З
        ("2345-П0С3", "ПОС", "цифра вместо буквы в шифре"),
        ("2345–ПЗ", "ПЗ", "вместо дефиса — тире"),
    ],
)
def test_typical_writing_errors(raw, code, issue):
    d = parse(raw, RU)
    assert d.code == code
    assert issue in d.issues


def test_digit_fix_keeps_part_number():
    d = parse("2345-П0С3", RU)
    assert d.numbers == (3,)


def test_spaces_and_case_are_normalized():
    d = parse(" 2345 - пз ", RU)
    assert (d.base, d.code) == ("2345", "ПЗ")


def test_missing_base_and_unknown_code():
    assert "нет базового обозначения" in parse("ПЗ", RU).issues
    d = parse("2345-ЖЖ", RU)
    assert d.code == "ЖЖ"
    assert "шифр не из справочника юрисдикции" in d.issues


def test_not_a_designation():
    assert parse("", RU) is None
    assert parse("12345", RU) is None
    assert parse("Пояснительная записка", RU) is None


def test_interpret_sections():
    s = interpret(parse("2345-ИОС4.1.2", RU), RU)
    assert (s.number, s.subsection, s.part, s.book) == (5, 4, 1, 2)
    s = interpret(parse("2345-ПЗУ2", RU), RU)
    assert (s.number, s.part, s.book) == (2, 2, None)
    s = interpret(parse("2345-ППО", RU), RU, linear=True)
    assert s.number == 2
    assert interpret(parse("2345-ЭЭ", RU), RU).number is None  # прежняя редакция ПП 87


def test_belongs_to():
    tom = parse("2345-ИОС4", RU)
    assert belongs_to(parse("2345-ИОС4.1.2", RU), tom)
    assert belongs_to(parse("2345-ИОС4.1.2-С", RU), parse("2345-ИОС4.1.2", RU))
    assert belongs_to(parse("2345-АР.ГЧ", RU), parse("2345-АР", RU))
    assert not belongs_to(parse("2345-ИОС4.2", RU), parse("2345-ИОС4.1", RU))
    assert not belongs_to(parse("2346-АР", RU), parse("2345-АР", RU))
