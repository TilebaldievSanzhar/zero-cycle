"""Карта обратного засева (scripts/seed_map.py): коды сметной программы, файлы в архивах, «обновлённый» документ."""

import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("seed_map", ROOT / "scripts" / "seed_map.py")
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


@pytest.mark.parametrize(
    ("added", "filename"),
    [
        ("пояснительная записка к сметной документации", "PZ1-27_RU.docx"),
        ("сводная ведомость материальных ресурсов и оборудования", "SVMROB186_Форма 7 06.04.26.pdf"),
        ("сводный сметный расчет", "1. SvSR230426 - Э_RU.xlsx"),
        ("теплотехнические расчеты наружных ограждающих конструкций", "ОВ.zip/ОВ/Теплотехн. расчет ОВ Явленка (1).pdf"),
    ],
)
def test_matches_codes_and_archive_members(added, filename):
    assert sm.score(added, filename) >= 0.5


@pytest.mark.parametrize(
    ("added", "filename"),
    [
        ("пояснительная записка к сметной документации", "LS1-01-00-01_RU.xlsx"),
        ("локальная смета", "SLS1.xlsx"),  # код только не после латинской буквы
    ],
)
def test_codes_do_not_overmatch(added, filename):
    assert sm.score(added, filename) < 0.5


def test_updated_document_was_there_before():
    assert sm.REVISED.search("Представлена обновленная топографическая съемка")
    assert not sm.REVISED.search("Представлена топографическая съемка")


def test_archive_members_are_listed_with_archive_name(tmp_path: Path):
    files = tmp_path / "files"
    files.mkdir()
    with zipfile.ZipFile(files / "ПСД 1.zip", "w") as z:
        z.writestr("ПСД 1/ВК расчеты.pdf", b"%PDF")
    (files / "ОПЗ.pdf").write_bytes(b"%PDF")
    assert sm.archive_members(files) == ["ПСД 1.zip/ПСД 1/ВК расчеты.pdf"]
