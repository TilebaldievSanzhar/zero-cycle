"""Бенчмарк v1 (PLAN 0.12, PROTOCOL — «Заморозка»): полный прогон рук на отложенной части слоя S.

Запускается из замороженной копии репозитория (git worktree на коммите заморозки) с PYTHONPATH на её src — так
движок и обвязка не меняются, пока в main идёт работа. Прогон идёт днями на подписке Claude: перед каждым агентским
прогоном мерим окна и ждём сброса, обрыв по лимиту повторяем. Процесс можно убить и запустить снова: сделанные прогоны
пропускаются (runs.jsonl), замок не даёт запустить две копии.

    python scripts/bench_v1.py            # всё: скилы A1 → руки → оценка
    python scripts/bench_v1.py --score    # только оценка того, что есть
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

from zerocycle.bench.pilot import author_skills, limit_hit, report_pilot, run_pilot, score_pilot, wait_for_room

REPO = Path(r"C:\Projects\development_challenge\zero-cycle")  # итоги и корпус — в var/ основного репозитория
CORPUS = REPO / "var" / "bench" / "v1" / "corpus"
OUT = REPO / "var" / "bench" / "v1" / "results"
RUNS_ROOT = Path(r"C:\zc-bench\runs")  # папки рук — вне репозитория и корпуса
AUTHOR_DIR = Path(r"C:\zc-bench\a1-author")
BIM_DATA = REPO.parent / "bim-data"
LOCK = Path(r"C:\zc-bench\bench.lock")

MODEL = "claude-opus-5-5"
BUDGET = 10.0  # потолок долларов на прогон по ценам API; в пилоте самый дорогой — $3,54
AUTHOR_BUDGET = 15.0  # скилы A1: отдельная сессия, около часа работы агента
ARMS = ["C", "B", "A1", "A0"]  # на каждом номере прогона: сначала пара главной гипотезы B и A1, потом A0
RUNS = {"C": 5, "B": 5, "A1": 5, "A0": 5}
START_BELOW = 0.8  # пятичасовое окно: начинать прогон, только если выбрано меньше (с поправкой на объём пакета)


def log(msg: str) -> None:
    print(f"[{time.strftime('%d.%m %H:%M')}] {msg}", flush=True)


def lock() -> bool:
    """Одна копия на машину: замок с PID; замок мёртвого процесса перехватываем."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        pid = int(LOCK.read_text().strip() or 0)
        if pid and _alive(pid):
            log(f"уже идёт: PID {pid}")
            return False
    LOCK.write_text(str(os.getpid()))
    return True


def _alive(pid: int) -> bool:
    import subprocess

    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def skills() -> Path:
    """Окончательные скилы A1: сессия-автор по публичным источникам; оборванную лимитом продолжаем после сброса."""
    target = OUT / "skills_A1"
    for attempt in range(4):
        if any(target.rglob("SKILL.md")):
            return target
        wait_for_room(0.25, RUNS_ROOT)  # автор — до 70% окна
        resume = attempt > 0 and AUTHOR_DIR.exists()
        r = author_skills(OUT, AUTHOR_DIR, BIM_DATA, MODEL, AUTHOR_BUDGET if not resume else 8.0, None, resume)
        log(f"скилы A1, сессия {r.k}: {'ok' if r.ok else r.error} · ${r.cost_usd} · {r.minutes} мин")
        if not r.ok and not limit_hit(r) and not any(target.rglob("SKILL.md")):
            raise RuntimeError(f"сессия-автор скилов A1 не справилась: {r.error}")
    raise RuntimeError("скилы A1 не готовы после четырёх сессий")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--score", action="store_true", help="только оценка")
    args = p.parse_args()
    packages = sorted(d.name for d in CORPUS.iterdir() if (d / "ground_truth.json").exists())
    if not args.score:
        if not lock():
            return 1
        log(f"бенчмарк v1: {len(packages)} пакетов, руки {ARMS}, прогонов {RUNS}, модель {MODEL}")
        while True:
            try:
                skills_dir = skills()
                run_pilot(
                    CORPUS,
                    OUT,
                    packages,
                    ARMS,
                    RUNS,
                    MODEL,
                    BUDGET,
                    RUNS_ROOT,
                    skills=skills_dir,
                    parallel=1,
                    start_below=START_BELOW,
                )
                break
            except Exception:  # сеть, CLI, диск — подождать и продолжить с того же места
                log("сбой, продолжу через 10 минут:\n" + traceback.format_exc())
                time.sleep(600)
        LOCK.unlink(missing_ok=True)
    rows = score_pilot(CORPUS, OUT)
    report_pilot(OUT, rows, sum(r["sheets"] for r in rows) / max(len(rows), 1))
    log(f"оценка: {OUT / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
