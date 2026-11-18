"""Прогоны V3 на Codex (docs/bench/PROTOCOL_V3.md): папки рук, codex exec, ожидание лимита, итоги.

Рука работает в C:\\zc-v3\\work\\<прогон> — в копии package/ вне репозитория. Песочница Codex (профиль прав и
Windows-песочница elevated) даёт писать только в эту папку и не даёт читать репозитории, конфиги агентов и личные
папки (deny_paths). Итоги — var/v3/runs/<план>/: runs.jsonl и папка прогона с задачей, событиями и ответом.

Лимит подписки кончился посреди прогона — драйвер ждёт времени из сообщения Codex и повторяет прогон; оборванный
прогон в итоги не идёт. Повторный запуск продолжает с места: сделанные прогоны пропускаются.

    python -X utf8 -m zerocycle.bench.v3run run --plan check          # обвязка: A0 на двух пакетах разработки
    python -X utf8 -m zerocycle.bench.v3run status --plan check
    python -X utf8 -m zerocycle.bench.v3run probe sandbox|skills      # дешёвые проверки среды
    python -X utf8 -m zerocycle.bench.v3run autostart --install --plan pilot | --remove
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from zerocycle.bench.arms import ANSWER_SCHEMA, CATEGORIES, MANUAL, VIOLATED
from zerocycle.bench.pilot import _norm, _outside
from zerocycle.bench.v3 import split

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "var" / "v3" / "kz"
RESULTS = REPO / "var" / "v3" / "runs"
WORK = Path(r"C:\zc-v3\work")  # папки рук: вне репозитория и корпуса, короткий путь — длинные имена архивов
START = Path(r"C:\zc-v3\start.cmd")
STARTUP = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
LINK = STARTUP / "zc-v3.lnk"

MODEL, EFFORT = "gpt-6.1-sol", "high"  # PROTOCOL_V3, «Руки»
TIMEOUT_MIN = 60
ARMS = {
    "A0": "задача и пакет",
    "A1": "+ скилы по открытым нормам в .agents/skills",
    "Be": "B′, движок первым",
    "Ba": "B′, агент сначала",
}

TASK_V3 = """Ты — нормоконтролёр и ГИП. Перед подачей в экспертизу проверь пакет проектно-сметной документации
(Республика Казахстан, стадия «РП»). Пакет лежит в папке package/ текущей рабочей папки: PDF, сметы и таблицы Excel,
документы Word, изображения; часть файлов — в подпапках.

Что искать:
- комплектность: разделы проекта, исходно-разрешительные документы, изыскания, расчёты, сметная документация и другие
  документы, которые требуют нормы РК (Правила проведения комплексной вневедомственной экспертизы — Приказ № 299 и
  приложения к нему, СН РК 1.02-03-2022);
- оформление по ГОСТ 21.101-97: основные надписи, нумерация листов, ведомости и содержания томов;
- увязка между томами: наименование объекта, технико-экономические показатели;
- ссылки на нормативные документы, которые не действуют или заменены, по АГСК-1.

Как отвечать:
- в findings — только то, что ты подтвердил по файлам пакета: документ (путь внутри package/), страница PDF с 1
  (null — документ целиком), что не так и основание — документ и пункт нормы. Если документа в пакете нет — напиши в
  description, какого документа не хватает, а в document — «package/»;
- статус «нарушение» — дефект подтверждён; «проверить вручную» — нужен человек;
- не выдумывай ссылки на нормы и числа; чего проверить не удалось — напиши в summary.

Работай только с папкой package/ и текущей рабочей папкой: файлы вне неё не открывай и не ищи, черновики складывай в
текущую папку. Интернета нет. Python 3 — команда python: pdfplumber, pypdfium2, PyMuPDF (fitz), openpyxl,
python-docx, pandas, lxml, Pillow."""

ENGINE_FIRST = """

У тебя есть инструменты MCP «Нулевого цикла» (сервер zc) — движок формальных проверок пакета. Сначала запусти
проверку пакета (check_package, затем run_status, пока она не закончится), разбери его находки и отчёт о покрытии,
потом сам проверь то, чего движок не проверял, и сверь его находки с файлами. В ответ — только подтверждённое."""

AGENT_FIRST = """

