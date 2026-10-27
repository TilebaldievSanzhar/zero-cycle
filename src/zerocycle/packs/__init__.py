"""Пакеты данных и правил (docs/adr/0003-jurisdiction-packs.md): `spds` — семья СПДС по редакциям, `ru` и `kz` —
юрисдикции, поверх них — слой организации.

Здесь — контекст пакета для извлечения: справочник шифров юрисдикции, профиль основных надписей той редакции
СПДС, которую она задаёт, и формы юрлиц. Значений по умолчанию нет: юрисдикцию называет вызывающий, и всё
извлечение получает контекст явно.

Данные:
- `spds/<редакция>.toml` — формы основных надписей, подписи граф, марки и хвосты обозначений; `extends` — от какой
  редакции файл наследуется, `verified` — сверен ли профиль с текстом стандарта;
- `<пакет>/pack.toml` — манифест: родитель; у юрисдикции — ещё редакция СПДС и формы юрлиц;
- `<юрисдикция>/sections.toml` — разделы и шифры документов;
- слой организации — TOML с таблицами `spds` и `sections`, которые накладываются поверх (`merge`), и списком
  `org_forms`, если у организации свои формы юрлиц.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from functools import cache, cached_property
from importlib import resources
from pathlib import Path


def _load(pack: str, name: str) -> dict:
    return tomllib.loads(resources.files(f"zerocycle.packs.{pack}").joinpath(name).read_text(encoding="utf-8"))


def _manifest(pack: str) -> dict | None:
    path = resources.files("zerocycle.packs").joinpath(pack, "pack.toml")
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def merge(base: dict, over: dict) -> dict:
    """Наложить слой на родителя: таблицы сливаются по ключам, списки и значения заменяются целиком."""
    out = dict(base)
    for key, value in over.items():
        out[key] = merge(out[key], value) if isinstance(value, dict) and isinstance(out.get(key), dict) else value
    return out


def available() -> list[str]:
    """Юрисдикции — пакеты, манифест которых называет редакцию СПДС."""
    root = resources.files("zerocycle.packs")
    return sorted(p.name for p in root.iterdir() if p.is_dir() and "spds" in (_manifest(p.name) or {}))


def _edition(edition: str) -> dict:
    """Данные редакции СПДС поверх той, от которой она наследуется."""
    d = _load("spds", f"{edition}.toml")
    parent = d.pop("extends", None)
    return merge(_edition(parent), d) if parent else d


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
    """Профиль основных надписей редакции СПДС: геометрия форм и подписи граф."""

    edition: str  # «2020», «97»
    standard: str  # «ГОСТ Р 21.101-2020»
    verified: bool  # профиль сверен с текстом стандарта
    width: float  # ширина всех форм, мм
    frame: dict[str, float]  # поля внутренней рамки: left, top, right, bottom, мм
    change_columns: dict[int, Band]  # графы 14–19: x0, x1
    sign_columns: dict[int, Band]  # графы 10–13: x0, x1
    header_labels: tuple[str, ...]  # подписи шапки, как их пишет стандарт
    header_roles: dict[int, tuple[str, ...]]  # графа таблицы изменений → образцы подписи в реальных штампах
    label_graphs: dict[str, int]  # подпись правой части → графа со значением под ней
    format_labels: tuple[str, ...]  # подпись графы 26
    forms: dict[int, FormProfile]

    @property
    def left_width(self) -> float:
        """Ширина левой части основной надписи — таблиц изменений и подписей, мм."""
        return max(x1 for _x0, x1 in self.change_columns.values())

    @property
    def left_columns(self) -> tuple[float, ...]:
        """Правые границы колонок таблицы изменений, мм."""
        return tuple(sorted(x1 for _x0, x1 in self.change_columns.values()))

    @property
    def heights(self) -> dict[float, int]:
        """Высота → первая форма такой высоты, от высокой к низкой: у формы 3 есть и короткие горизонтали."""
        out: dict[float, int] = {}
        for f in self.forms.values():
            out.setdefault(f.height, f.form)
        return dict(sorted(out.items(), reverse=True))

    @property
    def header_from_bottom(self) -> dict[int, float]:
        """Форма → центр строки шапки над низом основной надписи, мм; формы с той же шапкой — один раз."""
        out: dict[int, float] = {}
        for f in self.forms.values():
            offset = f.height - sum(f.header) / 2
            if offset not in out.values():
                out[f.form] = offset
        return out

    @property
    def max_change_rows(self) -> int:
        return max(len(f.changes) for f in self.forms.values())

    @property
    def max_sign_rows(self) -> int:
        return max(len(f.signatures) for f in self.forms.values())

    @property
    def customer_row(self) -> float:
        """Высота строки графы 27 (заказчик) над основной надписью, мм."""
        return max(-float(f.graphs[27][2]) for f in self.forms.values() if 27 in f.graphs)


def _profile(edition: str, d: dict) -> TitleBlockProfile:
    width = float(d["width"])
    forms = {}
    for key, f in d["forms"].items():
        forms[int(key)] = FormProfile(
            form=int(key),
            title=f["title"],
            width=width,
            height=float(f["height"]),
            header=tuple(f["header"]),
            changes=tuple(tuple(b) for b in f["changes"]),
            signatures=tuple(tuple(b) for b in f["signatures"]),
            graphs={int(g): tuple(c) for g, c in f["graphs"].items()},
            labels={name: tuple(c) for name, c in f.get("labels", {}).items()},
        )
    return TitleBlockProfile(
        edition=edition,
        standard=d["standard"],
        verified=bool(d.get("verified", False)),
        width=width,
        frame={k: float(v) for k, v in d["frame"].items()},
        change_columns={int(g): tuple(c) for g, c in d["columns"]["changes"].items()},
        sign_columns={int(g): tuple(c) for g, c in d["columns"]["signatures"].items()},
        header_labels=tuple(d["header"]["labels"]),
        header_roles={int(g): tuple(p) for g, p in d["header"]["roles"].items()},
        label_graphs={name: int(g) for name, g in d["label_graphs"].items()},
        format_labels=tuple(d["format"]["labels"]),
        forms=forms,
    )


@cache
def titleblock_profile(edition: str) -> TitleBlockProfile:
    """Профиль основных надписей редакции СПДС («2020», «97») без слоя организации."""
    return _profile(edition, _edition(edition))


@dataclass(frozen=True)
class SectionInfo:
    number: int
    title: str
    codes: tuple[str, ...]
    subsections: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Codebook:
    """Справочник шифров: разделы и шифры документов юрисдикции, марки и хвосты обозначений редакции СПДС."""

    jurisdiction: str
    nonlinear: tuple[SectionInfo, ...]
    linear: tuple[SectionInfo, ...]
    legacy: dict[str, str]
    codes: dict[str, str]  # шифры документов юрисдикции без номера раздела (РК)
    marks: dict[str, str]
    tails: dict[str, str]

    @cached_property
    def section_codes(self) -> frozenset[str]:
        numbered = frozenset(c for s in self.nonlinear + self.linear for c in s.codes)
        return numbered | frozenset(self.legacy) | frozenset(self.codes)

    @cached_property
    def all_codes(self) -> frozenset[str]:
        """Всё, что может стоять шифром в обозначении: разделы и шифры юрисдикции, марки РД."""
        return self.section_codes | frozenset(self.marks)

    def section(self, code: str, linear: bool = False) -> SectionInfo | None:
        for s in self.linear if linear else self.nonlinear:
            if code in s.codes:
                return s
        return None


def _codebook(jurisdiction: str, sections: dict, spds: dict) -> Codebook:
    def numbered(key: str) -> tuple[SectionInfo, ...]:
        return tuple(
            SectionInfo(
                number=s["number"],
                title=s["title"],
                codes=tuple(s["codes"]),
                subsections={x["number"]: x["title"] for x in s.get("subsections", [])},
            )
            for s in sections.get(key, [])
        )

    return Codebook(
        jurisdiction=jurisdiction,
        nonlinear=numbered("nonlinear"),
        linear=numbered("linear"),
        legacy=dict(sections.get("legacy", {})),
        codes=dict(sections.get("codes", {})),
        marks={**spds.get("marks", {}), **sections.get("marks", {})},
        tails={**spds.get("tails", {}), **sections.get("tails", {})},
    )


@dataclass(frozen=True)
class Context:
    """Что извлечению нужно знать о пакете: юрисдикция и её справочник, редакция СПДС с профилем, организация."""

    jurisdiction: str  # «kz»
    title: str  # «Казахстан»
    chain: tuple[str, ...]  # пакеты от корня: («core», «spds», «kz»)
    codebook: Codebook
    titleblocks: TitleBlockProfile
    org_forms: tuple[str, ...]  # формы юрлиц: «ТОО», «ООО» …
    organization: str | None = None  # слой организации, если подключён

    @property
    def edition(self) -> str:
        """Редакция СПДС: «97»."""
        return self.titleblocks.edition

    @property
    def standard(self) -> str:
        """Стандарт редакции: «ГОСТ 21.101-97»."""
        return self.titleblocks.standard

    @cached_property
    def org(self) -> re.Pattern[str]:
        """Признак наименования организации в основной надписи: форма юрлица или кавычки."""
        forms = "|".join(re.escape(f) for f in sorted(self.org_forms, key=len, reverse=True))
        return re.compile(rf"\b(?:{forms})\b|«|\"" if forms else r"«|\"")


def _chain(pack: str) -> tuple[str, ...]:
    """Пакеты от корня до этого: по полю parent манифестов; у корня манифеста может не быть."""
    out: list[str] = []
    while pack and pack not in out:
        out.insert(0, pack)
        pack = (_manifest(pack) or {}).get("parent")
    return tuple(out)


@cache
def context(jurisdiction: str, organization: str | Path | None = None) -> Context:
    """Контекст пакета: пакет юрисдикции, его редакция СПДС и, если задан, слой организации (путь к TOML)."""
    if jurisdiction not in available():
        raise ValueError(f"нет пакета юрисдикции «{jurisdiction}»; есть: {', '.join(available())}")
    manifest = _manifest(jurisdiction) or {}
    org = tomllib.loads(Path(organization).read_text(encoding="utf-8")) if organization else {}
    edition = manifest["spds"]
    spds = merge(_edition(edition), org.get("spds", {}))
    sections = merge(_load(jurisdiction, "sections.toml"), org.get("sections", {}))
    return Context(
        jurisdiction=jurisdiction,
        title=manifest.get("title", jurisdiction),
        chain=_chain(jurisdiction),
        codebook=_codebook(jurisdiction, sections, spds),
        titleblocks=_profile(edition, spds),
        org_forms=(*manifest.get("org_forms", ()), *org.get("org_forms", ())),
        organization=org.get("name", Path(organization).stem) if organization else None,
    )


def codebook(jurisdiction: str) -> Codebook:
    """Справочник шифров юрисдикции без слоя организации."""
    return context(jurisdiction).codebook
