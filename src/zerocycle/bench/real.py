"""Бенчмарк на реальных пакетах РК (docs/bench/PROTOCOL_REAL.md): обратный засев по заключениям экспертизы.

Пакеты из закупок прошли экспертизу. В заключении перечислено, что проектировщик добавил по замечаниям; такой документ
убираем из пакета, а заключение рукам не показываем. Дефект настоящий: его нашёл эксперт на этом самом проекте.

    python -m zerocycle.bench.real build  <корпус>             # пакеты и эталон из var/real
    python -m zerocycle.bench.real publish <корпус> <папка>    # только package/, без эталона: для ветки облака
    python -m zerocycle.bench.real engine <корпус> <итоги>     # рука C
    python -m zerocycle.bench.real score  <корпус> <итоги>     # найденные дефекты по рукам

Эталон и заключения в git не кладём: иначе агент в облаке мог бы их прочитать.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from zerocycle.bench.arms import VIOLATED

REAL = Path("var/real/goszakup_kz")
ABSENT = re.compile(r"\bнет\b|отсутств|не\s+(представ|предостав|найден|приложен|содерж|включ|входит|обнаруж)", re.I)
# заключение и письма экспертной организации выдают ответ; техспецификация закупки — не часть ПСД
HIDDEN = re.compile(r"аключени|^Исх\.письмо|^techspec", re.I)

TASK_KZ = """Ты — нормоконтролёр и ГИП. Перед подачей в экспертизу проверь пакет проектно-сметной документации
(Республика Казахстан, стадия «РП») на формальные дефекты. Пакет лежит в папке package/ текущей рабочей папки: файлы
PDF.

Что искать:
- комплектность: разделы, исходно-разрешительные документы, расчёты, сметная документация по Правилам проведения
  комплексной вневедомственной экспертизы (Приказ № 299, приложения);
- оформление по ГОСТ 21.101-97: основные надписи (обозначение в графе 1, стадия, «Лист» и «Листов», подписи и даты,
  формат листа), нумерация листов, ведомости и содержания томов;
- увязка между томами: наименование объекта и здания, технико-экономические показатели;
- ссылки на нормативные документы, которые не действуют или заменены, по АГСК-1.

Как отвечать:
- в findings — только то, что ты подтвердил по файлам пакета: документ (путь внутри package/), страница PDF с 1
  (null — документ целиком), что не так и основание — документ и пункт нормы;
- статус «нарушение» — дефект подтверждён; «проверить вручную» — нужен человек;
- не выдумывай ссылки на нормы и числа; чего проверить не удалось — напиши в summary.

Работай только с папкой package/: файлы вне текущей рабочей папки не открывай и не ищи, черновики складывай только в
текущую папку. Python 3 с pdfplumber, pypdfium2, lxml и Pillow доступен командой python; PDF можно читать и
инструментом Read."""

A1_SOURCES_KZ = [
    "norms/kz/GOST_21.101-97_SPDS_osnovnye_trebovaniya_meganorm.txt",
    "norms/kz/AGSK-1_2025-06.pdf",
    "norms/kz/gosexpertiza_prilozhenie_2_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_3_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_4_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_4-1_299.docx",
    "norms/kz/gosexpertiza_prilozhenie_5_299.docx",
]

A1_AUTHOR_TASK_KZ = """Ты — сильный пользователь Claude Code. Подготовь скилы Claude Code, с которыми агент проверит
пакет проектно-сметной документации Казахстана перед экспертизой на формальные дефекты. Агент получит такую задачу:

---
{task}
---

Источники — только файлы в папке sources/:
- ГОСТ 21.101-97 (текст);
- приложения 2, 3, 4, 4-1 и 5 к Правилам проведения экспертизы (Приказ № 299);
- АГСК-1, июнь 2025 (PDF): перечень действующих нормативных документов.

Других источников нет: ни веба, ни пакетов-примеров.

Что сдать — папку skills_out/ в текущей папке (не .claude/: туда запись запрещена):
- один или несколько скилов: skills_out/<имя>/SKILL.md с описанием, когда скил применять, и инструкциями;
- если нужно — вспомогательные скрипты на Python 3 и справочные файлы, выписанные из источников. Для скриптов
  доступны pdfplumber, pypdfium2, lxml и Pillow, команда python;
- короткий skills_out/README.md о том, что делают скилы.

Скрипты проверь на тестовых PDF, которые сделаешь сам. Работай только в текущей папке: файлы вне неё не открывай и
не ищи. Бюджет ограничен — уложись примерно в час работы."""


VERDICTS = ["подтверждено", "не подтверждено", "проверить нельзя"]

JUDGE_TASK = """Ты — независимый эксперт по проектно-сметной документации Республики Казахстан. В items.json — находки,
которые разные проверяющие сделали по пакету в папке package/. Находки перемешаны, кто их сделал — неизвестно; не
старайся ни подтвердить, ни опровергнуть побольше.