У тебя есть инструменты MCP «Нулевого цикла» (сервер zc) — движок формальных проверок пакета. Сначала проверь пакет
сам. Когда закончишь, запусти проверку движком (check_package, затем run_status, пока она не закончится), сверь его
находки и отчёт о покрытии со своими и дополни ответ. В ответ — только подтверждённое."""

# Возможности Codex, которых у рук нет: память, плагины и их скилы (notion, sites…), приложения-коннекторы, браузер,
# управление компьютером, картинки, субагенты. Одинаково у всех рук; у B′ остаётся только MCP «Нулевого цикла».
OFF = (
    "memories",
    "plugins",
    "remote_plugin",
    "apps",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "computer_use",
    "image_generation",
    "multi_agent",
)

LIMIT = re.compile(r"usage limit|hit your limit|rate limit reached|quota", re.I)
TRANSIENT = re.compile(
    r"stream disconnected|error sending request|connection|timed? ?out|temporar|unavailable|overloaded|"
    r"at capacity|\b50[234]\b|internal server error|reconnect",
    re.I,
)


class LimitHit(RuntimeError):
    """Лимит подписки: ждать сброса и повторить прогон."""


class Transient(RuntimeError):
    """Сеть или сервер: повторить прогон через несколько минут."""


@dataclass(frozen=True)
class Item:
    package: str
    arm: str
    k: int

    @property
    def id(self) -> str:
        return f"{self.package}.{self.arm}.{self.k}"


def plan(name: str) -> list[Item]:
    """Порядок: повтор → пакет → рука — если время кончится, сравнение останется парным (PROTOCOL_V3)."""
    parts = split()
    if name == "check":  # обвязка: два небольших пакета разработки
        return [Item("kz-06", "A0", 1), Item("kz-03", "A0", 1)]
    if name == "pilot":
        return [Item(p, a, 1) for p in parts["dev"] for a in ARMS]
    if name == "main":
        repeats = {"A0": 1, "A1": 2, "Ba": 2}  # порядок B′ выбирается в пилоте — до заморозки поправить здесь
        return [Item(p, a, k) for k in (1, 2) for p in parts["heldout"] for a in repeats if k <= repeats[a]]
    raise ValueError(f"нет плана {name}")


def prompt_for(arm: str) -> str:
    return TASK_V3 + {"Be": ENGINE_FIRST, "Ba": AGENT_FIRST}.get(arm, "")


# --- среда руки ---------------------------------------------------------------------------------------------------


REPO_DIRS = ("var", "src", "docs", "tests", "scripts", ".git")  # корпус, эталон, разметка, код засева, история


def deny_paths() -> list[Path]:
    """Что рука не читает: папки репозитория с корпусом, эталоном, разметкой и кодом засева; соседние папки
    рабочего пространства; прежний бенчмарк; конфиги и сессии агентов; черновики Claude Code; личные папки.

    Запрет — на каждой папке, а не на корне репозитория: прежние сессии Codex с рабочей папкой в репозитории оставляют
    на нём явное разрешение группе песочницы, и оно перекрывает унаследованный запрет (проверено 18.11). Явный запрет
    на самой папке идёт раньше унаследованного разрешения. В корне репозитория остаются pyproject, README и кэши.
    """
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    workspace = REPO.parent
    siblings = sorted(p for p in workspace.iterdir() if p.is_dir() and p != REPO) if workspace.exists() else []
    paths = [
        *(REPO / n for n in REPO_DIRS),
        *siblings,
        Path(r"C:\zc-bench"),
        home / ".codex",
        home / ".claude",
        local / "Temp" / "claude",
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "OneDrive",
    ]
    out: list[Path] = []
    for p in paths:
        if p.exists() and not any(p == q or q in p.parents for q in out):
            out.append(p)
    return out


def permissions() -> str:
    """Профиль прав руки одним значением -c: ключ в -c Codex делит по точкам без учёта кавычек, и путь с точкой
    («.codex») так не задать; внутри TOML-таблицы кавычки работают. Elevated-песочница требует чтения корня, запреты
    поверх — deny_paths; писать — только в рабочей папке; сети нет."""
    fs = {":root": "read", ":workspace_roots": "write", **{str(p): "deny" for p in deny_paths()}}
    table = ", ".join(f"{json.dumps(k)}={json.dumps(v)}" for k, v in fs.items())
    return "permissions={arm={filesystem={" + table + "}, network={enabled=false}}}"


def codex_exe() -> tuple[str, dict[str, str]]:
    """Сам codex.exe, а не обёртка npm: через .cmd кавычки в ключах -c не доходят целыми."""
    npm = Path(os.environ.get("APPDATA", "")) / "npm" / "node_modules" / "@openai" / "codex"
    exe = npm / "node_modules" / "@openai" / "codex-win32-x64" / "vendor" / "x86_64-pc-windows-msvc" / "bin"
    exe = exe / "codex.exe"
    if exe.exists():
        return str(exe), {"CODEX_MANAGED_BY_NPM": "1", "CODEX_MANAGED_PACKAGE_ROOT": str(npm)}
    found = shutil.which("codex")
    if not found:
        raise RuntimeError("не найден codex")
    return found, {}


def codex_version() -> str:
    exe, extra = codex_exe()
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, env={**os.environ, **extra}).stdout
    return out.strip().removeprefix("codex-cli ").strip()


def codex_cmd(
    run_dir: Path,
    answer: Path,
    schema: Path | None,
    model: str = MODEL,
    effort: str = EFFORT,
    mcp: dict | None = None,
) -> list[str]:
    """codex exec без пользовательского конфига: модель, усилие и права — только отсюда."""
    exe, _ = codex_exe()
    cfg = [
        f'model_reasoning_effort="{effort}"',
        'windows.sandbox="elevated"',  # без elevated Windows-песочница не умеет запрет чтения
        'default_permissions="arm"',
        permissions(),
        "skills.bundled.enabled=false",
        'web_search="disabled"',  # поиск идёт на сервере, мимо песочницы: нашёл бы заключение по названию объекта
    ]
    if mcp:
        cfg += [
            f"mcp_servers.zc.command={json.dumps(mcp['command'])}",
            f"mcp_servers.zc.args={json.dumps(mcp['args'], ensure_ascii=False)}",
            "mcp_servers.zc.env={" + ", ".join(f"{k}={json.dumps(v)}" for k, v in mcp.get("env", {}).items()) + "}",
            "mcp_servers.zc.startup_timeout_sec=120",
            "mcp_servers.zc.tool_timeout_sec=600",
            'mcp_servers.zc.default_tools_approval_mode="approve"',
        ]
    cmd = [exe, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check"]
    cmd += [x for f in OFF for x in ("--disable", f)]
    cmd += ["-m", model, *(x for c in cfg for x in ("-c", c)), "-C", str(run_dir)]
    if schema:
        cmd += ["--output-schema", str(schema)]
    return [*cmd, "-o", str(answer), "--json", "-"]


@functools.cache
def arm_python() -> str:
    """Системный Python для рук — без окружения проекта: иначе A0 и A1 смогли бы импортировать движок.

    Сам интерпретатор, а не псевдоним из WindowsApps: псевдоним менеджера установки в песочнице может не запуститься.
    """
    if os.environ.get("ZC_ARM_PYTHON"):
        return os.environ["ZC_ARM_PYTHON"]
    venv = str(REPO / ".venv").lower()
    for d in os.environ.get("PATH", "").split(os.pathsep):
        exe = Path(d) / "python.exe"
        if not d or venv in d.lower() or not exe.exists():
            continue
        try:
            real = subprocess.run([str(exe), "-c", "import sys; print(sys.executable)"], capture_output=True, text=True)
        except OSError:  # псевдоним WindowsApps без цели
            continue
        if real.returncode == 0 and real.stdout.strip() and venv not in real.stdout.lower():
            return real.stdout.strip()
    raise RuntimeError("не найден системный python")


def guard_targets() -> list[Path]:
    """Что рука прочесть не должна — по файлу на главные запреты: код засева, сводка корпуса, эталон, история git,
    конфиг Codex, память Claude Code."""
    out = [
        REPO / "src" / "zerocycle" / "bench" / "v3.py",
        REPO / "docs" / "bench" / "DATASET_V3.md",
        REPO / ".git" / "HEAD",
        Path.home() / ".codex" / "config.toml",
        Path.home() / ".claude" / "settings.json",
    ]
    out += sorted(CORPUS.glob("*/ground_truth.json"))[:1]
    return [p for p in out if p.exists()]


def sandbox_guard(tries: int = 4, say=print) -> None:
    """Бесплатная проверка перед прогоном (codex sandbox, без модели): рука читает и пишет в своей папке, не читает
    закрытое и не пишет мимо папки. Иначе прогон не начинаем.

    Запреты — общие для группы песочницы: обычная сессия Codex на этой машине в то же время может их снять. Поэтому
    проверка — перед каждым прогоном, а не раз на план.
    """
    exe, extra = codex_exe()
    run_dir = WORK / "guard"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "inside.txt").write_text("ok", encoding="utf-8")
    base = [exe, "sandbox", "-c", 'windows.sandbox="elevated"', "-c", permissions(), "--permission-profile", "arm"]
    stray = REPO / "zz_v3_guard.txt"  # запись мимо папки руки — в корень репозитория

    def sandboxed(command: str) -> bytes:
        cmd = [*base, "-C", str(run_dir), "--", "cmd", "/c", command]
        return subprocess.run(cmd, capture_output=True, env={**os.environ, **extra}, timeout=120).stdout

    for attempt in range(1, tries + 1):
        wrong = []
        for target in [run_dir / "inside.txt", *guard_targets()]:
            readable = b"READ" in sandboxed(f'type "{target}" >nul 2>nul && echo READ || echo NO')
            if readable != (target.parent == run_dir):
                wrong.append(f"чтение {target}")
        if b"WROTE" not in sandboxed('(echo x> "own.txt") 2>nul && echo WROTE || echo NO'):
            wrong.append("запись в своей папке")
        if b"WROTE" in sandboxed(f'(echo x> "{stray}") 2>nul && echo WROTE || echo NO') or stray.exists():
            stray.unlink(missing_ok=True)
            wrong.append(f"запись в {stray.parent}")
        if not wrong:
            say("песочница: своя папка открыта; репозиторий, сессии агентов и личные папки закрыты")
            return
        say(f"песочница не готова: {wrong} — жду ({attempt}/{tries})")
        time.sleep(30)
    raise RuntimeError("песочница Codex не закрывает репозиторий — прогон не начинаю")


def arm_env(run_dir: Path) -> dict[str, str]:
    venv = str(REPO / ".venv").lower()
    drop = {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"}
    env = {k: v for k, v in os.environ.items() if k.upper() not in drop}
    path = [d for d in env.get("PATH", "").split(os.pathsep) if d and venv not in d.lower()]
    py = str(Path(arm_python()).parent)
    env["PATH"] = os.pathsep.join([py, *[d for d in path if d != py]])
    tmp = run_dir / ".tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env.update(TMP=str(tmp), TEMP=str(tmp), PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.update(codex_exe()[1])
    return env


def prepare(run_dir: Path, package: Path, arm: str, skills: Path | None = None) -> None:
    """Папка руки: копия package/; у A1 — скилы в .agents/skills (их Codex находит сам)."""
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    if package.exists():
        shutil.copytree(package, run_dir / "package")
    if arm == "A1":
        if not skills or not any(skills.rglob("SKILL.md")):
            raise RuntimeError("для руки A1 нужны скилы (--skills): их пишет сессия-автор, PROTOCOL_V3")
        shutil.copytree(skills, run_dir / ".agents" / "skills")


# --- запуск -------------------------------------------------------------------------------------------------------


def valid(answer: dict) -> str | None:
    """Ответ по схеме: None — да, иначе что не так."""
    if not isinstance(answer, dict) or not isinstance(answer.get("findings"), list):
        return "нет findings"
    for i, f in enumerate(answer["findings"]):
        if not isinstance(f, dict):
            return f"находка {i}: не объект"
        missing = [k for k in ("category", "status", "document", "page", "description", "basis") if k not in f]
        if missing:
            return f"находка {i}: нет {', '.join(missing)}"
        if f["category"] not in CATEGORIES or f["status"] not in (VIOLATED, MANUAL):
            return f"находка {i}: категория или статус вне схемы"
    return None


def audit(commands: list[str], run_dir: Path) -> list[str]:
    """Пути вне папки руки в командах и правках — кроме системных: оболочки, Python рук, сам Codex."""
    allowed = [r"C:\Windows", r"C:\Program Files", str(Path(arm_python()).parent), str(Path(codex_exe()[0]).parent)]
    allowed += [str(Path(sys.base_prefix))]
    prefixes = tuple(_norm(a) for a in allowed)
    return sorted(p for p in _outside({"c": commands}, run_dir) if not _norm(p).startswith(prefixes))


def _kill(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)


def execute(cmd: list[str], prompt: str, run_dir: Path, out: Path, timeout_min: float, say=print) -> dict:
    """codex exec с задачей через stdin; события — в out/events.jsonl по мере прихода.

    Потолок времени — по часам бодрствования: сон ноутбука срок не съедает (сдвигаем срок на длину сна).
    """
    events_path, errors, items, commands = out / "events.jsonl", [], {}, []
    usage: dict = {}
    thread_id = None
    proc = subprocess.Popen(
        cmd,
        cwd=run_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=arm_env(run_dir),
    )
    stderr_lines: list[str] = []

    def drain() -> None:
        for raw in proc.stderr:
            stderr_lines.append(raw.decode("utf-8", "replace"))

    started = time.time()
    state = {"deadline": started + timeout_min * 60, "timed_out": False, "done": False}

    def watchdog() -> None:
        last, beat = time.time(), time.time()
        while not state["done"]:
            time.sleep(15)
            now = time.time()
            if now - last > 120:  # спали: срок сдвигается на время сна
                state["deadline"] += now - last - 15
            last = now
            if now - beat >= 300 and not state["done"]:
                beat = now
                n, shell = sum(items.values()), items.get("command_execution", 0)
                say(f"    … идёт {round((now - started) / 60)} мин: шагов {n}, команд {shell}")
            if now > state["deadline"] and not state["done"]:
                state["timed_out"] = True
                _kill(proc)
                return

    threading.Thread(target=drain, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    proc.stdin.write(prompt.encode("utf-8"))
    proc.stdin.close()
    with events_path.open("w", encoding="utf-8") as log:
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            log.write(line + "\n")
            log.flush()
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = e.get("type")
            if kind == "thread.started":
                thread_id = e.get("thread_id")
            elif kind == "item.completed":
                it = e.get("item", {})
                items[it.get("type", "?")] = items.get(it.get("type", "?"), 0) + 1
                if it.get("type") == "command_execution":
                    commands.append(str(it.get("command", "")))
                elif it.get("type") == "file_change":
                    commands.extend(str(c.get("path", "")) for c in it.get("changes", []))
            elif kind == "turn.completed":
                usage = e.get("usage", {}) or usage
            elif kind in ("error", "turn.failed"):
                errors.append(str(e.get("message") or (e.get("error") or {}).get("message", "")))
    proc.wait()
    state["done"] = True
    (out / "stderr.txt").write_text("".join(stderr_lines), encoding="utf-8")
    return {
        "thread_id": thread_id,
        "usage": usage,
        "items": items,
        "errors": errors,
        "timed_out": state["timed_out"],
        "returncode": proc.returncode,
        "minutes": round((time.time() - started) / 60, 2),
        "outside": audit(commands, run_dir),
        "stderr_tail": "".join(stderr_lines)[-600:],
    }


def run_one(item: Item, plan_name: str, skills: Path | None, keep: bool, say=print) -> dict:
    out = RESULTS / plan_name / item.id
    out.mkdir(parents=True, exist_ok=True)
    run_dir = WORK / item.id
    prepare(run_dir, CORPUS / item.package / "package", item.arm, skills)
    prompt = prompt_for(item.arm)
    (out / "prompt.txt").write_text(prompt, encoding="utf-8")
    schema = out / "schema.json"
    schema.write_text(json.dumps(ANSWER_SCHEMA, ensure_ascii=False, indent=1), encoding="utf-8")
    answer_path = out / "answer.json"
    answer_path.unlink(missing_ok=True)
    mcp = engine_mcp(run_dir) if item.arm in ("Be", "Ba") else None
    started = datetime.now()
    try:
        r = execute(codex_cmd(run_dir, answer_path, schema, mcp=mcp), prompt, run_dir, out, TIMEOUT_MIN, say)
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)
    text = " | ".join(r["errors"]) + " " + r["stderr_tail"]
    if LIMIT.search(text) and not answer_path.exists():
        raise LimitHit(text.strip()[:500])
    rec = {
        "id": item.id,
        "package": item.package,
        "arm": item.arm,
        "k": item.k,
        "plan": plan_name,
        "started": started.isoformat(timespec="seconds"),
        "codex": codex_version(),
        "model": MODEL,
        "effort": EFFORT,
        "pdf_pages": _pages(item.package),
        **{k: r[k] for k in ("thread_id", "usage", "items", "minutes", "outside", "errors")},
    }
    error = None
    if r["timed_out"]:
        error = f"таймаут {TIMEOUT_MIN} мин"
    elif not answer_path.exists():
        if TRANSIENT.search(text):
            raise Transient(text.strip()[:500])
        error = f"нет ответа: {text.strip()[:300] or 'код ' + str(r['returncode'])}"
    else:
        try:
            answer = json.loads(answer_path.read_text(encoding="utf-8"))
            error = valid(answer)
            rec["findings"] = len(answer.get("findings", [])) if isinstance(answer, dict) else 0
        except json.JSONDecodeError as e:
            error = f"ответ не JSON: {e}"
    rec["status"], rec["error"] = ("failed", error) if error else ("ok", None)
    return rec


def _pages(package: str) -> int | None:
    truth = CORPUS / package / "ground_truth.json"
    return json.loads(truth.read_text(encoding="utf-8")).get("pdf_pages") if truth.exists() else None


def engine_mcp(run_dir: Path) -> dict:
    """MCP-сервер движка для B′. Движок v2 ещё не готов (PROTOCOL_V3, «Порядок работ», п. 4)."""
    raise RuntimeError("рука B′ ждёт движка v2: путь к замороженной копии и её окружение задаются при заморозке")


# --- лимит и ожидание ---------------------------------------------------------------------------------------------

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct"], 1)}
MONTHS |= {"nov": 11, "dec": 12}
AT_DATE = re.compile(
    r"try again at\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(?:(\d{4}),?\s+)?"
    r"(\d{1,2}):(\d{2})\s*([AP]M)?",
    re.I,
)


def reset_at(msg: str, now: datetime | None = None) -> datetime | None:
    """Время сброса лимита из сообщения Codex: «try again at 8:13 PM», «… at Nov 20th, 2026 9:00 AM», «… in 2 hours»."""
    now = now or datetime.now()
    m = AT_DATE.search(msg)
    if m and m[1].lower() in MONTHS:
        hour = int(m[4]) % 12 + (12 if (m[6] or "").upper() == "PM" else 0) if m[6] else int(m[4])
        t = datetime(int(m[3] or now.year), MONTHS[m[1].lower()], int(m[2]), hour, int(m[5]))
        return t if t > now or m[3] else t.replace(year=t.year + 1)
    m = re.search(r"try again at\s+(\d{1,2}):(\d{2})\s*([AP]M)?", msg, re.I)
    if m:
        hour = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0) if m[3] else int(m[1])
        t = now.replace(hour=hour, minute=int(m[2]), second=0, microsecond=0)
        return t if t > now else t + timedelta(days=1)
    m = re.search(r"try again in\s+((?:\d+\s*(?:days?|hours?|hrs?|minutes?|mins?)[\s,and]*)+)", msg, re.I)
    if m:
        delta = timedelta()
        for n, unit in re.findall(r"(\d+)\s*(d|h|m)", m[1].lower()):
            delta += {"d": timedelta(days=int(n)), "h": timedelta(hours=int(n)), "m": timedelta(minutes=int(n))}[unit]
        return now + delta if delta else None
    return None


def sleep_until(t: datetime, say=print) -> None:
    say(f"  лимит Codex: жду до {t:%d.%m %H:%M} ({_left(t)})")
    while (left := (t - datetime.now()).total_seconds()) > 0:
        time.sleep(min(left, 300))


def _left(t: datetime) -> str:
    m = max(0, round((t - datetime.now()).total_seconds() / 60))
    return f"через {m // 60} ч {m % 60:02d} мин" if m >= 60 else f"через {m} мин"


# --- драйвер ------------------------------------------------------------------------------------------------------


def runs_path(plan_name: str) -> Path:
    return RESULTS / plan_name / "runs.jsonl"


def load_runs(plan_name: str) -> list[dict]:
    p = runs_path(plan_name)
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def lock_path() -> Path:
    return RESULTS / "v3run.lock"


def _alive(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def take_lock() -> bool:
    p = lock_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        pid = int(p.read_text().strip() or 0)
        if pid and pid != os.getpid() and _alive(pid):
            return False
    p.write_text(str(os.getpid()))
    return True


def log(msg: str) -> None:
    line = f"[{datetime.now():%d.%m %H:%M}] {msg}"
    print(line, flush=True)
    with (RESULTS / "driver.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def bar(done: int, total: int, width: int = 24) -> str:
    fill = round(width * done / max(total, 1))
    return f"[{'#' * fill}{'.' * (width - fill)}] {done}/{total}"


def drive(plan_name: str, skills: Path | None = None, keep: bool = False) -> int:
    items = plan(plan_name)
    if not take_lock():
        log(f"драйвер уже идёт (замок {lock_path()})")
        return 1
    try:
        done = {r["id"] for r in load_runs(plan_name)}
        todo = [i for i in items if i.id not in done]
        log(f"план {plan_name}: {bar(len(done), len(items))}, осталось {len(todo)}; {MODEL}, {EFFORT}")
        for item in todo:
            transient = 0
            while True:
                try:
                    sandbox_guard(say=log)
                except RuntimeError as e:  # запреты сняла чужая сессия Codex — подождать, а не гнать руку без них
                    log(f"  {e}; повторю через 10 мин")
                    time.sleep(600)
                    continue
                log(f"{bar(len(done), len(items))} {item.id}: старт ({ARMS[item.arm]})")
                try:
                    rec = run_one(item, plan_name, skills, keep, say=log)
                except LimitHit as e:
                    until = reset_at(str(e)) or datetime.now() + timedelta(minutes=30)
                    sleep_until(until + timedelta(minutes=2), log)
                    continue
                except Transient as e:
                    transient += 1
                    log(f"  сбой сети или сервера ({transient}/5): {str(e)[:160]}")
                    if transient < 5:
                        time.sleep(300 * transient)
                        continue
                    rec = {"id": item.id, "package": item.package, "arm": item.arm, "k": item.k, "plan": plan_name}
                    rec.update(status="failed", error=f"сбой сети: {str(e)[:300]}")
                with runs_path(plan_name).open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                done.add(item.id)
                u = rec.get("usage") or {}
                log(
                    f"{bar(len(done), len(items))} {item.id}: {rec['status']}"
                    + (f" — {rec['error']}" if rec.get("error") else f", находок {rec.get('findings')}")
                    + f", {rec.get('minutes', 0)} мин, токенов {u.get('input_tokens', 0):,} вх."
                    f" (кэш {u.get('cached_input_tokens', 0):,}), {u.get('output_tokens', 0):,} вых."
                    + (f"; вне папки: {rec['outside'][:3]}" if rec.get("outside") else "")
                )
                break
        log(f"план {plan_name} закончен: {bar(len(done), len(items))}")
        return 0
    finally:
        lock_path().unlink(missing_ok=True)


def status(plan_name: str) -> str:
    items = plan(plan_name)
    runs = {r["id"]: r for r in load_runs(plan_name)}
    lines = [f"план {plan_name}: {bar(len(runs), len(items))}"]
    for arm in ARMS:
        mine = [r for r in runs.values() if r["arm"] == arm]
        want = sum(1 for i in items if i.arm == arm)
        if want:
            ok = sum(r["status"] == "ok" for r in mine)
            tok = sum((r.get("usage") or {}).get("input_tokens", 0) for r in mine)
            lines.append(f"  {arm}: {len(mine)}/{want}, ok {ok}, входных токенов {tok:,}")
    p = lock_path()
    alive = p.exists() and _alive(int(p.read_text().strip() or 0))
    lines.append("драйвер: " + ("идёт, PID " + p.read_text().strip() if alive else "не запущен"))
    driver_log = RESULTS / "driver.log"
    tail = driver_log.read_text(encoding="utf-8").splitlines()[-3:] if driver_log.exists() else []
    return "\n".join(lines + tail)


# --- проверки среды -----------------------------------------------------------------------------------------------

PROBE_SKILL = """---
name: zc-probe
description: Проверочный скил обвязки бенчмарка. Применять, только когда спрашивают, какие скилы доступны.
---

