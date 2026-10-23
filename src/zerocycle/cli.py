"""Командная строка «Нулевого цикла».

zerocycle scan <пакет> [--json out.json]          опись: файлы, листы, основные надписи, обозначения, info.xml
zerocycle gen <папка> [--seed 1] [--count 5]       засеянные пакеты с эталоном (--clean — без дефектов)
zerocycle designation <обозначение> ...            разбор обозначений по ГОСТ Р 21.101-2020
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from zerocycle.extract.designation import interpret, parse
from zerocycle.model import Media, Package


def _scan(args: argparse.Namespace) -> int:
    from zerocycle.scan import scan

    pkg = scan(Path(args.package))
    if args.json:
        Path(args.json).write_text(pkg.model_dump_json(indent=1), encoding="utf-8")
    _print_summary(pkg)
    return 0


def _print_summary(pkg: Package) -> None:
    pages = [p for d in pkg.documents for p in d.pages]
    forms = Counter(p.title_block.form for p in pages if p.title_block)
    media = Counter(d.media.value for d in pkg.documents)
    print(f"Пакет: {pkg.root} · базовое обозначение: {pkg.base_designation or '—'}")
    print(f"Файлов: {len(pkg.documents)} ({', '.join(f'{k} {v}' for k, v in media.most_common())})")
    no_tb = sum(1 for p in pages if p.title_block is None)
    print(f"Листов: {len(pages)} · основные надписи по формам: {dict(sorted(forms.items()))}", end="")
    print(f" · без основной надписи: {no_tb}")
    for d in pkg.documents:
        if d.media != Media.pdf:
            continue
        des = d.designation.text if d.designation else "—"
        sec = f"раздел {d.section.number}" if d.section and d.section.number else "раздел ?"
        flags = "; ".join([*(d.designation.issues if d.designation else ()), *d.issues])
        print(f"  {des:<24} {sec:<10} {len(d.pages):>3} л.  {d.path}" + (f"  ⚠ {flags}" if flags else ""))
    if pkg.pde:
        n_docs = sum(1 for n in pkg.pde.nodes if n.kind == "document")
        print(f"info.xml: {n_docs} документов, замечаний {len(pkg.pde.issues)}")
        for issue in pkg.pde.issues:
            print(f"  ⚠ {issue}")
    else:
        print("info.xml: нет (пакет без реквизитной части ПДЭ)")


def _gen(args: argparse.Namespace) -> int:
    from zerocycle.bench.corpus import generate

    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    for seed in range(args.seed, args.seed + args.count):
        out = generate(seed, Path(args.out), mode="clean" if args.clean else "auto", codes=codes)
        gt = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
        print(f"{out}: дефектов {len(gt['defects'])} ({', '.join(d['id'] for d in gt['defects']) or 'чистый'})")
    return 0


def _designation(args: argparse.Namespace) -> int:
    for raw in args.text:
        d = parse(raw)
        if d is None:
            print(f"{raw}: не обозначение")
            continue
        sec = interpret(d)
        where = f"раздел {sec.number}" if sec and sec.number else "—"
        if sec and sec.subsection:
            where += f", подраздел {sec.subsection}"
        if sec and sec.part:
            where += f", часть {sec.part}"
        if sec and sec.book:
            where += f", книга {sec.book}"
        extra = f" · {'; '.join(d.issues)}" if d.issues else ""
        print(f"{raw}: база «{d.base}», шифр {d.code or '—'}, {where}, суффикс {d.suffix or '—'}{extra}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="zerocycle", description="Нулевой цикл: проверка пакета ПД перед экспертизой")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="опись пакета")
    s.add_argument("package", help="папка или zip")
    s.add_argument("--json", help="сохранить опись в JSON")
    s.set_defaults(func=_scan)

    g = sub.add_parser("gen", help="засеянные пакеты с эталоном")
    g.add_argument("out")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--count", type=int, default=1)
    g.add_argument("--clean", action="store_true", help="без дефектов")
    g.add_argument("--codes", help="явный список дефектов: D01,D03,F02")
    g.set_defaults(func=_gen)

    d = sub.add_parser("designation", help="разбор обозначений")
    d.add_argument("text", nargs="+")
    d.set_defaults(func=_designation)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
