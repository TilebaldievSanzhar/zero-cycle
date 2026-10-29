"""H5: средние руки на пакете, сбои и лимит, парный бутстреп и условия гипотезы."""

from zerocycle.bench.h5 import cells, compare, report_h5, verdict


def row(pkg, arm, k, found=None, violated=0, false_alarms=0, keys=(), seeded=4, error=None):
    r = {"package": pkg, "arm": arm, "k": k, "ok": error is None, "error": error}
    if found is not None:
        r.update(seeded=seeded, found=found, violated=violated, false_alarms=false_alarms, keys=list(keys))
    return r


def test_failed_run_is_empty_answer_and_limit_is_skipped():
    rows = [
        row("p1", "B", 1, found=4, violated=5, false_alarms=1, keys=["a", "b"]),
        row("p1", "B", 2, error="таймаут"),
        row("p1", "B", 3, error="You've hit your 5-hour limit"),
    ]
    c = cells(rows)[("p1", "B")]
    assert (c.runs, c.failed) == (2, 1)
    assert c.found == 2 and c.hits == 2 and c.violated == 2.5
    assert c.jaccard == 0  # пустой ответ не совпадает с непустым


def test_jaccard_over_runs():
    rows = [row("p1", "A1", k, found=1, violated=1, keys=keys) for k, keys in enumerate([["a"], ["a"], ["a", "b"]])]
    assert abs(cells(rows)[("p1", "A1")].jaccard - (1 + 0.5 + 0.5) / 3) < 1e-9


def test_same_arms_give_zero_interval():
    rows = [row(f"p{i}", arm, 1, found=i % 4, violated=4, false_alarms=4 - i % 4) for i in range(6) for arm in "AB"]
    _, d = compare(cells(rows), "A", "B")
    assert all(d[m].diff == 0 and d[m].low == 0 and d[m].high == 0 for m in ("recall", "precision"))


def table(b_found, b_false):
    rows = []
    for i in range(10):
        for k in (1, 2, 3):
            rows.append(row(f"p{i}", "A1", k, found=2, violated=3, false_alarms=1, keys=["x", f"n{k}"]))
            rows.append(
                row(f"p{i}", "B", k, found=b_found, violated=b_found + b_false, false_alarms=b_false, keys=["x", "y"])
            )
    return cells(rows)


def test_h5_confirmed_when_b_finds_more_and_stays_precise():
    _, d = compare(table(b_found=4, b_false=0), "B", "A1")
    assert abs(d["recall"].diff - 0.5) < 1e-9
    assert [v for _, v in verdict(d)] == [True, True, True]


def test_h5_fails_on_precision():
    _, d = compare(table(b_found=4, b_false=4), "B", "A1")
    assert [v for _, v in verdict(d)] == [True, False, True]


def test_report_writes_files(tmp_path):
    rows = [row("p1", arm, k, found=2, violated=2, keys=["x"]) for arm in ("A0", "A1", "B", "C") for k in (1, 2)]
    text = report_h5(tmp_path, rows)
    assert "H5 не подтверждена" in text
    assert (tmp_path / "h5.md").exists() and (tmp_path / "h5.json").exists()
