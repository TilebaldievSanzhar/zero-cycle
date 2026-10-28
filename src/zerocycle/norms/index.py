"""Индекс действующих нормативных документов юрисдикции и вердикт по ссылке из пакета.

Индекс перечисляет документы полностью только для своих видов (`covers`): АГСК-1 перечисляет СН РК, СП РК, СНиП,
НТП РК…, но не межгосударственные ГОСТ. Ссылка на вид вне индекса — «не покрыто», а не «не действует»
(та же оговорка, что для реестра «Стройкомплекс.РФ»: «нет в реестре» ≠ «не действует»).
"""

from __future__ import annotations

from datetime import date
from functools import cached_property
from typing import Literal

from pydantic import BaseModel, Field

from zerocycle.norms.designation import NormDesignation, find_designations


class NormRecord(BaseModel):
    code: str  # каноническая запись: «СН РК 1.02-03-2022»
    kind: str
    number: str
    year: int | None = None
    title: str | None = None
    replaces: list[str] = Field(default_factory=list)  # канонические записи заменённых документов
    since: str | None = None  # дата введения, как в источнике: «01.08.2022»
    page: int | None = None  # страница источника — якорь для проверки человеком


class NormIndex(BaseModel):
    jurisdiction: str
    source: str  # что за документ: «АГСК-1 по состоянию на июнь 2025 года …»
    as_of: date  # на какую дату индекс перечисляет действующие документы
    sha256: str  # файла-источника
    covers: list[str]  # виды документов, которые индекс перечисляет полностью
    records: list[NormRecord]

    @cached_property
    def _by_key(self) -> dict[tuple[str, str], list[NormRecord]]:
        out: dict[tuple[str, str], list[NormRecord]] = {}
        for r in self.records:
            out.setdefault((r.kind, r.number), []).append(r)
        return out

    @cached_property
    def _replaced_by(self) -> dict[str, NormRecord]:
        return {old: r for r in self.records for old in r.replaces}

    @cached_property
    def _replaced_by_key(self) -> dict[tuple[str, str], NormRecord]:
        out = {}
        for old, r in self._replaced_by.items():
            found = find_designations(old)
            if found:
                out[found[0][0].key] = r
        return out

    def verdict(self, ref: NormDesignation) -> RefVerdict:
        if ref.kind not in self.covers:
            return RefVerdict(status="not_covered")
        same = self._by_key.get(ref.key, [])
        newest = max(same, key=lambda r: r.year or 0) if same else None
        if ref.year is not None and ref.code in self._replaced_by and not any(r.year == ref.year for r in same):
            return RefVerdict(status="replaced", current=self._replaced_by[ref.code])
        if not same:
            # «СНиП РК 4.01-02» без года, а в индексе — «Взамен СНиП РК 4.01-02-2009»
            if ref.year is None and ref.key in self._replaced_by_key:
                return RefVerdict(status="replaced", current=self._replaced_by_key[ref.key])
            return RefVerdict(status="not_listed")
        if ref.year is None:
            return RefVerdict(status="no_year", current=newest)
        if any(r.year == ref.year for r in same):
            return RefVerdict(status="current", current=next(r for r in same if r.year == ref.year))
        if newest and (newest.year or 0) > ref.year:
            return RefVerdict(status="outdated_edition", current=newest)
        return RefVerdict(status="not_listed", current=newest)  # ссылка новее индекса — индекс устарел?


Status = Literal["current", "outdated_edition", "replaced", "not_listed", "not_covered", "no_year"]


class RefVerdict(BaseModel):
    status: Status
    current: NormRecord | None = None  # действующая запись индекса, если есть


def replaced_codes(note: str, kind: str) -> list[str]:
    """«Взамен СН РК 1.02-01-2016, СН РК 1.02-02-2016» → канонические записи; «Взамен 1.02-03-2011*» — вид записи."""
    found = [d.code for d, _, _ in find_designations(note)]
    if found:
        return found
    bare = find_designations(f"{kind} {note.split('Взамен', 1)[-1].strip()}")
    return [d.code for d, _, _ in bare[:1]]


__all__ = ["NormDesignation", "NormIndex", "NormRecord", "RefVerdict", "replaced_codes"]