Для каждой находки проверь по файлам пакета, есть ли дефект на самом деле:
- «подтверждено» — дефект есть в указанном документе (и на странице, если она указана, ±1);
- «не подтверждено» — по файлам дефекта нет: требование выполнено, документ есть, текст прочитан неверно или норма
  применена не к месту;
- «проверить нельзя» — нет текстового слоя и по изображению не разобрать, или нужен расчёт либо экспертиза содержания.

Ссылки на нормы сверяй с перечнем действующих документов sources/AGSK-1_2025-06.pdf; оформление — с
sources/GOST_21.101-97_SPDS_osnovnye_trebovaniya_meganorm.txt; комплектность — с приложениями Приказа № 299 в sources/.
В reason — одна-две фразы: что именно увидел и где.

Работай только в текущей папке. Python 3 с pdfplumber, pypdfium2, lxml и Pillow доступен командой python; PDF можно
читать и инструментом Read."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "verdict": {"type": "string", "enum": VERDICTS},
                    "reason": {"type": "string"},
                },
                "required": ["id", "verdict", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Defect:
    id: str
    file: str  # начало имени файла, который убираем
    remark: int  # номер пункта в разделе «Дополнения и изменения» заключения
    pattern: str  # как документ называют в находке


@dataclass(frozen=True)
class Package:
    announce: str
    defects: tuple[Defect, ...] = ()


PACKAGES = {
    "real-1": Package(
        "17560023",
        (
            Defect(
                "igi",
                "Отчет по инженерно-геологическим",
                3,
                r"инженерно[- ]геолог|геологическ\w+\s+(отч[её]т|изыскан)|\bИГИ\b",
            ),
            Defect("thermal", "Теплотехнический расчёт", 6, r"теплотехническ"),
            Defect("energy", "+ Энергетический паспорт", 20, r"энергетическ\w+\s+паспорт|энергопаспорт"),
            Defect(
                "resources",
                "+ Сводная ресурсная ведомость",
                51,
                r"ресурсн\w+\s+ведомост|ведомост\w+\s+(материальн|ресурс)",
            ),
        ),
    ),
    "real-2": Package(
        "17571053",
        (Defect("fire", "Том 16 - МОПБ", 14, r"пожарн\w+\s+безопасност|\bМОПБ\b"),),
    ),
    "real-3": Package(
        "17067800",
        (
            Defect("calc", "Расчет_кж", 20, r"расч[её]т\w*[^.]{0,60}(конструкц|\bКЖ\b|\bУТ\b|прочност)"),
            Defect("content", "Казсодержание", 29, r"казахстанск\w+\s+содержани|казсодержани|ведомост\w+\s+потребност"),
        ),
    ),
    "real-4": Package("17026268"),  # документов целиком из замечаний нет: только находки на реальном пакете
}


def build(corpus: Path, real: Path = REAL) -> None:
    """Копии пакетов без скрытых файлов и без файлов дефектов; эталон — рядом с package/."""
    for name, spec in PACKAGES.items():
        src = real / spec.announce / "files"
        out = corpus / name / "package"
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        files = sorted(p for p in src.iterdir() if p.suffix.lower() == ".pdf")
        removed = {d.id: [p.name for p in files if p.name.startswith(d.file)] for d in spec.defects}
        for d in spec.defects:
            if len(removed[d.id]) != 1:
                raise ValueError(f"{name}: дефект {d.id}: файлы {removed[d.id]}")
        gone = {x for names in removed.values() for x in names}
        hidden = [p.name for p in files if HIDDEN.search(p.name)]
        for p in files:
            if p.name not in gone and p.name not in hidden:
                shutil.copy2(p, out / p.name)
        truth = {
            "package": name,
            "announce": spec.announce,
            "hidden": hidden,
            "defects": [{"id": d.id, "file": removed[d.id][0], "remark": d.remark} for d in spec.defects],
        }
        (corpus / name / "ground_truth.json").write_text(
            json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8"
        )


def publish(corpus: Path, target: Path) -> None:
    """Для ветки облака — только package/: ни эталона, ни заключений."""
    for name in PACKAGES:
        out = target / name / "package"
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(corpus / name / "package", out)


def plan(arms: list[str], repeats: int) -> list[tuple[str, str, int]]:
    """Порядок: повтор → пакет → рука: если деньги кончатся, сравнение останется парным."""
    return [(p, a, k) for k in range(1, repeats + 1) for p in PACKAGES for a in arms]


def hit(defect: Defect, finding: dict) -> bool:
    """Находка называет убранный документ и говорит, что его нет."""
    text = " ".join(str(finding.get(k) or "") for k in ("description", "basis"))
    return bool(re.search(defect.pattern, text, re.I) and ABSENT.search(text))


def score_answer(package: str, answer: dict) -> dict[str, str | None]:
    """Дефект → статус лучшей находки: «нарушение», «проверить вручную» или None."""
    out: dict[str, str | None] = {}
    for d in PACKAGES[package].defects:
        statuses = [f.get("status") for f in answer.get("findings", []) if hit(d, f)]
        out[d.id] = VIOLATED if VIOLATED in statuses else (statuses[0] if statuses else None)
    return out


def engine(corpus: Path, results: Path) -> None:
    """Рука C: движок без агента."""
    from zerocycle.bench.arms import engine_answer
    from zerocycle.rules.cli import check

    for name in PACKAGES:
        answer = engine_answer(check(corpus / name / "package", "kz"))
        path = results / "answers" / f"{name}.C.1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(answer, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{name}: находок {len(answer.get('findings', []))}", flush=True)


