"""Индекс норм РФ из реестра «Стройкомплекс.РФ» → src/zerocycle/packs/ru/stroykompleks.json (PLAN, шаг 0.7).

    uv run python -X utf8 scripts/stroykompleks_index.py [../bim-data/datasets/stroykompleks/ntd_documents.json]

Выгрузка реестра — `bim-data/scripts/stroykompleks.py docs` (открытый API, 1 046 документов на 21.10.2026). В индекс
попадают своды правил, ГОСТ, ГОСТ Р и СанПиН: действующие записи, их прежние редакции и актуализированные СНиП.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from zerocycle.norms.stroykompleks import build

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src/zerocycle/packs/ru/stroykompleks.json"
SOURCE = ROOT.parent / "bim-data/datasets/stroykompleks/ntd_documents.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("registry", nargs="?", default=str(SOURCE))
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    index = build(Path(a.registry))
    Path(a.out).write_text(index.model_dump_json(indent=1, exclude_none=True), encoding="utf-8")
    kinds = Counter(r.kind for r in index.records)
    print(f"{a.out}: записей {len(index.records)}; по видам: {dict(kinds.most_common())}")
    editions = sum(1 for r in index.records for c in r.replaces if not c.startswith("СНиП"))
    snips = sum(1 for r in index.records for c in r.replaces if c.startswith("СНиП"))
    print(f"заменённых редакций: {editions}; актуализированных СНиП: {snips}")


if __name__ == "__main__":
    main()
