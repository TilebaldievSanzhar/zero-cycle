"""Датасет V3: скрытые файлы, чистка имён, сборка пакета с архивом и засевом."""

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from zerocycle.bench.archives import seven_zip, tar_exe
from zerocycle.bench.v3 import (
    KINDS,
    PACKAGES,
    SEEN,
    Package,
    Seed,
    build,
    clean_name,
    conclusions_of,
    excluded_items,
    is_hidden,
    kind,
    split,
)


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
        ("Альбом 2 ЭСН-после замеч.pdf", "Альбом 2 ЭСН.pdf"),
        ("ГП Текели дом после замечаний.pdf", "ГП Текели дом.pdf"),
        ("Смета - Туран (после экспертизы)", "Смета - Туран"),
        ("смета КОС Ботакара после зам окончательный", "смета КОС Ботакара окончательный"),
        ("ГП-КНС1-3 Ботокара до экспертизы (14,11,2025).pdf", "ГП-КНС1-3 Ботокара (14,11,2025).pdf"),
        ("1.ГП_ЖД_№15_по_зам_fec5941.pdf", "1.ГП_ЖД_№15_fec5941.pdf"),
        ("Проект по замене труб.pdf", "Проект по замене труб.pdf"),
        ("Теп.Отоп", "Теп.Отоп"),
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
        ("старое Заключение Благоустроиство Кемель_compressed.pdf", True),
        ("Тех_Заключение_de3016444c24253efafc5a498b722057.pdf", False),  # обследование здания — исходные данные
        ("Заключение по результатам обследования.pdf", False),
    ],
)
def test_hidden(name, hidden):
    assert is_hidden(name) is hidden


def test_packages_are_distinct():
    announces = [p.announce for p in PACKAGES.values()]
    assert len(set(announces)) == len(announces)
    keys = [s.remark for p in PACKAGES.values() for s in p.seeds]
    assert len(set(keys)) == len(keys)


def test_every_seed_has_a_kind():
    kinds = [kind(s) for p in PACKAGES.values() for s in p.seeds]
    assert set(kinds) <= {k for k, *_ in KINDS}
    # как в сводке DATASET_V3: казсодержание 11, сводная ведомость ресурсов 11, энергопаспорт 8
    assert (kinds.count("kazsod"), kinds.count("svmr"), kinds.count("energy")) == (11, 11, 8)


@pytest.mark.parametrize(
    ("file", "expected"),
    [
        ("ТУ_ТС_5a836ece65c729f3.pdf", "tu"),
        ("ПОС_993f6c98c40f2c74.pdf", "pos"),
        ("ПОДПИСАН. Перечень цен по прейскурантам.pdf", "prices"),
        ("Теплотехн. расчет ОВ Явленка (1).pdf", "thermal"),
        ("Гидравлический расчет.pdf", "calc"),
        ("F8550 сводная ведом. материал. ресур. и оборуд..pdf", "svmr"),
        ("Сводная ведомость потребности основных материалов, изделий.pdf", "kazsod"),
        ("Протокол Астр месторожд1501ЦКН.PDF", "water"),
        ("Отчет по инженерно-геологическим изысканиям.pdf", "igi"),
        ("PZ1-27_RU.docx", "pz_smeta"),
    ],
)
def test_kind_by_name(file, expected):
    assert kind(Seed(f"архив.rar/папка/{file}", "x:y")) == expected


def test_kind_unknown_needs_explicit():
    with pytest.raises(ValueError):
        kind(Seed("Брошюра 2 _ исправ.pdf", "x:y"))
    assert kind(Seed("Брошюра 2 _ исправ.pdf", "x:y", kind="energy")) == "energy"


def test_split_keeps_groups_together_and_seen_in_dev():
    parts = split()
    dev, held = set(parts["dev"]), set(parts["heldout"])
    assert dev | held == set(PACKAGES) and not dev & held
    assert set(SEEN) <= dev
    for g in {p.group for p in PACKAGES.values()} - {None}:
        members = {n for n, p in PACKAGES.items() if p.group == g}
        assert members <= dev or members <= held


