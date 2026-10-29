"""Главная гипотеза H5 и вторичные сравнения рук (docs/bench/PROTOCOL.md): парный бутстреп по пакетам.

Значение руки на пакете — среднее по её прогонам. Recall и точность руки — по всем дефектам и находкам вместе:
найденных дефектов на сумму засеянных, верных «нарушений» на сумму «нарушений». Жаккар — среднее по пакетам.
Сбой прогона (таймаут, ответ не по схеме, потолок долларов) — пустой ответ: продукт, который не ответил, ничего не
нашёл. Прогон, оборванный лимитом подписки, в оценку не входит — драйвер повторяет его после сброса окна.

Интервал — 95%, перцентильный: пакеты выбираются с возвращением, одни и те же для обеих рук пары.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path

from zerocycle.bench.arms import jaccard

N_BOOT = 10_000
SEED = 2026
METRICS = ("recall", "precision", "jaccard")
# главное сравнение и вторичные, без вывода «подтверждено»
PAIRS = [("B", "A1"), ("C", "A0"), ("B", "A0"), ("A1", "A0")]
MIN_RECALL_GAIN = 0.10  # условие 1: разница recall не меньше, нижняя граница интервала больше нуля
MIN_PRECISION_LOW = -0.05  # условие 2: нижняя граница интервала разницы точности больше
MIN_JACCARD_LOW = 0.0  # условие 3: нижняя граница интервала разницы Жаккара больше


@dataclass
class Cell:
    """Рука на пакете: средние по прогонам."""

    seeded: int
    found: float  # найденных дефектов
    hits: float  # верных «нарушений»
    violated: float  # всех «нарушений»
    jaccard: float | None
    runs: int
    failed: int


def _limit(row: dict) -> bool:
    err = (row.get("error") or "").lower()
    return "limit" in err and "hit your" in err


def cells(rows: list[dict]) -> dict[tuple[str, str], Cell]:
    """Строки оценки (score_pilot) → средние руки на пакете."""
    seeded = {r["package"]: r["seeded"] for r in rows if r.get("seeded") is not None}
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if not _limit(r) and r["package"] in seeded:
            groups.setdefault((r["package"], r["arm"]), []).append(r)
    out = {}
    for (pkg, arm), rs in groups.items():
        scored = [r for r in rs if r.get("found") is not None]  # остальные — сбои: пустой ответ
        n = len(rs)
        sets = [set(r.get("keys", [])) for r in rs]
        pairs = [jaccard(x, y) for i, x in enumerate(sets) for y in sets[i + 1 :]]
        out[(pkg, arm)] = Cell(
            seeded=seeded[pkg],
            found=sum(r["found"] for r in scored) / n,
            hits=sum(r["violated"] - r["false_alarms"] for r in scored) / n,
            violated=sum(r["violated"] for r in scored) / n,
            jaccard=sum(pairs) / len(pairs) if pairs else None,
            runs=n,
            failed=n - len(scored),
        )
    return out


def stats(cs: list[Cell]) -> dict[str, float | None]:
    seeded = sum(c.seeded for c in cs)
    violated = sum(c.violated for c in cs)
    jac = [c.jaccard for c in cs if c.jaccard is not None]
    return {
        "recall": sum(c.found for c in cs) / seeded if seeded else None,
        "precision": sum(c.hits for c in cs) / violated if violated else None,
        "jaccard": sum(jac) / len(jac) if jac else None,
    }


@dataclass
class Diff:
    a: float | None
    b: float | None
    diff: float | None
    low: float | None
    high: float | None


def compare(table: dict[tuple[str, str], Cell], a: str, b: str, n: int = N_BOOT, seed: int = SEED):
    """Разница a − b по метрикам на общих пакетах и её парный бутстреп-интервал."""
    packages = sorted({p for p, arm in table if arm == a} & {p for p, arm in table if arm == b})
    if not packages:
        return [], {}
    left, right = [table[(p, a)] for p in packages], [table[(p, b)] for p in packages]
    sa, sb = stats(left), stats(right)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {m: [] for m in METRICS}
    for _ in range(n):
        idx = [rng.randrange(len(packages)) for _ in packages]
        xa, xb = stats([left[i] for i in idx]), stats([right[i] for i in idx])
        for m in METRICS:
            if xa[m] is not None and xb[m] is not None:
                draws[m].append(xa[m] - xb[m])
    out = {}
    for m in METRICS:
        d = sorted(draws[m])
        point = sa[m] - sb[m] if sa[m] is not None and sb[m] is not None else None
        lo, hi = (_quantile(d, 0.025), _quantile(d, 0.975)) if d else (None, None)
        out[m] = Diff(sa[m], sb[m], point, lo, hi)
    return packages, out


def _quantile(xs: list[float], q: float) -> float:
    """Квантиль с линейной интерполяцией (как numpy по умолчанию)."""
    pos = (len(xs) - 1) * q
    i = int(pos)
    return xs[i] + (xs[min(i + 1, len(xs) - 1)] - xs[i]) * (pos - i)


def verdict(d: dict[str, Diff]) -> list[tuple[str, bool | None]]:
    """Три условия H5; None — посчитать нельзя."""

    def ok(cond):
        try:
            return bool(cond())
        except TypeError:
            return None

    r, p, j = d.get("recall"), d.get("precision"), d.get("jaccard")
    return [
        (
            f"recall B − A1 ≥ {MIN_RECALL_GAIN:.2f} и нижняя граница > 0",
            ok(lambda: r.diff >= MIN_RECALL_GAIN - 1e-9 and r.low > 0),  # ровно 0,10 не теряем на округлении
        ),
        (f"нижняя граница разницы точности > −{-MIN_PRECISION_LOW:.2f}", ok(lambda: p.low > MIN_PRECISION_LOW)),
        (f"нижняя граница разницы Жаккара > {MIN_JACCARD_LOW:.0f}", ok(lambda: j.low > MIN_JACCARD_LOW)),
    ]


def report_h5(out: Path, rows: list[dict]) -> str:
    table = cells(rows)
    lines = ["# H5 и сравнения рук: парный бутстреп по пакетам", ""]
    lines += [
        f"Повторов бутстрепа {N_BOOT}, сид {SEED}, интервал 95% перцентильный. Значение руки — среднее по прогонам.",
        "",
    ]
    lines += ["| Рука | Пакетов | Прогонов | Сбоев | Recall | Точность | Жаккар |", "|---|---|---|---|---|---|---|"]
    for arm in sorted({arm for _, arm in table}):
        cs = [c for (_, a), c in table.items() if a == arm]
        s = stats(cs)
        lines.append(
            f"| {arm} | {len(cs)} | {sum(c.runs for c in cs)} | {sum(c.failed for c in cs)} |"
            f" {_f(s['recall'])} | {_f(s['precision'])} | {_f(s['jaccard'])} |"
        )
    lines += [
        "",
        "| Сравнение | Пакетов | Метрика | Первая | Вторая | Разница | 95% интервал |",
        "|---|---|---|---|---|---|---|",
    ]
    results = {}
    for a, b in PAIRS:
        packages, d = compare(table, a, b)
        results[(a, b)] = d
        for m in METRICS if d else ():
            x = d[m]
            lines.append(
                f"| {a} − {b} | {len(packages)} | {m} | {_f(x.a)} | {_f(x.b)} | {_f(x.diff, sign=True)} |"
                f" {_f(x.low, sign=True)} … {_f(x.high, sign=True)} |"
            )
    lines += ["", "## Главная гипотеза H5: B против A1", ""]
    main = results.get(("B", "A1")) or {}
    if not main:
        lines.append("Нет пакетов, пройденных обеими руками.")
    else:
        checks = verdict(main)
        lines += [f"- {'да' if v else 'нет' if v is False else 'не посчитать'}: {text}" for text, v in checks]
        done = all(v for _, v in checks)
        lines += ["", f"**H5 {'подтверждена' if done else 'не подтверждена'}** на {len(_common(table))} пакетах."]
    text = "\n".join(lines) + "\n"
    (out / "h5.md").write_text(text, encoding="utf-8")
    (out / "h5.json").write_text(
        json.dumps(
            {f"{a}-{b}": {m: vars(x) for m, x in d.items()} for (a, b), d in results.items()},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return text


def _common(table) -> list[str]:
    return sorted({p for p, arm in table if arm == "B"} & {p for p, arm in table if arm == "A1"})


def _f(x: float | None, sign: bool = False) -> str:
    if x is None:
        return "—"
    return (f"{x:+.3f}" if sign else f"{x:.3f}").replace("-", "−")
