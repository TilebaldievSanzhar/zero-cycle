"""Обвязка V3 на Codex: план, командная строка, песочница, лимит, проверка ответа, засчитывание."""

from datetime import datetime
from pathlib import Path

import pytest

from zerocycle.bench import v3run
from zerocycle.bench.v3 import PACKAGES, split
from zerocycle.bench.v3run import (
    TASK_V3,
    Item,
    audit,
    codex_cmd,
    deny_paths,
    plan,
    prepare,
    prompt_for,
    reset_at,
    valid,
)
from zerocycle.bench.v3score import match, pattern_of, score_answer

NOW = datetime(2026, 11, 18, 18, 0)


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        ("You've hit your usage limit. Upgrade to Pro … or try again at 8:13 PM.", datetime(2026, 11, 18, 20, 13)),
        ("… try again at 7:05 AM.", datetime(2026, 11, 19, 7, 5)),
        ("… try again at 21:40", datetime(2026, 11, 18, 21, 40)),
        ("… or try again at Nov 20th, 2026 9:00 AM.", datetime(2026, 11, 20, 9, 0)),
        ("… try again at Dec 2nd 6:30 PM", datetime(2026, 12, 2, 18, 30)),
        ("… try again in 2 hours 5 minutes", datetime(2026, 11, 18, 20, 5)),
        ("… try again in 3 days", datetime(2026, 11, 21, 18, 0)),
        ("You've hit your usage limit.", None),
    ],
)
def test_reset_at(msg, expected):
    assert reset_at(msg, NOW) == expected


