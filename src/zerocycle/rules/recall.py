"""Сколько засеянных дефектов находят правила: recall по кодам дефектов и ложные тревоги (PLAN, H2 до 0.12).

Корпус — папки `seed-NNNN/` генератора (`zerocycle gen`): `package/` и рядом `ground_truth.json`. Каждый дефект
эталона ищется среди находок «нарушено» правил, которые его ловят (`DETECTORS`), по месту: файл тома, страница,
обозначение, GUID, номер раздела. Находка «проверить вручную» на месте дефекта — «отправлено человеку», не recall.
Ложная тревога — «нарушено», которое не совпало ни с одним дефектом эталона; на чистом пакете — любое «нарушено».

Это замер правил на собственном генераторе, а не бенчмарк против агента: тот — шаг 0.12 (docs/bench/PROTOCOL.md).
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from zerocycle.model import Finding, Status
from zerocycle.norms.designation import parse_designation

# какие правила ловят дефект каталога (bench/defects.py)
DETECTORS: dict[str, tuple[str, ...]] = {
    "D01": ("ru.tep.consistent",),
    "D02": ("spds.sheets.numbering", "spds.sheets.count"),
    "D03": ("spds.titleblock.consistent", "spds.titleblock.designation"),
    "D07": ("ru.norms.registry",),
    "D07R": ("ru.norms.registry",),
    "D09": ("ru.completeness.pp87",),
    "D10": ("spds.titleblock.signatures",),
    "F01": ("spds.sheets.numbering",),
    "F02": ("spds.sheets.count",),
    "F03": ("core.stage.consistent",),
    "F04": ("core.building-name.consistent",),
    "F05": ("spds.composition.volumes",),
    "F06": ("spds.pde.file-names",),
    "F07": ("spds.pde.guid",),
    "F08": ("spds.sheets.format",),
    "F09": ("spds.composition.volumes", "ru.completeness.pp87"),
}


def _code(norm: str) -> str:
    d = parse_designation(norm or "")
    return d.code if d else norm


def matches(defect: dict, f: Finding) -> bool:
    """Находка указывает на место дефекта: правило из детекторов и тот же файл, страница, ссылка или раздел."""
    if f.rule not in DETECTORS.get(defect["id"], ()):
        return False
    files = {a.file for a in f.anchors} | {str(f.values.get("document", ""))}
    kind = defect["id"]
    if kind in ("D07", "D07R"):
        return _code(f.values.get("ref", "")) == _code(defect["actual"]) and defect.get("file") in files
    if kind == "D09":
        return f.values.get("section") == defect["section"]
    if kind == "F07":
        return str(f.values.get("guid", "")).lower() == defect["guid"].lower()
    if kind == "F09" and f.rule == "ru.completeness.pp87":  # тома нет — нет и раздела или подраздела
        code = str(f.values.get("code") or "")
        return bool(code) and defect["designation"].endswith(f"-{code}")
    if kind in ("F05", "F09"):
        return f.values.get("designation") == defect["designation"]
    if defect.get("file") not in files:
        return False
    if kind in ("D03", "D10", "F03", "F08") and defect.get("page"):
        return any(a.page == defect["page"] for a in f.anchors if a.file == defect["file"])
    return True


@dataclass
class PackageResult:
    name: str
    clean: bool
    defects: list[dict]
    found: dict[int, list[str]] = field(default_factory=dict)  # номер дефекта → правила, нашедшие «нарушено»
    manual: dict[int, list[str]] = field(default_factory=dict)  # номер дефекта → «проверить вручную» на месте
    false_alarms: list[Finding] = field(default_factory=list)
    # «нарушено» другого правила на листе дефекта: следствие того же дефекта (лист с чужим обозначением в графе 1
    # разрывает документ — и «Листов» не сходится), не ложная тревога
    related: list[Finding] = field(default_factory=list)
    withheld: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[dict] = field(default_factory=list)  # места, где верный ответ — не «нарушено» (эталон v1)
    at_notes: dict[int, list[str]] = field(default_factory=dict)  # номер места → статусы находок на нём


def at_note(note: dict, f: Finding) -> bool:
    """Находка на отвлекающем месте эталона: тот же раздел, обозначение или файл и лист (page = null — весь файл)."""
    if note.get("section") is not None and f.values.get("section") == note["section"]:
        return True
    if note.get("designation") and f.values.get("designation") == note["designation"]:
        return True
    files = {a.file for a in f.anchors} | {str(f.values.get("document", ""))}
    if not note.get("file") or note["file"] not in files:
        return False
    return note.get("page") is None or any(a.page == note["page"] for a in f.anchors if a.file == note["file"])


def evaluate(name: str, truth: dict, findings: list[Finding]) -> PackageResult:
    defects = truth.get("defects", [])
    res = PackageResult(name=name, clean=bool(truth.get("clean")) or not defects, defects=defects)
    used: set[int] = set()
    for i, d in enumerate(defects):
        for k, f in enumerate(findings):
            if not matches(d, f):
                continue
            if f.status == Status.violated:
                res.found.setdefault(i, []).append(f.rule)
                used.add(k)
            elif f.status == Status.manual:
                res.manual.setdefault(i, []).append(f.rule)
                used.add(k)
    places = {(d["file"], d["page"]) for d in defects if d.get("file") and d.get("page")}
    for k, f in enumerate(findings):
        if f.status != Status.violated or k in used:
            continue
        if any((a.file, a.page) in places for a in f.anchors):
            res.related.append(f)
        else:
            res.false_alarms.append(f)
    res.notes = truth.get("notes", [])
    for j, note in enumerate(res.notes):
        hits = [f.status.value for k, f in enumerate(findings) if k not in used and at_note(note, f)]
        if hits:
            res.at_notes[j] = hits
    return res


@dataclass
class Summary:
    packages: list[PackageResult]

    def by_code(self) -> dict[str, dict[str, int]]:
        out: dict[str, Counter[str]] = defaultdict(Counter)
        for p in self.packages:
            for i, d in enumerate(p.defects):
                c = out[d["id"]]
                c["seeded"] += 1
                if i in p.found:
                    c["found"] += 1
                elif i in p.manual:
                    c["manual"] += 1
                else:
                    c["missed"] += 1
        return {k: dict(v) for k, v in sorted(out.items())}

    @property
    def seeded(self) -> int:
        return sum(len(p.defects) for p in self.packages)

    @property
    def found(self) -> int:
        return sum(len(p.found) for p in self.packages)

    def false_alarms(self, clean: bool | None = None) -> int:
        return sum(len(p.false_alarms) for p in self.packages if clean is None or p.clean == clean)


def run_corpus(corpus: Path, jurisdiction: str = "ru") -> Summary:
    from zerocycle.rules.cli import check

    results = []
    for seed in sorted(p for p in Path(corpus).iterdir() if (p / "ground_truth.json").exists()):
        truth = json.loads((seed / "ground_truth.json").read_text(encoding="utf-8"))
        report = check(seed / "package", jurisdiction)
        res = evaluate(seed.name, truth, report.findings)
        res.withheld = len(report.withheld)
        res.errors = [f"{c.rule}: {c.error}" for c in report.coverage if c.error]
        results.append(res)
    return Summary(results)


def markdown(s: Summary) -> str:
    clean = [p for p in s.packages if p.clean]
    seeded = [p for p in s.packages if not p.clean]
    recall = s.found / s.seeded if s.seeded else 0.0
    lines = [
        f"Пакетов: {len(s.packages)} (с дефектами {len(seeded)}, чистых {len(clean)}); дефектов засеяно {s.seeded}, "
        f"найдено «нарушено» {s.found} — recall {recall:.2f}.",
        f"Ложных «нарушено»: на пакетах с дефектами — {s.false_alarms(False)}, на чистых — {s.false_alarms(True)}; "
        f"сопутствующих (другое правило на листе дефекта) — {sum(len(p.related) for p in s.packages)}; "
        f"задержано гейтом — {sum(p.withheld for p in s.packages)}; упавших правил — "
        f"{sum(len(p.errors) for p in s.packages)}.",
        "",
        "| Дефект | Засеяно | Найдено | Проверить вручную | Пропущено |",
        "|---|---|---|---|---|",
    ]
    for code, c in s.by_code().items():
        row = (c.get("seeded", 0), c.get("found", 0), c.get("manual", 0), c.get("missed", 0))
        lines.append(f"| {code} | {' | '.join(map(str, row))} |")
    missed = [(p.name, d) for p in s.packages for i, d in enumerate(p.defects) if i not in p.found]
    if missed:
        lines += ["", "Не найдено как «нарушено»:", ""]
        for name, d in missed:
            where = d.get("file") or d.get("designation") or ""
            detail = {k: v for k, v in d.items() if k in ("expected", "actual", "page", "role", "field")}
            lines.append(f"- {name}: {d['id']} {where} {json.dumps(detail, ensure_ascii=False)}")
    kinds: dict[str, Counter[str]] = defaultdict(Counter)
    for p in s.packages:
        for j, note in enumerate(p.notes):
            c = kinds[f"{note.get('kind', '?')} (ждём {note.get('expected', '?')})"]
            c["places"] += 1
            for status in set(p.at_notes.get(j, [])):
                c[status] += 1
    if kinds:
        lines += [
            "",
            "Отвлекающие места эталона — «нарушено» на них ложное:",
            "",
            "| Место | Мест | С «нарушено» | С «проверить вручную» |",
            "|---|---|---|---|",
        ]
        for kind, c in sorted(kinds.items()):
            lines.append(f"| {kind} | {c['places']} | {c.get('violated', 0)} | {c.get('manual', 0)} |")
    alarms = [(p.name, f) for p in s.packages for f in p.false_alarms]
    if alarms:
        lines += ["", "Ложные тревоги:", ""]
        lines += [f"- {name}: `{f.rule}` — {f.message}" for name, f in alarms]
    return "\n".join(lines) + "\n"
