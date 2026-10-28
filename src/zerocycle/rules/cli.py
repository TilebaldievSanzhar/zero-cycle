"""Команды движка правил для `zerocycle`: `check` — проверка пакета, `rules` — правила и их карточки.

    zerocycle check <пакет> [-j kz] [--org org.toml] [--work new|reconstruction|capital_repair] [--json f] [--md f]
    zerocycle rules [-j kz] [--cards папка]

Подключаются в `zerocycle.cli` вызовом `add_commands(sub)`; до этого — `python -m zerocycle.rules …`.
"""

from __future__ import annotations

import argparse
import tempfile
from collections import Counter
from pathlib import Path

from zerocycle.model import Package, Status
from zerocycle.packs import available
from zerocycle.rules.cards import FAMILY, SEVERITY, STATUS, card
from zerocycle.rules.context import Context
from zerocycle.rules.engine import Report, load_rules, run

SHOW = 12  # находок одного правила в выводе на экран; полный список — в --json и --md


def _scan(path: Path, jurisdiction: str, workdir: Path | None, org: str | None = None) -> Package:
    from zerocycle.packs import context as pack_context
    from zerocycle.scan import scan

    return scan(path, pack_context(jurisdiction, org), workdir=workdir)


def check(
    package: Path,
    jurisdiction: str,
    work: str | None = None,
    scan_out: Path | None = None,
    from_scan: Path | None = None,
    org: str | None = None,
) -> Report:
    """Опись + правила. Опись можно сохранить (`scan_out`) и переиспользовать (`from_scan`) — для папки пакета."""
    with tempfile.TemporaryDirectory(prefix="zerocycle-check-") as tmp:
        if from_scan and package.is_dir():
            pkg = Package.model_validate_json(from_scan.read_text(encoding="utf-8"))
        else:
            pkg = _scan(package, jurisdiction, None if package.is_dir() else Path(tmp), org)
        if scan_out:
            scan_out.write_text(pkg.model_dump_json(), encoding="utf-8")
        root = package if package.is_dir() else Path(tmp)
        files = {d.path: root / d.path for d in pkg.documents}
        ctx = Context(pkg, jurisdiction=jurisdiction, files=files, params={"work_kind": work} if work else {})
        return run(ctx)


def markdown(report: Report) -> str:
    counts = Counter(f.status.value for f in report.findings)
    lines = [
        f"# Проверка пакета «{report.package}»",
        "",
        f"Юрисдикция {report.jurisdiction} · СПДС {report.edition or '—'} · вид работ {report.work_kind or '—'} · "
        f"пакеты правил {' → '.join(report.packs)} · движок {report.run.engine}",
        "",
        "| Статус | Находок |",
        "|---|---|",
        *(f"| {STATUS[s.value]} | {counts.get(s.value, 0)} |" for s in Status),
        "",
    ]
    if report.run.norm_index:
        lines += [f"Индекс норм: {report.run.norm_index}", ""]
    for f in report.findings:
        where = "; ".join(f"{a.file}" + (f", с. {a.page}" if a.page else "") for a in f.anchors[:3])
        lines.append(
            f"- **{STATUS[f.status.value]}** · {FAMILY[f.family.value]} · {SEVERITY[f.severity.value]} · `{f.rule}` — "
            f"{f.message}" + (f" ({'; '.join(f.basis)})" if f.basis else "") + (f" — {where}" if where else "")
        )
    if report.withheld:
        lines += ["", "## Задержаны гейтом", *(f"- `{w.rule}`: {w.message} — {w.reason}" for w in report.withheld)]
    errors = [c for c in report.coverage if c.error]
    if errors:
        lines += ["", "## Правила с ошибкой", *(f"- `{c.rule}`: {c.error}" for c in errors)]
    return "\n".join(lines) + "\n"


def _print(report: Report) -> None:
    counts = Counter(f.status.value for f in report.findings)
    applicable = sum(c.applicable for c in report.coverage)
    print(
        f"Пакет: {report.package} · юрисдикция {report.jurisdiction} · СПДС {report.edition or '—'} · "
        f"вид работ: {report.work_kind or 'не определён'}"
    )
    print(
        f"Правил применимо: {applicable} из {len(report.coverage)} · "
        + " · ".join(f"{STATUS[s.value]}: {counts.get(s.value, 0)}" for s in Status)
    )
    by_rule: dict[str, list] = {}
    for f in report.findings:
        by_rule.setdefault(f.rule, []).append(f)
    for rule_id, findings in by_rule.items():
        print(f"\n{rule_id}")
        for f in findings[:SHOW]:
            where = ""
            if f.anchors:
                a = f.anchors[0]
                where = f"  ← {a.file}" + (f", с. {a.page}" if a.page else "")
            print(f"  [{STATUS[f.status.value]}] {f.message}{where}")
        if len(findings) > SHOW:
            print(f"  … ещё {len(findings) - SHOW}")
    for c in report.coverage:
        if c.error:
            print(f"\n⚠ правило {c.rule} упало: {c.error}")
    for w in report.withheld:
        print(f"⚠ задержано гейтом ({w.rule}): {w.reason} — {w.message[:100]}")


def _check(args: argparse.Namespace) -> int:
    scan_out = Path(args.scan) if args.scan else None
    from_scan = Path(args.from_scan) if args.from_scan else None
    report = check(Path(args.package), args.jurisdiction, args.work, scan_out, from_scan, args.org)
    if args.json:
        Path(args.json).write_text(report.model_dump_json(indent=1), encoding="utf-8")
    if args.md:
        Path(args.md).write_text(markdown(report), encoding="utf-8")
    _print(report)
    return 0 if report.clean else 1


def _rules(args: argparse.Namespace) -> int:
    rules = load_rules(args.jurisdiction)
    for r in rules:
        print(f"{r.id:<32} {r.version:<7} {FAMILY[r.family.value]:<22} {SEVERITY[r.severity.value]:<12} {r.title}")
    if args.cards:
        out = Path(args.cards)
        out.mkdir(parents=True, exist_ok=True)
        for r in rules:
            (out / f"{r.id}.md").write_text(card(r), encoding="utf-8")
        print(f"Карточки: {out} ({len(rules)})")
    return 0


def add_commands(sub: argparse._SubParsersAction) -> None:
    c = sub.add_parser("check", help="проверка пакета правилами юрисдикции")
    c.add_argument("package", help="папка или zip")
    c.add_argument("-j", "--jurisdiction", choices=available(), default="ru", help="пакет юрисдикции (по умолчанию ru)")
    c.add_argument("--org", help="слой организации: TOML со своими шифрами и формами штампа")
    c.add_argument("--work", choices=["new", "reconstruction", "capital_repair"], help="вид работ, если не очевиден")
    c.add_argument("--json", help="отчёт целиком в JSON")
    c.add_argument("--md", help="отчёт в Markdown")
    c.add_argument("--scan", help="сохранить опись пакета в JSON")
    c.add_argument("--from-scan", help="взять опись из JSON вместо повторной описи (для папки пакета)")
    c.set_defaults(func=_check)

    r = sub.add_parser("rules", help="правила юрисдикции и их карточки")
    r.add_argument("-j", "--jurisdiction", choices=available(), default="ru")
    r.add_argument("--cards", help="записать карточки правил в папку")
    r.set_defaults(func=_rules)
