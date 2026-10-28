"""Индекс норм: разбор обозначений, вердикт по ссылке, сборка индекса РК из строк АГСК-1."""

from datetime import date

import pytest

from zerocycle.norms.agsk1 import _Row, record_of
from zerocycle.norms.designation import find_designations, parse_designation
from zerocycle.norms.index import NormIndex, NormRecord


@pytest.mark.parametrize(
    ("text", "code", "year"),
    [
        ("СН РК 1.02-03-2022", "СН РК 1.02-03-2022", 2022),
        ("по СН РК 1.02-03-2011*", "СН РК 1.02-03-2011", 2011),
        ("СН РК 1.02-03", "СН РК 1.02-03", None),  # «03» — номер в группе, а не год
        ("ГОСТ 21.101-97", "ГОСТ 21.101-97", 1997),
        ("ГОСТ Р 21.101-2020", "ГОСТ Р 21.101-2020", 2020),
        ("СНиП 3.05.04-85*", "СНиП 3.05.04-85", 1985),
        ("CH PK 1.02-03-2022", "СН РК 1.02-03-2022", 2022),  # латиница-двойник
        ("СП 54.13330.2016", "СП 54.13330.2016", 2016),
        ("СП 256.1325800.2016", "СП 256.1325800.2016", 2016),  # серии 1325800 и 1311500 — семь цифр
        ("СП 484.1311500.2020", "СП 484.1311500.2020", 2020),
        ("СанПиН 2.1.3684-21", "СанПиН 2.1.3684-21", 2021),  # год СанПиН — двумя цифрами и после 2000
        ("ГОСТ 8020-90", "ГОСТ 8020-90", 1990),
        ("ГОСТ 21-501-2018", "ГОСТ 21.501-2018", 2018),  # опечатка в номере ГОСТ 21.501
        ("СП РК 4.02- 104-2013", "СП РК 4.02-104-2013", 2013),  # пробел после дефиса
        ("НТП РК 01-01-3.1(4.1)-2017", "НТП РК 01-01-3.1(4.1)-2017", 2017),
        ("МСН 2.03-01-95", "МСН 2.03-01-95", 1995),
        ("ВСН 41-85", "ВСН 41-85", 1985),
        ("СНиП III-23-76", "СНиП III-23-76", 1976),
        ("п. 4.3.2.28 СП РК 3.06-101-2012*", "СП РК 3.06-101-2012", 2012),
    ],
)
def test_parse_designation(text, code, year):
    d = parse_designation(text)
    assert (d.code, d.year) == (code, year)


@pytest.mark.parametrize(
    ("text", "code", "issues"),
    [
        ("(МСН 2.04-02-2004)", "МСН 2.04-02-2004", ()),  # закрывающая скобка — не часть номера
        ("НТП РК 01-01-3.1(4.1)-2017)", "НТП РК 01-01-3.1(4.1)-2017", ()),
        ("СН РК 1.03.00-2011", "СН РК 1.03-00-2011", ("точка вместо дефиса",)),
        ("СП РК 2.04.-01-2017", "СП РК 2.04-01-2017", ("точка вместо дефиса",)),
        ("СНиП РК 2.02-052009", "СНиП РК 2.02-05-2009", ("год без дефиса",)),
        ("СН РК 1.02-03-\r\n2022", "СН РК 1.02-03-2022", ()),  # перенос строки внутри обозначения
        ("СН РК 5.01-02-201\r\n3.", "СН РК 5.01-02-2013", ()),
        ("СП РК 1.02\r\n-102-2014", "СП РК 1.02-102-2014", ()),
        ("НТП РК 01-0\r\n1-3.1(4.1)-2017", "НТП РК 01-01-3.1(4.1)-2017", ()),
        ("СН РК 1.02-03\r\n2.4 Нагрузки", "СН РК 1.02-03", ()),  # следующая строка без года — не продолжение
    ],
)
def test_designation_typos_and_line_breaks(text, code, issues):
    [(d, _, _)] = find_designations(text)
    assert (d.code, d.issues) == (code, issues)


