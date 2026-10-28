"""Руки бенчмарка: схема ответа, сопоставление находок с эталоном по протоколу, аудит изоляции папки руки."""

import json
from pathlib import Path

import pytest

from zerocycle.bench.arms import ALLOWED, ANSWER_SCHEMA, CATEGORIES, MANUAL, VIOLATED, matches, norm_doc, score
from zerocycle.bench.corpus import generate
from zerocycle.bench.defects import CATALOG
from zerocycle.bench.pilot import _outside


@pytest.fixture(scope="module")
def truth(tmp_path_factory):
    out = generate(5, tmp_path_factory.mktemp("arms"), codes=sorted(CATALOG), version=1, size="small")
    return json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))


def _finding(d: dict) -> dict:
    """Находка, какой её напишет рука, нашедшая засеянный дефект."""
    category = sorted(ALLOWED[d["id"]])[0]
    texts = {
        "D09": lambda: f"Нет раздела {d['section']} ({d['code']})",
        "F09": lambda: f"Нет тома {d['designation']}",
        "F05": lambda: f"Том {d['designation']} не указан в «Составе ПД»",
        "F07": lambda: f"Повтор GUID {d['guid']}",
    }
    text = texts[d["id"]]() if d["id"] in texts else "дефект"
    doc = d.get("file") or ("info.xml" if d["id"] == "F07" else "")
    return {
        "category": category,
        "status": VIOLATED,
        "document": f".\\package\\{doc}".replace("/", "\\"),
        "page": d.get("page"),
        "description": text,
        "basis": "ГОСТ Р 21.101-2020",
    }


def test_schema_categories_are_engine_families():
    assert ANSWER_SCHEMA["properties"]["findings"]["items"]["properties"]["category"]["enum"] == CATEGORIES
    assert all(c <= set(CATEGORIES) for c in ALLOWED.values())


def test_norm_doc():
    assert norm_doc(r"C:\run\package\04 КР\x.pdf") == "04 КР/x.pdf"
    assert norm_doc("./package/info.xml") == "info.xml"
    assert norm_doc("04 КР/x.pdf") == "04 КР/x.pdf"


def test_every_seeded_defect_is_matched_by_its_finding(truth):
    findings = [_finding(d) for d in truth["defects"]]
    _s, metrics = score(truth, findings)
    assert metrics["recall"] == 1.0 and metrics["precision"] == 1.0 and metrics["false_alarms"] == 0


def test_wrong_page_or_category_does_not_match(truth):
    d = next(d for d in truth["defects"] if d["id"] in ("D03", "D10", "F03", "F08"))
    f = _finding(d)
    assert matches(d, f)
    assert not matches(d, {**f, "page": d["page"] + 3})
    assert matches(d, {**f, "page": d["page"] + 1})  # страница ±1 (PROTOCOL)
    assert not matches(d, {**f, "category": "нормы и ссылки на них"})


def test_manual_and_false_alarms(truth):
    d = truth["defects"][0]
    stray = {
        "category": "оформление",
        "status": VIOLATED,
        "document": "нет/такого.pdf",
        "page": 1,
        "description": "x",
        "basis": "",
    }
    _s, m = score(truth, [{**_finding(d), "status": MANUAL}, stray])
    assert m["recall"] == 0.0 and m["manual_at_defects"] == 1
    assert m["false_alarms"] == 1 and m["precision"] == 0.0 and m["evidence"] == 0.5


def test_outside_paths(tmp_path):
    run = Path(r"C:\zc-pilot\runs\abc123")
    inside = {"command": r"python x.py C:\zc-pilot\runs\abc123\package /c/zc-pilot/runs/abc123/package/info.xml"}
    assert _outside(inside, run) == set()
    assert _outside({"command": "ls C:/zc-pilot/runs"}, run) == {"C:/zc-pilot/runs"}
    assert _outside({"command": "ls .. && cat ../x"}, run)
    assert _outside({"file_path": r"C:\Projects\development_challenge\zero-cycle\var\x.json"}, run)
