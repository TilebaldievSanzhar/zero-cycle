"""Контекст проверки: пакет после описи, тексты страниц, ссылки на нормы, параметры юрисдикции.

Тексты страниц движок читает сам и лениво (pypdfium2 — быстро и с позициями символов): опись хранит основные
надписи, а правилам нужны начало каждой страницы (по нему узнаются документы ИРД) и ссылки на нормы с рамкой
на листе. В фикстурах тексты подаются готовыми, без файлов.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium

from zerocycle.extract.designation import parse
from zerocycle.model import Anchor, BBox, Designation, Document, Media, Package, Page, TitleBlock
from zerocycle.norms.designation import NormDesignation, find_designations
from zerocycle.packs import Codebook, available
from zerocycle.packs import context as pack_context

# Редакция СПДС по юрисдикции — пока общий контекст юрисдикции не отдаёт её сам (параллельный рефакторинг)
HEAD_CHARS = 300  # начало страницы: заголовок документа, а не упоминание в тексте
TITLE_LINES = 10  # строк заголовка: у документов РК шапка на двух языках
_WORK = (
    ("capital_repair", re.compile(r"капитальн\w*\s+ремонт", re.IGNORECASE)),
    ("reconstruction", re.compile(r"реконструкци|расширени\w*\s+(?:здани|объект)|модернизаци", re.IGNORECASE)),
    ("new", re.compile(r"\bстроительств\w*\b|\bстроительство\b", re.IGNORECASE)),
)


@dataclass
class NormRefHit:
    designation: NormDesignation
    anchor: Anchor


@dataclass
class Listed:
    """Строка ведомости «Состав проектной документации»: обозначение тома, лист ведомости и сама строка."""

    designation: Designation
    anchor: Anchor
    line: str


# «Состав проектной документации» (ГОСТ Р 21.101-2020, 8.1.6), «Состав проекта», «Состав рабочего проекта» (РК)
_COMPOSITION = re.compile(r"состав\w*\s+(?:проектной\s+документации|(?:рабочего\s+)?проекта)\b", re.IGNORECASE)


_LIST_ITEM = re.compile(r"^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]|[-–—•·])\s")


@dataclass
class PageText:
    number: int
    text: str
    refs: list[NormRefHit] = field(default_factory=list)

    @property
    def head(self) -> str:
        return " ".join(self.text[: HEAD_CHARS * 2].split())[:HEAD_CHARS]

    @property
    def title(self) -> str:
        """Заголовок страницы — первые строки без пунктов списка: «АРХИТЕКТУРНО-ПЛАНИРОВОЧНОЕ ЗАДАНИЕ» — заголовок,
        «1. Задание на проектирование № …» в перечне исходных данных — упоминание."""
        lines = [s.strip() for s in self.text.splitlines() if s.strip()][:TITLE_LINES]
        return " ".join(s for s in lines if not _LIST_ITEM.match(s))[:HEAD_CHARS]


class Context:
    def __init__(
        self,
        package: Package,
        *,
        jurisdiction: str | None = None,
        files: Mapping[str, Path] | None = None,
        texts: Mapping[tuple[str, int], str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.package = package
        self.jurisdiction = jurisdiction or package.jurisdiction
        self.files = dict(files or {})  # путь документа в пакете → файл на диске
        self.params: dict[str, Any] = dict(params or {})
        self.sources: dict[str, str] = {}  # какие данные взяли правила: индекс норм и его версия — в запись прогона
        self._pages: dict[str, dict[int, PageText]] = {}
        self._docs = {d.path: d for d in package.documents}
        for (path, number), text in (texts or {}).items():
            doc = self._docs.get(path)
            page = PageText(number, text)
            if doc is not None:
                page.refs = [
                    NormRefHit(d, self.anchor(doc, number, quote=d.text)) for d, _, _ in find_designations(text)
                ]
            self._pages.setdefault(path, {})[number] = page
        self._loaded: set[str] = set(self._pages)

    # --- пакет -------------------------------------------------------------------------------------------------

    @property
    def edition(self) -> str | None:
        """Стандарт СПДС, по которому судим об оформлении: «ГОСТ 21.101-97» для РК, «ГОСТ Р 21.101-2020» для РФ.

        Берётся из контекста пакета юрисдикции (`zerocycle.packs.context`): там редакция профиля основных надписей.
        """
        if self.params.get("spds_edition"):
            return str(self.params["spds_edition"])
        return pack_context(self.jurisdiction).standard if self.jurisdiction in available() else None

    @property
    def work_kind(self) -> str | None:
        """Вид работ: задан явно, записан в пакете или понятен из наименования объекта в основных надписях."""
        if self.params.get("work_kind"):
            return str(self.params["work_kind"])
        if self.package.work_kind:
            return self.package.work_kind.value
        votes: Counter[str] = Counter()
        for _, _, tb in self.title_blocks():
            name = " ".join((tb.get(2), tb.get(3)))
            kind = next((k for k, rx in _WORK if rx.search(name)), None)
            if kind:
                votes[kind] += 1
        if not votes:
            return None
        (top, n), *rest = votes.most_common(2)
        return top if not rest or n > rest[0][1] else None

    def pdfs(self) -> list[Document]:
        return [d for d in self.package.documents if d.media == Media.pdf]

    def title_blocks(self) -> Iterator[tuple[Document, Page, TitleBlock]]:
        for doc in self.package.documents:
            for page in doc.pages:
                if page.title_block is not None:
                    yield doc, page, page.title_block

    def anchor(
        self, doc: Document, page: int | None = None, bbox: BBox | None = None, quote: str | None = None
    ) -> Anchor:
        return Anchor(file=doc.path, sha256=doc.sha256, page=page, bbox=bbox, quote=quote)

    def graph_anchor(self, doc: Document, page: Page, tb: TitleBlock, graph: int) -> Anchor:
        """Якорь на графу основной надписи: рамка ячейки, если опись её знает, иначе — рамка всей надписи."""
        return self.anchor(doc, page.number, tb.cells.get(graph, tb.bbox), tb.get(graph) or None)

    # --- тексты ------------------------------------------------------------------------------------------------

    def pages_of(self, doc: Document) -> dict[int, PageText]:
        """Тексты страниц документа (страницы без текстового слоя отсутствуют)."""
        if doc.path not in self._loaded:
            self._load(doc)
        return self._pages.get(doc.path, {})

    def text_pages(self) -> Iterator[tuple[Document, PageText]]:
        for doc in self.pdfs():
            for page in self.pages_of(doc).values():
                yield doc, page

    def norm_refs(self) -> list[NormRefHit]:
        """Все ссылки на нормативные документы в текстах пакета — с якорем на лист и рамкой обозначения."""
        return [hit for _, page in self.text_pages() for hit in page.refs]

    def textless(self) -> list[tuple[Document, int]]:
        """Страницы без текстового слоя по описи (сканы, текст в кривых) — для честного «проверить вручную»."""
        return [(doc, p.number) for doc in self.pdfs() for p in doc.pages if not p.has_text]

    @property
    def codebook(self) -> Codebook:
        return pack_context(self.jurisdiction).codebook

    def composition_docs(self) -> list[Document]:
        """Ведомость «Состав проектной документации»: документ «…-СП» или документ с таким наименованием."""
        return [
            d
            for d in self.pdfs()
            if (d.designation and d.designation.suffix == "СП") or (d.title and _COMPOSITION.search(d.title))
        ]

    def composition(self) -> list[Listed]:
        """Тома, записанные в ведомости «Состав проектной документации»: обозначение в начале строки ведомости.

        Ведомость — отдельный документ или страница с таким заголовком внутри тома (в РК — «Состав проекта» в ОПЗ).
        Своё обозначение ведомости («…-СП») и содержаний томов («…-С») в основной надписи — не строки ведомости.
        """
        cb = self.codebook
        own = {d.path for d in self.composition_docs()}
        out: list[Listed] = []
        seen: set[str] = set()
        for d in self.pdfs():
            for page in self.pages_of(d).values():
                if d.path not in own and not _COMPOSITION.search(page.title):
                    continue
                for line in (s.strip() for s in page.text.splitlines()):
                    for token in line.split()[:3]:
                        des = parse(token.strip('«»"(),;:'), cb)
                        if des is None or des.suffix or des.code is None or not re.search(r"\d", des.base):
                            continue
                        if des.core not in seen:
                            seen.add(des.core)
                            out.append(Listed(des, self.anchor(d, page.number, quote=line), line))
                        break
        return out

    def _load(self, doc: Document) -> None:
        self._loaded.add(doc.path)
        path = self.files.get(doc.path)
        if path is None:
            return
        pages: dict[int, PageText] = {}
        try:
            pdf = pdfium.PdfDocument(path)
        except Exception:  # опись уже записала, что файл не читается
            return
        try:
            for i in range(len(pdf)):
                page = pdf[i]
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range()
                    if len(text.strip()) < 20:  # как ingest.pdf.MIN_TEXT_CHARS: меньше — текстового слоя нет
                        continue
                    height = page.get_size()[1]
                    pt = PageText(i + 1, text)
                    for d, a, b in find_designations(text):
                        pt.refs.append(
                            NormRefHit(d, self.anchor(doc, i + 1, _span_box(textpage, text, a, b, height), d.text))
                        )
                    pages[i + 1] = pt
                finally:
                    textpage.close()
                    page.close()
        finally:
            pdf.close()
        self._pages[doc.path] = pages


def _span_box(textpage: pdfium.PdfTextPage, text: str, start: int, end: int, height: float) -> BBox | None:
    """Рамка символов [start, end) в пт от левого верхнего угла (pdfium считает от левого нижнего)."""
    boxes = []
    for k in range(start, min(end, len(text))):
        if text[k].isspace():
            continue
        left, bottom, right, top = textpage.get_charbox(k)
        if right > left:
            boxes.append((left, height - top, right, height - bottom))
    if not boxes:
        return None
    return (
        round(min(b[0] for b in boxes), 1),
        round(min(b[1] for b in boxes), 1),
        round(max(b[2] for b in boxes), 1),
        round(max(b[3] for b in boxes), 1),
    )
