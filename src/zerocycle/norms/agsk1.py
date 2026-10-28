"""Индекс норм РК из АГСК-1 — «Перечень нормативных правовых актов и нормативных технических документов в области
архитектуры, градостроительства и строительства, действующих на территории Республики Казахстан».

Перечень — таблица на двух языках: шифр | наименование | утверждено | МКС; у каждого документа две строки —
казахская («ҚР ҚН 1.02-03-2022») и русская («СН РК 1.02-03-2022»). Горизонтальных линий между строками нет,
поэтому строки собираются по колонкам слов: новая запись — когда в колонке шифра начинается новое обозначение
с прописной буквы; перенос внутри шифра («СН РК 1.02-03-» / «2022») — продолжение. Берём русские строки.

Сборка — `scripts/agsk1_index.py`; результат — `packs/kz/agsk1.json` (обозначения, наименования, замены, даты).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pdfplumber

from zerocycle.ingest.files import sha256_file
from zerocycle.norms.designation import DESIGNATION, find_designations, split_year, twins_to_cyrillic
from zerocycle.norms.index import NormIndex, NormRecord, replaced_codes

# границы колонок таблицы АГСК-1 (июнь 2025), пт: шифр | наименование | утверждено | МКС
COLUMN_EDGES = (159.0, 367.0, 473.0)
HEADER_BOTTOM = 95.0  # низ шапки таблицы — на страницах, где она повторяется
TOP_MARGIN = 40.0  # верх текста на страницах без шапки
LINE_TOLERANCE = 2.5
# виды, которые АГСК-1.1 перечисляет полностью (раздел 1.1.1); ГОСТ в нём нет — они в других каталогах
COVERS = ["СН РК", "СП РК", "СНиП РК", "НТП РК", "РДС РК", "РД РК", "МДС РК", "СНиП", "МСН", "МСП", "ВСН", "СН", "СП"]
_NOTE = re.compile(
    r"\b(?:Введен\w*\s+впервые|Взамен|Внесен\w*\s+(?:изменени|поправк|редакцион)|Изменени\w*\s+внесен|\(Аутентичен|"
    r"Отменен|Соответствует|В\s+новой\s+редакции)"
)
_SINCE = re.compile(r"\bс\s+(\d{2}\.\d{2}\.\d{4})")
# «Взамен ВСН 347-75 и в дополнение (развитие) СНиП РК 1.03-05-2001» — заменён только ВСН 347-75
_NOT_REPLACED = re.compile(r"\s(?:и\s+)?в\s+(?:дополнение|развитие)|\(|«")
# «Соответствует СН РК 1.03-14-2011 (приказ … утративший силу)» — прежний номер того же документа
_SUCCEEDS = re.compile(r"Соответствует\s+(.{0,40}?)\s*\([^)]*утративш")


@dataclass
class _Row:
    page: int
    cipher: list[str] = field(default_factory=list)
    title: list[str] = field(default_factory=list)
    approved: list[str] = field(default_factory=list)


def _lines(page, top: float = HEADER_BOTTOM) -> list[tuple[float, list[str]]]:
    """Строки страницы ниже `top`: (верх, [шифр, наименование, утверждено, МКС])."""
    words = [w for w in page.extract_words(x_tolerance=1.5, y_tolerance=2) if w["top"] > top]
    words.sort(key=lambda w: (round(w["top"] / LINE_TOLERANCE), w["x0"]))
    rows: list[tuple[float, list[list[str]]]] = []
    for w in words:
        if not rows or abs(w["top"] - rows[-1][0]) > LINE_TOLERANCE:
            rows.append((w["top"], [[], [], [], []]))
        col = sum(w["x0"] >= edge for edge in COLUMN_EDGES)
        rows[-1][1][col].append(w["text"])
    return [(top, [" ".join(c) for c in cols]) for top, cols in rows]


def _starts_entry(cipher: str, current: _Row | None) -> bool:
    if not cipher or not cipher[0].isupper():
        return False
    if current is None or not current.cipher:
        return True
    joined = " ".join(current.cipher)
    # «СНиП» / «РК 3.02-…» — шифр ещё без номера: строка продолжает его
    return bool(re.search(r"\d", joined)) and not joined.rstrip().endswith(("-", ".", "/"))


def _cipher_text(parts: list[str]) -> str:
    out = ""
    for p in parts:
        out += p if out.endswith(("-", ".", "/", ":")) else (" " + p if out else p)
    return out


def rows_of(path: Path) -> list[_Row]:
    rows: list[_Row] = []
    started = False
    with pdfplumber.open(path) as pdf:
        for no, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            header = "Наименование" in text[:400]  # шапка таблицы есть не на каждой странице
            started = started or header
            if not started:
                page.close()
                continue  # титул, введение, содержание — до таблицы
            current = rows[-1] if rows else None
            for _, (cipher, title, approved, _mks) in _lines(page, HEADER_BOTTOM if header else TOP_MARGIN):
                if _starts_entry(cipher, current):
                    current = _Row(no)
                    rows.append(current)
                if current is None:
                    continue
                if cipher:
                    current.cipher.append(cipher)
                if title:
                    current.title.append(title)
                if approved:
                    current.approved.append(approved)
            page.close()
    return rows


def record_of(row: _Row) -> NormRecord | None:
    cipher = twins_to_cyrillic(_cipher_text(row.cipher))
    m = DESIGNATION.match(cipher)
    if not m or m.start() != 0:
        return None  # казахская строка, нормативный правовой акт или национальное приложение
    found = find_designations(cipher)
    d = found[0][0]
    if d.year is None:  # «СП РК 2.04-01- 2017»: год отдельным словом
        tail = cipher[found[0][2] :].strip()
        if re.fullmatch(r"-?\s*\d{4}\*?", tail):
            number, year = split_year(d.kind, f"{d.number}-{tail.strip('-* ')}")
            d = d.__class__(d.kind, number, year, cipher)
    title = " ".join(row.title)
    note_at = _NOTE.search(title)
    notes = title[note_at.start() :] if note_at else ""
    title = (title[: note_at.start()] if note_at else title).strip(" .;") or None
    replaces: list[str] = []
    for chunk in re.findall(r"Взамен\s+(.+?)(?=(?:Взамен|Введен|Внесен|\(Аутентичен|Соответствует|$))", notes):
        chunk = _NOT_REPLACED.split(chunk, maxsplit=1)[0]
        replaces += replaced_codes("Взамен " + chunk, d.kind)
    for prior in _SUCCEEDS.findall(notes):
        replaces += [x.code for x, _, _ in find_designations(prior)]
    since = _SINCE.findall(" ".join(row.approved) + " " + notes)
    return NormRecord(
        code=d.code,
        kind=d.kind,
        number=d.number,
        year=d.year,
        title=title,
        replaces=sorted(set(replaces) - {d.code}),
        since=since[-1] if since else None,
        page=row.page,
    )


def build(path: Path, source: str, as_of: date) -> NormIndex:
    records: dict[str, NormRecord] = {}
    for row in rows_of(path):
        rec = record_of(row)
        if rec is not None and rec.code not in records:
            records[rec.code] = rec
    return NormIndex(
        jurisdiction="kz",
        source=source,
        as_of=as_of,
        sha256=sha256_file(path),
        covers=COVERS,
        records=sorted(records.values(), key=lambda r: (r.kind, r.number, r.year or 0)),
    )
