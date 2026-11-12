"""Датасет V3, слой 1: реальные пакеты РК после экспертизы с обратным засевом (docs/bench/DATASET_V3.md).

Пакет закупки берётся целиком, во всех форматах: архивы распакованы (tar.exe — bsdtar: RAR, 7z, ZIP), сметы и
таблицы остаются. Из пакета убраны:
- заключение и всё, что выдаёт итог экспертизы (HIDDEN): письма экспертизы, приказ об утверждении ПСД с показателями
  «до экспертизы / после экспертизы», техспецификация закупки;
- документы, добавленные по замечаниям (PACKAGES): карта scripts/seed_map.py, просмотренная вручную, — вместе с
  точными копиями этих файлов в других местах пакета.

Из имён файлов убраны пометки «после ГЭ», «по замечаниям», «закл», «+++»: до экспертизы их не было, а агенту они
подсказывают, что правили.

    python -X utf8 -m zerocycle.bench.v3 build <корпус>  [--real var/real/goszakup_kz]
        [--items var/labels/kz_all_items.jsonl] [--labels var/labels/kz_codex_high.jsonl]

Эталон — <корпус>/<пакет>/ground_truth.json рядом с package/, вне git: в нём тексты пунктов заключений.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

REAL = Path("var/real/goszakup_kz")
ITEMS = Path("var/labels/kz_all_items.jsonl")
LABELS = Path("var/labels/kz_codex_high.jsonl")

# Заключение (и по-казахски), письма экспертизы, приказ об утверждении ПСД (в нём показатели до и после экспертизы),
# техспецификация закупки — не часть ПСД.
HIDDEN = re.compile(r"аключени|қорытынд|^Исх\.?\s*письм|^Приказ\b|^techspec|^техспец", re.I)
ARCHIVES = {".zip", ".rar", ".7z"}
# пометки состояния после экспертизы в именах файлов
MARKS = (
    re.compile(r"\s*после\s+ГЭ(?![а-яёa-z])", re.I),
    re.compile(r"\s*по\s+замеч[а-яё]*", re.I),
    re.compile(r"\s+закл(?![а-яё])", re.I),
    re.compile(r"\s*\+{2,}"),
    re.compile(r"^\+\s*"),
)
MAX_DEPTH = 3  # архивы в архивах


@dataclass(frozen=True)
class Seed:
    file: str  # путь в пакете закупки: имя файла или «архив/путь внутри архива» (как в списке tar -tf)
    remark: str  # ключ пункта заключения (label_remarks.py items): sha256 заключения[:12] + ":" + sha1 текста[:10]


@dataclass(frozen=True)
class Package:
    announce: str
    seeds: tuple[Seed, ...] = ()
    group: str | None = None  # пакеты одного комплекса: их дефекты не независимы


OKZHETPES = "okzhetpes"  # 2-я и 3-я очереди одного комплекса: свои заключения, но 12 общих файлов

# Карта обратного засева 12.11 (DATASET_V3.md): 17 сопоставлений, просмотрены. Отброшено одно: «расчёты расходов
# воды и ёмкости резервуаров» ↔ «Резервуар чистой воды 92-95.pdf» (17161636) — там чертежи типового резервуара.
PACKAGES = {
    "kz-01": Package(
        "16909959", (Seed("КАЗ.СОДЕРЖАНИЕ____7e8724e8d66cdb7f104885d6af97b792.pdf", "bd29377de1f1:95b0704e0d"),)
    ),
    "kz-02": Package("17039050", (Seed("Казсодержание.pdf", "338861f2444b:2f773f531a"),)),
    "kz-03": Package(  # real-3
        "17067800",
        (Seed("Расчет_кж.pdf", "cdeb8bfbaa59:5cf1985b50"), Seed("Казсодержание.pdf", "cdeb8bfbaa59:9a0ac9a1cd")),
    ),
    "kz-04": Package("17248156", (Seed("Каз содержание.xlsx", "5e372844f614:4428bb21f2"),)),
    "kz-05": Package(  # real-1
        "17560023",
        (
            Seed("Отчет по инженерно-геологическим изысканиям +++.pdf", "1629476656ac:7299a6fa12"),
            Seed("Теплотехнический расчёт +++.pdf", "1629476656ac:fae4b24307"),
            Seed("+ Энергетический паспорт закл.pdf", "1629476656ac:f234a27513"),
            Seed("+ Сводная ресурсная ведомость закл.pdf", "1629476656ac:07f9be9222"),
        ),
        OKZHETPES,
    ),
    "kz-06": Package(
        "17560893",
        (
            Seed("Отчет по инженерно-геологическим изысканиям +++.pdf", "3b7a369c0071:7299a6fa12"),
            Seed("Теплотехнический расчет +++.pdf", "3b7a369c0071:fae4b24307"),
            Seed("+ Энергетический паспорт закл.pdf", "3b7a369c0071:f234a27513"),
        ),
        OKZHETPES,
    ),
    "kz-07": Package("17571053", (Seed("Том 16 - МОПБ.pdf", "ee4f8730daca:170c86ee77"),)),  # real-2
    "kz-08": Package(
        "17763665",
        (Seed("Энергетический_паспорт_на_Блок_А_be9d5afa4a273c3facd2abae4458b2c2.pdf", "f8e75af10b67:e941ddba81"),),
    ),
    "kz-09": Package(
        "17778737",
        (
            Seed("Геологический отчет с.Тауыш ФАП.pdf", "158c6fd0eef2:cf370f1795"),
            Seed("ПОДПИСАН. Перечень цен по прейскурантам.pdf", "158c6fd0eef2:e38cb08a84"),
        ),
    ),
}


def clean_name(name: str) -> str:
    """Имя файла без пометок состояния после экспертизы: «+ Том 2. ПЗ закл.pdf» → «Том 2. ПЗ.pdf»."""
    p = PurePosixPath(name)
    stem = p.stem if p.suffix and len(p.suffix) <= 6 else name
    suffix = p.suffix if stem != name else ""
    for mark in MARKS:
        stem = mark.sub("", stem)
    stem = stem.strip()
    return (stem or p.stem) + suffix


def tar_exe() -> str:
    """bsdtar: на Windows — System32\\tar.exe (tar из Git — GNU tar, он не читает RAR и ZIP)."""
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
    return str(system) if system.exists() else "tar"


def extract(archive: Path, into: Path) -> Path:
    """Распаковать в новую пустую папку; корень — единственная папка верхнего уровня, если она одна."""
    into.mkdir(parents=True)
    subprocess.run([tar_exe(), "-xf", str(archive), "-C", str(into)], check=True, capture_output=True)
    base = into.resolve()
    for p in into.rglob("*"):
        if p.is_symlink():
            p.unlink()
        elif not p.resolve().is_relative_to(base):
            raise ValueError(f"{archive.name}: путь вне папки распаковки: {p}")
    top = list(into.iterdir())
    return top[0] if len(top) == 1 and top[0].is_dir() else into


def stage(src: Path, out: Path, scratch: Path) -> dict[str, Path]:
    """Все файлы пакета закупки → out, архивы распакованы в папки с их именами.

    Возвращает исходный путь («имя» или «архив/путь внутри архива») → файл в out.
    """
    files: dict[str, Path] = {}

    def add(path: Path, key: str, dest: Path, depth: int) -> None:
        if path.suffix.lower() in ARCHIVES and depth < MAX_DEPTH:
            tmp = scratch / f"x{len(list(scratch.iterdir()))}"
            root = extract(path, tmp)
            target = dest.with_name(path.stem)
            while target.exists():
                target = target.with_name(target.name + "_")
            target.mkdir(parents=True)
            for p in sorted(root.rglob("*")):
                if p.is_file():  # ключ — путь как в архиве, в пакете — без лишней папки верхнего уровня
                    add(p, f"{key}/{p.relative_to(tmp).as_posix()}", target / p.relative_to(root), depth + 1)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        files[key] = dest

    scratch.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        if p.is_file():
            add(p, p.name, out / p.name, 0)
    return files


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pdf_pages(path: Path) -> int | None:
    """Листов в PDF; None — не открывается."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        return None
    try:
        return len(pdf)
    finally:
        pdf.close()


