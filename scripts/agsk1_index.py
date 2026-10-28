"""Индекс норм РК из PDF АГСК-1 → src/zerocycle/packs/kz/agsk1.json (PLAN, шаг 0.7k).

    uv run python -X utf8 scripts/agsk1_index.py <АГСК-1.pdf> [--as-of 2025-06-20] [--source "…"]

PDF — официальное издание КДСиЖКХ МПС РК; в репозиторий кладём только индекс (обозначения, наименования, замены,
даты введения, страницы), сам файл — в var/. Где взять: перечень публикуется ежегодно; издание «по состоянию на
июнь 2025 года» (приказ КДСиЖКХ МПС РК от 20.06.2025 № 97-НҚ) скачано 27.10.2026 с smeta-kz.com/normativy-rk/agsk/.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from pathlib import Path

from zerocycle.norms.agsk1 import build

OUT = Path(__file__).resolve().parents[1] / "src/zerocycle/packs/kz/agsk1.json"
SOURCE = (
    "АГСК-1 «Перечень нормативных правовых актов и нормативных технических документов в области архитектуры, "
    "градостроительства и строительства, действующих на территории Республики Казахстан» по состоянию на июнь "
    "2025 года, утверждён приказом председателя КДСиЖКХ МПС РК от 20.06.2025 № 97-НҚ"
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--as-of", default="2025-06-20")
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    index = build(Path(a.pdf), a.source, date.fromisoformat(a.as_of))
    Path(a.out).write_text(index.model_dump_json(indent=1), encoding="utf-8")
    kinds = Counter(r.kind for r in index.records)
    print(f"{a.out}: записей {len(index.records)}; по видам: {dict(kinds.most_common())}")
    replacing = sum(1 for r in index.records if r.replaces)
    print(f"с заменами: {replacing}; без года: {sum(r.year is None for r in index.records)}")


if __name__ == "__main__":
    main()
