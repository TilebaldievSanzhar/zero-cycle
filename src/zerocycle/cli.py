"""Командная строка «Нулевого цикла».

zerocycle scan <пакет> [-j kz] [--org org.toml] [--json out.json]   опись: файлы, листы, основные надписи, обозначения
zerocycle gen <папка> [--seed 1] [--count 5]       засеянные пакеты с эталоном (--clean — без дефектов)
zerocycle gen <папка> --v1 [--heldout] [--small]   генератор v1: 300–1 500 листов, шаблоны организаций
zerocycle designation <обозначение> ... [-j kz]    разбор обозначений по справочнику юрисдикции
zerocycle remarks <заключение.pdf | папка> ...     замечания из заключений экспертизы РК, категории и замер H1
zerocycle check <пакет> [-j kz] [--work new]       проверка пакета правилами юрисдикции: находки с якорями
zerocycle rules [-j kz] [--cards папка]            правила юрисдикции и их карточки
zerocycle recall <корпус>                          recall правил по эталону засеянного корпуса
zerocycle mcp [--root папка] [--runs-dir папка]    MCP-сервер для агента (stdio)

Юрисдикция (-j) выбирает справочник шифров и редакцию СПДС: ru — ГОСТ Р 21.101-2020, kz — ГОСТ 21.101-97.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from zerocycle.extract.designation import interpret, parse
from zerocycle.model import Media, Package
from zerocycle.packs import Context, available, context


def _scan(args: argparse.Namespace) -> int:
    from zerocycle.scan import scan

    ctx = context(args.jurisdiction, args.org)
    pkg = scan(Path(args.package), ctx)
    if args.json:
        Path(args.json).write_text(pkg.model_dump_json(indent=1), encoding="utf-8")
    _print_summary(pkg, ctx)
    return 0


def _print_summary(pkg: Package, ctx: Context) -> None:
    pages = [p for d in pkg.documents for p in d.pages]
    forms = Counter(p.title_block.form for p in pages if p.title_block)
    media = Counter(d.media.value for d in pkg.documents)
    unverified = "" if ctx.titleblocks.verified else " (профиль основных надписей не сверен со стандартом)"
    org = f" · организация: {ctx.organization}" if ctx.organization else ""
    print(f"Юрисдикция: {ctx.title} · СПДС: {ctx.standard}{unverified}{org}")
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
        out = generate(
            seed,
            Path(args.out),
            mode="clean" if args.clean else "auto",
            codes=codes,
            version=1 if args.v1 else 0,
            split="heldout" if args.heldout else "dev",
            size="small" if args.small else "full",
        )
        gt = json.loads((out / "ground_truth.json").read_text(encoding="utf-8"))
        extra = ""
        if prof := gt.get("profile"):
            extra = f" · шаблон {prof['template']} ({prof['split']}), листов {prof['sheets']}"
        print(f"{out}: дефектов {len(gt['defects'])} ({', '.join(d['id'] for d in gt['defects']) or 'чистый'}){extra}")
    return 0


def _designation(args: argparse.Namespace) -> int:
    cb = context(args.jurisdiction, args.org).codebook
    for raw in args.text:
        d = parse(raw, cb)
        if d is None:
            print(f"{raw}: не обозначение")
            continue
        sec = interpret(d, cb)
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


CONCLUSION_NAME = re.compile(r"заключ|қорытынд", re.IGNORECASE)


def _conclusion_pages(path: Path, sha: str, cache: Path | None) -> tuple[list[str], int]:
    from zerocycle.remarks.conclusion import read_pages

    cached = cache / f"{sha[:16]}.json" if cache else None
    if cached and cached.exists():
        pages, text_pages = json.loads(cached.read_text(encoding="utf-8"))
        return pages, text_pages
    pages, text_pages = read_pages(path)
    if cached:
        cache.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps([pages, text_pages], ensure_ascii=False), encoding="utf-8")
    return pages, text_pages


def _remarks(args: argparse.Namespace) -> int:
    from zerocycle.ingest.files import sha256_file
    from zerocycle.remarks.classify import CATEGORIES
    from zerocycle.remarks.conclusion import Conclusion, parse_conclusion
    from zerocycle.remarks.measure import apply, dedupe, export_labels, h1, load_labels, load_notes, write_index

    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():  # в папке — только файлы с «заключением» в имени: рядом лежат тома ПСД
            files += sorted(f for f in p.rglob("*.pdf") if CONCLUSION_NAME.search(f.name.replace("_", " ")))
        else:
            files.append(p)
    by_sha: dict[str, Conclusion] = {}
    for f in files:
        sha = sha256_file(f)
        if sha in by_sha:
            continue
        try:
            pages, text_pages = _conclusion_pages(f, sha, Path(args.cache) if args.cache else None)
        except Exception as e:  # битый или недокачанный PDF — фиксируем и идём дальше
            print(f"  не прочитан: {f} ({type(e).__name__})", file=sys.stderr)
            by_sha[sha] = Conclusion(file=f.as_posix(), sha256=sha, pages=0, text_pages=0, status="unreadable")
            continue
        by_sha[sha] = parse_conclusion(pages, text_pages, f.as_posix(), sha)
    conclusions, copies = dedupe(by_sha.values())
    conclusions.sort(key=lambda c: c.file)
    labels = load_labels(Path(args.labels)) if args.labels else {}
    apply(conclusions, labels)
    if args.export_labels:
        notes = load_notes(Path(args.labels)) if args.labels else {}
        n = export_labels(conclusions, Path(args.export_labels), labels, notes)
        print(f"Черновик разметки: {args.export_labels} ({n} пунктов)")
    if args.jsonl:
        with Path(args.jsonl).open("w", encoding="utf-8") as out:
            for c in conclusions:
                out.write(c.model_dump_json() + "\n")
    if args.index:
        write_index(conclusions, copies, Path(args.index))

    statuses = Counter(c.status for c in conclusions)
    print(
        f"Файлов: {len(files)} · заключений: {len(conclusions)} (копии файлов: {len(files) - len(by_sha)}, "
        f"копии номера или списка пунктов: {len(copies)}) · "
        f"с пунктами {statuses['ok']}, «не вносились» {statuses['empty']}, без подраздела {statuses['no_section']}, "
        f"без текста {statuses['no_text']}, не прочитано {statuses['unreadable']}"
    )
    for copy, original in copies:
        same = original.number or "—"
        print(f"  копия      {len(copy.remarks):>3} п. {copy.number or '—':<16} = {same:<16} {copy.file}")
    for c in conclusions:
        if args.items or c.status in ("no_section", "no_text", "unreadable"):
            print(f"  {c.status:<10} {len(c.remarks):>3} п. {c.number or '—':<16} {c.expert_org or '—':<28} {c.file}")
        if args.items:
            for r in c.remarks:
                mark = "✓" if r.rule == "manual" else " "
                print(f"      {r.n:>3} {r.discipline or '?':<4} {r.category:<17}{mark} {r.text[:150]}")
    m = h1(conclusions)
    total = sum(m.by_category.values())
    print(f"Пунктов: {total} · просмотрено человеком: {m.reviewed}", end="")
    print(f" · правила совпали с разметкой: {m.agree} из {m.reviewed}" if m.reviewed else "")
    print(f"  {'категория':<28}{'пунктов':>8}{'доля':>8}{'заключений':>12}")
    for cid, cat in CATEGORIES.items():
        k = m.by_category.get(cid, 0)
        kind = {True: "форм.", False: "содерж.", None: "—"}[cat.formal]
        share = f"{k / total:.1%}" if total else "—"
        print(f"  {cat.name + ' (' + kind + ')':<28}{k:>8}{share:>8}{m.conclusions_by_category.get(cid, 0):>12}")
    if m.main.n:
        print(
            f"H1 — формальные среди пунктов с определённым содержанием: {m.main.p:.1%} "
            f"[{m.main.lo:.1%}; {m.main.hi:.1%}] ({m.main.k} из {m.main.n}; 95%, бутстреп по заключениям)\n"
            f"   среднее по заключениям: {m.macro:.1%} · с составом изображения: {m.upper.p:.1%} · "
            f"с «без содержания» в знаменателе: {m.conservative.p:.1%}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="zerocycle", description="Нулевой цикл: проверка пакета ПД перед экспертизой")
    sub = p.add_subparsers(dest="cmd", required=True)

    def pack_options(cmd: argparse.ArgumentParser) -> None:
        cmd.add_argument(
            "-j",
            "--jurisdiction",
            choices=available(),
            default="ru",
            help="пакет юрисдикции: справочник шифров и редакция СПДС (по умолчанию ru)",
        )
        cmd.add_argument("--org", help="слой организации: TOML со своими шифрами, подписями граф и формами штампа")

    s = sub.add_parser("scan", help="опись пакета")
    s.add_argument("package", help="папка или zip")
    s.add_argument("--json", help="сохранить опись в JSON")
    pack_options(s)
    s.set_defaults(func=_scan)

    g = sub.add_parser("gen", help="засеянные пакеты с эталоном")
    g.add_argument("out")
    g.add_argument("--seed", type=int, default=1)
    g.add_argument("--count", type=int, default=1)
    g.add_argument("--clean", action="store_true", help="без дефектов")
    g.add_argument("--codes", help="явный список дефектов: D01,D03,F02")
    g.add_argument("--v1", action="store_true", help="генератор v1 (шаг 0.4b): объём и шаблоны организаций")
    g.add_argument("--heldout", action="store_true", help="v1: отложенная выборка — в разработке не открывать")
    g.add_argument("--small", action="store_true", help="v1: маленький пакет (~100 листов) для отладки")
    g.set_defaults(func=_gen)

    d = sub.add_parser("designation", help="разбор обозначений")
    d.add_argument("text", nargs="+")
    pack_options(d)
    d.set_defaults(func=_designation)

    r = sub.add_parser("remarks", help="замечания из заключений экспертизы РК и замер H1")
    r.add_argument("paths", nargs="+", help="PDF заключений или папки (в папках — файлы с «заключением» в имени)")
    r.add_argument("--labels", help="разметка человеком (TSV): категории поверх правил")
    r.add_argument("--export-labels", help="записать черновик разметки (TSV) для просмотра")
    r.add_argument("--jsonl", help="сохранить заключения и пункты в JSONL")
    r.add_argument("--index", help="записать перечень заключений с копиями (TSV)")
    r.add_argument("--cache", help="папка кэша текста страниц")
    r.add_argument("--items", action="store_true", help="показать пункты")
    r.set_defaults(func=_remarks)

    from zerocycle.mcp.cli import add_commands as add_mcp
    from zerocycle.rules.cli import add_commands

    add_commands(sub)  # check — проверка пакета правилами, rules — правила и их карточки, recall — сверка с эталоном
    add_mcp(sub)  # mcp — сервер для агента

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
