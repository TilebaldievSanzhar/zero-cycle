"""`python -m zerocycle.rules check|rules …` — пока команды не подключены в `zerocycle.cli`."""

import argparse
import sys

from zerocycle.rules.cli import add_commands


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="zerocycle.rules")
    add_commands(p.add_subparsers(dest="cmd", required=True))
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