def load_jsonl(path: Path) -> dict[str, dict]:
    rows = (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return {r["key"]: r for r in rows}


def build_one(
    name: str, spec: Package, corpus: Path, real: Path, items: dict[str, dict], labels: dict[str, dict]
) -> dict:
    src = real / spec.announce
    out = corpus / name / "package"
    scratch = corpus / name / "_extract"
    for d in (out, scratch):
        if d.exists():
            shutil.rmtree(d)
    files = stage(src / "files", out, scratch)
    shutil.rmtree(scratch)

    hidden = sorted(k for k in files if HIDDEN.search(PurePosixPath(k).name))
    defects = []
    for n, seed in enumerate(spec.seeds, 1):
        if seed.file not in files:
            raise ValueError(f"{name}: нет файла для засева: {seed.file}")
        if seed.remark not in items or seed.remark not in labels:
            raise ValueError(f"{name}: нет пункта {seed.remark} в разметке")
        digest = sha256(files[seed.file])
        copies = sorted(k for k, p in files.items() if k != seed.file and sha256(p) == digest)
        defects.append(
            {
                "id": f"{name}.{n}",
                "file": seed.file,
                "sha256": digest,
                "copies_removed": copies,
                "remark_key": seed.remark,
                "remark": items[seed.remark]["text"],
                "added": labels[seed.remark].get("added"),
                "category": labels[seed.remark].get("category"),
            }
        )
    removed = set(hidden) | {d["file"] for d in defects} | {c for d in defects for c in d["copies_removed"]}
    for k in removed:
        files.pop(k).unlink()

    renamed = {}
    for k, p in sorted(files.items()):
        new = p.with_name(clean_name(p.name))
        if new != p and not new.exists():
            p.rename(new)
            files[k] = new
            renamed[k] = new.relative_to(out).as_posix()
    for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()

    gone = {d["sha256"] for d in defects}
    left = [p for p in out.rglob("*") if p.is_file() and sha256(p) in gone]
    if left:
        raise AssertionError(f"{name}: в пакете остались копии убранных документов: {left}")

    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    missing = {f["name"] for f in manifest["files"].values() if f.get("status") != "ok"}
    pages = {k: pdf_pages(p) for k, p in files.items() if p.suffix.lower() == ".pdf"}
    truth = {
        "package": name,
        "announce": spec.announce,
        "title": manifest.get("title"),
        "group": spec.group,
        "files": len(files),
        "formats": dict(Counter(p.suffix.lower() or "—" for p in files.values()).most_common()),
        "pdf_pages": sum(n for n in pages.values() if n),  # знаменатель «ложных тревог на 100 листов»
        "unreadable": sorted(k for k, n in pages.items() if n is None),
        "hidden": hidden,
        # не скачались с портала: их отсутствие в пакете — не засеянный дефект
        "missing_in_source": sorted(n for n in missing if not HIDDEN.search(n)),
        "renamed": renamed,
        "defects": defects,
    }
    (corpus / name / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth


def build(
    corpus: Path,
    real: Path = REAL,
    items: Path = ITEMS,
    labels: Path = LABELS,
    packages: dict[str, Package] | None = None,
) -> list[dict]:
    items_by_key, labels_by_key = load_jsonl(items), load_jsonl(labels)
    return [
        build_one(name, spec, corpus, real, items_by_key, labels_by_key)
        for name, spec in (packages or PACKAGES).items()
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description="Датасет V3: пакеты РК с обратным засевом")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("corpus", type=Path)
    b.add_argument("--real", type=Path, default=REAL)
    b.add_argument("--items", type=Path, default=ITEMS)
    b.add_argument("--labels", type=Path, default=LABELS)
    a = ap.parse_args()
    for t in build(a.corpus, a.real, a.items, a.labels):
        ds = t["defects"]
        copies = sum(len(d["copies_removed"]) for d in ds)
        print(
            f"{t['package']} ({t['announce']}): файлов {t['files']}, листов PDF {t['pdf_pages']}, "
            f"скрыто {len(t['hidden'])}, дефектов {len(ds)}"
            + (f" (+ копий {copies})" if copies else "")
            + f", переименовано {len(t['renamed'])}"
            + (f"; не скачано: {', '.join(t['missing_in_source'])}" if t["missing_in_source"] else "")
            + (f"; не открываются: {', '.join(t['unreadable'])}" if t["unreadable"] else "")
        )


if __name__ == "__main__":
    main()
