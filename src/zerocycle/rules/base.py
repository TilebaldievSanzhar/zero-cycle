"""Правило = код + метаданные + фикстуры ([ADR 0004](../../../docs/adr/0004-rules-evidence-grounding.md)).

Правило — функция над контекстом проверки, которая отдаёт исходы (`Outcome`): что сказано про один объект —
пункт перечня, ссылку на норму, группу листов. Движок превращает исходы в находки, добавляя метаданные правила,
и пропускает через grounding-гейт. Правило регистрируется декоратором `@rule`; пакет правила — первая часть id:
`kz.completeness.ird` → пакет `kz`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from zerocycle.model import Anchor, Family, Package, Severity, Status

if TYPE_CHECKING:
    from zerocycle.rules.context import Context

Kind = Literal["presence", "parameter", "consistency", "reference", "format", "manual"]
Review = Literal["draft", "reviewed", "released"]


@dataclass(frozen=True)
class Outcome:
    """Что правило сказало про один объект проверки; движок делает из него находку."""

    status: Status
    message: str
    anchors: tuple[Anchor, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)  # сравниваемые значения: всё, что названо в сообщении
    basis: tuple[str, ...] = ()  # основание пункта, если уже основания правила: «Правила КВЭ, прил. 2, п. 1, подп. 5)»
    item: str | None = None  # что проверено: пункт перечня, ссылка, документ — ключ для решений ГИПа
    severity: Severity | None = None  # если отличается от тяжести правила
    confidence: float = 1.0


@dataclass(frozen=True)
class Applies:
    """Когда правило применимо; пусто — без ограничения."""

    jurisdictions: tuple[str, ...] = ()
    work_kinds: tuple[str, ...] = ()  # new, reconstruction, capital_repair


@dataclass(frozen=True)
class FixtureCase:
    """Минимальный пакет для фикстуры: модель описи, тексты страниц и параметры проверки."""

    package: Package
    texts: Mapping[tuple[str, int], str] = field(default_factory=dict)  # (путь документа, страница) → текст
    params: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fixture:
    name: str
    build: Callable[[], FixtureCase]
    expect: Mapping[Status, int]  # сколько находок с каким статусом; статусы не из словаря не проверяются
    items: Mapping[str, Status] = field(default_factory=dict)  # статусы отдельных объектов: item → статус


@dataclass(frozen=True)
class Rule:
    id: str  # «kz.completeness.ird»
    version: str
    title: str
    family: Family
    kind: Kind
    severity: Severity
    basis: tuple[str, ...] | Mapping[str, tuple[str, ...]]  # общее или по редакции СПДС: {«ГОСТ 21.101-97»: (…)}
    why: str  # почему эксперт это ловит — одна-две фразы
    remark: str  # как сформулировал бы эксперт
    check: Callable[[Context], Iterable[Outcome]]
    applies: Applies = Applies()
    fixtures: tuple[Fixture, ...] = ()
    review: Review = "draft"
    evidence: tuple[str, ...] = ()  # откуда знаем, что это ловят: замер, заключения, перечни типовых ошибок

    @property
    def pack(self) -> str:
        return self.id.split(".", 1)[0]

    def basis_for(self, edition: str | None) -> tuple[str, ...]:
        if isinstance(self.basis, tuple):
            return self.basis
        return tuple(self.basis.get(edition or "", ()))


REGISTRY: dict[str, Rule] = {}
EVERYWHERE = Applies()


def rule(
    id: str,
    *,
    version: str,
    title: str,
    family: Family,
    kind: Kind,
    severity: Severity,
    basis: tuple[str, ...] | Mapping[str, tuple[str, ...]],
    why: str,
    remark: str,
    applies: Applies = EVERYWHERE,
    fixtures: tuple[Fixture, ...] = (),
    review: Review = "draft",
    evidence: tuple[str, ...] = (),
) -> Callable[[Callable[[Context], Iterable[Outcome]]], Callable[[Context], Iterable[Outcome]]]:
    """Регистрирует функцию проверки как правило. Правило без фикстур не регистрируется (ADR 0004)."""

    def register(fn: Callable[[Context], Iterable[Outcome]]) -> Callable[[Context], Iterable[Outcome]]:
        if not fixtures:
            raise ValueError(f"правило {id} без фикстур не регистрируется")
        if id in REGISTRY and REGISTRY[id].check is not fn:
            raise ValueError(f"правило {id} уже зарегистрировано")
        REGISTRY[id] = Rule(
            id=id,
            version=version,
            title=title,
            family=family,
            kind=kind,
            severity=severity,
            basis=basis,
            why=why,
            remark=remark,
            check=fn,
            applies=applies,
            fixtures=fixtures,
            review=review,
            evidence=evidence,
        )
        return fn

    return register
