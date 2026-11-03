"""Бенчмарк на реальных пакетах (docs/bench/PROTOCOL_REAL.md): запуск в облачном сеансе Claude Code.

Берёт bench_real/ из ветки: пакеты без эталона и источники скилов. Сначала сессия-автор пишет скилы A1, потом руки B и
A1 идут парами. Каждый прогон сразу коммитится и пушится: сеанс может оборваться. Повторный запуск продолжает.

    /tmp/venv/bin/python scripts/bench_real_cloud.py           # движок — в /tmp/venv, рукам — /tmp/armenv без него
    /tmp/venv/bin/python scripts/bench_real_cloud.py --judge   # слепая оценка находок по bench_real/judge/items.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from zerocycle.bench.pilot import Run, prepare, run_agent
from zerocycle.bench.real import A1_AUTHOR_TASK_KZ, JUDGE_SCHEMA, JUDGE_TASK, TASK_KZ, batches, plan

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO / "bench_real"
OUT = ROOT / "results"
JUDGE = ROOT / "judge"
RUNS = Path("/tmp/zc-runs")
MODEL = "claude-opus-5-5"
EFFORT = "high"  # явно: у облачного сеанса своё значение по умолчанию
BUDGET = 10.0
AUTHOR_BUDGET = 15.0
JUDGE_BUDGET = 3.0
_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M')}] {msg}", flush=True)


def spent(out: Path = OUT) -> float:
    path = out / "runs.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
    return sum(r["cost_usd"] for r in rows)


def done(out: Path = OUT) -> set[tuple[str, str, int]]:
    path = out / "runs.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
    return {(r["package"], r["arm"], r["k"]) for r in rows}


def record(r: Run, out: Path = OUT) -> None:
    with _lock:
        out.mkdir(parents=True, exist_ok=True)
        with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        log(
            f"{r.name}: {'ok' if r.ok else 'сбой'} · ${r.cost_usd} · {r.minutes} мин · всего ${spent(out):.2f}"
            + (f" · {r.error}" if r.error else "")
        )
        subprocess.run(["git", "add", str(out)], cwd=REPO, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"bench real: {r.name}"], cwd=REPO, check=True)
        for attempt in range(3):
            if subprocess.run(["git", "push", "-q", "origin", "HEAD"], cwd=REPO).returncode == 0:
                break
            time.sleep(20 * (attempt + 1))
        else:
            log("не запушилось: повторю со следующим прогоном")


def author() -> Path:
    """Скилы A1 по тем же открытым нормам РК, на которых стоит пакет правил kz."""
    target = OUT / "skills_A1"
    if any(target.rglob("SKILL.md")):
        return target
    run_dir = RUNS / uuid.uuid4().hex[:12]
    run_dir.mkdir(parents=True)
    shutil.copytree(ROOT / "sources", run_dir / "sources")
    mcp = run_dir.parent / f"{run_dir.name}.mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    prompt = A1_AUTHOR_TASK_KZ.format(task=TASK_KZ)
    r = run_agent(Run("author", "A1-author", 1), run_dir, mcp, OUT, MODEL, AUTHOR_BUDGET, EFFORT, prompt, None)
    produced = run_dir / "skills_out"
    if produced.is_dir():
        shutil.copytree(produced, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    record(r)
    if not any(target.rglob("SKILL.md")):
        raise SystemExit(f"сессия-автор не сдала скилы: {r.error}")
    return target


def one(item: tuple[str, str, int], skills: Path, limit: float) -> None:
    package, arm, k = item
    if spent() >= limit:
        log(f"{package}.{arm}.{k}: пропущен, потолок ${limit}")
        return
    run_dir = RUNS / uuid.uuid4().hex[:12]
    mcp = prepare(run_dir, ROOT / "corpus" / package / "package", arm, skills)
    try:
        r = run_agent(Run(package, arm, k), run_dir, mcp, OUT, MODEL, BUDGET, EFFORT, TASK_KZ)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
        mcp.unlink(missing_ok=True)
    record(r)


def judge(limit: float) -> None:
    """Слепая оценка: оценщик видит пакет, нормы и находки без указания руки."""
    tasks = json.loads((JUDGE / "items.json").read_text(encoding="utf-8"))
    todo = [(i, package, items) for i, (package, items) in enumerate(batches(tasks), 1)]
    todo = [x for x in todo if (x[1], "judge", x[0]) not in done(JUDGE)]
    log(f"оценка: прогонов {len(todo)}, потрачено ${spent(JUDGE):.2f}, потолок ${limit}")

    def batch(x: tuple[int, str, list[dict]]) -> None:
        i, package, items = x
        if spent(JUDGE) >= limit:
            log(f"оценка {i}: пропущена, потолок ${limit}")
            return
        run_dir = RUNS / uuid.uuid4().hex[:12]
        mcp = prepare(run_dir, ROOT / "corpus" / package / "package", "judge", None)
        shutil.copytree(ROOT / "sources", run_dir / "sources")
        (run_dir / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        try:
            r = run_agent(
                Run(package, "judge", i), run_dir, mcp, JUDGE, MODEL, JUDGE_BUDGET, EFFORT, JUDGE_TASK, JUDGE_SCHEMA
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)
            mcp.unlink(missing_ok=True)
        record(r, JUDGE)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(batch, todo))
    log(f"оценка готова, потрачено ${spent(JUDGE):.2f}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=float, default=85.0, help="не начинать прогон, если потрачено больше, $")
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--only-first", action="store_true", help="только скилы и первая пара: замер цены")
    p.add_argument("--judge", action="store_true", help="слепая оценка находок; --limit — её потолок")
    a = p.parse_args()
    arm_python = Path("/tmp/armenv/bin")
    if not (arm_python / "python").exists():
        raise SystemExit("нет /tmp/armenv: рукам нужен python без движка")
    os.environ["PATH"] = f"{arm_python}{os.pathsep}{os.environ['PATH']}"
    os.environ.pop("CLAUDE_EFFORT", None)  # усилие — флагом, одинаково у обеих рук
    check = subprocess.run(["python", "-c", "import zerocycle"], capture_output=True)
    if check.returncode == 0:
        raise SystemExit("движок виден рукам: python импортирует zerocycle")
    RUNS.mkdir(parents=True, exist_ok=True)
    if a.judge:
        judge(a.limit)
        return 0
    skills = author()
    todo = [x for x in plan(["B", "A1"], a.repeats) if x not in done()]
    if a.only_first:
        todo = todo[:2]
    log(f"прогонов: {len(todo)}, потрачено ${spent():.2f}, потолок ${a.limit}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda x: one(x, skills, a.limit), todo))
    log(f"готово, потрачено ${spent():.2f}, прогонов в итогах {len(done())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
