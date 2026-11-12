"""Разметка пунктов (scripts/label_remarks.py): одинаковый текст уходит модели один раз, метка копируется."""

import importlib.util
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("label_remarks", ROOT / "scripts" / "label_remarks.py")
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)


def _items(path: Path, rows: list[tuple[str, str]]) -> Path:
    path.write_text("".join(json.dumps({"key": k, "text": t}) + "\n" for k, t in rows), encoding="utf-8")
    return path


def test_same_text_labeled_once_and_copied(tmp_path, monkeypatch):
    seen: list[list[str]] = []

    def fake_runner(text, model, effort):
        keys = json.loads(text)
        seen.append(keys)
        return {"labels": [{"key": k, "category": "completeness", "added": None, "note": ""} for k in keys]}, {}

    monkeypatch.setattr(lr, "prompt", lambda part: json.dumps([it["key"] for it in part]))
    monkeypatch.setattr(lr, "run_codex", fake_runner)
    out = tmp_path / "out.jsonl"
    items = _items(
        tmp_path / "a.jsonl",
        [("k1:a", "Представлен расчёт."), ("k2:a", "  представлен   РАСЧЁТ. "), ("k1:b", "Исправлен штамп.")],
    )
    lr.cmd_run(items, out, "codex", "m", "high", batch=40, sample=None, seed=1)
    assert seen == [["k1:a", "k1:b"]]
    labs = {(d := json.loads(x))["key"]: d for x in out.read_text(encoding="utf-8").splitlines()}
    assert set(labs) == {"k1:a", "k2:a", "k1:b"}
    assert labs["k2:a"]["same_text_as"] == "k1:a" and "same_text_as" not in labs["k1:a"]

    # список пунктов дополнили (скачались новые заключения): пункт с уже размеченным текстом — копия без модели
    items = _items(
        tmp_path / "a.jsonl",
        [("k1:a", "Представлен расчёт."), ("k1:b", "Исправлен штамп."), ("k3:b", "исправлен штамп.")],
    )
    lr.cmd_run(items, out, "codex", "m", "high", batch=40, sample=None, seed=1)
    assert len(seen) == 1
    labs = {(d := json.loads(x))["key"]: d for x in out.read_text(encoding="utf-8").splitlines()}
    assert labs["k3:b"]["same_text_as"] == "k1:b"


def test_reset_at_parses_codex_limit_message():
    now = datetime(2026, 11, 9, 17, 54)
    msg = "You’ve hit your usage limit. Upgrade to Pro … or try again at 8:13 PM."
    assert lr.reset_at(msg, now) == datetime(2026, 11, 9, 20, 13)
    assert lr.reset_at("try again at 1:05 AM", datetime(2026, 11, 9, 23, 0)) == datetime(2026, 11, 10, 1, 5)
    assert lr.reset_at("no time here", now) is None


def test_wait_retries_same_batch_after_limit(tmp_path, monkeypatch):
    calls, slept = [], []

    def runner(text, model, effort):
        calls.append(text)
        if len(calls) == 1:
            raise lr.LimitHit("You’ve hit your usage limit … try again at 8:13 PM")
        return {
            "labels": [{"key": k, "category": "formatting", "added": None, "note": ""} for k in json.loads(text)]
        }, {}

    monkeypatch.setattr(lr, "prompt", lambda part: json.dumps([it["key"] for it in part]))
    monkeypatch.setattr(lr, "run_codex", runner)
    monkeypatch.setattr(lr.time, "sleep", slept.append)
    out = tmp_path / "out.jsonl"
    items = _items(tmp_path / "a.jsonl", [("k1:a", "Исправлен штамп.")])
    lr.cmd_run(items, out, "codex", "m", "high", batch=40, sample=None, seed=1, wait=True)
    assert len(calls) == 2 and calls[0] == calls[1] and len(slept) == 1
    assert json.loads(out.read_text(encoding="utf-8"))["key"] == "k1:a"
