"""Индекс норм РФ из реестра «Стройкомплекс.РФ» (`bim-data/datasets/stroykompleks/ntd_documents.json`).

Реестр перечисляет документы с обязательными требованиями, по которым с 01.03.2026 работает экспертиза: своды правил,
часть ГОСТ и ГОСТ Р, СанПиН, а также законы, постановления и ФНиП. Добровольных стандартов (СПДС) в нём нет, поэтому
индекс не исчерпывающий: «нет в реестре» ≠ «не действует».

Статус записи: 40 — действующая запись, 50 — прежняя версия записи (у СП 54.13330.2022 есть обе), 60 — не расшифрован.
Замена редакции видна так: у того же номера есть более новый год со статусом 40, а старый год — только со статусом 50
(СП 6.13130.2021 → СП 6.13130.2025). Наименование свода правил называет актуализированный СНиП («… Актуализированная
редакция СНиП 2.04.02-84*»), поэтому ссылка на этот СНиП — ссылка на заменённый документ. Даты в API — дни
от 30.12.1899 (`bim-data/datasets/README.md`).

Сборка — `scripts/stroykompleks_index.py`; результат — `packs/ru/stroykompleks.json`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from zerocycle.ingest.files import sha256_file
from zerocycle.norms.designation import NormDesignation, find_designations
from zerocycle.norms.index import NormIndex, NormRecord

# вид в реестре → вид в обозначении; законы, постановления, ФНиП и ТР ЕАЭС обозначаются иначе и пока не разбираются
KINDS = {"Свод правил": "СП", "ГОСТ Р": "ГОСТ Р", "ГОСТ": "ГОСТ", "СанПиН": "СанПиН"}
# СНиП реестр не перечисляет, но знает, какой свод правил его актуализировал
COVERS = ["СП", "ГОСТ Р", "ГОСТ", "СанПиН", "СНиП"]
CURRENT, PREVIOUS = 40, 50
_EPOCH = date(1899, 12, 30)


def api_date(days: int | None) -> date | None:
    """Дата API «Стройкомплекс.РФ»: 46244 → 10.08.2026."""
    return _EPOCH + timedelta(days=days) if days else None


def designation_of(doc: dict) -> NormDesignation | None:
    """Обозначение документа реестра: вид + номер («Свод правил», «6.13130.2025» → СП 6.13130.2025); без года — None.

    В номерах встречается мусор: «СП 523.1325800.2023» с видом в номере, «535.1325800.202» без последней цифры года.
    """
    kind = KINDS.get(doc.get("kindTitle") or "")
    number = (doc.get("number") or "").strip()
    if not kind or not number:
        return None
    found = find_designations(f"{kind} {number}")
    if not found or found[0][0].year is None or found[0][0].kind != kind:
        return None
    return found[0][0]


def _record(d: NormDesignation, doc: dict) -> NormRecord:
    since = api_date(doc.get("effectiveDate"))
    return NormRecord(
        code=d.code,
        kind=d.kind,
        number=d.number,
        year=d.year,
        title=" ".join((doc.get("title") or "").split()) or None,
        since=f"{since:%d.%m.%Y}" if since else None,
        source_id=doc.get("id"),
    )


def _snips(title: str) -> list[str]:
    """СНиП, который актуализирует свод правил: «… Актуализированная редакция СНиП 2.04.02-84*» → «СНиП 2.04.02-84»."""
    return [d.code for d, _, _ in find_designations(title or "") if d.kind == "СНиП" and d.year is not None]


def build(path: Path) -> NormIndex:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    stems: dict[tuple[str, str], list[tuple[NormDesignation, dict]]] = defaultdict(list)
    for doc in raw["documents"]:
        d = designation_of(doc)
        if d is not None and doc.get("status") in (CURRENT, PREVIOUS):
            stems[d.key].append((d, doc))
    records: list[NormRecord] = []
    for entries in stems.values():
        current: dict[int, NormRecord] = {}
        for d, doc in entries:
            if doc["status"] == CURRENT and d.year not in current:
                current[d.year] = _record(d, doc)
        if not current:
            continue  # только прежняя запись без действующей: что с документом, реестр не говорит
        newest = current[max(current)]
        replaced = {d.code for d, doc in entries if d.year not in current and d.year < newest.year}
        snips = {s for _, doc in entries for s in _snips(doc.get("title"))}
        newest.replaces = sorted(replaced | snips)
        records.extend(current.values())
    as_of = date.fromisoformat(str(raw.get("fetched", ""))[:10])
    return NormIndex(
        jurisdiction="ru",
        source=(
            f"Реестр требований «Стройкомплекс.РФ»: документы с обязательными требованиями, выгрузка {as_of:%d.%m.%Y} "
            f"({raw.get('total', len(raw['documents']))} записей; {raw.get('source', '')})"
        ),
        as_of=as_of,
        sha256=sha256_file(Path(path)),
        covers=COVERS,
        records=sorted(records, key=lambda r: (r.kind, r.number, r.year or 0)),
        exhaustive=False,
    )
