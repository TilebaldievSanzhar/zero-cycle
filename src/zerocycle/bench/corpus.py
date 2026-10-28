"""Засеянный корпус: пакет по сиду — описание, дефекты, файлы и эталон.

Раскладка: <out_root>/seed-NNNN/package/ — сам пакет (его и только его видят руки бенчмарка);
рядом ground_truth.json (дефекты) и pages.jsonl (что вписано в каждую основную надпись).

Генератор v1 (version=1, шаг 0.4b) кладёт пакет в <out_root>/v1-<split>-NNNN/. Выборка split="heldout" —
отложенная часть слоя S: шаблоны и формулировки, которых нет в разработке (docs/bench/PROTOCOL.md).
"""

from __future__ import annotations

import random
from pathlib import Path

from zerocycle.bench.defects import CATALOG, inject
from zerocycle.bench.generator import build, render

MIN_DEFECTS, MAX_DEFECTS = 4, 8
V1_DEFECTS = 5, 10  # пакет v1 крупнее: томов больше, дефектов тоже


def generate(
    seed: int,
    out_root: Path,
    mode: str = "auto",
    codes: list[str] | None = None,
    version: int = 0,
    split: str = "dev",
    size: str = "full",
    rates: dict | None = None,
) -> Path:
    """mode: «auto» — случайные дефекты, «clean» — без дефектов; codes — явный список кодов.

    version=1 — генератор v1: split «dev» | «heldout», size «full» (300–1 500 листов) | «small» (для тестов),
    rates — доли поворотов, уменьшений и отвлекающего содержимого (bench/v1.py, RATES).
    """
    if version == 1:
        from zerocycle.bench import v1

        spec = v1.build(seed, split, size, rates)
        rng = random.Random(f"zerocycle-v1-defects:{split}:{seed}")
        lo, hi = V1_DEFECTS
        name = f"v1-{split}-{seed:04d}"
    else:
        spec = build(seed)
        rng = random.Random(seed * 7919 + 17)
        lo, hi = MIN_DEFECTS, MAX_DEFECTS
        name = f"seed-{seed:04d}"
    if codes:
        chosen = codes
    elif mode == "clean":
        chosen = []
    else:
        chosen = rng.sample(sorted(CATALOG), rng.randint(lo, hi))
    records = inject(spec, rng, chosen)
    return render(spec, Path(out_root) / name, records)