def test_find_designations_keeps_positions_in_source_text():
    text = "Расчёт по СНиП РК 1.03-05-2001 и СН РК СП РК 1.03-106-2012; CH PK 1.02-03-2022."
    found = find_designations(text)
    assert [d.code for d, _, _ in found] == ["СНиП РК 1.03-05-2001", "СП РК 1.03-106-2012", "СН РК 1.02-03-2022"]
    assert [text[a:b] for _, a, b in found] == ["СНиП РК 1.03-05-2001", "СП РК 1.03-106-2012", "CH PK 1.02-03-2022"]


def _index(*records: NormRecord) -> NormIndex:
    return NormIndex(
        jurisdiction="kz",
        source="тест",
        as_of=date(2025, 6, 20),
        sha256="0" * 64,
        covers=["СН РК", "СП РК", "СНиП"],
        records=list(records),
    )


INDEX = _index(
    NormRecord(code="СН РК 1.02-03-2022", kind="СН РК", number="1.02-03", year=2022, replaces=["СН РК 1.02-03-2011"]),
    NormRecord(code="СП РК 2.04-01-2017", kind="СП РК", number="2.04-01", year=2017),
    NormRecord(code="СН РК 4.01-02-2013", kind="СН РК", number="4.01-02", year=2013, replaces=["СНиП РК 4.01-02-2009"]),
)


@pytest.mark.parametrize(
    ("ref", "status", "current"),
    [
        ("СН РК 1.02-03-2022", "current", "СН РК 1.02-03-2022"),
        ("СН РК 1.02-03-2011", "replaced", "СН РК 1.02-03-2022"),
        ("СП РК 2.04-01-2010", "outdated_edition", "СП РК 2.04-01-2017"),
        ("СНиП РК 4.01-02-2009", "not_covered", None),  # «СНиП РК» индекс этого теста не покрывает
        ("СНиП 3.05.04-85", "not_listed", None),
        ("ГОСТ 21.101-97", "not_covered", None),
        ("СН РК 1.02-03", "no_year", "СН РК 1.02-03-2022"),
    ],
)
def test_verdict(ref, status, current):
    v = INDEX.verdict(parse_designation(ref))
    assert v.status == status
    assert (v.current.code if v.current else None) == current


def test_replaced_without_year_by_key():
    index = INDEX.model_copy(update={"covers": ["СН РК", "СП РК", "СНиП", "СНиП РК"]})
    v = index.verdict(parse_designation("СНиП РК 4.01-02"))
    assert (v.status, v.current.code) == ("replaced", "СН РК 4.01-02-2013")


def test_agsk1_row_with_wrapped_cipher_and_replacement():
    row = _Row(
        page=16,
        cipher=["СН РК 1.02-03-", "2022 (по", "состоянию на", "26.07.2023)"],
        title=[
            "Порядок разработки, согласования,",
            "утверждения и состав проектно-сметной",
            "документации",
            "Взамен 1.02-03-2011*",
            "Внесены изменения (приказы КДСиЖКХ",
        ],
        approved=["Приказ КДС и ЖКХ", "МИИР РК от", "20.07.2022 № 152-НҚ", "с 01.08.2022"],
    )
    rec = record_of(row)
    assert rec.code == "СН РК 1.02-03-2022"
    assert rec.title == "Порядок разработки, согласования, утверждения и состав проектно-сметной документации"
    assert rec.replaces == ["СН РК 1.02-03-2011"]
    assert (rec.since, rec.page) == ("01.08.2022", 16)


def test_agsk1_row_development_is_not_replacement_and_prior_number_is():
    dev = record_of(
        _Row(
            27,
            ["СН РК 1.03-35-", "2006"],
            [
                "Типовая инструкция",
                "Взамен ВСН 347-75 и в дополнение (развитие)",
                "СНиП РК 1.03-05-2001 «Охрана труда»",
            ],
        )
    )
    assert dev.replaces == ["ВСН 347-75"]
    prior = record_of(
        _Row(
            104,
            ["СН РК 4.01-02-", "2013"],
            ["Внутренние системы", "Соответствует СН РК 4.01-02-2011", "(приказ от 29.12.2014, утративший силу)"],
        )
    )
    assert prior.replaces == ["СН РК 4.01-02-2011"]
    assert prior.title == "Внутренние системы"