def test_plans_follow_protocol():
    parts = split()
    assert plan("check") == [Item("kz-06", "A0", 1), Item("kz-03", "A0", 1)]
    pilot = plan("pilot")
    assert len(pilot) == 6 * 4 and {i.package for i in pilot} == set(parts["dev"])
    main = plan("main")
    assert len(main) == 33 * (1 + 2 + 2)
    assert {i.package for i in main} == set(parts["heldout"])
    first_two = {i.k for i in main[: len(main) // 2]}
    assert first_two == {1}  # сначала первый повтор по всем пакетам — сравнение остаётся парным
    assert len({i.id for i in main}) == len(main)


def test_prompts():
    assert prompt_for("A0") == prompt_for("A1") == TASK_V3
    assert "check_package" in prompt_for("Be") and "check_package" in prompt_for("Ba")
    assert prompt_for("Be") != prompt_for("Ba")
    assert "Сначала проверь пакет" in prompt_for("Ba") and "Сначала запусти" in prompt_for("Be")


def test_codex_cmd_jails_the_arm(monkeypatch):
    monkeypatch.setattr(v3run, "codex_exe", lambda: ("codex.exe", {}))
    cmd = codex_cmd(Path(r"C:\zc-v3\work\kz-06.A0.1"), Path("answer.json"), Path("schema.json"))
    flat = " ".join(cmd)
    assert cmd[:2] == ["codex.exe", "exec"] and "--ignore-user-config" in cmd and "--ephemeral" in cmd
    assert "-s" not in cmd and "--sandbox" not in cmd  # старый флаг песочницы отменил бы профиль прав
    assert 'default_permissions="arm"' in cmd and 'windows.sandbox="elevated"' in cmd
    perm = next(c for c in cmd if c.startswith("permissions="))
    var = str(v3run.REPO / "var").replace("\\", "\\\\")
    assert f'"{var}"="deny"' in perm and '":root"="read"' in perm and "network={enabled=false}" in perm
    codex_home = str(Path.home() / ".codex").replace("\\", "\\\\")
    assert f'"{codex_home}"="deny"' in perm  # путь с точкой — внутри таблицы, не в ключе -c
    assert "--output-schema" in cmd and cmd[-4:] == ["-o", "answer.json", "--json", "-"]
    assert "mcp_servers" not in flat
    assert 'web_search="disabled"' in cmd  # поиск на сервере обошёл бы песочницу
    off = {cmd[i + 1] for i, c in enumerate(cmd) if c == "--disable"}
    assert {"memories", "plugins", "apps", "browser_use", "computer_use"} <= off


def test_codex_cmd_with_mcp(monkeypatch):
    monkeypatch.setattr(v3run, "codex_exe", lambda: ("codex.exe", {}))
    mcp = {"command": r"C:\py\python.exe", "args": ["-m", "zerocycle", "mcp"], "env": {"PYTHONPATH": r"C:\e\src"}}
    cmd = codex_cmd(Path("w"), Path("a.json"), None, mcp=mcp)
    assert 'mcp_servers.zc.command="C:\\\\py\\\\python.exe"' in cmd
    assert 'mcp_servers.zc.env={PYTHONPATH="C:\\\\e\\\\src"}' in cmd
    assert "--output-schema" not in cmd


def test_deny_paths_cover_repo_and_agents():
    deny = deny_paths()
    for name in ("var", "src", "docs", ".git"):
        assert v3run.REPO / name in deny  # запрет на каждой папке: на корне его перекрывает разрешение песочнице
    assert v3run.REPO not in deny
    if (Path.home() / ".codex").exists():
        assert Path.home() / ".codex" in deny
    assert len(deny) == len(set(deny))


def test_audit_ignores_system_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(v3run, "arm_python", lambda: r"C:\Py\python.exe")
    monkeypatch.setattr(v3run, "codex_exe", lambda: (r"C:\npm\codex\bin\codex.exe", {}))
    run = tmp_path / "run"
    commands = [
        r'"C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe" -Command "Get-ChildItem package"',
        r"C:\Py\python.exe -c print(1)",
        rf"type {run}\package\a.txt",
        r"type C:\Projects\development_challenge\zero-cycle\var\v3\kz\kz-06\ground_truth.json",
    ]
    out = audit(commands, run)
    assert len(out) == 1 and "ground_truth" in out[0]


def test_prepare_a1_needs_skills(tmp_path):
    pkg = tmp_path / "package"
    pkg.mkdir()
    (pkg / "a.pdf").write_bytes(b"%PDF")
    with pytest.raises(RuntimeError):
        prepare(tmp_path / "run", pkg, "A1", None)
    skills = tmp_path / "skills" / "kz-check"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: kz-check\n---\n", encoding="utf-8")
    prepare(tmp_path / "run", pkg, "A1", tmp_path / "skills")
    assert (tmp_path / "run" / ".agents" / "skills" / "kz-check" / "SKILL.md").exists()
    assert (tmp_path / "run" / "package" / "a.pdf").exists()


def test_author_task_and_sources():
    fill = {"task": TASK_V3, "schema": v3run.schema_text()}
    prompt = v3run.A1_AUTHOR_TASK.format(**fill)
    assert TASK_V3 in prompt and "skills_out/" in prompt and "sources/" in prompt
    assert '"findings"' in prompt and '"summary"' in prompt  # автор знает точную схему ответа руки
    resume = v3run.A1_AUTHOR_RESUME.format(**fill)
    assert resume.startswith("Ты продолжаешь") and TASK_V3 in resume
    fix = v3run.FOLLOWUPS["schema"].format(**fill)
    assert "summary — строка" in fix and '"category"' in fix and TASK_V3 in fix
    if v3run.BIM_DATA.exists():
        missing = [rel for rel in v3run.A1_SOURCES if not (v3run.BIM_DATA / rel).exists()]
        assert not missing


def test_check_skill(tmp_path):
    good = tmp_path / "kz" / "SKILL.md"
    good.parent.mkdir()
    good.write_text("---\nname: kz\ndescription: проверка пакета ПСД РК\n---\nИнструкции\n", encoding="utf-8")
    assert v3run.check_skill(good) is None
    bad = tmp_path / "x" / "SKILL.md"
    bad.parent.mkdir()
    bad.write_text("# без шапки\n", encoding="utf-8")
    assert "name:" in v3run.check_skill(bad)
    assert [p for _, p in v3run.skill_manifest(tmp_path)] == ["kz/SKILL.md", "x/SKILL.md"]


def f(description: str, category: str = "комплектность", status: str = "нарушение") -> dict:
    return {
        "category": category,
        "status": status,
        "document": "package/",
        "page": None,
        "description": description,
        "basis": "Приказ № 299, прил. 3",
    }


def test_valid_answer():
    assert valid({"findings": [f("нет энергопаспорта")], "summary": "ok"}) is None
    assert valid({"findings": [f("x", category="прочее")]}) is not None
    assert valid({"findings": [{"category": "комплектность"}]}) is not None
    assert valid({"summary": "нет находок"}) is not None


def test_every_seed_has_a_finding_pattern():
    for p in PACKAGES.values():
        for s in p.seeds:
            assert pattern_of(s)


def test_two_passports_need_two_findings():
    one = score_answer("kz-39", {"findings": [f("Отсутствует энергетический паспорт здания школы")]})
    assert [v["found"] for v in one["defects"].values()] == [True, False]
    two = score_answer(
        "kz-39",
        {
            "findings": [
                f("Отсутствует энергетический паспорт здания СШ им. Абая"),
                f("Не представлен энергопаспорт СШ им. Естемесова", status="проверить вручную"),
            ]
        },
    )
    assert all(v["found"] for v in two["defects"].values())
    assert two["defects"]["kz-39.1"]["status"] == "нарушение"  # лучшая находка — «нарушение»


def test_one_finding_may_close_defects_of_different_kinds():
    s = score_answer(
        "kz-05",
        {"findings": [f("Не представлены отчёт об инженерно-геологических изысканиях и теплотехнический расчёт")]},
    )
    found = {d for d, v in s["defects"].items() if v["found"]}
    assert found == {"kz-05.1", "kz-05.2"}


def test_named_without_absence_is_near_miss():
    s = score_answer("kz-05", {"findings": [f("Теплотехнический расчёт выполнен для другого района", "расчёты")]})
    assert not any(v["found"] for v in s["defects"].values())
    assert s["near"] == [{"defects": ["kz-05.2"], "finding": 0}]


def test_completeness_category_counts_without_absence_words():
    assert match(pattern_of(PACKAGES["kz-08"].seeds[0]), f("Требуется энергетический паспорт здания")) == "category"
    assert match(pattern_of(PACKAGES["kz-08"].seeds[0]), f("Требуется энергетический паспорт", "оформление")) is None


@pytest.mark.parametrize(
    ("package", "n", "text"),
    [
        ("kz-03", 1, "Не представлен расчёт несущих конструкций узла трубопровода"),
        ("kz-10", 2, "Отсутствует гидравлический расчёт системы отопления"),
        ("kz-11", 1, "Не приложены расчёты по водоснабжению и водоотведению"),
        ("kz-14", 1, "Нет аэродинамического расчёта дымовой трубы"),
        ("kz-25", 1, "Не представлен расчёт продолжительности строительства"),
        ("kz-27", 2, "Отсутствует протокол подтверждения запасов подземных вод месторождения"),
        ("kz-12", 1, "Не представлены технические условия на теплоснабжение"),
        ("kz-07", 1, "Нет раздела «Мероприятия по обеспечению пожарной безопасности»"),
        ("kz-09", 2, "Не представлен перечень цен по прейскурантам"),
        ("kz-04", 2, "Нет пояснительной записки к сметной документации"),
        ("kz-17", 1, "В составе сметной документации нет сводной ведомости материальных ресурсов"),
        ("kz-31", 1, "Отсутствует сводная ведомость потребности в материалах и оборудовании"),
        ("kz-16", 1, "Не представлен проект организации строительства (ПОС)"),
    ],
)
def test_defect_found_by_its_name(package, n, text):
    s = score_answer(package, {"findings": [f(text)]})
    assert s["defects"][f"{package}.{n}"]["found"], s


def test_pipe_stress_analysis_is_not_the_concrete_calculation():
    # проверка обвязки 18.11: убран ТС.КЖ.Р (железобетонные узлы и опоры), а находка — о расчёте труб в «Старт-Проф»
    text = "Не найден заявленный расчёт прочности и жёсткости трубопроводов в «Старт-проф»: отсутствуют расчётная схема"
    assert not score_answer("kz-03", {"findings": [f(text)]})["defects"]["kz-03.1"]["found"]
    concrete = "Отсутствуют расчёты железобетонных конструкций узлов трубопроводов УТ1–УТ5 и неподвижных опор"
    assert score_answer("kz-03", {"findings": [f(concrete)]})["defects"]["kz-03.1"]["found"]


def test_review_verdict_overrides_match(tmp_path, monkeypatch):
    import json

    from zerocycle.bench import v3score

    plan_dir = tmp_path / "check"
    (plan_dir / "kz-06.A0.1").mkdir(parents=True)
    answer = {"findings": [f("Отсутствует технический отчёт по инженерно-геологическим изысканиям")], "summary": ""}
    (plan_dir / "kz-06.A0.1" / "answer.json").write_text(json.dumps(answer, ensure_ascii=False), encoding="utf-8")
    run = {"id": "kz-06.A0.1", "package": "kz-06", "arm": "A0", "k": 1, "status": "ok"}
    (plan_dir / "runs.jsonl").write_text(json.dumps(run) + "\n", encoding="utf-8")
    monkeypatch.setattr(v3score, "RESULTS", tmp_path)
    monkeypatch.setattr(v3score, "load_runs", lambda name: [run])
    assert v3score.score_plan("check")["summary"]["A0"]["per_defect"]["kz-06.1"] == 1.0
    verdict = {"kz-06.A0.1|kz-06.1": {"found": False, "why": "проверка"}}
    (plan_dir / "verdicts.json").write_text(json.dumps(verdict, ensure_ascii=False), encoding="utf-8")
    assert v3score.score_plan("check")["summary"]["A0"]["per_defect"]["kz-06.1"] == 0.0


def test_calculation_seeds_do_not_match_each_other():
    s = score_answer("kz-10", {"findings": [f("Отсутствует гидравлический расчёт системы отопления")]})
    assert s["defects"]["kz-10.2"]["found"] and not s["defects"]["kz-10.1"]["found"]


def test_failed_run_is_empty_answer():
    s = score_answer("kz-06", None)
    assert len(s["defects"]) == 3 and not any(v["found"] for v in s["defects"].values())
