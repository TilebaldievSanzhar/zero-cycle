"""Бенчмарк на реальных пакетах: сопоставление находок с убранными документами, порядок прогонов, слепая оценка."""

import json
from pathlib import Path

import pytest

from zerocycle.bench.arms import MANUAL, VIOLATED
from zerocycle.bench.real import (
    HIDDEN,
    PACKAGES,
    batches,
    build,
    hit,
    judge_report,
    plan,
    publish,
    sample,
    score_answer,
)


def finding(text: str, status: str = VIOLATED) -> dict:
    return {"status": status, "description": text, "basis": "Приказ № 299, приложение 2"}


def test_found_when_named_and_absent():
    igi = PACKAGES["real-1"].defects[0]
    assert hit(igi, finding("Отсутствует отчёт по инженерно-геологическим изысканиям"))
    assert hit(igi, finding("В пакете нет отчёта ИГИ"))


def test_mentioning_document_is_not_finding_it():
    igi = PACKAGES["real-1"].defects[0]
    assert not hit(igi, finding("Отчёт по ИГИ устарел"))
    assert not hit(igi, finding("Нет подписи ГИП на листе 3"))


def test_best_status_wins():
    answer = {"findings": [finding("Нет энергетического паспорта", MANUAL), finding("Энергопаспорт отсутствует")]}
    assert score_answer("real-1", answer)["energy"] == VIOLATED
    assert score_answer("real-1", {"findings": []})["energy"] is None


def test_pairs_stay_together_in_order():
    order = plan(["B", "A1"], 2)
    assert order[:2] == [("real-1", "B", 1), ("real-1", "A1", 1)]
    assert len(order) == 16
    assert all(k == 1 for _, _, k in order[:8])


@pytest.mark.parametrize(
    ("name", "hidden"),
    [
        ("Заключение ФК Окжетпес 2 очередь.pdf", True),
        ("Заключение экспертизы ЦПС.pdf", True),
        ("Исх.письмо №17 Южгор.PDF", True),
        ("techspec_86747487.pdf", True),
        ("+ Том 2. Общая пояснительная записка закл.pdf", False),
    ],
)
def test_hidden_files(name, hidden):
    assert bool(HIDDEN.search(name)) is hidden


def test_build_hides_answers_and_publish_has_no_truth(tmp_path: Path):
    real = tmp_path / "real"
    for spec in PACKAGES.values():
        files = real / spec.announce / "files"
        files.mkdir(parents=True)
        (files / "Заключение.pdf").write_bytes(b"%PDF")
        (files / "ОПЗ.pdf").write_bytes(b"%PDF")
        for d in spec.defects:
            (files / f"{d.file} закл.pdf").write_bytes(b"%PDF")
    corpus = tmp_path / "corpus"
    build(corpus, real)
    names = {p.name for p in (corpus / "real-1" / "package").iterdir()}
    assert names == {"ОПЗ.pdf"}
    published = tmp_path / "published"
    publish(corpus, published)
    assert not list(published.rglob("ground_truth.json"))
    assert not [p for p in published.rglob("*.pdf") if "аключени" in p.name]


def test_sample_hides_arm_and_keeps_key(tmp_path: Path):
    answers = tmp_path / "answers"
    answers.mkdir()
    for arm in ("B", "A1"):
        for k in (1, 2):
            findings = [{**finding(f"{arm}{k} нарушение {i}"), "document": "ОПЗ.pdf", "page": i} for i in range(5)]
            findings.append(finding("не в выборку", MANUAL))
            path = answers / f"real-1.{arm}.{k}.json"
            path.write_text(json.dumps({"findings": findings}), encoding="utf-8")
    tasks, secret = sample(tmp_path, arms=("B", "A1"), per_arm=6, seed=1)
    assert len(tasks) == 12 and set(secret) == {t["id"] for t in tasks}
    assert all("arm" not in t and "проверить" not in t["description"] for t in tasks)
    assert {secret[t["id"]].split(".")[1] for t in tasks} == {"B", "A1"}
    assert [len(items) for _, items in batches(tasks, size=5)] == [5, 5, 2]
    verdicts = [{"id": t["id"], "verdict": "подтверждено", "reason": ""} for t in tasks]
    assert "| B | 6 | 0 | 0 | 1.00 |" in judge_report(secret, verdicts)
