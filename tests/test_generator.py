"""Засеянный корпус: раскладка, воспроизводимость, опись против эталона, видимость дефектов."""

import hashlib
import json

import pytest

from zerocycle.bench.corpus import generate
from zerocycle.bench.generator import build
from zerocycle.packs import context
from zerocycle.scan import scan

CODES = ["D03", "F01", "F02", "F03", "F08", "D09", "F09", "F06", "F07", "D07"]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    dirty = generate(13, root, codes=CODES)
    clean = generate(14, root, mode="clean")
    return {
        "dirty": (dirty, scan(dirty / "package", context("ru"))),
        "clean": (clean, scan(clean / "package", context("ru"))),
    }


def _gt(out):
    return json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))


def _pages(out):
    return [json.loads(x) for x in (out / "pages.jsonl").read_text(encoding="utf-8").splitlines()]


def test_ground_truth_lives_outside_package(corpus):
    out, pkg = corpus["clean"]
    assert (out / "package" / "info.xml").is_file()
    assert not any(d.path.endswith(("ground_truth.json", "pages.jsonl")) for d in pkg.documents)


def test_same_seed_same_bytes(tmp_path):
    def digest(root):
        return {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    a, b = generate(7, tmp_path / "a"), generate(7, tmp_path / "b")
    assert digest(a) == digest(b)


@pytest.mark.parametrize("name", ["clean", "dirty"])
def test_scan_reads_every_title_block(corpus, name):
    out, pkg = corpus[name]
    pages = {(d.path, p.number): p for d in pkg.documents for p in d.pages}
    for e in _pages(out):
        tb = pages[(e["file"], e["page"])].title_block
        if e["form"] is None:
            assert tb is None, e
            continue
        assert tb is not None and tb.form == e["form"], e
        assert {str(g): tb.get(g) for g in map(int, e["graphs"])} == e["graphs"], e
        assert [[r.role, r.name, r.date] for r in tb.signatures] == e["signatures"], e


def test_clean_package_is_consistent(corpus):
    out, pkg = corpus["clean"]
    assert _gt(out)["defects"] == []
    assert pkg.pde is not None and pkg.pde.issues == []
    assert all(d.guid for d in pkg.documents)
    assert all(d.designation and not d.designation.issues for d in pkg.documents)


def test_defects_are_visible_in_scan(corpus):
    out, pkg = corpus["dirty"]
    defects = {d["id"]: d for d in _gt(out)["defects"]}
    assert set(defects) == set(CODES)
    docs = {d.path: d for d in pkg.documents}

    def page(d):
        return docs[d["file"]].pages[d["page"] - 1]

    assert page(defects["D03"]).title_block.get(1) == defects["D03"]["actual"]
    assert page(defects["F02"]).title_block.get(8) == defects["F02"]["actual"]
    assert page(defects["F01"]).title_block.get(7) == str(defects["F01"]["jumps_to"])
    assert page(defects["F03"]).title_block.get(6) == "Р"
    assert page(defects["F08"]).paper == "A2" and page(defects["F08"]).title_block.get(26) == "А3"
    for code in ("D09", "F09"):
        assert not any(d.designation and d.designation.text == defects[code]["designation"] for d in pkg.documents)
    assert defects["F06"]["file"] in docs
    assert any(defects["F07"]["guid"] in issue for issue in pkg.pde.issues)


def test_unsafe_characters_in_file_names():
    """База «ЖК-44/2026»: «/» в имени файла — «_» (ГОСТ Р 21.101-2020, прил. А к п. 8.2.6), а не лишняя папка."""
    spec = next(s for s in map(build, range(1, 60)) if "/" in s.base)
    for tom in [*spec.toms, spec.composition]:
        assert tom.path == f"{tom.folder}/{tom.designation.replace('/', '_')}.pdf"
