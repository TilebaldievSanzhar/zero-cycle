"""Бенчмарк v1: дефекты генератора среди ложных тревог (PROTOCOL — «Несопоставленные находки на S»).

После прогонов несопоставленные «нарушения» рук просмотрены по ГОСТ Р 21.101-2020 и по самим пакетам. Ниже — что
оказалось настоящим дефектом, который генератор посеял нечаянно. По протоколу такая находка не считается ложной
тревогой ни для одной руки и не убирается молча: она уходит из «нарушений» и идёт в отчёт отдельной строкой. Полноту
она не меняет: засеянных дефектов не прибавляется.

Правило — регулярные выражения по описанию и основанию находки; каждое сверено с текстом пакетов (docs/bench/
V1_RESULTS.md, «Проверка ложных тревог»). Срабатывает только на ложной тревоге: на находке, сопоставленной с засеянным
дефектом, правило не применяется.

    python -m zerocycle.bench.review_v1 <корпус> <итоги>    # итоги — в <итоги>/review/: removed.json, h5.md, h5.json
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from zerocycle.bench.arms import norm_doc, score
from zerocycle.bench.h5 import report_h5


@dataclass(frozen=True)
class GeneratorDefect:
    id: str
    what: str
    basis: str
    patterns: tuple[str, ...]  # все должны найтись в описании и основании находки

    def hit(self, finding: dict) -> bool:
        text = " ".join(str(finding.get(k) or "") for k in ("description", "basis"))
        return all(re.search(p, text, re.I) for p in self.patterns)


GOST = "ГОСТ Р 21.101-2020"
DEFECTS = [
    GeneratorDefect(
        "G01",
        "наименование объекта на титуле строчными и в кавычках",
        f"{GOST}, прил. С (поле 4) и С.5",
        (r"строчн|прописн|кавычк", r"титул"),
    ),
    GeneratorDefect(
        "G02",
        "в конце содержания тома нет общего количества листов",
        f"{GOST}, 8.1.5",
        (r"содержани", r"(общ\w+|итогов\w+)\s+(количеств\w+\s+)?лист|нет\s+итог"),
    ),
    GeneratorDefect(
        "G03",
        "«Состав проектной документации» отдельным томом без титульного листа",
        f"{GOST}, 8.1.6",
        (
            r"Состав",
            r"титульн\w+\s+лист\w*\s+(нет|у него нет|не имеет|у этого тома нет)"
            r"|без\s+титульн|нет\s+титульного|но\s+титульного",
        ),
    ),
    GeneratorDefect(
        "G04",
        "на титулах нет подписей и дат или отметок об ЭП",
        f"{GOST}, прил. С (поле 10)",
        (r"титул", r"подпис", r"дат|ЭП"),
    ),
    GeneratorDefect(
        "G05",
        "наименования документов в содержании не совпадают с основной надписью",
        f"{GOST}, 8.1.5",
        (r"содержани", r"не\s+совпада\w+\s+с\s+(основн|граф)|в\s+графе\s+5"),
    ),
    GeneratorDefect(
        "G06",
        "ведомость графической части обрывается на 26-м листе",
        f"{GOST}, прил. В (форма 1)",
        (r"[Вв]едомост\w+\s+графическ", r"\b26\b"),
    ),
    GeneratorDefect(
        "G07",
        "номер листа повторяется на стыке текстовой и графической частей ПБ",
        f"{GOST}, 7.3 (нумерация листов)",
        (r"повтор|дважды", r"номер|лист\s+\d+\s+стоит"),
    ),
    GeneratorDefect(
        "G08",
        "КР: полезная нагрузка 1,50 кПа в таблице и 2,0 кПа в тексте",
        "увязка внутри тома",
        (r"1,50", r"2,0\s*кПа"),
    ),
    GeneratorDefect(
        "G09",
        "ИОС1: расчётная мощность 480 кВт на чертеже и 497 кВт в тексте",
        "увязка внутри тома",
        (r"\b497\b", r"\b480\b"),
    ),
    GeneratorDefect(
        "G10",
        "задание требует смету, а раздел 12 «Не разрабатывается»",
        "увязка: задание на проектирование ↔ «Состав проектной документации»",
        (r"[Нн]е\s+разрабатывается", r"задани"),
    ),
    GeneratorDefect(
        "G11",
        "площадь застройки 2 136,8 м² при «не более 2 136 м²» в задании",
        "увязка: задание на проектирование ↔ ТЭП",
        (r"не\s+более\s+2\s?136",),
    ),
    GeneratorDefect(
        "G12",
        "в сметах нет итогов, глав и начислений: стоимость не прослеживается",
        "сметная документация",
        (r"ССР|сводн\w+\s+сметн|сметн\w+\s+расч", r"итог|НДС|глав|не\s+прослеживается|не\s+подтвержда"),
    ),
    GeneratorDefect(
        "G13",
        "в содержании тома СМ у сметных документов нет обозначений",
        f"{GOST}, 8.1.5",
        (r"содержани", r"обозначен\w*", r"смет"),
    ),
]


def classify(finding: dict) -> str | None:
    return next((d.id for d in DEFECTS if d.hit(finding)), None)


def review(corpus: Path, results: Path):
    """Строки оценки, как у score_pilot, но без ложных тревог, оказавшихся дефектами генератора; и что убрано."""
    rows, removed = [], []
    for r in _last(results):
        if r["arm"] == "A1-author":
            continue
        row = {k: r[k] for k in ("package", "arm", "k", "sheets", "ok", "error", "cost_usd", "minutes")}
        path = results / "answers" / f"{r['package']}.{r['arm']}.{r['k']}.json"
        if r["ok"] and path.exists():
            truth = json.loads((corpus / r["package"] / "ground_truth.json").read_text(encoding="utf-8"))
            findings = json.loads(path.read_text(encoding="utf-8")).get("findings", [])
            s, _ = score(truth, findings)
            drop = {i for i in s.false_alarms if classify(findings[i])}
            for i in sorted(drop):
                f = findings[i]
                removed.append(
                    {
                        "run": f"{r['package']}.{r['arm']}.{r['k']}",
                        "arm": r["arm"],
                        "defect": classify(f),
                        "document": norm_doc(f.get("document")),
                        "page": f.get("page"),
                        "description": f.get("description"),
                    }
                )
            kept = [f for i, f in enumerate(findings) if i not in drop]
            s, metrics = score(truth, kept)
            row.update(metrics, seeded=s.seeded, found=len(s.found), findings=s.findings, violated=s.violated)
            row.update(keys=sorted(s.keys))
        rows.append(row)
    return rows, removed


def _last(results: Path) -> list[dict]:
    rows: dict[tuple, dict] = {}
    for line in (results / "runs.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[(r["package"], r["arm"], r["k"])] = r
    return list(rows.values())


def summary(removed: list[dict]) -> str:
    by = Counter((x["defect"], x["arm"]) for x in removed)
    arms = sorted({x["arm"] for x in removed})
    lines = ["| Дефект генератора | Основание | " + " | ".join(arms) + " |", "|---|---|" + "---|" * len(arms)]
    for d in DEFECTS:
        counts = [by.get((d.id, a), 0) for a in arms]
        if any(counts):
            lines.append(f"| {d.id}: {d.what} | {d.basis} | " + " | ".join(map(str, counts)) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("corpus", type=Path)
    p.add_argument("results", type=Path)
    a = p.parse_args()
    rows, removed = review(a.corpus, a.results)
    target = a.results / "review"
    target.mkdir(exist_ok=True)
    (target / "removed.json").write_text(json.dumps(removed, ensure_ascii=False, indent=1), encoding="utf-8")
    text = report_h5(target, rows)
    print(summary(removed))
    print(text)


if __name__ == "__main__":
    main()