Если тебя спросили о доступных скилах, назови этот скил: zc-probe.
"""


def probe(kind: str, keep: bool = False, skills: Path | None = None) -> dict:
    """Дешёвые проверки: песочница (Python есть, репозиторий закрыт, запись в папке) и скилы в .agents/skills —
    проверочный или настоящие (skills)."""
    out = RESULTS / "probe" / kind
    out.mkdir(parents=True, exist_ok=True)
    sandbox_guard()
    run_dir = WORK / f"probe-{kind}"
    prepare(run_dir, Path("-"), "A1" if skills else "A0", skills)
    secret = REPO / "pyproject.toml"
    if kind == "sandbox":
        prompt = (
            "Это проверка среды, а не задача. Выполни три команды и для каждой одной строкой напиши, что вышло:\n"
            "1) python -c \"import pdfplumber, pypdfium2, openpyxl, docx; print('libs ok')\"\n"
            f"2) прочитай первые строки файла {secret} (это вне рабочей папки — ожидаем отказ доступа);\n"
            "3) создай в текущей папке файл probe.txt со словом ok.\nБольше ничего не делай."
        )
    elif kind == "skills":
        if not skills:
            (run_dir / ".agents" / "skills" / "zc-probe").mkdir(parents=True)
            (run_dir / ".agents" / "skills" / "zc-probe" / "SKILL.md").write_text(PROBE_SKILL, encoding="utf-8")
        prompt = "Это проверка среды. Перечисли имена скилов и инструментов, которые тебе доступны. Ничего не выполняй."
    else:
        raise ValueError(kind)
    answer = out / "answer.txt"
    answer.unlink(missing_ok=True)
    try:
        r = execute(codex_cmd(run_dir, answer, None), prompt, run_dir, out, 10, say=print)
        r["probe_file"] = (run_dir / "probe.txt").exists()
    finally:
        if not keep:
            shutil.rmtree(run_dir, ignore_errors=True)
    r["answer"] = answer.read_text(encoding="utf-8") if answer.exists() else None
    (out / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return r


# --- скилы A1 -----------------------------------------------------------------------------------------------------

AUTHOR_DIR = Path(r"C:\zc-v3\a1-author")  # вне репозитория, те же запреты песочницы, что у рук
SKILLS_A1 = REPO / "docs" / "bench" / "skills_A1"  # публикуются с результатами (PROTOCOL_V3)
SKILLS_SHA = REPO / "docs" / "bench" / "freeze_v3" / "skills_A1.sha256"
BIM_DATA = REPO.parent / "bim-data"
# Открытые нормы — те же, на которых стоит пакет правил kz (и бенчмарк на реальных пакетах): движок v2 из открытых
# источников берёт только их, отличие B′ — знание из замечаний экспертизы. Текста СН РК 1.02-03-2022 в открытом
# доступе нет (поиск 18.11): нет ни у A1, ни у движка.
A1_SOURCES = (
    "norms/kz/GOST_21.101-97_SPDS_osnovnye_trebovaniya_meganorm.txt",
    "norms/kz/GOST_21.101-97_forms",
    "norms/kz/AGSK-1_2025-06.pdf",
    "norms/kz/gosexpertiza_prilozhenie_2_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_3_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_4_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_4-1_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_5_299.docx",
)
AUTHOR_TIMEOUT_MIN = 90

A1_AUTHOR_TASK = """Ты — сильный пользователь Codex. Подготовь скилы Codex, с которыми агент проверит пакет
проектно-сметной документации Казахстана перед подачей в экспертизу. Агент получит такую задачу:

