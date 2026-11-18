"""Команда `zerocycle mcp` — MCP-сервер для агента по stdio.

    zerocycle mcp [--root папка] [--runs-dir папка] [-j kz]

`--root` — дальше этой папки проверка не ходит: рука бенчмарка видит только `package/`, а эталон лежит рядом.
`--runs-dir` — куда сохранять отчёты прогонов (JSON) для записи бенчмарка.
`-j` — юрисдикция по умолчанию, если агент её не назвал (ru).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _mcp(args: argparse.Namespace) -> int:
    from zerocycle.mcp.server import serve

    root = Path(args.root) if args.root else None
    serve(root, Path(args.runs_dir) if args.runs_dir else None, args.jurisdiction)
    return 0


def add_commands(sub: argparse._SubParsersAction) -> None:
    m = sub.add_parser("mcp", help="MCP-сервер для агента (stdio)")
    m.add_argument("--root", help="папка, за пределы которой проверка не выходит")
    m.add_argument("--runs-dir", help="куда сохранять отчёты прогонов (JSON)")
    m.add_argument("-j", "--jurisdiction", default="ru", help="юрисдикция по умолчанию: ru или kz")
    m.set_defaults(func=_mcp)
