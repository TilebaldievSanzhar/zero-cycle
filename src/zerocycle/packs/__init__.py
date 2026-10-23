"""Пакеты правил и данных: `spds` — семья СПДС, `ru` — Россия (docs/adr/0003-jurisdiction-packs.md).

Здесь — загрузка данных пакетов: профиль основных надписей и справочник шифров. Правила появятся на шаге 0.5.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import cache
from importlib import resources


def _load(pack: str, name: str) -> dict:
    return tomllib.loads(resources.files(f"zerocycle.packs.{pack}").joinpath(name).read_text(encoding="utf-8"))


Cell = tuple[float, float, float, float]  # x0, x1, y0, y1 в мм от левого верхнего угла основной надписи
Band = tuple[float, float]  # y0, y1 в мм


@dataclass(frozen=True)
class FormProfile:
    """Форма основной надписи (ГОСТ Р 21.101-2020, прил. Ж)."""

    form: int
    title: str
    width: float
    height: float
    header: Band  # строка «Изм. | Кол.уч | Лист | № док. | Подп. | Дата»
    changes: tuple[Band, ...]
    signatures: tuple[Band, ...]
    graphs: dict[int, Cell]
    labels: dict[str, Cell]


@dataclass(frozen=True)
class TitleBlockProfile:
    frame: dict[str, float]  # поля внутренней рамки: left, top, right, bottom, мм
    change_columns: dict[int, Band]  # графы 14–19: x0, x1
    sign_columns: dict[int, Band]  # графы 10–13: x0, x1
    header_labels: tuple[str, ...]
    forms: dict[int, FormProfile]


@cache
def titleblock_profile() -> TitleBlockProfile:
    d = _load("spds", "titleblocks.toml")
    forms = {}
    for key, f in d["forms"].items():
        forms[int(key)] = FormProfile(
            form=int(key),
            title=f["title"],
            width=185.0,
            height=float(f["height"]),
            header=tuple(f["header"]),
            changes=tuple(tuple(b) for b in f["changes"]),
            signatures=tuple(tuple(b) for b in f["signatures"]),
            graphs={int(g): tuple(c) for g, c in f["graphs"].items()},
            labels={name: tuple(c) for name, c in f.get("labels", {}).items()},
        )
    return TitleBlockProfile(
        frame={k: float(v) for k, v in d["frame"].items()},
        change_columns={int(g): tuple(c) for g, c in d["columns"]["changes"].items()},
        sign_columns={int(g): tuple(c) for g, c in d["columns"]["signatures"].items()},
        header_labels=tuple(d["header"]["labels"]),
        forms=forms,
    )


@dataclass(frozen=True)
class SectionInfo:
    number: int
    title: str
    codes: tuple[str, ...]
    subsections: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Codebook:
    """Справочник шифров юрисдикции: разделы ПД, марки рабочих чертежей, хвосты организаций."""

    jurisdiction: str
    nonlinear: tuple[SectionInfo, ...]
    linear: tuple[SectionInfo, ...]
    legacy: dict[str, str]
    marks: dict[str, str]
    tails: dict[str, str]

    @property
    def section_codes(self) -> frozenset[str]:
        return frozenset(c for s in self.nonlinear + self.linear for c in s.codes) | frozenset(self.legacy)

    @property
    def all_codes(self) -> frozenset[str]:
        """Всё, что может стоять шифром в обозначении: разделы ПД и марки РД."""
        return self.section_codes | frozenset(self.marks)

    def section(self, code: str, linear: bool = False) -> SectionInfo | None:
        for s in self.linear if linear else self.nonlinear:
            if code in s.codes:
                return s
        return None


@cache
def codebook(jurisdiction: str = "ru") -> Codebook:
    d = _load(jurisdiction, "sections.toml")

    def sections(key: str) -> tuple[SectionInfo, ...]:
        return tuple(
            SectionInfo(
                number=s["number"],
                title=s["title"],
                codes=tuple(s["codes"]),
                subsections={x["number"]: x["title"] for x in s.get("subsections", [])},
            )
            for s in d.get(key, [])
        )

    return Codebook(
        jurisdiction=jurisdiction,
        nonlinear=sections("nonlinear"),
        linear=sections("linear"),
        legacy=dict(d.get("legacy", {})),
        marks=dict(d.get("marks", {})),
        tails=dict(d.get("tails", {})),
    )