def sample(results: Path, arms: tuple[str, ...] = ("B", "A1", "C"), per_arm: int = 25, seed: int = 2026):
    """Слепая оценка: по per_arm «нарушений» каждой руки, поровну со всех её прогонов; рука в задании не видна.

    Возвращает задания (без руки, перемешаны) и ключ id → рука, прогон — ключ остаётся локально.
    """
    rng = random.Random(seed)
    items, key = [], {}
    for arm in arms:
        pool = []
        for path in sorted((results / "answers").glob(f"real-*.{arm}.*.json")):
            run = path.name.removesuffix(".json")
            answer = json.loads(path.read_text(encoding="utf-8"))
            pool += [(run, f) for f in answer.get("findings", []) if f.get("status") == VIOLATED]
        for run, f in rng.sample(pool, min(per_arm, len(pool))):
            fields = {k: f.get(k) for k in ("document", "page", "category", "description", "basis")}
            items.append({"package": run.split(".")[0], **fields})
            key[len(items) - 1] = run
    order = list(range(len(items)))
    rng.shuffle(order)
    tasks, secret = [], {}
    for n, i in enumerate(order, 1):
        tasks.append({"id": f"j{n:03d}", **items[i]})
        secret[f"j{n:03d}"] = key[i]
    return tasks, secret


def batches(tasks: list[dict], size: int = 10) -> list[tuple[str, list[dict]]]:
    """Задания оценщику: по пакету, не больше size находок за прогон."""
    out = []
    for package in PACKAGES:
        mine = [t for t in tasks if t["package"] == package]
        out += [(package, mine[i : i + size]) for i in range(0, len(mine), size)]
    return out


def judge_report(secret: dict[str, str], verdicts: list[dict]) -> str:
    counts: dict[str, dict[str, int]] = {}
    for v in verdicts:
        arm = secret[v["id"]].split(".")[1]
        counts.setdefault(arm, {x: 0 for x in VERDICTS})[v["verdict"]] += 1
    lines = ["| Рука | " + " | ".join(VERDICTS) + " | Доля подтверждённых из проверенных |", "|---|---|---|---|---|"]
    for arm, c in sorted(counts.items()):
        checked = c["подтверждено"] + c["не подтверждено"]
        share = f"{c['подтверждено'] / checked:.2f}" if checked else "—"
        lines.append(f"| {arm} | " + " | ".join(str(c[x]) for x in VERDICTS) + f" | {share} |")
    return "\n".join(lines) + "\n"


def report(results: Path) -> str:
    lines = [
        "| Прогон | Найдено дефектов | Из них «нарушение» | Всего «нарушений» | Что не найдено |",
        "|---|---|---|---|---|",
    ]
    for path in sorted((results / "answers").glob("real-*.json")):
        package, arm, k = path.name.removesuffix(".json").split(".")
        answer = json.loads(path.read_text(encoding="utf-8"))
        s = score_answer(package, answer)
        found = [d for d, v in s.items() if v]
        strict = [d for d, v in s.items() if v == VIOLATED]
        missed = ", ".join(d for d, v in s.items() if not v) or "—"
        violated = sum(1 for f in answer.get("findings", []) if f.get("status") == VIOLATED)
        lines.append(f"| {package}.{arm}.{k} | {len(found)} из {len(s)} | {len(strict)} | {violated} | {missed} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for cmd, second in (
        ("build", None),
        ("publish", "target"),
        ("engine", "results"),
        ("score", "results"),
        ("sample", "results"),
        ("verdicts", "results"),
    ):
        s = sub.add_parser(cmd)
        s.add_argument("corpus", type=Path)
        if second:
            s.add_argument(second, type=Path)
    a = p.parse_args()
    if a.cmd == "sample":  # задания — в ветку облака (judge/items.json), ключ — только локально
        tasks, secret = sample(a.results)
        (a.results / "judge_items.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")
        (a.results / "judge_key.json").write_text(json.dumps(secret, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"заданий {len(tasks)}, прогонов оценщика {len(batches(tasks))}")
    elif a.cmd == "verdicts":
        secret = json.loads((a.results / "judge_key.json").read_text(encoding="utf-8"))
        verdicts = [
            v
            for path in sorted((a.results / "judge" / "answers").glob("*.json"))
            for v in json.loads(path.read_text(encoding="utf-8")).get("verdicts", [])
            if v["id"] in secret
        ]
        print(judge_report(secret, verdicts))
    elif a.cmd == "build":
        build(a.corpus)
    elif a.cmd == "publish":
        publish(a.corpus, a.target)
    elif a.cmd == "engine":
        engine(a.corpus, a.results)
    else:
        print(report(a.results))


if __name__ == "__main__":
    main()
