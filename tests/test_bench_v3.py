"""Датасет V3: скрытые файлы, чистка имён, сборка пакета с архивом и засевом."""

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from zerocycle.bench.v3 import HIDDEN, PACKAGES, Package, Seed, build, clean_name, tar_exe


def bsdtar() -> bool:
    try:
        out = subprocess.run([tar_exe(), "--version"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return False
    return "bsdtar" in out


@pytest.mark.parametrize(
    ("name", "clean"),
    [
        ("+ Том 2. Общая пояснительная записка закл.pdf", "Том 2. Общая пояснительная записка.pdf"),
        ("Отчет по инженерно-геологическим изысканиям +++.pdf", "Отчет по инженерно-геологическим изысканиям.pdf"),
        ("311-2026. Том 4. Сметная документация ПОСЛЕ ГЭ.pdf", "311-2026. Том 4. Сметная документация.pdf"),
        ("Том 1. Книга 1. ОПЗ ПОСЛЕ ГЭ_compressed.pdf", "Том 1. Книга 1. ОПЗ_compressed.pdf"),
        ("ПЗ парк Жансугуров по замеч.pdf", "ПЗ парк Жансугуров.pdf"),
        ("Прайс листы по замечаним.pdf", "Прайс листы.pdf"),
        ("Сводная ведомость КЗ.pdf", "Сводная ведомость КЗ.pdf"),
        ("Том 3. Альбом 1. Генеральный план..pdf", "Том 3. Альбом 1. Генеральный план..pdf"),
        ("+++.pdf", "+++.pdf"),
    ],
)
def test_clean_name(name, clean):
    assert clean_name(name) == clean


@pytest.mark.parametrize(
    ("name", "hidden"),
    [
        ("001 ФОК Талап ЗАКЛЮЧЕНИЕ.pdf", True),
        ("Заключение+ПЭО+Парк_dnc (1).pdf", True),
        ("Приказ ПСД ЦПС.pdf", True),
        ("техспец кап ремонт.doc", True),
        ("techspec_88133072.pdf", True),
        ("Исх.письмо №17 Южгор.PDF", True),
        ("Форма 7.pdf", False),
        ("Том 1 ОПЗ.pdf", False),
        ("+ Казсодержание закл.pdf", False),
    ],
)
def test_hidden(name, hidden):
    assert bool(HIDDEN.search(name)) is hidden


def test_packages_are_distinct():
    announces = [p.announce for p in PACKAGES.values()]
    assert len(set(announces)) == len(announces)
    keys = [s.remark for p in PACKAGES.values() for s in p.seeds]
    assert len(set(keys)) == len(keys)


def make_source(real: Path) -> None:
    files = real / "100" / "files"
    files.mkdir(parents=True)
    (files / "Заключение+ПЭО.pdf").write_bytes(b"%PDF answer")
    (files / "+ Том 2. ПЗ закл.pdf").write_bytes(b"%PDF pz")
    (files / "Каз содержание.xlsx").write_bytes(b"kaz")
    with zipfile.ZipFile(files / "Сметы.zip", "w") as z:
        z.writestr("Сметы/ЛС1.xlsx", b"ls1")
        z.writestr("Сметы/Копия каз.xlsx", b"kaz")  # та же ведомость ещё раз — убрать и её
        z.writestr("Сметы/Заключение.pdf", b"%PDF answer 2")
    manifest = {
        "title": "Объект",
        "files": {
            "u1": {"name": "Сводная.pdf", "status": "ошибка: оборвано"},
            "u2": {"name": "x", "status": "ok"},
            "u3": {"name": "техспец.doc", "status": "пропущен: .doc"},  # скрытый — не в «не скачано»
        },
    }
    (real / "100" / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


@pytest.mark.skipif(not bsdtar(), reason="нужен bsdtar (на Windows — System32\\tar.exe)")
def test_build_removes_answers_seeds_and_copies(tmp_path: Path):
    real = tmp_path / "real"
    make_source(real)
    key = "aaaaaaaaaaaa:bbbbbbbbbb"
    items = tmp_path / "items.jsonl"
    items.write_text(json.dumps({"key": key, "text": "Предоставлено Казсодержание"}, ensure_ascii=False), "utf-8")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({"key": key, "category": "completeness", "added": "Казсодержание"}), "utf-8")
    corpus = tmp_path / "corpus"
    packages = {"kz-01": Package("100", (Seed("Каз содержание.xlsx", key),))}

    [truth] = build(corpus, real, items, labels, packages)

    package = corpus / "kz-01" / "package"
    left = sorted(p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file())
    assert left == ["Сметы/ЛС1.xlsx", "Том 2. ПЗ.pdf"]
    assert truth["hidden"] == ["Заключение+ПЭО.pdf", "Сметы.zip/Сметы/Заключение.pdf"]
    [defect] = truth["defects"]
    assert defect["copies_removed"] == ["Сметы.zip/Сметы/Копия каз.xlsx"]
    assert defect["remark"] == "Предоставлено Казсодержание" and defect["added"] == "Казсодержание"
    assert truth["missing_in_source"] == ["Сводная.pdf"]
    assert truth["formats"] == {".pdf": 1, ".xlsx": 1}
    assert truth["unreadable"] == ["+ Том 2. ПЗ закл.pdf"] and truth["pdf_pages"] == 0  # b"%PDF pz" — не PDF
    assert truth["renamed"] == {"+ Том 2. ПЗ закл.pdf": "Том 2. ПЗ.pdf"}
    assert json.loads((corpus / "kz-01" / "ground_truth.json").read_text(encoding="utf-8")) == truth
    assert not (corpus / "kz-01" / "_extract").exists()


@pytest.mark.skipif(not bsdtar(), reason="нужен bsdtar")
def test_build_fails_when_seed_is_absent(tmp_path: Path):
    real = tmp_path / "real"
    make_source(real)
    (tmp_path / "e.jsonl").write_text("", encoding="utf-8")
    packages = {"kz-01": Package("100", (Seed("Энергопаспорт.pdf", "k"),))}
    with pytest.raises(ValueError, match="нет файла"):
        build(tmp_path / "corpus", real, tmp_path / "e.jsonl", tmp_path / "e.jsonl", packages)
