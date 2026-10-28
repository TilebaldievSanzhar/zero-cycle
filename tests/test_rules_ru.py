"""Пакет `ru` и правила листов на засеянном корпусе: что находят правила и как recall сверяет находки с эталоном."""

import json

import pytest

from zerocycle.bench.corpus import generate
from zerocycle.model import Anchor, Family, Finding, Severity, Status
from zerocycle.rules.cli import check
from zerocycle.rules.recall import DETECTORS, evaluate, matches


def _finding(rule, status=Status.violated, file="01 ПЗ/2345-ПЗ.pdf", page=3, **values):
    return Finding(
        rule=rule,
        rule_version="0",
        family=Family.formatting,
        severity=Severity.minor,
        status=status,
        message="",
        anchors=[Anchor(file=file, sha256="0", page=page)],
        values=values,
    )


def test_every_catalog_defect_has_a_detector():
    from zerocycle.bench.defects import CATALOG

    assert set(CATALOG) == set(DETECTORS)


@pytest.mark.parametrize(
    ("defect", "finding", "ok"),
    [
        ({"id": "F02", "file": "01 ПЗ/2345-ПЗ.pdf", "page": 3}, _finding("spds.sheets.count"), True),
        ({"id": "F02", "file": "02 ПЗУ/2345-ПЗУ.pdf", "page": 3}, _finding("spds.sheets.count"), False),
        ({"id": "F08", "file": "01 ПЗ/2345-ПЗ.pdf", "page": 4}, _finding("spds.sheets.format"), False),  # не тот лист
        (
            {"id": "D07", "file": "01 ПЗ/2345-ПЗ.pdf", "actual": "СП 54.13330.2016"},
            _finding("ru.norms.registry", ref="СП 54.13330.2016"),
            True,
        ),
        ({"id": "D09", "section": 10}, _finding("ru.completeness.pp87", section=10), True),
        ({"id": "F09", "designation": "2345-ИОС4"}, _finding("ru.completeness.pp87", code="ИОС4"), True),
        (
            {"id": "F09", "designation": "2345-ИОС4"},
            _finding("spds.composition.volumes", designation="2345-ИОС4"),
            True,
        ),
        ({"id": "F07", "guid": "AB-CD"}, _finding("spds.pde.guid", guid="ab-cd"), True),
        ({"id": "F02", "file": "01 ПЗ/2345-ПЗ.pdf"}, _finding("spds.sheets.format"), False),  # правило не то
    ],
)
def test_matches(defect, finding, ok):
    assert matches(defect, finding) is ok


def test_evaluate_counts_found_manual_related_and_false_alarms():
    truth = {
        "defects": [
            {"id": "F02", "file": "01 ПЗ/2345-ПЗ.pdf", "page": 3},
            {"id": "D09", "section": 10},
        ]
    }
    findings = [
        _finding("spds.sheets.count"),
        _finding("ru.completeness.pp87", Status.manual, section=10),
        _finding("spds.titleblock.consistent"),  # на листе дефекта F02 — сопутствующая
        _finding("spds.sheets.format", file="03 АР/2345-АР.pdf", page=4),  # ложная
    ]
    res = evaluate("seed", truth, findings)
    assert list(res.found) == [0] and list(res.manual) == [1]
    assert [f.rule for f in res.related] == ["spds.titleblock.consistent"]
    assert [f.rule for f in res.false_alarms] == ["spds.sheets.format"]


def test_seeded_package_defects_are_found(tmp_path):
    """Сквозной прогон: генератор → опись → правила `ru` → сверка с эталоном."""
    codes = ["D07", "D07R", "D09", "F05", "F07", "F08"]
    seed_dir = generate(7, tmp_path, codes=codes)
    truth = json.loads((seed_dir / "ground_truth.json").read_text(encoding="utf-8"))
    report = check(seed_dir / "package", "ru")
    res = evaluate(seed_dir.name, truth, report.findings)
    seeded = [d["id"] for d in truth["defects"]]
    found = sorted(seeded[i] for i in res.found)
    assert found == sorted(seeded)
    assert res.false_alarms == [] and report.clean


def test_clean_package_has_no_violations(tmp_path):
    seed_dir = generate(11, tmp_path, mode="clean")
    report = check(seed_dir / "package", "ru")
    assert [f.message for f in report.findings if f.status == Status.violated] == []
