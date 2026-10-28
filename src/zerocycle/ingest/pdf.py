"""Чтение PDF: страницы, слова с координатами и отрезки линий (pdfplumber, MIT).

Слова нужны для основных надписей и текста, отрезки — чтобы найти рамку основной надписи по линиям.
Страница без текстового слоя — скан: has_text=False, разбор — OCR на ступени 1.
Координаты — в пт от левого верхнего угла, как во всём движке.

Поворот страницы:
- /Rotate pdfplumber учитывает сам: размеры и координаты — как при показе.
- Лист боком без /Rotate: альбомный чертёж «напечатан в PDF» на книжную страницу. Если большая часть символов
  идёт снизу вверх или сверху вниз, к странице прикладывается повёрнутый вариант — PdfPage.turned.
  Принимает его опись, и только если основная надпись находится на нём, а на исходной странице — нет.
  Вертикальный текст бывает и на ровном листе: поле для подшивки у чертежа с текстом в кривых, штамп ЭЦП на
  скане, отметки в таблице профиля. У повёрнутого варианта координаты — в системе повёрнутого листа, угол —
  в turn; тот же вид даёт рендер pypdfium2 render(rotation=turn).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from pdfplumber.utils import extract_words

MIN_SEGMENT = 2.0  # пт: короче — штриховка и символы, не линии таблиц
MIN_TEXT_CHARS = 20  # меньше — текстового слоя нет: скан или текст САПР переведён в кривые (нужен OCR)
TURN_SHARE = 0.6  # доля символов не по горизонтали, начиная с которой лист читаем повёрнутым боком
WORDS = {"x_tolerance": 1.5, "y_tolerance": 2, "keep_blank_chars": False, "extra_attrs": ["size"]}


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
    turn: int = 0  # на сколько градусов по часовой повёрнут лист боком, чтобы читать (0, 90, 270)
    turned: PdfPage | None = None  # тот же лист, повёрнутый по преобладающему направлению текста

    @property
    def has_text(self) -> bool:
        return self.chars >= MIN_TEXT_CHARS


def iter_pdf(path: Path) -> Iterator[PdfPage]:
    """Страницы по одной: большие тома не держим в памяти целиком."""
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            yield _read_page(page, i)
            page.close()


def read_pdf(path: Path) -> list[PdfPage]:
    return list(iter_pdf(path))


def _read_page(page, number: int) -> PdfPage:
    out = _page(page, number, 0)
    if turn := _turn(page.chars):
        out.turned = _page(page, number, turn)
    return out


def _page(page, number: int, turn: int) -> PdfPage:
    width, height = float(page.width), float(page.height)
    if turn:  # лист боком: символы и линии — в систему повёрнутого листа, слова — заново
        chars = []
        for c in page.chars:
            box = _turned(c, turn, width, height)
            # кегль повёрнутого символа pdfminer пишет шириной глифа — у каждой буквы свой, и слова рассыпаются
            size = box["bottom"] - box["top"] if not c["upright"] else c["size"]
            chars.append({**c, **box, "upright": not c["upright"], "size": size, "doctop": box["top"]})
        raw_words = extract_words(chars, **WORDS)
        edges = [
            {**e, **_turned(e, turn, width, height), "orientation": "v" if e["orientation"] == "h" else "h"}
            for e in page.edges
        ]
        width, height = height, width
    else:
        raw_words, edges = page.extract_words(**WORDS), page.edges
    words = [Word(w["text"], w["x0"], w["top"], w["x1"], w["bottom"], round(w.get("size", 0.0), 2)) for w in raw_words]
    hsegs, vsegs = [], []
    for e in edges:
        seg = Segment(e["x0"], e["top"], e["x1"], e["bottom"])
        if e["orientation"] == "h" and seg.x1 - seg.x0 >= MIN_SEGMENT:
            hsegs.append(seg)
        elif e["orientation"] == "v" and seg.bottom - seg.top >= MIN_SEGMENT:
            vsegs.append(seg)
    return PdfPage(
        number=number,
        width=width,
        height=height,
        words=words,
        hsegs=hsegs,
        vsegs=vsegs,
        chars=len(page.chars),
        images=len(page.images),
        turn=turn,
    )


def _turn(chars: list[dict]) -> int:
    """Похоже на лист боком: большинство символов не по горизонтали. Текст снизу вверх — повернуть по часовой (90),
    сверху вниз — против часовой (270). Это только предложение: повёрнутый вариант принимает опись (scan.read_sheet)."""
    rotated = [c for c in chars if not c.get("upright", True)]
    if len(rotated) < MIN_TEXT_CHARS or len(rotated) < TURN_SHARE * len(chars):
        return 0
    return 90 if sum(c["matrix"][1] for c in rotated) > 0 else 270


def _turned(o: dict, turn: int, width: float, height: float) -> dict[str, float]:
    """Рамка объекта на листе, повёрнутом по часовой на turn градусов; width, height — размеры до поворота."""
    if turn == 90:  # левый край становится верхним: (x, y) → (height − y, x)
        return {"x0": height - o["bottom"], "x1": height - o["top"], "top": o["x0"], "bottom": o["x1"]}
    # 270 — правый край становится верхним: (x, y) → (y, width − x)
    return {"x0": o["top"], "x1": o["bottom"], "top": width - o["x1"], "bottom": width - o["x0"]}
