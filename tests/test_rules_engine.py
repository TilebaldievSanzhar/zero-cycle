"""Движок правил: фикстуры каждого правила, основания, grounding-гейт, покрытие, тексты из настоящего PDF."""

import pytest

from zerocycle.model import Anchor, Family, Severity, Status
from zerocycle.rules import REGISTRY, Context, Fixture, Outcome, load_rules, rule, run
from zerocycle.rules.engine import bases, finding, gate
from zerocycle.rules.fixtures import case, doc, package, page, run_fixture

RULES = {r.id: r for j in ("kz", "ru") for r in load_rules(j)}
EDITIONS = ("ГОСТ 21.101-97", "ГОСТ Р 21.101-2020")


def test_chains_load_layers():
    kz = {r.pack for r in load_rules("kz")}
    assert {"core", "spds", "kz"} <= kz
    assert "kz" not in {r.pack for r in load_rules("ru")}


@pytest.mark.parametrize(
    ("rule_id", "fixture"),
    [(r.id, f) for r in RULES.values() for f in r.fixtures],
    ids=[f"{r.id}: {f.name}" for r in RULES.values() for f in r.fixtures],
)
def test_rule_fixture(rule_id, fixture):
    assert run_fixture(RULES[rule_id], fixture) == []


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_rule_metadata(rule_id):
    r = RULES[rule_id]
    assert r.fixtures and r.why and r.remark and r.title
    for edition in EDITIONS:
        for b in r.basis_for(edition):
            assert b.split(",", 1)[0].strip() in bases(), f"основание «{b}» не в rules/bases.toml"
    if not isinstance(r.basis, tuple):
        assert all(r.basis_for(e) for e in EDITIONS), "основание есть для каждой редакции СПДС"


def test_rule_without_fixtures_is_rejected():
    with pytest.raises(ValueError, match="без фикстур"):
        rule(
            "test.no-fixtures",
            version="0.0.1",
            title="—",
            family=Family.formatting,
            kind="format",
            severity=Severity.info,
            basis=(),
            why="—",
            remark="—",
        )(lambda ctx: [])


def _probe_rule(rule_id: str, outcomes, kind="format", basis=("ГОСТ 21.101-97, прил. Д",)):
    def check(ctx):
        if isinstance(outcomes, Exception):
            raise outcomes
        return outcomes

    rule(
        rule_id,
        version="0.0.1",
        title="проба",
        family=Family.formatting,
        kind=kind,
        severity=Severity.minor,
        basis=basis,
        why="—",
        remark="—",
        fixtures=(Fixture("—", lambda: case(package()), {}),),
    )(check)
    return REGISTRY.pop(rule_id)


ANCHOR = Anchor(file="АР.pdf", sha256="0" * 64, page=3, quote="311-2026-АР")


@pytest.mark.parametrize(
    ("outcome", "kind", "reason"),
    [
        (Outcome(Status.violated, "Нарушено без якоря"), "format", "нет якоря"),
        (Outcome(Status.violated, "Нет документа"), "presence", None),  # отсутствие доказывается перечнем
        (Outcome(Status.violated, "На 7 листах", anchors=(ANCHOR,)), "format", "числа не из фактов: 7"),
        (Outcome(Status.violated, "На 7 листах", anchors=(ANCHOR,), values={"sheets": 7}), "format", None),
        (Outcome(Status.violated, "Лист 311-2026-АР", anchors=(ANCHOR,)), "format", None),  # число из цитаты якоря
        (Outcome(Status.violated, "x", anchors=(ANCHOR,), basis=("СНиП 1.01-01-85, п. 1",)), "format", "основание"),
    ],
)
def test_gate(outcome, kind, reason):
    r = _probe_rule("test.gate", [outcome], kind=kind)
    ctx = Context(package(doc("АР.pdf")))
    why = gate(r, finding(r, outcome, ctx), ctx)
    assert (why is None) if reason is None else (why and why.startswith(reason))


def test_run_coverage_withheld_and_errors():
    ok = _probe_rule("test.ok", [Outcome(Status.ok, "Норма")])
    bad = _probe_rule("test.withheld", [Outcome(Status.violated, "На 9 листах", anchors=(ANCHOR,))])
    broken = _probe_rule("test.broken", RuntimeError("сломалось"))
    ctx = Context(package(doc("АР.pdf")))
    report = run(ctx, [ok, bad, broken])
    assert [f.rule for f in report.findings] == ["test.ok"]
    assert [w.rule for w in report.withheld] == ["test.withheld"]
    cov = {c.rule: c for c in report.coverage}
    assert cov["test.ok"].counts == {"ok": 1}
    assert cov["test.broken"].error == "RuntimeError: сломалось"
    assert not report.clean


def test_rule_not_applicable_outside_jurisdiction():
    kz_rule = RULES["kz.completeness.ird"]
    report = run(Context(package(doc("АР.pdf"), jurisdiction="ru")), [kz_rule])
    assert report.findings == [] and report.coverage[0].applicable is False


def test_work_kind_inferred_from_object_name():
    pkg = package(
        doc("АР.pdf", [page(1, {2: "Капитальный ремонт школы № 5 в г. Аркалык"}), page(2, {2: "Капитальный ремонт"})]),
        doc("ОВ.pdf", [page(1, {2: "Строительство котельной"})]),
    )
    assert Context(pkg).work_kind == "capital_repair"
    assert Context(pkg, params={"work_kind": "new"}).work_kind == "new"


def test_card_shows_basis_verification_and_fixtures():
    from zerocycle.rules.cards import card

    text = card(RULES["kz.completeness.ird"])
    assert text.startswith("# Исходно-разрешительная документация")
    assert "черновик — не проверено практиком" in text
    assert "Правила КВЭ (Приказ № 299), прил. 2–4" in text
    assert "по копиям приложений 2, 3, 4, 4-1, 5" in text  # как сверено основание — из bases.toml
    assert "- всё на месте → норма — 12" in text


def test_markdown_report_lists_findings_and_withheld():
    from zerocycle.rules.cli import markdown

    ok = _probe_rule("test.md-ok", [Outcome(Status.ok, "Норма")])
    bad = _probe_rule("test.md-withheld", [Outcome(Status.violated, "На 9 листах", anchors=(ANCHOR,))])
    text = markdown(run(Context(package(doc("АР.pdf"))), [ok, bad]))
    assert "| норма | 1 |" in text and "`test.md-ok` — Норма" in text
    assert "## Задержаны гейтом" in text and "числа не из фактов: 9" in text


def test_context_reads_pdf_text_and_norm_ref_boxes(tmp_path):
    from reportlab.pdfgen import canvas

    from zerocycle.bench.draw import FONT, register_font

    register_font()
    path = tmp_path / "ПЗ.pdf"
    c = canvas.Canvas(str(path), pagesize=(595, 842))
    c.setFont(FONT, 11)
    c.drawString(72, 742, "Общая пояснительная записка. Порядок разработки — по СН РК 1.02-03-2011*.")
    c.showPage()
    c.save()
    pz = doc("ПЗ.pdf", 1)
    ctx = Context(package(pz), files={"ПЗ.pdf": path})
    [hit] = ctx.norm_refs()
    assert hit.designation.code == "СН РК 1.02-03-2011"
    x0, top, x1, bottom = hit.anchor.bbox
    assert 72 < x0 < x1 < 595 and 90 < top < bottom < 110  # строка на 742 пт от низа = 100 пт от верха
    assert ctx.pages_of(pz)[1].head.startswith("Общая пояснительная записка")
    assert ctx.textless() == []
