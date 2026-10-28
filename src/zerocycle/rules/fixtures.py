"""Фикстуры правил: короткая сборка пакета-модели и прогон «должно сработать / не должно».

Фикстура не рисует PDF: правило работает над моделью описи и текстами страниц, поэтому пакет собирается из
документов, листов и основных надписей прямо в коде правила, рядом с его метаданными.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from zerocycle.model import Document, Media, Package, Page, SignRow, Status, TitleBlock, WorkKind
from zerocycle.rules.base import Fixture, FixtureCase, Rule
from zerocycle.rules.context import Context

_BOX = (400.0, 700.0, 924.0, 840.0)  # рамка основной надписи А3 в пт — для якорей фикстур


def title_block(graphs: Mapping[int, str], form: int = 3, signatures: Iterable[tuple[str, str]] = ()) -> TitleBlock:
    return TitleBlock(
        form=form,
        bbox=_BOX,
        graphs=dict(graphs),
        signatures=[SignRow(role=role, name=name, date="", bbox=_BOX) for role, name in signatures],
        method="grid",
        confidence=1.0,
    )


def page(number: int = 1, tb: TitleBlock | Mapping[int, str] | None = None, *, has_text: bool = True) -> Page:
    block = title_block(tb) if isinstance(tb, Mapping) else tb
    return Page(number=number, width=1190.55, height=841.89, paper="A3", has_text=has_text, title_block=block)


def doc(path: str, pages: Iterable[Page] | int = 1, *, media: Media = Media.pdf) -> Document:
    sheets = [page(i) for i in range(1, pages + 1)] if isinstance(pages, int) else list(pages)
    sha = hashlib.sha256(path.encode()).hexdigest()
    return Document(path=path, sha256=sha, size=1, media=media, pages=sheets)


def package(*docs: Document, jurisdiction: str = "kz", work_kind: str | None = None) -> Package:
    return Package(
        root="fixture",
        jurisdiction=jurisdiction,
        work_kind=WorkKind(work_kind) if work_kind else None,
        documents=list(docs),
    )


def case(pkg: Package, texts: Mapping[tuple[str, int], str] | None = None, **params: Any) -> FixtureCase:
    return FixtureCase(package=pkg, texts=dict(texts or {}), params=params)


def run_fixture(rule: Rule, fixture: Fixture) -> list[str]:
    """Расхождения фикстуры с ожиданием; пустой список — фикстура зелёная."""
    fc = fixture.build()
    ctx = Context(fc.package, texts=fc.texts, params=fc.params)
    outcomes = list(rule.check(ctx))
    got = Counter(o.status for o in outcomes)
    problems = [
        f"{fixture.name}: {status.value} — ожидалось {n}, получено {got.get(status, 0)}"
        for status, n in fixture.expect.items()
        if got.get(status, 0) != n
    ]
    by_item = {o.item: o.status for o in outcomes if o.item}
    for item, status in fixture.items.items():
        if by_item.get(item) != status:
            got_status = by_item[item].value if item in by_item else "нет исхода"
            problems.append(f"{fixture.name}: {item} — ожидалось {status.value}, получено {got_status}")
    return problems


__all__ = ["Status", "case", "doc", "package", "page", "run_fixture", "title_block"]
