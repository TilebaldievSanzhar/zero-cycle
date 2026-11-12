"""Карта обратного засева: что эксперт заставил добавить → какой это файл в пакете закупки.

Обратный засев (docs/bench/PROTOCOL_REAL.md): убрать из пакета после экспертизы документ, который по заключению
добавили по замечанию, — пакет возвращается к состоянию до экспертизы по этому пункту. Вручную так подобраны 7
дефектов реального бенчмарка (`bench/real.py`, PACKAGES); здесь — то же автоматически, для сотен заключений.

Вход: пункты заключений с ключами (`label_remarks.py items`), разметка модели с полем `added`
(`label_remarks.py run`), список объявлений с файлами пакета (`goszakup_kz_psd.py list`) и папка закупок, где лежат
сами заключения (по ним ключ пункта связывается с объявлением: ключ начинается с sha256 файла заключения).

    python -X utf8 scripts/seed_map.py <items.jsonl> <out.jsonl> --labels L.jsonl [--labels …]
                                       --list var/real/goszakup_kz/_list.json --root var/real/goszakup_kz
                                       [--archives]   и файлы внутри скачанных архивов: «архив/путь внутри»
                                       [--check]      сверка с дефектами реального бенчмарка

Сопоставление — по основам слов (6 букв) без служебных слов и с синонимами сокращений: «ИГИ» ↔ «геологических»,
«казсодержание» ↔ «казахстанское содержание», «МОПБ» ↔ «пожарной безопасности». Файлы сметной программы названы
кодами («PZ1-27_RU.docx») — код раскрывается в название документа (CODES).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

CONCLUSION = re.compile(r"заключ|қорытынд", re.I)
# Документ исправили, а не добавили: до экспертизы он уже был в пакете, убирать его нельзя («представлена
# обновлённая топографическая съёмка» — старая была).
REVISED = re.compile(r"приведен\w*\s+в\s+соответстви|откорректирован|переработан|исправлен|обновл[её]нн", re.I)
STOP = frozenset(
    [
        "том",
        "тома",
        "альбом",
        "книга",
        "раздел",
        "раздела",
        "часть",
        "проект",
        "проекта",
        "рабочий",
        "рабочего",
        "документ",
        "документы",
        "документация",
        "документации",
        "pdf",
        "закл",
        "для",
        "по",
        "на",
        "и",
        "в",
        "с",
        "к",
        "от",
        "г",
        "о",
        "об",
        "а",
        "из",
        "при",
        "представлен",
        "представлены",
        "предоставлен",
        "приложен",
    ]
)
# сокращения → основы слов, которыми их пишут полностью: «МОПБ» = «мероприятия по обеспечению пожарной безопасности»
SYNONYMS: dict[str, tuple[str, ...]] = {
    "иги": ("геолог",),
    "игди": ("геодез",),
    "иэи": ("эколог",),
    "игми": ("гидром",),
    "казсод": ("казахс", "содерж"),
    "каз": ("казахс",),  # «Каз содержание.xlsx»
    "мопб": ("меропр", "обеспе", "пожарн", "безопа"),
    "кж": ("констр", "железо"),
    "пос": ("органи", "строит"),
    "опз": ("поясни",),
    "пз": ("поясни",),
    "ссрс": ("сводны", "сметны"),
    "энергопаспорт": ("энерге", "паспор"),
    "ту": ("технич", "услови"),
}
STEM = 6
# Сметная программа SANA называет файлы кодами: что за документ, видно только по коду. Проверено по содержимому
# файлов закупки 17248156 (12.11.2026). Код — не после латинской буквы: «1. SvSR230426 - Э_RU.xlsx», «KENML-LS4-…».
CODES = tuple(
    (re.compile(rf"(?<![a-z]){code}\d", re.I), name)
    for code, name in (
        ("LSSvResVed", "сводная ресурсная ведомость по локальной смете"),
        ("SVMROB", "сводная ведомость материальных ресурсов и оборудования"),
        ("SvSR", "сводный сметный расчет"),
        ("ObSm", "объектная смета"),
        ("PZ", "пояснительная записка к сметной документации"),
        ("LS", "локальная смета"),
        ("ID", "исходные данные по локальной смете"),
    )
)
# основные тома по замечаниям правят, а не добавляют: для них только доля слов пункта, найденных в имени файла
CORE = re.compile(r"пояснит|\bо?пз\b|паспорт\w*\s+(?:рабоч|проект)|сметн|альбом", re.I)
CORE_MIN = 0.8
# основы, общие для многих документов: одного такого совпадения мало — «Расчёты на конструкции» ≠ «Теплотехнический
# расчёт»
GENERIC = frozenset(
    [
        "расчет",
        "расчеты",
        "отчет",
        "отчеты",
        "ведомо",
        "раздел",
        "сведен",
        "докуме",
        "проект",
        "задани",
        "письмо",
        "справк",
        "схема",
        "схемы",
        "план",
        "перече",
        "спецификац"[:STEM],
        "сводна",
        "сводны",
        "общая",
        "основн",
    ]
)


def expand_codes(name: str) -> str:
    """«PZ1-27_RU» → «пояснительная записка к сметной документации PZ1-27_RU»."""
    for rx, title in CODES:
        if rx.search(name):
            return f"{title} {name}"
    return name


def stems(text: str) -> set[str]:
    words = re.findall(r"[а-яёa-z0-9]+", text.lower().replace("ё", "е"))
    out = set()
    for w in words:
        if w in STOP or w.isdigit():
            continue
        for key, syn in SYNONYMS.items():
            if w == key or (len(key) >= 4 and w.startswith(key)):
                out.update(syn)
        if len(w) >= 2:
            out.add(w[:STEM])
    return out


def score(added: str, filename: str) -> float:
    """Сходство названия документа из пункта и имени файла: доля общих основ — от пункта или от имени файла.

    «Представлен теплотехнический расчёт ограждающих конструкций …» ↔ «Теплотехнический расчёт +++.pdf»: из пункта в
    имени — две основы из многих, но всё имя файла есть в пункте. Основной том («пояснительная записка дополнена …»)
    по замечаниям правят, а не добавляют: для него только доля от пункта, и с запасом — не меньше CORE_MIN.
    """
    name = expand_codes(re.sub(r"\.[a-z0-9]{2,5}$", "", PurePosixPath(filename).name, flags=re.I))
    a, f = stems(added), stems(name)
    common = {s for s in a & f if len(s) >= 4}
    # одной общей основы мало: «план организации рельефа» ≠ «Проект организации строительства»
    if len(common) < 2 or common <= GENERIC:
        return 0.0
    from_added = len(common) / len(a)
    if CORE.search(name):
        return from_added if from_added >= CORE_MIN else 0.0
    return max(from_added, len(common) / len(f))


def archive_members(folder: Path) -> list[str]:
    """Файлы внутри скачанных архивов закупки: «архив/путь внутри». Тома многотомного RAR — каждый по отдельности."""
    from zerocycle.bench.archives import is_archive, members

    out: list[str] = []
    for p in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not is_archive(p.name) or not p.stat().st_size:
            continue
        try:
            out += [f"{p.name}/{m}" for m in members(p)]
        except (ValueError, OSError, zipfile.BadZipFile) as e:
            print(f"  {folder.parent.name}/{p.name}: не прочитан ({e})")
    return out


def conclusion_index(root: Path) -> dict[str, str]:
    """sha256[:12] файла заключения → номер объявления (папка закупки)."""
    idx = {}
    for d in root.iterdir():
        files = d / "files"
        if not files.is_dir():
            continue
        for p in files.glob("*.pdf"):
            if CONCLUSION.search(p.name.replace("_", " ")):
                idx[hashlib.sha256(p.read_bytes()).hexdigest()[:12]] = d.name
    return idx


def main() -> None:
    ap = argparse.ArgumentParser(description="Карта обратного засева: добавленный по замечанию документ → файл пакета")
    ap.add_argument("items", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--labels", type=Path, action="append", required=True)
    ap.add_argument("--list", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--archives", action="store_true", help="и файлы внутри скачанных архивов")
    ap.add_argument("--check", action="store_true", help="сверка с дефектами реального бенчмарка")
    a = ap.parse_args()

    items = {json.loads(x)["key"]: json.loads(x) for x in a.items.read_text(encoding="utf-8").splitlines() if x.strip()}
    labels = {}
    for path in a.labels:
        for x in path.read_text(encoding="utf-8").splitlines():
            if x.strip():
                lab = json.loads(x)
                labels[lab["key"]] = lab
    listed = json.loads(a.list.read_text(encoding="utf-8"))
    idx = conclusion_index(a.root)

    rows, per = [], defaultdict(list)
    no_package = revised = 0
    in_archives: dict[str, list[str]] = {}
    for key, lab in labels.items():
        if lab.get("category") != "completeness" or not lab.get("added") or key not in items:
            continue
        if REVISED.search(items[key]["text"]):
            revised += 1
            continue
        aid = idx.get(key.split(":")[0])
        files = list(dict.fromkeys(n for _, n in listed.get(aid, {}).get("files", []))) if aid else []
        if not files:
            no_package += 1
            continue
        if a.archives:
            if aid not in in_archives:
                in_archives[aid] = archive_members(a.root / aid / "files")
            files += in_archives[aid]
        files = [n for n in files if not CONCLUSION.search(PurePosixPath(n).name)]
        ranked = sorted(((score(lab["added"], n), n) for n in files), reverse=True)
        best_score, best = ranked[0] if ranked else (0.0, None)
        row = {
            "announce": aid,
            "key": key,
            "text": items[key]["text"],
            "added": lab["added"],
            "file": best if best_score >= a.min_score else None,
            "score": round(best_score, 2),
            "runner_up": ranked[1][1] if len(ranked) > 1 and ranked[1][0] >= a.min_score else None,
        }
        rows.append(row)
        if row["file"]:
            per[aid].append(row)
    with a.out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    matched = [r for r in rows if r["file"]]
    print(
        f"пунктов «представлен документ» (комплектность): {len(rows)}; без пакета в списке: {no_package}; "
        f"документ исправлен, а не добавлен: {revised}"
    )
    print(f"сопоставлено с файлом пакета: {len(matched)}; объявлений с сопоставлениями: {len(per)}")
    by_count = sorted(per.items(), key=lambda kv: -len({r["file"] for r in kv[1]}))
    for aid, rs in by_count[:15]:
        uniq = sorted({r["file"] for r in rs})
        print(f"  {aid}: документов {len(uniq)} — {', '.join(u[:40] for u in uniq[:5])}")

    if a.archives:
        n_members = sum(len(v) for v in in_archives.values())
        print(f"файлов внутри архивов: {n_members} в {sum(1 for v in in_archives.values() if v)} закупках")
    if a.check:
        from zerocycle.bench.real import PACKAGES

        found = total = 0
        for name, spec in PACKAGES.items():
            for d in spec.defects:
                total += 1
                hit = [r for r in per.get(spec.announce, []) if r["file"] and r["file"].startswith(d.file)]
                found += bool(hit)
                why = f" ← «{hit[0]['added'][:60]}»" if hit else ""
                print(f"  {name} {d.id}: {'найден' if hit else 'нет'} — {d.file}{why}")
        print(f"сверка с ручным подбором реального бенчмарка: {found} из {total}")


if __name__ == "__main__":
    main()
