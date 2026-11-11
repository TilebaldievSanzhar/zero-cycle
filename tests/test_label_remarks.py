"""Разметка пунктов (scripts/label_remarks.py): одинаковый текст уходит модели один раз, метка копируется."""

import importlib.util
import json
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
