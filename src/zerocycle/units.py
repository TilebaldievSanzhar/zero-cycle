"""Миллиметры, пункты PDF и форматы листов по ГОСТ 2.301."""

from __future__ import annotations

import re

PT_PER_MM = 72 / 25.4


def pt(mm_: float) -> float:
    return mm_ * PT_PER_MM


def mm(pt_: float) -> float:
    return pt_ / PT_PER_MM


# Основные форматы: короткая и длинная сторона, мм
BASE_FORMATS: dict[str, tuple[int, int]] = {
    "A0": (841, 1189),
    "A1": (594, 841),
    "A2": (420, 594),
    "A3": (297, 420),
    "A4": (210, 297),
}
# Дополнительные форматы (ГОСТ 2.301, табл. 2): короткая сторона — длинная сторона основного формата,
# длинная — короткая сторона основного × n; например, A4x3 = 297 × 630.
EXTRA_MULTIPLES: dict[str, tuple[int, ...]] = {
    "A0": (2, 3),
    "A1": (3, 4),
    "A2": (3, 4, 5),
    "A3": (3, 4, 5, 6, 7),
    "A4": (3, 4, 5, 6, 7, 8, 9),
}


def format_size(name: str) -> tuple[float, float] | None:
    """«A4x3» → (297, 630) мм: короткая и длинная сторона."""
    m = re.fullmatch(r"(A[0-4])(?:x(\d))?", name)
    if not m or m.group(1) not in BASE_FORMATS:
        return None
    short, long_ = BASE_FORMATS[m.group(1)]
    if not m.group(2):
        return float(short), float(long_)
    n = int(m.group(2))
    if n not in EXTRA_MULTIPLES[m.group(1)]:
        return None
    return float(long_), float(short * n)


def paper_format(width_pt: float, height_pt: float, tol_mm: float = 3.0) -> str | None:
    """Формат листа по размеру страницы; None — нестандартный размер."""
    short, long_ = sorted((mm(width_pt), mm(height_pt)))
    names = list(BASE_FORMATS) + [f"{b}x{n}" for b, ns in EXTRA_MULTIPLES.items() for n in ns]
    for name in names:
        size = format_size(name)
        if size and abs(short - size[0]) <= tol_mm and abs(long_ - size[1]) <= tol_mm:
            return name
    return None


_FORMAT_TEXT = re.compile(r"^\s*[AА]\s*([0-4])\s*(?:[xхХ×*]\s*(\d))?\s*$")


def normalize_format(text: str) -> str | None:
    """Запись формата в графе 26 → каноническое имя: «А4» → «A4», «А3х3» → «A3x3»; иначе None."""
    cleaned = text.replace("Формат", "").strip()
    m = _FORMAT_TEXT.match(cleaned)
    if not m:
        return None
    name = f"A{m.group(1)}" + (f"x{m.group(2)}" if m.group(2) else "")
    return name if format_size(name) else None