---
{task}
---

Агент работает в Codex на Windows (оболочка PowerShell), без интернета. Его рабочая папка — папка прогона: пакет в
package/, твои скилы будут лежать в .agents/skills/<имя>/, Codex находит их сам по описанию. Python 3 — команда
python: pdfplumber, pypdfium2, PyMuPDF (fitz), openpyxl, python-docx, pandas, lxml, Pillow, reportlab.

Источники — только файлы в папке sources/:
- ГОСТ 21.101-97 «СПДС. Основные требования к рабочей документации» — текст и рисунки форм основных надписей
  (GOST_21.101-97_forms/);
- приложения 2, 3, 4, 4-1 и 5 к Правилам проведения комплексной вневедомственной экспертизы (Приказ № 299);
- АГСК-1, июнь 2025 (PDF): перечень нормативных документов в строительстве РК.
Других источников нет: ни веба, ни пакетов-примеров. Опирайся на эти тексты; чего в них нет, не выдумывай.

Что сдать — папку skills_out/ в текущей папке:
- один или несколько скилов: skills_out/<имя>/SKILL.md — в начале YAML с полями name и description (когда скил
  применять), дальше инструкции;
- если нужно — вспомогательные скрипты на Python 3 (skills_out/<имя>/scripts/) и справочные файлы, выписанные из
  источников (skills_out/<имя>/references/). В SKILL.md вызывай их по пути .agents/skills/<имя>/scripts/…;
