"""Движок v2 (docs/bench/PROTOCOL_V3.md): знание экспертизы РК и «нарушение» только у правил с измеренной точностью."""

import pytest

from zerocycle.model import Family, Severity, Status
from zerocycle.packs import manual_only
from zerocycle.packs.kz.expertise import classify, doc_types, knowledge, object_class, work_kind
from zerocycle.rules.base import Outcome, Rule
from zerocycle.rules.context import Context
from zerocycle.rules.engine import UNMEASURED, run
from zerocycle.rules.fixtures import doc, package


def _rule(rule_id: str, basis: str) -> Rule:
    return Rule(
        id=rule_id,
        version="0.0.1",
        title="проверка",
        family=Family.formatting,
        kind="presence",
        severity=Severity.minor,
        basis=(basis,),
        why="",
        remark="",
        check=lambda ctx: iter([Outcome(Status.violated, "Пустая графа")]),
    )


def test_unmeasured_rules_become_manual_in_kz_only():
    assert "spds." in manual_only("kz") and manual_only("ru") == ()
    kz = run(Context(package(doc("АР.pdf"), jurisdiction="kz")), [_rule("spds.test.empty", "ГОСТ 21.101-97")])
    assert kz.findings[0].status == Status.manual and kz.findings[0].message.endswith(UNMEASURED)
    ru = run(Context(package(doc("АР.pdf"), jurisdiction="ru")), [_rule("spds.test.empty", "ГОСТ Р 21.101-2020")])
    assert ru.findings[0].status == Status.violated
    own = run(Context(package(doc("АР.pdf"), jurisdiction="kz")), [_rule("kz.test.empty", "ГОСТ 21.101-97")])
    assert own.findings[0].status == Status.violated  # правила kz не понижаются


@pytest.mark.parametrize(
    ("added", "expected"),
    [
        ("Пояснительная записка к сметной документации", "estimate-note"),
        ("сводная ведомость материальных ресурсов и оборудования", "resource-sheet"),
        ("Сводная ведомость потребности основных материалов (казахстанское содержание)", "local-content"),
        ("расчет продолжительности строительства", "duration-calc"),
        ("энергетический паспорт", "energy-passport"),
        ("теплотехнический расчет ограждающих конструкций", "thermal-calc"),
        ("лист «Состав проекта»", "composition-sheet"),
        ("расчеты на компенсацию температурных удлинений", "pipe-stress"),
        ("откорректированная пояснительная записка к сметной документации", None),  # исправлен, а не добавлен
        ("титульные листы", None),
    ],
)
def test_classify_added(added, expected):
    assert classify(added) == expected


@pytest.mark.parametrize(
    ("name", "cls", "work"),
    [
        ("Строительство 27-квартирного жилого дома с внешними инженерными сетями", "building", "new"),
        ("Капитальный ремонт наружных сетей с благоустройством зданий ГКП", "linear", "capital_repair"),
        ("Реконструкция тепловых сетей ТМ-3 от ТК 3-41 до ТК-3-44", "linear", "reconstruction"),
        ("Реконструкция Притобольского парка (здание АБК, с устройством фонтана)", "landscape", "reconstruction"),
        ("Капитальный ремонт здания КГУ СОШ лицей им. А. Иманов", "building", "capital_repair"),
        ("Государственные закупки для ГУ «Отдел строительства»", None, "new"),
    ],
)
def test_object_class_and_work(name, cls, work):
    assert object_class(name) == cls and work_kind(name) == work


def test_knowledge_is_built_without_heldout():
    k = knowledge()
    assert k["conclusions"] > 300 and k["excluded_items"] > 1000  # заключения отложенной части V3 исключены
    ids = {d.id for d in doc_types()}
    assert set(k["doc_types"]) == ids
    assert all(v.get("all", 0) <= k["population"]["all"] for v in k["doc_types"].values())
