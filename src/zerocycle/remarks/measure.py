"""Замер H1 по пунктам «Дополнений и изменений»: доля формальных замечаний и её доверительный интервал.

Единица — пункт подраздела. Пункты одного заключения не независимы (один эксперт, одна организация, одна
манера), поэтому интервал — бутстреп с перевыборкой заключений, а не пунктов; рядом — среднее по заключениям,
чтобы длинные списки одного эксперта не решали за всех.

Разметка человеком — TSV: ключ пункта, категория, категория по правилам, раздел, текст, заметка. Пункт в файле
разметки считается просмотренным; совпадение правил с разметкой — метрика кодбука.
"""

from __future__ import annotations

import csv
import hashlib
import random
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from zerocycle.remarks.classify import CATEGORIES, classify, normalize
from zerocycle.remarks.conclusion import Conclusion, Remark

LABEL_FIELDS = ["key", "category", "rule_category", "conclusion", "discipline", "text", "note"]


def remark_key(c: Conclusion, r: Remark) -> str:
    """Ключ пункта, устойчивый к перенумерации: заключение + текст (одинаковые пункты одного заключения — один ключ).

    Нормализация здесь своя и не меняется вместе с кодбуком: иначе разметка потеряет ключи.
    """
    text = " ".join(r.text.lower().split())
    return f"{c.sha256[:12]}:{hashlib.sha1(text.encode()).hexdigest()[:10]}"


COPY_JACCARD = 0.8  # списки пунктов совпадают на 80% и больше — один список, переписанный в другое заключение
COPY_MIN_ITEMS = 5


def dedupe(conclusions: Iterable[Conclusion]) -> tuple[list[Conclusion], list[tuple[Conclusion, Conclusion]]]:
    """Одно заключение — один раз: тот же номер (копии файла в разных закупках) или тот же список пунктов.

    Эксперт, который ведёт несколько однотипных объектов, переносит список изменений из заключения
    в заключение почти дословно — для замера это один список, а не три. Возвращает (оставленные, [(копия, оригинал)]).
    """
    kept: list[Conclusion] = []
    copies: list[tuple[Conclusion, Conclusion]] = []
    texts: dict[int, set[str]] = {}
    for c in sorted(conclusions, key=lambda c: -len(c.remarks)):
        items = {normalize(r.text) for r in c.remarks}
        twin = next((k for k in kept if c.number is not None and k.number == c.number), None)
        if twin is None and len(items) >= COPY_MIN_ITEMS:
            twin = next(
                (k for k in kept if len(items & texts[id(k)]) / len(items | texts[id(k)]) >= COPY_JACCARD), None
            )
        if twin is not None:
            copies.append((c, twin))
            continue
        kept.append(c)
        texts[id(c)] = items
    return kept, copies


def _label_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def load_labels(path: Path) -> dict[str, str]:
    labels = {}
    for row in _label_rows(path):
        if row["category"] not in CATEGORIES:
            raise ValueError(f"{path}: неизвестная категория «{row['category']}» у {row['key']}")
        labels[row["key"]] = row["category"]
    return labels


def load_notes(path: Path) -> dict[str, str]:
    return {row["key"]: row["note"] for row in _label_rows(path) if row.get("note")}


INDEX_FIELDS = ["announce", "number", "issued", "expert_org", "verdict", "status", "remarks", "copy_of", "file"]


def write_index(conclusions: Iterable[Conclusion], copies: list[tuple[Conclusion, Conclusion]], path: Path) -> None:
    """Перечень заключений: что вошло в замер, что — копия, где подраздела нет. Закупка — папка над files/."""

    def row(c: Conclusion, copy_of: str = "") -> list[str]:
        parts = Path(c.file).parts
        announce = parts[-3] if len(parts) >= 3 and parts[-2] == "files" else ""
        issued = c.issued.isoformat() if c.issued else ""
        return [announce, c.number or "", issued, c.expert_org or "", c.verdict or "", c.status, str(len(c.remarks)),
                copy_of, Path(c.file).name]  # fmt: skip

    rows = [row(c) for c in conclusions] + [row(c, o.number or Path(o.file).name) for c, o in copies]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(INDEX_FIELDS)
        w.writerows(sorted(rows))


