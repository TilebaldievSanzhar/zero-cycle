"""Карточка правила — для рецензента-практика и для обучения команды (ADR 0004).

Карточка собирается из метаданных правила: что проверяем, на каком основании, почему эксперт это ловит, как он
это пишет, откуда мы знаем, что это ловят, и какими фикстурами правило проверено.
"""

from __future__ import annotations

from zerocycle.rules.base import Rule
from zerocycle.rules.engine import bases

FAMILY = {
    "completeness": "комплектность",
    "formatting": "оформление",
    "consistency": "увязка",
    "norms": "нормы и ссылки на них",
    "calculation": "расчёты",
    "estimate": "смета",
    "submission_format": "формат подачи",
}
KIND = {
    "presence": "наличие",
    "parameter": "параметр",
    "consistency": "согласованность",
    "reference": "ссылка",
    "format": "оформление",
    "manual": "вручную",
}
SEVERITY = {"blocker": "блокирует", "major": "существенно", "minor": "мелочь", "info": "к сведению"}
REVIEW = {"draft": "черновик — не проверено практиком", "reviewed": "проверено практиком", "released": "выпущено"}
STATUS = {"violated": "нарушено", "ok": "норма", "manual": "проверить вручную", "cannot_check": "проверить невозможно"}


def card(rule: Rule) -> str:
    if isinstance(rule.basis, tuple):
        basis = [f"- {b}" for b in rule.basis]
    else:
        basis = [f"- {edition}: {'; '.join(items)}" for edition, items in rule.basis.items()]
    docs = sorted({b.split(",", 1)[0].strip() for items in _basis_items(rule) for b in items})
    verified = [f"- {d}: {' '.join(bases()[d]['verified'].split())}" for d in docs if d in bases()]
    applies = []
    if rule.applies.jurisdictions:
        applies.append("юрисдикция: " + ", ".join(rule.applies.jurisdictions))
    if rule.applies.work_kinds:
        applies.append("вид работ: " + ", ".join(rule.applies.work_kinds))
    fixtures = []
    for f in rule.fixtures:
        expect = ", ".join(f"{STATUS[s.value]} — {n}" for s, n in f.expect.items())
        items = ", ".join(f"{i}: {STATUS[s.value]}" for i, s in f.items.items())
        fixtures.append(f"- {f.name}" + (f" → {expect}" if expect else "") + (f"; {items}" if items else ""))
    lines = [
        f"# {rule.title}",
        "",
        f"`{rule.id}` · версия {rule.version} · {REVIEW[rule.review]}",
        "",
        "| Семейство | Вид проверки | Тяжесть | Применимость |",
        "|---|---|---|---|",
        f"| {FAMILY[rule.family.value]} | {KIND[rule.kind]} | {SEVERITY[rule.severity.value]} | "
        f"{'; '.join(applies) or 'везде'} |",
        "",
        "**Почему эксперт это ловит.** " + rule.why,
        "",
        "**Как это пишет эксперт.** " + rule.remark,
        "",
        "**Основание.**",
        *basis,
        "",
        "**Как сверено основание.**",
        *(verified or ["- не указано"]),
    ]
    if rule.evidence:
        lines += ["", "**Откуда знаем, что это ловят.**", *(f"- {e}" for e in rule.evidence)]
    lines += ["", "**Фикстуры.**", *fixtures, ""]
    return "\n".join(lines)


def _basis_items(rule: Rule) -> list[tuple[str, ...]]:
    return [rule.basis] if isinstance(rule.basis, tuple) else list(rule.basis.values())