def test_split_pulls_group_of_seen_package():
    packages = {"kz-05": Package("1", group="g"), "kz-06": Package("2", group="g"), "kz-50": Package("3")}
    assert split(packages) == {"dev": ["kz-05", "kz-06"], "heldout": ["kz-50"]}


def test_excluded_items_follow_copies_of_conclusion(tmp_path: Path):
    files = tmp_path / "real" / "1" / "files"
    files.mkdir(parents=True)
    (files / "Заключение.pdf").write_bytes(b"%PDF c1")
    (files / "АР.pdf").write_bytes(b"%PDF ar")
    prefixes = conclusions_of(tmp_path / "real", ["1"])
    assert len(prefixes) == 1  # АР.pdf — не заключение
    p = next(iter(prefixes))
    items = {
        f"{p}:a": {"conclusion": "SE-1/25"},
        "bbbbbbbbbbbb:a": {"conclusion": "SE-1/25"},  # то же заключение, приложенное к другой закупке
        "cccccccccccc:a": {"conclusion": "SE-2/25"},
    }
    keys, numbers = excluded_items(items, prefixes)
    assert keys == {f"{p}:a", "bbbbbbbbbbbb:a"} and numbers == {"SE-1/25"}


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
    assert truth["extract_lost"] == [] and truth["leak_suspects"] == {}


@pytest.mark.skipif(not seven_zip(), reason="нужен 7-Zip")
def test_volume_set_is_one_archive_in_package(tmp_path: Path):
    from test_bench_archives import two_volumes
    from zerocycle.bench.v3 import stage

    two_volumes(tmp_path / "files")
    files, lost = stage(tmp_path / "files", tmp_path / "out", tmp_path / "scratch")
    assert lost == []
    out = tmp_path / "out" / "ПСД"  # папка набора, без номера тома и без лишнего уровня «ПСД/»
    assert files == {
        "ПСД.rar/ПСД/ОПЗ.pdf": out / "ОПЗ.pdf",
        "ПСД.rar/ПСД/АР.pdf": out / "АР.pdf",
        "ПСД.rar/ПСД/Қ ГП.pdf": out / "Қ ГП.pdf",
    }


@pytest.mark.skipif(not bsdtar(), reason="нужен bsdtar")
def test_without_seven_zip_volumes_keep_set_key_and_lose_split_files(tmp_path: Path, monkeypatch):
    from test_bench_archives import two_volumes
    from zerocycle.bench import archives, v3

    monkeypatch.setattr(archives, "seven_zip", lambda: None)
    monkeypatch.setattr(v3, "seven_zip", lambda: None)
    two_volumes(tmp_path / "files")
    files, lost = v3.stage(tmp_path / "files", tmp_path / "out", tmp_path / "scratch")
    assert "ПСД.rar/ПСД/АР.pdf" not in files
    assert "ПСД.rar/ПСД/АР.pdf: из тома в том" in lost


@pytest.mark.skipif(not bsdtar(), reason="нужен bsdtar")
def test_build_fails_when_seed_is_absent(tmp_path: Path):
    real = tmp_path / "real"
    make_source(real)
    (tmp_path / "e.jsonl").write_text("", encoding="utf-8")
    packages = {"kz-01": Package("100", (Seed("Энергопаспорт.pdf", "k"),))}
    with pytest.raises(ValueError, match="нет файла"):
        build(tmp_path / "corpus", real, tmp_path / "e.jsonl", tmp_path / "e.jsonl", packages)


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ("ЗАКЛЮЧЕНИЕ комплексной вневедомственной экспертизы по рабочему проекту", True),
        ("Мотивированный отказ в выдаче заключения", True),
        ("Пояснительная записка (откорректирован по замечаниям экспертизы)", True),
        ("Общая пояснительная записка. Заключение о соответствии нормам", False),
    ],
)
def test_leak_in_text(text, leak):
    from zerocycle.bench.v3 import LEAK

    assert bool(LEAK.search(text)) is leak
