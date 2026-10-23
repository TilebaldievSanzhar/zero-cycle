"""Засеянный корпус: пакет по сиду — описание, дефекты, файлы и эталон.

Раскладка: <out_root>/seed-NNNN/package/ — сам пакет (его и только его видят руки бенчмарка);
рядом ground_truth.json (дефекты) и pages.jsonl (что вписано в каждую основную надпись).
"""

from __future__ import annotations

import random
from pathlib import Path

from zerocycle.bench.defects import CATALOG, inject
from zerocycle.bench.generator import build, render

MIN_DEFECTS, MAX_DEFECTS = 4, 8


def generate(seed: int, out_root: Path, mode: str = "auto", codes: list[str] | None = None) -> Path:
    """mode: «auto» — 4–8 случайных дефектов, «clean» — без дефектов; codes — явный список кодов."""
    spec = build(seed)
    rng = random.Random(seed * 7919 + 17)
    if codes:
        chosen = codes
    elif mode == "clean":
        chosen = []
    else:
        chosen = rng.sample(sorted(CATALOG), rng.randint(MIN_DEFECTS, MAX_DEFECTS))
    records = inject(spec, rng, chosen)
    return render(spec, Path(out_root) / f"seed-{seed:04d}", records)
