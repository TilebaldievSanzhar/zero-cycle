"""Прогоны рук бенчмарка и пилот бюджета (docs/bench/PROTOCOL.md, «Пилот до заморозки»).

Агентская рука — Claude Code без интерфейса (`claude -p`) в отдельной папке вне репозитория. В папке — только копия
`package/`; у A1 — ещё скилы в `.claude/skills/`, у B — MCP-сервер «Нулевого цикла» с корнем в этой папке.

Одно и то же у всех агентских рук:
- задача и схема ответа (bench/arms.py);
- модель, усилие и потолок долларов на прогон;
- инструменты: Bash, Read, Write, Edit, Glob, Grep, Skill; веба нет;
- Python для PDF — системный, без кода «Нулевого цикла».

Настройки пользователя не подхватываются (`--setting-sources project`), сессия не сохраняется. Рука C — `zerocycle
check` в этом же процессе.

На прогон пишем:
- токены ввода, вывода и кэша; доллары по ценам API — их считает Claude Code;
- минуты, ходы и вызовы инструментов;
- долю пятичасового и недельного окон подписки после прогона;
- сбои и пути вне папки руки в вызовах инструментов — проверка изоляции.

Раскладка результатов: <out>/runs.jsonl, <out>/answers/<прогон>.json, <out>/transcripts/<прогон>.jsonl,
<out>/scores.jsonl, <out>/report.md.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from zerocycle.bench.arms import ANSWER_SCHEMA, TASK, engine_answer, jaccard, score

AGENT_ARMS = ("A0", "A1", "B")
TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"]
TIMEOUT_S = 120 * 60  # прогон дольше двух часов — сбой «таймаут»
PATHS = re.compile(r"(?i)(?:[a-z]:[\\/]|/[a-z]/)[^\s\"'`|<>]+")  # абсолютные пути Windows и Git Bash в вызовах
UP = re.compile(r"(?:^|[\s'\"=(])\.\.(?:[/\\]|$|[\s'\")])")  # подъём из рабочей папки: «ls ..», «../»
_lock = threading.Lock()


@dataclass
class Run:
    package: str
    arm: str
    k: int
    sheets: int = 0
    model: str | None = None
    ok: bool = False
    error: str | None = None
    cost_usd: float = 0.0
    minutes: float = 0.0
    turns: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)  # input, output, cache_read, cache_write
    five_hour: float | None = None  # доля пятичасового окна подписки после прогона
    seven_day: float | None = None
    outside: list[str] = field(default_factory=list)  # пути вне папки руки в вызовах инструментов

    @property
    def name(self) -> str:
        return f"{self.package}.{self.arm}.{self.k}"


def claude_exe() -> str:
    exe = shutil.which("claude.cmd") or shutil.which("claude")
    if not exe:
        raise RuntimeError("не найден Claude Code (claude)")
    return exe


def prepare(run_dir: Path, package: Path, arm: str, skills: Path | None) -> Path:
    """Папка руки: копия package/; у A1 — скилы, у B — MCP-сервер «Нулевого цикла» с корнем в этой папке."""
    if run_dir.exists():
        shutil.rmtree(run_dir)
    shutil.copytree(package, run_dir / "package")
    if arm in ("A1", "B1"):
        if not skills or not skills.is_dir():
            raise RuntimeError(f"для руки {arm} нужна папка скилов (--skills)")
        shutil.copytree(skills, run_dir / ".claude" / "skills")
    servers = {}
    if arm in ("B", "B1"):
        servers["zerocycle"] = {"command": sys.executable, "args": ["-m", "zerocycle", "mcp", "--root", str(run_dir)]}
    mcp = run_dir.parent / f"{run_dir.name}.mcp.json"  # конфиг — рядом, а не в папке руки
    mcp.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return mcp


def run_agent(
    run: Run,
    run_dir: Path,
    mcp: Path,
    out: Path,
    model: str,
    budget: float,
    effort: str | None,
    prompt: str = TASK,
    schema: dict | None = ANSWER_SCHEMA,
) -> Run:
    """Claude Code без интерфейса в папке руки; schema=None — свободный ответ (сессия-автор скилов A1)."""
    tools = TOOLS + (["mcp__zerocycle"] if run.arm in ("B", "B1") else [])
    cmd = [
        claude_exe(),
        "-p",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        *(["--json-schema", json.dumps(schema, ensure_ascii=False)] if schema else []),
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp),
        "--no-session-persistence",
        "--permission-mode",
        "dontAsk",
        "--tools",
        *TOOLS,
        "--allowedTools",
        *tools,
        "--max-budget-usd",
        str(budget),
    ]
    if effort:
        cmd += ["--effort", effort]
    transcript = out / "transcripts" / f"{run.name}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    run.model = model
    start = time.time()
    result: dict = {}
    rate: dict = {}
    calls: dict[str, int] = {}
    outside: set[str] = set()
    with transcript.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=run_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        timer = threading.Timer(TIMEOUT_S, proc.kill)
        timer.start()
        try:
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                log.write(line + "\n")
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if e.get("type") == "assistant":
                    for b in e.get("message", {}).get("content", []):
                        if b.get("type") == "tool_use":
                            calls[b["name"]] = calls.get(b["name"], 0) + 1
                            outside |= _outside(b.get("input", {}), run_dir)
                elif e.get("type") == "rate_limit_event":
                    rate = e.get("rate_limit_info", {})
                elif e.get("type") == "result":
                    result = e
            proc.wait()
            stderr = proc.stderr.read().decode("utf-8", "replace")
        finally:
            timer.cancel()
    run.minutes = round((time.time() - start) / 60, 2)
    run.tool_calls = calls
    run.outside = sorted(outside)
    windows = rate.get("unifiedWindows", {})
    run.five_hour = windows.get("five_hour", {}).get("utilization")
    run.seven_day = windows.get("seven_day", {}).get("utilization")
    if result:
        u = result.get("usage", {})
        run.cost_usd = round(result.get("total_cost_usd") or 0.0, 4)
        run.turns = result.get("num_turns") or 0
        run.tokens = {
            "input": u.get("input_tokens", 0),
            "output": u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0),
            "cache_write": u.get("cache_creation_input_tokens", 0),
        }
        answer = result.get("structured_output") if schema else {"result": result.get("result")}
        if result.get("is_error") or answer is None:
            run.error = f"{result.get('subtype')}: {str(result.get('result') or '')[:300]}"
        else:
            run.ok = True
            _write(out / "answers" / f"{run.name}.json", answer)
    else:
        run.error = "нет итогового события: " + (
            "таймаут" if time.time() - start >= TIMEOUT_S else stderr.strip()[-300:] or f"код {proc.returncode}"
        )
    return run


def _strings(x) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return []


def _norm(p: str) -> str:
    return re.sub(r"/+", "/", p.lower().replace("\\", "/"))


def _outside(tool_input: dict, run_dir: Path) -> set[str]:
    """Пути вне папки руки в вызове инструмента: абсолютные и подъём «..» из рабочей папки."""
    own = _norm(str(run_dir))
    own_bash = "/" + own.replace(":", "", 1)  # c:/zc-pilot/… в Git Bash — /c/zc-pilot/…
    found = set()
    for text in _strings(tool_input):
        for m in PATHS.finditer(text):
            p = _norm(m.group(0)).rstrip(".,;)")
            if not p.startswith((own, own_bash, "/dev/null", "/tmp")):
                found.add(m.group(0)[:160])
        if UP.search(text):
            found.add(".. " + text[:120])
    return found


def run_engine(run: Run, package: Path, out: Path) -> Run:
    from zerocycle.rules.cli import check

    start = time.time()
    try:
        answer = engine_answer(check(package, "ru"))
        _write(out / "answers" / f"{run.name}.json", answer)
        run.ok = True
    except Exception as e:  # прогон руки не должен ронять пилот
        run.error = f"{type(e).__name__}: {e}"
    run.minutes = round((time.time() - start) / 60, 2)
    run.model = "zerocycle check (без вызовов модели)"
    return run


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _sheets(package_root: Path) -> int:
    truth = json.loads((package_root / "ground_truth.json").read_text(encoding="utf-8"))
    return int(truth.get("profile", {}).get("sheets") or 0)


def run_pilot(
    corpus: Path,
    out: Path,
    packages: list[str],
    arms: list[str],
    runs: dict[str, int],
    model: str,
    budget: float,
    runs_root: Path,
    skills: Path | None = None,
    effort: str | None = None,
    parallel: int = 3,
    stop_at: float = 0.95,
) -> list[Run]:
    """Прогоны рук по пакетам корпуса; agent-прогоны — параллельно, пока доля окна подписки ниже stop_at."""
    out.mkdir(parents=True, exist_ok=True)
    # порядок: прогон → рука → пакет; одновременно идут разные пакеты одной руки
    plan = [Run(p, a, k) for k in range(1, max(runs.values()) + 1) for a in arms if k <= runs[a] for p in packages]
    done = {(r["package"], r["arm"], r["k"]) for r in map(json.loads, _lines(out / "runs.jsonl"))}
    plan = [r for r in plan if (r.package, r.arm, r.k) not in done]  # повторный запуск дозаполняет
    stop = threading.Event()
    results: list[Run] = []

    def one(r: Run) -> Run:
        if stop.is_set():
            return r
        root = corpus / r.package
        r.sheets = _sheets(root)
        if r.arm == "C":
            r = run_engine(r, root / "package", out)
        else:
            run_dir = runs_root / uuid.uuid4().hex[:12]  # имя не выдаёт ни руку, ни пакет
            mcp = prepare(run_dir, root / "package", r.arm, skills)
            try:
                r = run_agent(r, run_dir, mcp, out, model, budget, effort)
            finally:
                shutil.rmtree(run_dir, ignore_errors=True)  # следующие руки не увидят чужую работу
                mcp.unlink(missing_ok=True)
            if (r.five_hour or 0) >= stop_at or (r.seven_day or 0) >= stop_at:
                stop.set()  # окно подписки почти выбрано: остальные прогоны — после сброса
        with _lock:
            with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            results.append(r)
            print(
                f"{r.name}: {'ok' if r.ok else 'сбой'} · ${r.cost_usd} · {r.minutes} мин · "
                f"окно 5 ч {r.five_hour} · неделя {r.seven_day}" + (f" · {r.error}" if r.error else ""),
                flush=True,
            )
        return r

    for r in [r for r in plan if r.arm == "C"]:  # движок — без очереди: он бесплатный и быстрый
        one(r)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        list(pool.map(one, [r for r in plan if r.arm != "C"]))
    return results


def _lines(path: Path) -> list[str]:
    return [x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


def score_pilot(corpus: Path, out: Path) -> list[dict]:
    """Оценка ответов по эталону пакетов; коэффициент Жаккара — между прогонами одной руки на одном пакете."""
    rows = []
    keys: dict[tuple[str, str], list[set[str]]] = {}
    for line in _lines(out / "runs.jsonl"):
        r = json.loads(line)
        if r["arm"] == "A1-author":  # разовая подготовка руки A1 — в отчёте отдельной строкой
            continue
        answer_path = out / "answers" / f"{r['package']}.{r['arm']}.{r['k']}.json"
        row = {k: r[k] for k in ("package", "arm", "k", "sheets", "ok", "error", "cost_usd", "minutes")}
        if r["ok"] and answer_path.exists():
            truth = json.loads((corpus / r["package"] / "ground_truth.json").read_text(encoding="utf-8"))
            answer = json.loads(answer_path.read_text(encoding="utf-8"))
            s, metrics = score(truth, answer.get("findings", []))
            row.update(metrics, seeded=s.seeded, found=len(s.found), findings=s.findings, notes_by_kind=s.on_notes)
            keys.setdefault((r["package"], r["arm"]), []).append(s.keys)
        rows.append(row)
    for row in rows:
        sets = keys.get((row["package"], row["arm"]), [])
        pairs = [jaccard(a, b) for i, a in enumerate(sets) for b in sets[i + 1 :]]
        row["jaccard"] = round(sum(pairs) / len(pairs), 3) if pairs else None
    _write(out / "scores.json", rows)
    return rows


# --- отчёт: стоимость прогона по рукам и прогноз полного прогона ---------------------------------------------------

# сценарии полного прогона (PROTOCOL): отложенная часть главного слоя — не меньше 80 дефектов; у генератора v1
# в среднем 7,5 дефекта на пакет → 11 пакетов; прогоны агентских рук — 3 или 5; B и A1 не сокращаются
SCENARIOS = [
    ("минимум: A1 и B, 11 пакетов × 3 прогона", ("A1", "B"), 11, 3),
    ("A0, A1, B, 11 пакетов × 3 прогона", ("A0", "A1", "B"), 11, 3),
    ("A0, A1, B, 11 пакетов × 5 прогонов", ("A0", "A1", "B"), 11, 5),
    ("A0, A1, B, B1, 20 пакетов × 5 прогонов", ("A0", "A1", "B", "B1"), 20, 5),
]


def _fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    """Стоимость прогона = a + b × листов (наименьшие квадраты); одна точка — пропорционально листам."""
    if not points:
        return 0.0, 0.0
    xs = [x for x, _ in points]
    if len(set(xs)) < 2:
        return 0.0, sum(y for _, y in points) / max(sum(xs), 1)
    mx, my = sum(xs) / len(points), sum(y for _, y in points) / len(points)
    b = sum((x - mx) * (y - my) for x, y in points) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def report_pilot(out: Path, rows: list[dict], mean_sheets: float) -> str:
    runs = [json.loads(x) for x in _lines(out / "runs.jsonl")]
    by = {(r["package"], r["arm"], r["k"]): r for r in runs}
    lines = ["# Пилот бюджета: стоимость прогона по рукам", ""]
    lines += [
        "| Пакет | Листов | Рука | Прогонов | $ на прогон | Минут | Ходов | Вызовов"
        " | Токены: ввод / вывод / кэш, тыс. | Recall | Точность | Ложных (на отвлекающем) | Жаккар | Сбои |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    groups: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        groups.setdefault((row["package"], row["arm"]), []).append(row)
    for (pkg, arm), rs in sorted(groups.items(), key=lambda kv: (kv[1][0]["sheets"], kv[0][1])):
        raw = [by[(pkg, arm, r["k"])] for r in rs]
        ok = [r for r in rs if r["ok"]]

        def mean(key, src=ok):
            vals = [x[key] for x in src if x.get(key) is not None]
            return sum(vals) / len(vals) if vals else None

        tok = {k: sum(r["tokens"].get(k, 0) for r in raw) / len(raw) / 1000 for k in ("input", "output", "cache_read")}
        calls = sum(sum(r["tool_calls"].values()) for r in raw) / len(raw)
        fails = [r["error"][:60] for r in rs if r.get("error")]
        lines.append(
            f"| {pkg} | {rs[0]['sheets']} | {arm} | {len(rs)} | {mean('cost_usd', rs):.2f}"
            f" | {mean('minutes', rs):.1f} |"
            f" {sum(r['turns'] for r in raw) / len(raw):.0f} | {calls:.0f} |"
            f" {tok['input']:.0f} / {tok['output']:.0f} / {tok['cache_read']:.0f} |"
            f" {_fmt(mean('recall'))} | {_fmt(mean('precision'))} | {_fmt(mean('false_alarms'), 1)}"
            f" ({_fmt(mean('on_notes'), 1)}) |"
            f" {_fmt(rs[0].get('jaccard'))} | {'; '.join(fails) or '—'} |"
        )
    # прогноз
    lines += ["", f"## Прогноз полного прогона (средний пакет — {mean_sheets:.0f} листов)", ""]
    fits = {}
    for arm in sorted({r["arm"] for r in rows}):
        pts = [(r["sheets"], r["cost_usd"]) for r in rows if r["arm"] == arm and r["ok"]]
        fits[arm] = _fit(pts)
    fits.setdefault("B1", tuple(x + y for x, y in zip(fits.get("A1", (0, 0)), fits.get("B", (0, 0)), strict=True)))
    agent = [r for r in runs if r["arm"] in AGENT_ARMS and r.get("five_hour") is not None]
    share = None
    if len(agent) >= 2:
        spent = sum(r["cost_usd"] for r in agent)
        delta = max(r["five_hour"] for r in agent) - min(r["five_hour"] for r in agent)
        share = delta / spent if spent else None
    lines += ["| Сценарий | $ по ценам API | Пятичасовых окон подписки |", "|---|---|---|"]
    for title, arms, packages, k in SCENARIOS:
        total = sum((fits[a][0] + fits[a][1] * mean_sheets) * packages * k for a in arms if a in fits)
        windows = f"{total * share:.1f}" if share else "—"
        lines.append(f"| {title} | {total:,.0f} | {windows} |".replace(",", " "))
    lines += ["", "Стоимость прогона по рукам (a + b × листов):", ""]
    lines += [f"- {arm}: {a:.2f} + {b * 1000:.2f} $ на 1000 листов" for arm, (a, b) in sorted(fits.items())]
    if share:
        lines += ["", f"Доля пятичасового окна на 1 $ по ценам API — {share:.4f} (по прогонам пилота)."]
    for r in runs:
        if r["arm"] == "A1-author":
            lines += [
                "",
                f"Скилы A1 (разово): ${r['cost_usd']}, {r['minutes']} мин"
                + (f", сбой: {r['error']}" if r["error"] else ""),
            ]
    text = "\n".join(lines) + "\n"
    (out / "report.md").write_text(text, encoding="utf-8")
    return text


def _fmt(x, digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


# --- скилы руки A1: отдельная сессия-автор без доступа к репозиторию -----------------------------------------------


def author_skills(out: Path, workdir: Path, bim_data: Path, model: str, budget: float, effort: str | None) -> Run:
    """Скилы A1 пишет отдельная сессия Claude Code (PROTOCOL, рука A1).

    Сессия получает только публичные источники из bim-data и задачу рук. Репозитория «Нулевого цикла», правил, корпуса
    и отложенной части у неё нет. Итог — <out>/skills_A1/: его копируют в папки прогонов A1 и публикуют с результатами.
    """
    from zerocycle.bench.arms import A1_AUTHOR_TASK, A1_SOURCES

    if workdir.exists():
        shutil.rmtree(workdir)
    (workdir / "sources").mkdir(parents=True)
    for rel in A1_SOURCES:
        shutil.copy2(bim_data / rel, workdir / "sources" / Path(rel).name)
    mcp = workdir.parent / f"{workdir.name}.mcp.json"
    mcp.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    run = Run(package="—", arm="A1-author", k=1)
    run = run_agent(run, workdir, mcp, out, model, budget, effort, prompt=A1_AUTHOR_TASK.format(task=TASK), schema=None)
    skills = workdir / ".claude" / "skills"
    if skills.is_dir() and any(skills.iterdir()):
        target = out / "skills_A1"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skills, target)
    else:
        run.ok, run.error = False, run.error or "сессия-автор не оставила .claude/skills/"
    mcp.unlink(missing_ok=True)
    with (out / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")
    return run