- короткий skills_out/README.md: что делают скилы.

Ответ агент сдаёт строго по этой JSON-схеме — Codex проверяет её сам, ответ с другими полями не пройдёт:

{schema}

Скрипты проверь на тестовых файлах, которые сделаешь сам (PDF, Excel, Word); тестовые файлы держи вне skills_out/.
Работай только в текущей папке: файлы вне неё не открывай и не ищи. Бюджет ограничен — уложись примерно в час
работы. В конце коротко напиши, что сделал."""

A1_AUTHOR_RESUME = (
    """Ты продолжаешь прерванную работу: прошлая сессия оборвалась по времени или лимиту. Задача — ниже;
то, что уже сделано, лежит в skills_out/. Посмотри, что готово, доделай и проверь; бюджет — около получаса.

"""
    + A1_AUTHOR_TASK
)

# Поправка 18.11: в задаче первой сессии не было схемы ответа агента — автор придумал свою (evidence, summary-объект,
# без категории). Та же роль автора получает схему и приводит скилы к ней; остальное не меняет.
A1_AUTHOR_SCHEMA_FIX = """Ты продолжаешь свою работу над скилами в skills_out/. В первой постановке не была указана
точная схема ответа агента. Агент сдаёт ответ строго по этой JSON-схеме — Codex проверяет её сам, ответ с другими
полями не пройдёт:

