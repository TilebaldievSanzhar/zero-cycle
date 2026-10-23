"""Чтение PDF: страницы, слова с координатами и отрезки линий (pdfplumber, MIT).

Слова нужны для основных надписей и текста, отрезки — чтобы найти рамку основной надписи по линиям.
Страница без текстового слоя — скан: has_text=False, разбор — OCR на ступени 1.
Координаты — в пт от левого верхнего угла, как во всём движке.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

MIN_SEGMENT = 2.0  # пт: короче — штриховка и символы, не линии таблиц


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float = 0.0  # кегль, пт

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class Segment:
    """Горизонтальный или вертикальный отрезок: стороны прямоугольников, линии, отрезки кривых."""

    x0: float
    top: float
    x1: float
    bottom: float


@dataclass
class PdfPage:
    number: int  # с 1
    width: float
    height: float
    words: list[Word] = field(default_factory=list)
    hsegs: list[Segment] = field(default_factory=list)
    vsegs: list[Segment] = field(default_factory=list)
    chars: int = 0
    images: int = 0

    @property
    def has_text(self) -> bool:
        return self.chars > 0


def iter_pdf(path: Path) -> Iterator[PdfPage]:
    """Страницы по одной: большие тома не держим в памяти целиком."""
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            yield _read_page(page, i)
            page.close()


def read_pdf(path: Path) -> list[PdfPage]:
    return list(iter_pdf(path))


def _read_page(page, number: int) -> PdfPage:
    words = [
        Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"], round(w.get("size", 0.0), 2))
        for w in page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, extra_attrs=["size"])
    ]
    hsegs, vsegs = [], []
    for e in page.edges:
        seg = Segment(e["x0"], e["top"], e["x1"], e["bottom"])
        if e["orientation"] == "h" and seg.x1 - seg.x0 >= MIN_SEGMENT:
            hsegs.append(seg)
        elif e["orientation"] == "v" and seg.bottom - seg.top >= MIN_SEGMENT:
            vsegs.append(seg)
    return PdfPage(
        number=number,
        width=float(page.width),
        height=float(page.height),
        words=words,
        hsegs=hsegs,
        vsegs=vsegs,
        chars=len(page.chars),
        images=len(page.images),
    )
