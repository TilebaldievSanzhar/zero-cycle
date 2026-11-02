"""Бенчмарк на реальных пакетах: сопоставление находок с убранными документами и порядок прогонов."""

from pathlib import Path

import pytest

from zerocycle.bench.arms import MANUAL, VIOLATED
from zerocycle.bench.real import HIDDEN, PACKAGES, build, hit, plan, publish, score_answer


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
