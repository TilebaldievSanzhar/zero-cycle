"""Прогон правил: цепочка пакетов юрисдикции → применимые правила → исходы → находки → grounding-гейт → отчёт.

Гейт (ADR 0004) не выпускает находку, если:
- у «нарушено» или «проверить вручную» нет якоря — кроме правил наличия: отсутствие доказывается перечнем
  просмотренного, а не листом;
- основание начинается не с документа из `bases.toml`;
- в тексте находки есть число, которого нет ни в сравниваемых значениях, ни в основании, ни в цитатах якорей,
  ни в путях документов пакета.

Задержанные находки не теряются: отчёт показывает их отдельно, а прогон помечается.
"""

from __future__ import annotations

import importlib
import json
import re
import tomllib
from collections import Counter
from datetime import UTC, datetime
from functools import cache
from importlib import resources

from pydantic import BaseModel, Field

from zerocycle.model import Finding, Run, Severity, Status
from zerocycle.packs import context as pack_context
from zerocycle.rules.base import REGISTRY, Outcome, Rule
from zerocycle.rules.context import Context

ENGINE_VERSION = "0.1.0"
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_SEVERITY_ORDER = {Severity.blocker: 0, Severity.major: 1, Severity.minor: 2, Severity.info: 3}
_STATUS_ORDER = {Status.violated: 0, Status.manual: 1, Status.cannot_check: 2, Status.ok: 3}


def pack_chain(jurisdiction: str) -> tuple[str, ...]:
    """Слои правил юрисдикции от корня: универсальные → семья СПДС → национальные (ADR 0003) — по манифестам."""
    return pack_context(jurisdiction).chain


def load_rules(jurisdiction: str) -> list[Rule]:
    chain = pack_chain(jurisdiction)
    for pack in chain:
        try:
            importlib.import_module(f"zerocycle.packs.{pack}.rules")
        except ModuleNotFoundError as e:
            if e.name not in (f"zerocycle.packs.{pack}", f"zerocycle.packs.{pack}.rules"):
                raise  # сломан сам модуль правил, а не отсутствует
    return sorted((r for r in REGISTRY.values() if r.pack in chain), key=lambda r: (chain.index(r.pack), r.id))


@cache
def bases() -> dict[str, dict]:
    return tomllib.loads(resources.files("zerocycle.rules").joinpath("bases.toml").read_text(encoding="utf-8"))


class Coverage(BaseModel):
    rule: str
    title: str
    applicable: bool
    reason: str | None = None  # почему неприменимо
    counts: dict[str, int] = Field(default_factory=dict)  # статус → число находок
    error: str | None = None  # правило упало — прогон помечен


class Withheld(BaseModel):
    rule: str
    message: str
    reason: str


class Report(BaseModel):
    package: str
    jurisdiction: str
    edition: str | None
    work_kind: str | None
    packs: list[str]
    run: Run
    findings: list[Finding] = Field(default_factory=list)
    coverage: list[Coverage] = Field(default_factory=list)
    withheld: list[Withheld] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        """Прогон без сбоев правил и без задержанных находок."""
        return not self.withheld and not any(c.error for c in self.coverage)


def not_applicable(rule: Rule, ctx: Context) -> str | None:
    a = rule.applies
    if a.jurisdictions and ctx.jurisdiction not in a.jurisdictions:
        return f"юрисдикция {ctx.jurisdiction}"
    if a.work_kinds and ctx.work_kind not in a.work_kinds:
        return f"вид работ {ctx.work_kind or 'не определён'}"
    return None


def finding(rule: Rule, outcome: Outcome, ctx: Context) -> Finding:
    values = dict(outcome.values)
    if outcome.item:
        values.setdefault("item", outcome.item)
    return Finding(
        rule=rule.id,
        rule_version=rule.version,
        family=rule.family,
        severity=outcome.severity or rule.severity,
        status=outcome.status,
        message=outcome.message,
        anchors=list(outcome.anchors),
        values=values,
        basis=list(outcome.basis or rule.basis_for(ctx.edition)),
        confidence=outcome.confidence,
    )


def gate(rule: Rule, f: Finding, ctx: Context) -> str | None:
    """Причина задержать находку или None."""
    if f.status in (Status.violated, Status.manual) and not f.anchors and rule.kind != "presence":
        return "нет якоря"
    known = bases()
    for b in f.basis:
        doc = b.split(",", 1)[0].strip()
        if doc not in known:
            return f"основание не из списка оснований: {doc}"
    allowed = " ".join(
        [
            json.dumps(f.values, ensure_ascii=False, default=str),
            " ".join(f.basis),
            " ".join(a.quote or "" for a in f.anchors),
            " ".join(d.path for d in ctx.package.documents),
        ]
    )
    known_numbers = set(_NUMBER.findall(allowed))  # числа целиком: «7» не берётся из «21.101-97»
    stray = [n for n in _NUMBER.findall(f.message) if n not in known_numbers]
    if stray:
        return f"числа не из фактов: {', '.join(stray[:3])}"
    return None


def run(ctx: Context, rules: list[Rule] | None = None) -> Report:
    started = datetime.now(UTC)
    rules = rules if rules is not None else load_rules(ctx.jurisdiction)
    findings: list[Finding] = []
    coverage: list[Coverage] = []
    withheld: list[Withheld] = []
    for r in rules:
        reason = not_applicable(r, ctx)
        if reason:
            coverage.append(Coverage(rule=r.id, title=r.title, applicable=False, reason=reason))
            continue
        cov = Coverage(rule=r.id, title=r.title, applicable=True)
        try:
            outcomes = list(r.check(ctx))
        except Exception as e:  # упавшее правило не роняет прогон: видно в покрытии
            cov.error = f"{type(e).__name__}: {e}"
            coverage.append(cov)
            continue
        counts: Counter[str] = Counter()
        for o in outcomes:
            f = finding(r, o, ctx)
            why = gate(r, f, ctx)
            if why:
                withheld.append(Withheld(rule=r.id, message=f.message, reason=why))
                continue
            findings.append(f)
            counts[f.status.value] += 1
        cov.counts = dict(counts)
        coverage.append(cov)
    findings.sort(key=lambda f: (_STATUS_ORDER[f.status], _SEVERITY_ORDER[f.severity], f.rule))
    chain = list(pack_chain(ctx.jurisdiction))
    return Report(
        package=ctx.package.root,
        jurisdiction=ctx.jurisdiction,
        edition=ctx.edition,
        work_kind=ctx.work_kind,
        packs=chain,
        run=Run(
            engine=ENGINE_VERSION,
            packs={r.id: r.version for r in rules},
            norm_index="; ".join(f"{name}: {version}" for name, version in ctx.sources.items()) or None,
            started=started,
            finished=datetime.now(UTC),
        ),
        findings=findings,
        coverage=coverage,
        withheld=withheld,
    )
