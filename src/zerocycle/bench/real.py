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
    for cmd, second in (("build", None), ("publish", "target"), ("engine", "results"), ("score", "results")):
        s = sub.add_parser(cmd)
        s.add_argument("corpus", type=Path)
        if second:
            s.add_argument(second, type=Path)
    a = p.parse_args()
    if a.cmd == "build":
        build(a.corpus)
    elif a.cmd == "publish":
        publish(a.corpus, a.target)
    elif a.cmd == "engine":
        engine(a.corpus, a.results)
    else:
        print(report(a.results))


if __name__ == "__main__":
    main()
