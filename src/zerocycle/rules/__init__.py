"""Движок правил (PLAN, шаг 0.5): правило = код + метаданные + фикстуры, контекст проверки, прогон, гейт, карточки.

Правила живут в пакетах: `zerocycle.packs.<пакет>.rules` — модуль с функциями под декоратором `@rule`.
Цепочка пакетов юрисдикции — `core` → `spds` → `kz` / `ru` ([ADR 0003](../../../docs/adr/0003-jurisdiction-packs.md)).
"""

from zerocycle.rules.base import REGISTRY, Applies, Fixture, FixtureCase, Outcome, Rule, rule
from zerocycle.rules.context import Context
from zerocycle.rules.engine import Report, load_rules, run

__all__ = [
    "REGISTRY",
    "Applies",
    "Context",
    "Fixture",
    "FixtureCase",
    "Outcome",
    "Report",
    "Rule",
    "load_rules",
    "rule",
    "run",
]
