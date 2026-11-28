"""Засчитывание находок V3 (docs/bench/PROTOCOL_V3.md, «Как засчитываем»): находка ↔ убранный документ.

Находка засчитывается дефекту, если в ней назван документ того же вида (FINDING или Seed.text) и есть признак
отсутствия (ABSENT) либо категория «комплектность». Одна находка закрывает один дефект своего вида: два энергопаспорта
двух школ требуют двух находок. Совпадения и почти-совпадения (документ назван, отсутствия нет) просматриваются
глазами без имени руки — review.md.

    python -X utf8 -m zerocycle.bench.v3score <план>   → var/v3/runs/<план>/score.json, report.md, review.md
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict

from zerocycle.bench.arms import VIOLATED
from zerocycle.bench.v3 import KIND_GROUP, PACKAGES, Seed, kind, object_class
from zerocycle.bench.v3run import RESULTS, load_runs

# Как документ вида называют в находке. У «прочих расчётов» своего нет: какой расчёт — в Seed.text.
FINDING = {
    "kazsod": r"казахстанск\w*\s+содержани|каз\.?\s*содерж|казсодерж|ведомост\w*\s+потребност",
    "svmr": r"ресурсн\w*\s+ведомост|ведомост\w*\s+(?:[\w-]+\s+){0,3}ресурс",
    "pz_smeta": r"пояснительн\w+\s+записк\w*\s+(?:к\s+)?смет",
    "prices": r"перечень\s+(?:[\w-]+\s+){0,2}цен|прейскурант|прайс",
    "energy": r"энергетическ\w+\s+паспорт|энергопаспорт|паспорт\w*\s+энергет",
    "thermal": r"теплотехническ",
    "igi": r"инженерно[\s-]*геологическ|геологическ\w*\s+(?:отч[её]т|изыскан)|(?<![а-яё])ИГИ(?![а-яё])",
    "pos": r"(?<![а-яё])ПОС(?![а-яё])|проект\w*\s+организаци\w+\s+строительств",
    "mopb": r"(?<![а-яё])МОПБ(?![а-яё])|мероприяти\w+\s+(?:по\s+)?(?:обеспечени\w+\s+)?пожарн",
    "tu": r"технически\w*\s+услови|(?<![а-яё])ТУ(?![а-яё])",
    "water": r"запас\w*\s+(?:подземн\w+\s+)?вод|месторожден",
}
ABSENT = re.compile(
    r"\bнет\b|отсутств|не\s+(?:представ|предостав|найден|приложен|содерж|включ|входит|обнаруж|выявлен|разработ"
    r"|выполнен)|не\s+хватает|недоста[её]т",
    re.I,
)
COMPLETENESS = "комплектность"


def pattern_of(seed: Seed) -> str:
    if seed.text:
        return seed.text
    k = kind(seed)
    if k not in FINDING:
        raise ValueError(f"у дефекта вида {k} нет шаблона находки: {seed.file}")
    return FINDING[k]


def _text(f: dict) -> str:
    return " ".join(str(f.get(k) or "") for k in ("description", "basis", "document"))


def match(pattern: str, f: dict) -> str | None:
    """«absent» — назван и сказано, что нет; «category» — назван в находке о комплектности; None — не совпало."""
    text = _text(f)
    if not re.search(pattern, text, re.I):
        return None
    if ABSENT.search(text):
        return "absent"
    return "category" if f.get("category") == COMPLETENESS else None


def score_answer(package: str, answer: dict | None) -> dict:
    """Дефект → найден ли, какой находкой и почему; плюс для просмотра: запасные совпадения (ещё находки того же вида
    с признаком отсутствия — пригодятся, если просмотр отклонит основную) и почти-совпадения."""
    findings = (answer or {}).get("findings", [])
    seeds = PACKAGES[package].seeds
    out: dict = {"defects": {}, "spare": [], "near": []}
    by_pattern: dict[str, list[int]] = defaultdict(list)  # одинаковый шаблон — дефекты одного вида
    for n, s in enumerate(seeds, 1):
        by_pattern[pattern_of(s)].append(n)
    for pattern, ns in by_pattern.items():
        hits = [(i, why) for i, f in enumerate(findings) if (why := match(pattern, f))]
        # сначала «нарушения», потом «проверить вручную»: статус дефекта — лучшей находки
        hits.sort(key=lambda h: findings[h[0]].get("status") != VIOLATED)
        for j, n in enumerate(ns):
            i, why = hits[j] if j < len(hits) else (None, None)
            out["defects"][f"{package}.{n}"] = {
                "found": i is not None,
                "finding": i,
                "status": findings[i].get("status") if i is not None else None,
                "why": why,
            }
        out["spare"] += [{"defects": [f"{package}.{n}" for n in ns], "finding": i} for i, _ in hits[len(ns) :]]
        used = {i for i, _ in hits}
        out["near"] += [
            {"defects": [f"{package}.{n}" for n in ns], "finding": i}
            for i, f in enumerate(findings)
            if i not in used and re.search(pattern, _text(f), re.I)
        ]
    return out


def _load(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def verdicts(plan_name: str) -> dict[str, dict]:
    """Решения просмотра глазами: «прогон|дефект» → {"found": bool, "finding": i, "why": "…"}.

    Два источника: verdicts.json (ключ «прогон|дефект») и review_decisions.json — решения по номерам пунктов review.md,
    который читается без имени руки; номер переводится в прогон и дефект через review_key.json. Решение пишется сюда,
    а не правкой ответа.
    """
    base = RESULTS / plan_name
    out = _load(base / "verdicts.json")
    key = _load(base / "review_key.json")
    decisions = _load(base / "review_decisions.json")
    main = {f"{k['run']}|{d}" for k in key.values() if k.get("kind") == "совпадение" for d in k["defects"]}
    rejected = {
        f"{key[i]['run']}|{d}"
        for i, v in decisions.items()
        if not v.get("found") and key[i].get("kind") == "совпадение"
        for d in key[i]["defects"]
    }
    for item, d in sorted(decisions.items(), key=lambda kv: bool(kv[1].get("found"))):  # «засчитать» — последним
        k = key[item]
        if k.get("kind") != "совпадение":
            # запасное и почти-совпадение решают только там, где основной находки нет или её отклонили
            targets = [d.get("defect") or k["defects"][0]] if d.get("found") else []
            targets = [t for t in targets if f"{k['run']}|{t}" in rejected or f"{k['run']}|{t}" not in main]
        else:
            targets = k["defects"] if not d.get("found") else [d.get("defect") or k["defects"][0]]
        for defect in targets:
            out[f"{k['run']}|{defect}"] = {"found": bool(d["found"]), "finding": k["finding"], "why": d.get("why", "")}
    return out


def score_plan(plan_name: str, decided: dict | None = None) -> dict:
    """Все прогоны плана: по прогону — дефекты; по руке — полнота (доля прогонов, нашедших дефект, в среднем).

    decided — решения просмотра (по умолчанию verdicts(plan)); {} — сырое сопоставление, по нему строятся review.md и
    выборка слепой оценки, чтобы их номера не менялись от решений."""
    runs = load_runs(plan_name)
    decided = verdicts(plan_name) if decided is None else decided
    per_run = {}
    for r in runs:
        path = RESULTS / plan_name / r["id"] / "answer.json"
        answer = json.loads(path.read_text(encoding="utf-8")) if r["status"] == "ok" and path.exists() else None
        score = score_answer(r["package"], answer)
        for d, v in score["defects"].items():
            if (key := f"{r['id']}|{d}") in decided:
                dec = decided[key]
                v.update(found=bool(dec["found"]), why=f"просмотр: {dec.get('why', '')}")
                if not v["found"]:
                    v.update(status=None, finding=None)
                elif dec.get("finding") is not None and answer:
                    v.update(finding=dec["finding"], status=answer["findings"][dec["finding"]].get("status"))
        per_run[r["id"]] = {"run": r, "answer": answer, "score": score}
    found: dict[tuple[str, str], list[bool]] = defaultdict(list)  # (рука, дефект) → по прогонам
    found_violated: dict[tuple[str, str], list[bool]] = defaultdict(list)
    for x in per_run.values():
        for d, s in x["score"]["defects"].items():
            found[(x["run"]["arm"], d)].append(s["found"])
            found_violated[(x["run"]["arm"], d)].append(s["status"] == VIOLATED)
    arms = sorted({a for a, _ in found})
    summary = {}
    for a in arms:
        ds = sorted(d for b, d in found if b == a)
        share = {d: sum(found[(a, d)]) / len(found[(a, d)]) for d in ds}
        strict = {d: sum(found_violated[(a, d)]) / len(found_violated[(a, d)]) for d in ds}
        groups: dict[str, list[float]] = defaultdict(list)
        objects: dict[str, list[float]] = defaultdict(list)
        for d, v in share.items():
            package, n = d.rsplit(".", 1)
            groups[KIND_GROUP[kind(PACKAGES[package].seeds[int(n) - 1])]].append(v)
            objects[object_class(package)].append(v)
        summary[a] = {
            "defects": len(ds),
            "runs": sum(1 for x in per_run.values() if x["run"]["arm"] == a),
            "recall": round(sum(share.values()) / len(ds), 3) if ds else None,
            "recall_violated": round(sum(strict.values()) / len(ds), 3) if ds else None,
            "by_group": {g: round(sum(v) / len(v), 3) for g, v in sorted(groups.items())},
            "by_object": {g: round(sum(v) / len(v), 3) for g, v in sorted(objects.items())},
            "per_defect": share,
        }
    return {"plan": plan_name, "summary": summary, "runs": per_run}


def report(scored: dict) -> str:
    lines = [f"# V3, план {scored['plan']}: засчитывание", ""]
    lines += ["| Рука | Прогонов | Дефектов | Полнота | Только «нарушения» | По группам |", "|---|---|---|---|---|---|"]
    for a, s in scored["summary"].items():
        groups = ", ".join(f"{g} {v:.2f}" for g, v in s["by_group"].items())
        lines.append(f"| {a} | {s['runs']} | {s['defects']} | {s['recall']} | {s['recall_violated']} | {groups} |")
    lines += ["", "| Прогон | Статус | Находок | Найдено | Почти | Мин | Токены вх. (кэш) / вых. |"]
    lines += ["|---|---|---|---|---|---|---|"]
    for rid, x in sorted(scored["runs"].items()):
        r, s = x["run"], x["score"]
        got = [d for d, v in s["defects"].items() if v["found"]]
        u = r.get("usage") or {}
        lines.append(
            f"| {rid} | {r['status']} | {r.get('findings', '—')} | {len(got)}/{len(s['defects'])} | {len(s['near'])} "
            f"| {r.get('minutes', '—')} | {u.get('input_tokens', 0):,} ({u.get('cached_input_tokens', 0):,}) / "
            f"{u.get('output_tokens', 0):,} |"
        )
    return "\n".join(lines) + "\n"


def _finding_md(f: dict) -> list[str]:
    return [
        f"- {f.get('status')} · {f.get('category')} · `{f.get('document')}` · стр. {f.get('page')}",
        f"- {f.get('description')}",
        f"- основание: {f.get('basis')}",
        "",
    ]


def review(scored: dict, seed: int = 2026) -> tuple[str, dict]:
    """Совпадения и почти-совпадения вперемешку и без имени руки — для просмотра глазами (PROTOCOL_V3).

    Возвращает текст и ключ «номер → прогон, дефекты, находка»; ключ не открывать до конца просмотра."""
    rows = []
    for rid, x in scored["runs"].items():
        findings = (x["answer"] or {}).get("findings", [])
        for d, v in x["score"]["defects"].items():
            if v["found"] and v["finding"] is not None:
                rows.append(("совпадение " + v["why"], [d], rid, v["finding"], findings[v["finding"]]))
        for spare in x["score"].get("spare", []):
            rows.append(("запасное совпадение", spare["defects"], rid, spare["finding"], findings[spare["finding"]]))
        for near in x["score"]["near"]:
            rows.append(("почти", near["defects"], rid, near["finding"], findings[near["finding"]]))
    random.Random(seed).shuffle(rows)
    out, key = ["# Просмотр совпадений (руки скрыты)", ""], {}
    for n, (what, defects, rid, i, f) in enumerate(rows, 1):
        out += [f"## {n}. {what} — {', '.join(defects)}", *_finding_md(f)]
        key[str(n)] = {"run": rid, "defects": defects, "finding": i, "kind": what.split()[0]}
    return "\n".join(out), key


def blind_sample(scored: dict, per_arm: int = 10, seed: int = 2026) -> tuple[str, dict]:
    """Слепая оценка находок вне засева (PROTOCOL_V3, «Как засчитываем»): per_arm случайных находок каждой руки,
    вперемешку и без имени руки. Оценщик видит пакет: вердикт — подтверждено / не подтверждено / проверить нельзя,
    важность — «снял бы эксперт» или «мелочь». Ключ «номер → прогон, находка» — blind_key.json."""
    rng = random.Random(seed)
    pools: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for rid, x in sorted(scored["runs"].items()):
        seeded = {v["finding"] for v in x["score"]["defects"].values() if v["finding"] is not None}
        seeded |= {n["finding"] for n in x["score"]["near"] + x["score"].get("spare", [])}
        for i, _ in enumerate((x["answer"] or {}).get("findings", [])):
            if i not in seeded:
                pools[x["run"]["arm"]].append((rid, i))
    picked = [p for arm in sorted(pools) for p in rng.sample(pools[arm], min(per_arm, len(pools[arm])))]
    rng.shuffle(picked)
    out = ["# Слепая оценка находок (руки скрыты)", "", "Вердикт: подтверждено / не подтверждено / проверить нельзя;"]
    out += ["важность: снял бы эксперт / мелочь. Решения — blind_verdicts.json по номерам.", ""]
    key = {}
    for n, (rid, i) in enumerate(picked, 1):
        f = scored["runs"][rid]["answer"]["findings"][i]
        out += [f"## {n}. {scored['runs'][rid]['run']['package']}", *_finding_md(f)]
        key[str(n)] = {"run": rid, "finding": i}
    return "\n".join(out), key


def precision(plan_name: str) -> dict:
    """Точность по слепой оценке: подтверждено / (подтверждено + не подтверждено), по рукам."""
    base = RESULTS / plan_name
    key, decided = _load(base / "blind_key.json"), _load(base / "blind_verdicts.json")
    runs = {r["id"]: r for r in load_runs(plan_name)}
    out: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for item, d in decided.items():
        arm = runs[key[item]["run"]]["arm"]
        out[arm][d["verdict"]] += 1
        if d["verdict"] == "подтверждено":
            out[arm]["снял бы эксперт" if d.get("weight") == "снял бы эксперт" else "мелочь"] += 1
    res = {}
    for arm, c in sorted(out.items()):
        yes, no = c["подтверждено"], c["не подтверждено"]
        res[arm] = {**c, "precision": round(yes / (yes + no), 3) if yes + no else None}
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Засчитывание находок V3")
    ap.add_argument("plan")
    ap.add_argument("--blind", type=int, default=0, help="слепая оценка: находок на руку (пилот 10, основной 40)")
    a = ap.parse_args()
    base = RESULTS / a.plan

    def dump(name: str, data) -> None:
        (base / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    raw = score_plan(a.plan, decided={})  # номера просмотра и выборки — от сырого сопоставления
    text, key = review(raw)
    (base / "review.md").write_text(text, encoding="utf-8")
    dump("review_key.json", key)
    if a.blind:
        text, key = blind_sample(raw, a.blind)
        (base / "blind.md").write_text(text, encoding="utf-8")
        dump("blind_key.json", key)
    scored = score_plan(a.plan)
    slim = {"plan": scored["plan"], "summary": scored["summary"]}
    slim["runs"] = {k: v["score"] for k, v in scored["runs"].items()}
    dump("score.json", slim)
    (base / "report.md").write_text(report(scored), encoding="utf-8")
    print(report(scored))
    if (base / "blind_verdicts.json").exists():
        print(json.dumps(precision(a.plan), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