def apply(conclusions: Iterable[Conclusion], labels: dict[str, str] | None = None) -> None:
    """Категория каждого пункта: разметка человека, если есть, иначе правило кодбука."""
    labels = labels or {}
    for c in conclusions:
        for r in c.remarks:
            v = classify(r.text)
            key = remark_key(c, r)
            r.category, r.rule = (labels[key], "manual") if key in labels else (v.category, v.rule)


def export_labels(
    conclusions: Iterable[Conclusion],
    path: Path,
    labels: dict[str, str] | None = None,
    notes: dict[str, str] | None = None,
) -> int:
    """Черновик разметки: категория по правилам (или прежняя разметка) — для просмотра человеком."""
    labels, notes = labels or {}, notes or {}
    rows, seen = [], set()
    for c in conclusions:
        for r in c.remarks:
            key = remark_key(c, r)
            if key in seen:
                continue
            seen.add(key)
            rule = classify(r.text).category
            text = " ".join(r.text.split())
            row = [key, labels.get(key, rule), rule, c.number or "", r.discipline or "", text, notes.get(key, "")]
            rows.append(row)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(LABEL_FIELDS)
        w.writerows(rows)
    return len(rows)


@dataclass(frozen=True)
class Share:
    k: int
    n: int
    lo: float  # границы перцентильного бутстреп-интервала
    hi: float

    @property
    def p(self) -> float:
        return self.k / self.n if self.n else 0.0


def cluster_share(groups: list[tuple[int, int]], iters: int = 4000, seed: int = 1, alpha: float = 0.05) -> Share:
    """Доля Σk/Σn по группам (k, n) и интервал бутстрепа с перевыборкой групп."""
    k, n = sum(g[0] for g in groups), sum(g[1] for g in groups)
    if not groups or not n:
        return Share(k, n, 0.0, 0.0)
    rng = random.Random(seed)
    stats = []
    for _ in range(iters):
        sample = [groups[rng.randrange(len(groups))] for _ in groups]
        sn = sum(s[1] for s in sample)
        if sn:
            stats.append(sum(s[0] for s in sample) / sn)
    stats.sort()
    return Share(k, n, stats[int(alpha / 2 * len(stats))], stats[int((1 - alpha / 2) * len(stats)) - 1])


FORMAL = frozenset(c.id for c in CATEGORIES.values() if c.formal)
UNKNOWN = frozenset({"unspecified", "other"})  # содержание не определено — вне основного знаменателя


@dataclass(frozen=True)
class H1:
    main: Share  # формальные / пункты с определённым содержанием
    macro: float  # среднее долей по заключениям (с ≥ 3 пунктами с определённым содержанием)
    upper: Share  # + состав изображения в числителе
    conservative: Share  # «без содержания» и «не определено» — в знаменателе
    by_category: Counter
    conclusions_by_category: Counter
    reviewed: int  # пунктов с разметкой человека
    agree: int  # из них правила дали ту же категорию


def h1(conclusions: list[Conclusion]) -> H1:
    main, upper, cons, shares = [], [], [], []
    by_cat, by_concl = Counter(), Counter()
    reviewed = agree = 0
    for c in conclusions:
        cats = [r.category or "other" for r in c.remarks]
        if not cats:
            continue
        by_cat.update(cats)
        by_concl.update(set(cats))
        known = [x for x in cats if x not in UNKNOWN]
        formal = sum(x in FORMAL for x in known)
        main.append((formal, len(known)))
        upper.append((formal + sum(x == "drawing_content" for x in known), len(known)))
        cons.append((formal, len(cats)))
        if len(known) >= 3:
            shares.append(formal / len(known))
        for r in c.remarks:
            if r.rule == "manual":
                reviewed += 1
                agree += classify(r.text).category == r.category
    return H1(
        main=cluster_share(main),
        macro=sum(shares) / len(shares) if shares else 0.0,
        upper=cluster_share(upper),
        conservative=cluster_share(cons),
        by_category=by_cat,
        conclusions_by_category=by_concl,
        reviewed=reviewed,
        agree=agree,
    )