def test_agsk1_kazakh_row_is_skipped():
    assert record_of(_Row(16, ["ҚР ҚН 1.02-03-", "2022"], ["Құрылысқа арналған"])) is None


def test_shipped_kz_index_sanity():
    from zerocycle.packs.kz.rules import agsk1

    index = agsk1()
    assert index.jurisdiction == "kz" and index.as_of == date(2025, 6, 20)
    assert len(index.records) >= 600
    codes = {r.code: r for r in index.records}
    assert codes["СН РК 1.02-03-2022"].replaces == ["СН РК 1.02-03-2011"]
    assert index.verdict(parse_designation("СН РК 1.02-03-2011*")).status == "replaced"
    assert index.verdict(parse_designation("СП РК 2.04-01-2017")).status == "current"
    assert index.verdict(parse_designation("ГОСТ 21.101-97")).status == "not_covered"  # ГОСТ в АГСК-1 не входят


def _registry_doc(id_, kind, number, status, title="Наименование", effective=44000):
    return {
        "id": id_,
        "kindTitle": kind,
        "number": number,
        "status": status,
        "title": title,
        "effectiveDate": effective,
    }


def test_stroykompleks_index_editions_and_actualised_snip(tmp_path):
    import json

    from zerocycle.norms.stroykompleks import api_date, build

    docs = [
        _registry_doc(1, "Свод правил", "6.13130.2021", 50),
        _registry_doc(2, "Свод правил", "6.13130.2025", 40, effective=46202),
        _registry_doc(
            3, "Свод правил", "54.13330.2022", 40, "Здания жилые. Актуализированная редакция СНиП 31-01-2003"
        ),
        _registry_doc(4, "Свод правил", "54.13330.2022", 50),  # прежняя версия той же записи — не замена
        _registry_doc(5, "Свод правил", "535.1325800.202", 40),  # год без последней цифры — пропускаем
        _registry_doc(6, "СанПиН", "2.1.3684-21", 40),
        _registry_doc(7, "Федеральный закон", "384-ФЗ", 40),  # обозначается иначе — не в индексе
    ]
    path = tmp_path / "ntd.json"
    path.write_text(json.dumps({"fetched": "2026-10-21", "total": 7, "documents": docs}), encoding="utf-8")
    index = build(path)
    assert not index.exhaustive and index.as_of == date(2026, 10, 21)
    codes = {r.code: r for r in index.records}
    assert set(codes) == {"СП 6.13130.2025", "СП 54.13330.2022", "СанПиН 2.1.3684-21"}
    assert codes["СП 6.13130.2025"].replaces == ["СП 6.13130.2021"]
    assert codes["СП 6.13130.2025"].since == "29.06.2026" == f"{api_date(46202):%d.%m.%Y}"
    assert codes["СП 54.13330.2022"].replaces == ["СНиП 31-01-2003"]
    assert index.verdict(parse_designation("СП 6.13130.2021")).status == "replaced"
    assert index.verdict(parse_designation("СП 54.13330.2016")).status == "outdated_edition"
    assert index.verdict(parse_designation("СНиП 31-01-2003*")).status == "replaced"
    assert index.verdict(parse_designation("СП 54.13330")).status == "no_year"


def test_shipped_ru_index_sanity():
    from zerocycle.packs.ru.rules import registry

    index = registry()
    assert index.jurisdiction == "ru" and not index.exhaustive
    assert len(index.records) >= 450
    v = index.verdict(parse_designation("СП 3.13130.2009"))
    assert v.status == "replaced" and v.current.code == "СП 3.13130.2026"
    v = index.verdict(parse_designation("ГОСТ Р 21.1101-2013"))  # курируемая замена вне реестра
    assert v.status == "replaced" and v.current.basis == "ГОСТ Р 21.101-2020, предисловие, п. 4"
    assert index.verdict(parse_designation("СП 256.1325800.2016")).status == "current"
    assert index.verdict(parse_designation("ГОСТ 21.002-2014")).status == "not_listed"  # добровольный — нет в реестре
