"""Знание экспертизы РК — движок v2 (docs/bench/PROTOCOL_V3.md): какие документы эксперты заставляли добавить, по
видам объектов и видам работ.

Перечень видов документов — `expertise.toml`; частоты — `expertise.json`, его собирает `build` из разметки пунктов
заключений (поле «представлен документ») без заключений отложенной части V3 (`knowledge_exclude`). В знание идут
только числа: сколько заключений по объектам такого вида требовали документ. Тексты пунктов — документы третьих лиц —
остаются в var/labels вне git.

    python -X utf8 -m zerocycle.packs.kz.expertise build [--items …] [--labels …] [--real …] [--split …]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path

CLASSES = ("building", "linear", "landscape")
WORKS = ("new", "reconstruction", "capital_repair")
CLASS_RU = {"building": "здания и сооружения", "linear": "сети и дороги", "landscape": "благоустройство"}
WORK_RU = {"new": "новое строительство", "reconstruction": "реконструкция", "capital_repair": "капитальный ремонт"}

# Вид объекта по наименованию: решает первое по месту совпадение — главное слово стоит раньше
# («жилой дом с внешними сетями» — здание, «ремонт наружных сетей с благоустройством зданий» — сети).
_CLASS_RX = {
    "building": re.compile(
        r"школ|лице[йя]|гимнази|детск\w*\s+сад|ясл[иея]|больниц|поликлиник|амбулатори|фельдшерск|(?<![а-яё])фап"
        r"|врачебн|медицинск|госпитал|диспансер|центр\w*\s+(?:психическ|здоровь|реабилит|обслуживан)|жил\w*\s+дом"
        r"|многоквартир|общежити|интернат|спортивн|физкультурн|(?<![а-яё])фок(?![а-яё])|бассейн|стадион|манеж"
        r"|хоккей|ледов|зал\w*\s+единоборств|клуб|дом\w*\s+культур|музе|библиотек|театр|административн|акимат"
        r"|здани|корпус|котельн|пожарн\w*\s+(?:депо|част)|мечет|учебн|колледж|училищ|университет|столов|пищеблок"
        r"|скотомогильник|(?<![а-яё])бан[ияюе](?![а-яё])|приют|дом\w*\s+престарел|гостиниц|вокзал",
        re.IGNORECASE,
    ),
    "linear": re.compile(
        r"(?<![а-яё])сет(?:ь|и|ей|ям|ями|ях)(?![а-яё])|трубопровод|водопровод|водоснабжени|канализаци|водоотведени"
        r"|коллектор|теплоснабжени|теплотрасс|теплов\w*\s+сет|газоснабжени|газопровод|газификац|электроснабжени"
        r"|лини\w*\s+электропередач|(?<![а-яё])(?:вл|кл)(?:-|\s)?\d|подстанци|автомобильн\w*\s+дорог|автодорог"
        r"|дорог|улиц|проезд|(?<![а-яё])мост|путепровод|(?<![а-яё])канал(?![а-яё])|арык|ирригаци|наружн\w*\s+освещени"
        r"|очистн\w*\s+сооружени|водозабор|скважин|насосн",
        re.IGNORECASE,
    ),
    "landscape": re.compile(r"благоустройств|(?<![а-яё])парк|сквер|алле[яи]|озеленени", re.IGNORECASE),
}
_WORK_RX = (  # как rules.context._WORK: капремонт раньше реконструкции, строительство — последним
    ("capital_repair", re.compile(r"капитальн\w*\s+ремонт", re.IGNORECASE)),
    ("reconstruction", re.compile(r"реконструкци|расширени\w*\s+(?:здани|объект)|модернизаци", re.IGNORECASE)),
    ("new", re.compile(r"строительств|завершени\w*\s+строительств", re.IGNORECASE)),
)
# «Представлен откорректированный…» — документ был и исправлен, а не добавлен (как фильтр карты засева)
REVISED = re.compile(r"откорректир|исправлен|окончательн|обновл[её]нн|доработан|переработан|актуализир", re.IGNORECASE)
CONCLUSION_FILE = re.compile(r"заключ|экспертиз|қорыт", re.IGNORECASE)  # как разбор пунктов (items_from_conclusions)


def _norm(text: str) -> str:
    return " ".join(text.replace("_", " ").replace("ё", "е").replace("Ё", "Е").split())


@dataclass(frozen=True)
class DocType:
    id: str
    title: str
    group: str
    added: tuple[re.Pattern[str], ...]
    names: tuple[re.Pattern[str], ...]
    heads: tuple[re.Pattern[str], ...]
    body: tuple[re.Pattern[str], ...] = ()  # заголовок прописными где угодно на листе — с учётом регистра
    ird: bool = False  # проверяет kz.completeness.ird по Правилам КВЭ
    objects: tuple[str, ...] = ()
    works: tuple[str, ...] = ()

    def applies(self, obj: str | None, work: str | None) -> bool:
        return (not self.objects or obj in self.objects) and (not self.works or work in self.works)


@cache
def doc_types() -> tuple[DocType, ...]:
    raw = tomllib.loads(resources.files("zerocycle.packs.kz").joinpath("expertise.toml").read_text(encoding="utf-8"))

    def rx(xs: list[str]) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(x, re.IGNORECASE) for x in xs)

    return tuple(
        DocType(
            id=d["id"],
            title=d["title"],
            group=d["group"],
            added=rx(d.get("added", [])),
            names=rx(d.get("names", [])),
            heads=rx(d.get("heads", [])),
            body=tuple(re.compile(x) for x in d.get("body", [])),
            ird=bool(d.get("ird", False)),
            objects=tuple(d.get("objects", ())),
            works=tuple(d.get("works", ())),
        )
        for d in raw["doc"]
    )


def classify(added: str) -> str | None:
    """Вид документа по формулировке «представлен …» из разметки; исправленный, а не добавленный, — None."""
    text = _norm(added)
    if REVISED.search(text):
        return None
    return next((d.id for d in doc_types() if any(p.search(text) for p in d.added)), None)


def object_class(name: str | None) -> str | None:
    """Вид объекта по наименованию: здания, сети и дороги, благоустройство — по первому по месту совпадению."""
    if not name:
        return None
    text = _norm(name)
    hits = [(m.start(), cls) for cls, rx in _CLASS_RX.items() if (m := rx.search(text))]
    return min(hits)[1] if hits else None


def work_kind(name: str | None) -> str | None:
    if not name:
        return None
    return next((kind for kind, rx in _WORK_RX if rx.search(name)), None)


@cache
def knowledge() -> dict:
    """Собранное знание (expertise.json) или пустое, если его ещё не собирали."""
    path = resources.files("zerocycle.packs.kz").joinpath("expertise.json")
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"doc_types": {}, "population": {}}


def share(doc_id: str, obj: str | None, work: str | None) -> tuple[int, int]:
    """(в скольких заключениях требовали, сколько заключений) — по виду объекта и работ, если таких хватает,
    иначе по виду объекта, иначе по всем."""
    k = knowledge()
    stats = k["doc_types"].get(doc_id, {})
    pop = k.get("population", {})
    for key in (f"{obj}/{work}", obj or "", "all"):
        n = pop.get(key, 0)
        if n >= MIN_POPULATION:
            return stats.get(key, 0), n
    return 0, 0


MIN_POPULATION = 15  # меньше заключений в группе — частота шумная, берём группу шире


# --- сборка знания ------------------------------------------------------------------------------------------------


def _jsonl(path: Path) -> dict[str, dict]:
    rows = (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return {r["key"]: r for r in rows}


def conclusion_objects(real: Path, cache_path: Path) -> dict[str, dict]:
    """sha256[:12] заключения → закупка и наименование объекта (заголовок заключения или название закупки).

    Первые шесть страниц читает pypdfium2: заголовок с наименованием — в начале. Итог кэшируется: заключений сотни.
    """
    import pypdfium2 as pdfium

    from zerocycle.remarks.conclusion import _meta

    cached = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    titles = {}
    listing = real / "_list.json"
    if listing.exists():
        titles = {a: (v or {}).get("title") for a, v in json.loads(listing.read_text(encoding="utf-8")).items()}
    out = dict(cached)
    for path in sorted(real.glob("*/files/*")):
        if path.suffix.lower() != ".pdf" or not CONCLUSION_FILE.search(path.name) or "_x" in path.parts:
            continue
        sha = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        if sha in out:
            continue
        announce = path.parent.parent.name
        meta: dict = {}
        try:
            pdf = pdfium.PdfDocument(path)
            try:
                pages = []
                for i in range(min(6, len(pdf))):
                    page = pdf[i]
                    textpage = page.get_textpage()
                    pages.append(textpage.get_text_range())
                    textpage.close()
                    page.close()
            finally:
                pdf.close()
            _meta(pages, meta)
        except Exception:  # битый файл: остаётся название закупки
            pass
        out[sha] = {"announce": announce, "object": meta.get("object"), "title": titles.get(announce)}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def build(items: Path, labels: Path, real: Path, split: Path, cache_path: Path) -> dict:
    items_by_key, labels_by_key = _jsonl(items), _jsonl(labels)
    exclude = json.loads(split.read_text(encoding="utf-8"))["knowledge_exclude"]
    closed = set(exclude["conclusions_sha12"])
    closed_numbers = set(exclude["conclusion_numbers"])
    objects = conclusion_objects(real, cache_path)
    per_conclusion: dict[str, set[str]] = defaultdict(set)  # заключение → виды документов, которые требовали
    seen: set[str] = set()
    added_total = classified = 0
    unmatched: Counter[str] = Counter()
    excluded = 0
    for key, item in items_by_key.items():
        prefix = key.split(":")[0]
        if prefix in closed or item.get("conclusion") in closed_numbers:
            excluded += 1
            continue
        seen.add(prefix)
        added = (labels_by_key.get(key) or {}).get("added")
        if not added:
            continue
        added_total += 1
        kind = classify(added)
        if kind:
            classified += 1
            per_conclusion[prefix].add(kind)
        elif not REVISED.search(_norm(added)):
            unmatched[_norm(added).lower()[:80]] += 1
    population: Counter[str] = Counter()
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    unknown = 0
    for prefix in seen:
        meta = objects.get(prefix, {})
        name = meta.get("object") or meta.get("title")
        obj, work = object_class(name), work_kind(name)
        if obj is None or work is None:
            unknown += 1
        keys = ["all", *([obj] if obj else []), *([f"{obj}/{work}"] if obj and work else [])]
        for k in keys:
            population[k] += 1
        for doc in per_conclusion.get(prefix, ()):
            for k in keys:
                stats[doc][k] += 1
    return {
        "built": date.today().isoformat(),
        "source": "разметка пунктов заключений РК (Codex gpt-6.1-sol, high), поле «представлен документ»",
        "conclusions": len(seen),
        "conclusions_unclassified": unknown,
        "excluded_items": excluded,
        "excluded_conclusions": len(closed),
        "added_items": added_total,
        "added_classified": classified,
        "population": dict(sorted(population.items())),
        "doc_types": {d.id: dict(sorted(stats[d.id].items())) for d in doc_types()},
        "unmatched_top": dict(unmatched.most_common(40)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Знание экспертизы РК для движка v2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--items", type=Path, default=Path("var/labels/kz_all_items.jsonl"))
    b.add_argument("--labels", type=Path, default=Path("var/labels/kz_codex_high.jsonl"))
    b.add_argument("--real", type=Path, default=Path("var/real/goszakup_kz"))
    b.add_argument("--split", type=Path, default=Path("docs/bench/data/v3_split.json"))
    b.add_argument("--cache", type=Path, default=Path("var/labels/kz_conclusion_objects.json"))
    b.add_argument("--out", type=Path, default=Path("src/zerocycle/packs/kz/expertise.json"))
    a = ap.parse_args()
    k = build(a.items, a.labels, a.real, a.split, a.cache)
    unmatched = k.pop("unmatched_top")
    a.out.write_text(json.dumps(k, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(
        f"заключений {k['conclusions']} (без вида объекта или работ — {k['conclusions_unclassified']}), "
        f"исключено пунктов {k['excluded_items']}; «представлен документ» — {k['added_items']}, "
        f"разнесено по видам {k['added_classified']}"
    )
    print("группы:", k["population"])
    top = sorted(((v.get("all", 0), d) for d, v in k["doc_types"].items()), reverse=True)
    print("чаще всего:", ", ".join(f"{d} {n}" for n, d in top[:15]))
    print("не разнесено (первые):", list(unmatched.items())[:25])


if __name__ == "__main__":
    main()