{schema}

Приведи скилы в соответствие: контракт отчёта, проверку отчёта и инструкции — так, чтобы агент сразу готовил ответ
ровно в этой схеме (категория из перечня, summary — строка). Полезное из прежнего формата — доказательства,
ограничения, расхождения — переложи в поля, которые в схеме есть: description, basis, summary. Остальное в скилах не
меняй. Задача агента прежняя:

---
{task}
---

Работай только в текущей папке. Бюджет — около 20 минут. В конце коротко напиши, что изменил."""
FOLLOWUPS = {"schema": A1_AUTHOR_SCHEMA_FIX}


def schema_text() -> str:
    return json.dumps(ANSWER_SCHEMA, ensure_ascii=False, indent=1)


def skill_manifest(root: Path) -> list[tuple[str, str]]:
    """(sha256, путь) всех файлов скилов, без кэшей Python."""
    files = sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    return [(hashlib.sha256(p.read_bytes()).hexdigest(), p.relative_to(root).as_posix()) for p in files]


def check_skill(path: Path) -> str | None:
    """SKILL.md с YAML-шапкой name и description: None — годен, иначе что не так."""
    text = path.read_text(encoding="utf-8", errors="replace")
    head = text.split("---")[1] if text.startswith("---") and text.count("---") >= 2 else ""
    missing = [k for k in ("name:", "description:") if k not in head]
    return f"{path.parent.name}: нет {', '.join(missing)} в шапке" if missing else None


def _author_session(n: int, kind: str, prompt: str, timeout: float, out: Path, say) -> dict:
    """Одна сессия автора: запуск, запись в sessions.jsonl; оборвал лимит — ждём сброса."""
    sandbox_guard(say=say)
    session = out / f"session{n}"
    session.mkdir(exist_ok=True)
    answer = session / "answer.txt"
    answer.unlink(missing_ok=True)
    say(f"скилы A1: сессия {n} — {kind}")
    (session / "prompt.txt").write_text(prompt, encoding="utf-8")
    r = execute(codex_cmd(AUTHOR_DIR, answer, None), prompt, AUTHOR_DIR, session, timeout, say)
    rec = {k: r[k] for k in ("usage", "minutes", "timed_out", "errors", "outside", "items")}
    rec.update(session=n, kind=kind, finished=answer.exists(), codex=codex_version(), model=MODEL, effort=EFFORT)
    with (out / "sessions.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    u = r["usage"] or {}
    say(
        f"скилы A1: сессия {n} — {'готово' if rec['finished'] else 'оборвалась'}, {r['minutes']} мин, "
        f"токенов {u.get('input_tokens', 0):,} вх. (кэш {u.get('cached_input_tokens', 0):,}), "
        f"{u.get('output_tokens', 0):,} вых."
    )
    text = " | ".join(r["errors"]) + " " + r["stderr_tail"]
    if LIMIT.search(text) and not answer.exists():
        sleep_until((reset_at(text) or datetime.now() + timedelta(minutes=30)) + timedelta(minutes=2), say)
    return rec


def author(say=log, followup: str | None = None) -> dict:
    """Сессия-автор скилов A1 на Codex: открытые нормы в sources/, без пакетов, заключений и кода «Нулевого цикла».
    Оборвал лимит или потолок времени — следующая сессия доделывает начатое. followup — поправка той же роли автора
    после сдачи (FOLLOWUPS). Готовые скилы — в SKILLS_A1, их sha256 — в SKILLS_SHA."""
    out = RESULTS / "a1-author"
    out.mkdir(parents=True, exist_ok=True)
    sessions = out / "sessions.jsonl"
    done = [json.loads(x) for x in sessions.read_text(encoding="utf-8").splitlines()] if sessions.exists() else []
    if not done:
        shutil.rmtree(AUTHOR_DIR, ignore_errors=True)
        (AUTHOR_DIR / "sources").mkdir(parents=True)
        for rel in A1_SOURCES:
            src, dst = BIM_DATA / rel, AUTHOR_DIR / "sources" / Path(rel).name
            shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)
    fill = {"task": TASK_V3, "schema": schema_text()}
    while len(done) < 4 and not any(d["finished"] for d in done):
        first = not done
        prompt = (A1_AUTHOR_TASK if first else A1_AUTHOR_RESUME).format(**fill)
        kind = "старт" if first else "доделывает начатое"
        done.append(_author_session(len(done) + 1, kind, prompt, AUTHOR_TIMEOUT_MIN if first else 45, out, say))
    if followup:
        tries = 0
        while tries < 3 and not any(d.get("kind") == followup and d["finished"] for d in done):
            tries += 1
            prompt = FOLLOWUPS[followup].format(**fill)
            done.append(_author_session(len(done) + 1, followup, prompt, 45, out, say))
    skills_out = AUTHOR_DIR / "skills_out"
    found = sorted(skills_out.rglob("SKILL.md")) if skills_out.exists() else []
    if not found:
        raise RuntimeError("сессия-автор не сдала ни одного SKILL.md")
    problems = [p for p in (check_skill(s) for s in found) if p]
    shutil.rmtree(SKILLS_A1, ignore_errors=True)
    shutil.copytree(skills_out, SKILLS_A1, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = skill_manifest(SKILLS_A1)
    SKILLS_SHA.parent.mkdir(parents=True, exist_ok=True)
    SKILLS_SHA.write_text("".join(f"{h}  {p}\n" for h, p in manifest), encoding="utf-8")
    result = {"skills": [s.parent.name for s in found], "files": len(manifest), "problems": problems}
    say(f"скилы A1: {result}")
    return result


# --- автозапуск ---------------------------------------------------------------------------------------------------


def install_autostart(plan_name: str) -> None:
    """Ярлык в «Автозагрузке»: при входе в Windows драйвер поднимается сам, со свёрнутым окном — скрытое окно в v1
    уводило процессы на экономичные ядра. Прав администратора не нужно."""
    START.parent.mkdir(parents=True, exist_ok=True)
    START.write_text(
        "@echo off\r\n"
        f'cd /d "{REPO}"\r\n'
        f'"{sys.executable}" -X utf8 -m zerocycle.bench.v3run run --plan {plan_name}\r\n',
        encoding="ascii",
    )
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
        "$s.TargetPath = $env:ComSpec; $s.Arguments = '/c \"' + $env:START + '\"'; "
        "$s.WindowStyle = 7; $s.WorkingDirectory = $env:REPO; $s.Save()"
    )
    env = {**os.environ, "LNK": str(LINK), "START": str(START), "REPO": str(REPO)}
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], check=True, env=env)


def remove_autostart() -> None:
    LINK.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Прогоны V3 на Codex (PROTOCOL_V3.md)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plan", default="check", choices=["check", "pilot", "main"])
    r.add_argument("--skills", type=Path, default=SKILLS_A1, help="скилы A1 (папка со SKILL.md)")
    r.add_argument("--keep", action="store_true", help="не удалять папку руки после прогона")
    s = sub.add_parser("status")
    s.add_argument("--plan", default="check", choices=["check", "pilot", "main"])
    p = sub.add_parser("probe")
    p.add_argument("kind", choices=["sandbox", "skills"])
    p.add_argument("--keep", action="store_true")
    p.add_argument("--skills", type=Path, help="проверить настоящие скилы вместо проверочного")
    au = sub.add_parser("author", help="сессия-автор скилов A1 на Codex (PROTOCOL_V3, «Руки»)")
    au.add_argument("--followup", choices=sorted(FOLLOWUPS), help="поправка той же роли автора после сдачи")
    a = sub.add_parser("autostart")
    g = a.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--remove", action="store_true")
    a.add_argument("--plan", default="pilot", choices=["check", "pilot", "main"])
    args = ap.parse_args()
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.cmd == "run":
        return drive(args.plan, args.skills, args.keep)
    if args.cmd == "status":
        print(status(args.plan))
    elif args.cmd == "probe":
        print(json.dumps(probe(args.kind, args.keep, args.skills), ensure_ascii=False, indent=1))
    elif args.cmd == "author":
        if not take_lock():
            log(f"драйвер уже идёт (замок {lock_path()})")
            return 1
        try:
            print(json.dumps(author(followup=args.followup), ensure_ascii=False, indent=1))
        finally:
            lock_path().unlink(missing_ok=True)
    elif args.install:
        install_autostart(args.plan)
        print(f"автозапуск: {LINK} → {START}")
    else:
        remove_autostart()
        print("автозапуск снят")
    return 0


if __name__ == "__main__":
    sys.exit(main())
