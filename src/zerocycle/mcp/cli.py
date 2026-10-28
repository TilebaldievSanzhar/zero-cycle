"""Команда `zerocycle mcp` — MCP-сервер для агента по stdio.

    zerocycle mcp [--root папка] [--runs-dir папка]

`--root` — дальше этой папки проверка не ходит: рука бенчмарка видит только `package/`, а эталон лежит рядом.
`--runs-dir` — куда сохранять отчёты прогонов (JSON) для записи бенчмарка.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def _mcp(args: argparse.Namespace) -> int:
    from zerocycle.mcp.server import serve

    serve(Path(args.root) if args.root else None, Path(args.runs_dir) if args.runs_dir else None)
    return 0


def add_commands(sub: argparse._SubParsersAction) -> None:
    m = sub.add_parser("mcp", help="MCP-сервер для агента (stdio)")
    m.add_argument("--root", help="папка, за пределы которой проверка не выходит")
    m.add_argument("--runs-dir", help="куда сохранять отчёты прогонов (JSON)")
    m.set_defaults(func=_mcp)
