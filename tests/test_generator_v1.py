"""Генератор v1 (шаг 0.4b): объём, шаблоны организаций, раскладка листов, отвлекающее содержимое и эталон.

Опись здесь проверяется только на разработочной выборке: отложенную (split="heldout") генерируем, но не читаем —
она для замера 0.12 (docs/bench/PROTOCOL.md).
"""

import hashlib
import json

import pytest

from zerocycle.bench import v1
from zerocycle.bench.corpus import generate
from zerocycle.bench.defects import CATALOG
from zerocycle.bench.generator import composition_rows
from zerocycle.bench.templates import catalog, templates
from zerocycle.packs import context
from zerocycle.scan import scan

# доли чаще обычного: в маленьком пакете должны быть все виды раскладки
RATES = {"rotate": 0.15, "sideways": 0.15, "scaled": 0.15, "fit": 0.05}
NOTE_KINDS = {
    "snip_in_title",
    "tep_distractor",
    "appendix",
    "estimate",
    "scaled_print",
    "fit_print",
    "rotated",
    "sideways",
    "subcontractor",
    "not_developed",
}


@pytest.fixture(scope="module")
def package(tmp_path_factory):
    out = generate(5, tmp_path_factory.mktemp("v1"), codes=sorted(CATALOG), version=1, size="small", rates=RATES)
    return out, scan(out / "package", context("ru"))


def _gt(out):
    return json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))


def _pages(out):
    return [json.loads(x) for x in (out / "pages.jsonl").read_text(encoding="utf-8").splitlines()]


def test_profile_and_notes(package):
    out, _pkg = package
    gt = _gt(out)
    assert gt["profile"]["version"] == 1 and gt["profile"]["split"] == "dev"
    assert catalog()[gt["profile"]["template"]].split == "dev"
    assert gt["profile"]["sheets"] == len(_pages(out))
    assert gt["notes"] and {n["kind"] for n in gt["notes"]} <= NOTE_KINDS
    for n in gt["notes"]:
        assert n["expected"] in ("ok", "manual", "cannot_check") and n["why"], n
        assert n["file"].endswith(".pdf") and (n["page"] is None or n["page"] >= 1), n


def test_every_layout_is_present(package):
    rows = _pages(package[0])
    assert any(r.get("rotate") for r in rows)
    assert any(r.get("sideways") for r in rows)
    assert any(r.get("scale") for r in rows)
    assert any(r["kind"] == "appendix" for r in rows)


def test_scan_reads_every_title_block(package):
    """Опись на шаблоне разработки: штамп организации, /Rotate, лист боком, печать с уменьшением."""
    out, pkg = package
    pages = {(d.path, p.number): p for d in pkg.documents for p in d.pages}
    for e in _pages(out):
        p = pages[(e["file"], e["page"])]
        tb = p.title_block
        assert p.paper == e["paper"], e
        if e["form"] is None:
            assert tb is None, e
            continue
        assert tb is not None and tb.form == e["form"], e
        assert {g: tb.get(int(g)) for g in e["graphs"]} == e["graphs"], e
        assert [[r.role, r.name, r.date] for r in tb.signatures] == e["signatures"], e
        assert p.turn == e.get("sideways", 0), e
        assert abs(tb.scale - e.get("scale", 1.0)) < 0.03, e


def test_defects_are_visible_in_scan(package):
    out, pkg = package
    defects = {d["id"]: d for d in _gt(out)["defects"]}
    assert len(defects) >= 12
    docs = {d.path: d for d in pkg.documents}

    def page(d):
        return docs[d["file"]].pages[d["page"] - 1]

    if d := defects.get("D03"):
        assert page(d).title_block.get(1) == d["actual"]
    if d := defects.get("F02"):
        assert page(d).title_block.get(8) == d["actual"]
    if d := defects.get("F01"):
        assert page(d).title_block.get(7) == str(d["jumps_to"])
    if d := defects.get("F03"):
        assert page(d).title_block.get(6) == "Р"
    if d := defects.get("F08"):
        assert page(d).paper == "A2" and page(d).title_block.get(26) == "А3"
    if d := defects.get("D10"):
        row = next(r for r in page(d).title_block.signatures if r.role == d["role"])
        assert (row.name if d["field"] == "name" else row.date) == ""


def test_full_size_volume():
    """Объём как у реальных пакетов: 300–1 500 листов (PROTOCOL, слой S)."""
    sizes = []
    for seed in range(1, 7):
        spec = v1.build(seed, "dev", "full")
        sizes.append(sum(len(t.sheets) for t in spec.toms if t.present) + 1)
    assert all(300 <= n <= 1500 for n in sizes), sizes
    assert max(sizes) > 1.5 * min(sizes), sizes


def test_same_seed_same_bytes(tmp_path):
    def digest(root):
        return {
            p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*"))
            if p.is_file()
        }

    a = generate(3, tmp_path / "a", version=1, size="small")
    b = generate(3, tmp_path / "b", version=1, size="small")
    assert digest(a) == digest(b)


def test_heldout_templates_are_separate(tmp_path):
    """Отложенная выборка — свои шаблоны. Пакет генерируется, а опись на нём здесь не запускается."""
    dev, held = {t.id for t in templates("dev")}, {t.id for t in templates("heldout")}
    assert dev and held and not dev & held
    assert {catalog()[t].wording for t in held} == {"heldout"}
    out = generate(2, tmp_path, version=1, split="heldout", size="small")
    gt = _gt(out)
    assert out.name == "v1-heldout-0002"
    assert gt["profile"]["split"] == "heldout" and gt["profile"]["template"] in held


def test_not_developed_rows():
    """«Не разрабатывается» в «Составе ПД»: тома нет, строка есть, дефекты этот раздел не трогают."""
    spec = v1.build(4, "dev", "small", {"no_gas": 1.0})
    gas = next(t for t in spec.toms if t.code == "ИОС" and t.numbers == (6,))
    assert (gas.present, gas.developed, gas.in_composition) == (False, False, True)
    assert [gas.number, gas.designation, gas.title, "Не разрабатывается"] in composition_rows(spec)
    assert spec.tom("ИОС", (6,)) is None
