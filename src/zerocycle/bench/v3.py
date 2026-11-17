"""Датасет V3, слой 1: реальные пакеты РК после экспертизы с обратным засевом (docs/bench/DATASET_V3.md).

Пакет закупки берётся целиком, во всех форматах: архивы распакованы (bench/archives.py; тома одного RAR — в одну
папку), сметы и таблицы остаются. Чего распаковать не удалось (файл переходит из тома в том, казахские буквы в имени
у bsdtar), — в эталоне, extract_lost. Из пакета убраны:
- заключение и всё, что выдаёт итог экспертизы (HIDDEN): письма экспертизы, приказ об утверждении ПСД с показателями
  «до экспертизы / после экспертизы», техспецификация закупки;
- документы, добавленные по замечаниям (PACKAGES): карта scripts/seed_map.py, просмотренная вручную, — вместе с
  точными копиями этих файлов в других местах пакета.

Из имён файлов и папок убраны пометки «после ГЭ», «после замечаний», «по замечаниям», «до экспертизы», «закл», «+++»:
до экспертизы их не было, а агенту они подсказывают, что правили.

    python -X utf8 -m zerocycle.bench.v3 build <корпус>  [--real var/real/goszakup_kz]
        [--items var/labels/kz_all_items.jsonl] [--labels var/labels/kz_codex_high.jsonl]
    python -X utf8 -m zerocycle.bench.v3 split <корпус>  → docs/bench/data/v3_split.json (PROTOCOL_V3.md)

Эталон — <корпус>/<пакет>/ground_truth.json рядом с package/, вне git: в нём тексты пунктов заключений.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from zerocycle.bench.archives import extract, is_archive, set_name, seven_zip, volumes

REAL = Path("var/real/goszakup_kz")
ITEMS = Path("var/labels/kz_all_items.jsonl")
LABELS = Path("var/labels/kz_codex_high.jsonl")

# Заключение (и по-казахски), мотивированный отказ экспертизы (в нём замечания), письма экспертизы, приказ об
# утверждении ПСД (в нём показатели до и после экспертизы), техспецификация закупки — не часть ПСД.
HIDDEN = re.compile(r"аключени|қорытынд|мотивированн\w*\s+отказ|^Исх\.?\s*письм|^Приказ\b|^techspec|^техспец", re.I)
# «Техническое заключение по результатам обследования здания» — исходные данные реконструкции, а не ответ экспертизы
SURVEY = re.compile(r"тех\w*[\s._-]*заключ|заключени\w*[\s._-]+(?:по|о)[\s._-]+(?:техническ|обследов|результат)", re.I)
# Ответ внутри документа под другим именем: заключение, отказ, перечень замечаний — ищем в тексте первых листов PDF.
# Пометку «откорректирован по замечаниям экспертизы» на титуле тоже видно: убрать её из PDF нельзя, только записать.
LEAK = re.compile(
    r"заключени\w*\s+(?:по\s+рабочему\s+проекту|комплексной\s+вневедомственной|государственной\s+экспертиз)"
    r"|мотивированн\w+\s+отказ|сараптама\s+қорытынды|Дополнения\s+и\s+изменения,\s+внесенные|перечень\s+замечаний"
    r"|по\s+замечани\w+\s+экспертиз",
    re.I,
)
# пометки состояния до и после экспертизы в именах файлов и папок
MARKS = (
    re.compile(r"[\s_-]*\(?после[\s_]+(?:ГЭ(?![а-яёa-z])|экспертиз[а-яё]*|зам(?:еч[а-яё]*)?(?![а-яё]))\)?", re.I),
    re.compile(r"[\s_-]*\(?до[\s_]+экспертиз[а-яё]*\)?", re.I),
    re.compile(r"[\s_-]*по[\s_]+зам(?:еч[а-яё]*)?(?![а-яё])", re.I),
    re.compile(r"\s+закл(?![а-яё])", re.I),
    re.compile(r"\s*\+{2,}"),
    re.compile(r"^\+\s*"),
)
MAX_DEPTH = 3  # архивы в архивах


@dataclass(frozen=True)
class Seed:
    file: (
        str  # путь в пакете закупки: имя файла или «архив/путь внутри архива» (archives.members, набор томов — «X.rar»)
    )
    remark: str  # ключ пункта заключения (label_remarks.py items): sha256 заключения[:12] + ":" + sha1 текста[:10]
    also: tuple[str, ...] = ()  # другие файлы того же документа: ПОС томом, стройгенпланом и исходником — убрать все
    kind: str = ""  # вид документа (KINDS), если по имени файла его не понять
    text: str = ""  # как документ называют в находке, если вида мало: у «прочих расчётов» — какой расчёт (v3score)


@dataclass(frozen=True)
class Package:
    announce: str
    seeds: tuple[Seed, ...] = ()
    group: str | None = None  # пакеты одного комплекса: их дефекты не независимы


OKZHETPES = "okzhetpes"  # 2-я и 3-я очереди одного комплекса: свои заключения, но 12 общих файлов
OV = "Том 3.1.4 -Отопление и вентиляция.zip/Том 3.1.4 -Отопление и вентиляция"  # 17152405
DOSTYK = "школа предпринимателей достык 226.rar/школа предпринимателей достык 226"  # 15648783
BOTAKARA = "смета КОС Ботакара после зам окончательный.rar/смета КОС Ботакара после зам окончательный"  # 17018973
ZAL = "DownloadAllFinalDocuments (7).rar/DownloadAllFinalDocuments (7)"  # 17611189
KEMEL = "Кемель ФИНИШ с портала.rar/Кемель ФИНИШ с портала"  # 17849240
SVMR = "Сводная ведомость материальных ресурсов и оборудования"
SVMR_ = SVMR.replace(" ", "_")
ARKALYK = "ТОМ 1 книга 2 ЭП.rar/ТОМ 1 книга 2 ЭП/Энергетический паспорт Аркалык (1)"  # 15966961
TEPLO = "teplo-uch"  # распределительные тепловые сети, участки 10, 11, 16: одни заключения, один проектировщик
WATER = "water-sela"  # водоснабжение сёл Шалкар и Жармухамбет: соседние закупки, одинаковые заключения

# Карта обратного засева 12.11 (DATASET_V3.md): 17 сопоставлений, просмотрены. Отброшено одно: «расчёты расходов
# воды и ёмкости резервуаров» ↔ «Резервуар чистой воды 92-95.pdf» (17161636) — там чертежи типового резервуара.
PACKAGES = {
    "kz-01": Package(
        "16909959", (Seed("КАЗ.СОДЕРЖАНИЕ____7e8724e8d66cdb7f104885d6af97b792.pdf", "bd29377de1f1:95b0704e0d"),)
    ),
    "kz-02": Package("17039050", (Seed("Казсодержание.pdf", "338861f2444b:2f773f531a"),)),
    "kz-03": Package(  # real-3
        "17067800",
        (
            Seed(
                "Расчет_кж.pdf",
                "cdeb8bfbaa59:5cf1985b50",
                # ТС.КЖ.Р — железобетонные узлы УТ1–УТ5, неподвижные опоры, балки. Расчёт трубопроводов на прочность
                # («Старт-Проф») — другой документ: «прочность» одна его не называет (проверка обвязки 18.11)
                text=r"расч[её]т\w*[^.]{0,60}(?:несущ|строительн\w*\s+конструкц|железобетон|(?<![а-яё])КЖ(?![а-яё])"
                r"|(?<![а-яё])УТ\s*\d*(?![а-яё])|неподвижн\w*\s+опор|узл\w*\s+трубопровод)",
            ),
            Seed("Казсодержание.pdf", "cdeb8bfbaa59:9a0ac9a1cd"),
        ),
    ),
    "kz-04": Package(
        "17248156",
        (
            Seed("Каз содержание.xlsx", "5e372844f614:4428bb21f2"),
            Seed("PZ1-27_RU.docx", "5e372844f614:5d035cdda4"),  # пояснительная записка к смете: код SANA
        ),
    ),
    "kz-05": Package(  # real-1
        "17560023",
        (
            Seed("Отчет по инженерно-геологическим изысканиям +++.pdf", "1629476656ac:7299a6fa12"),
            Seed("Теплотехнический расчёт +++.pdf", "1629476656ac:fae4b24307"),
            Seed("+ Энергетический паспорт закл.pdf", "1629476656ac:f234a27513"),
            Seed("+ Сводная ресурсная ведомость закл.pdf", "1629476656ac:07f9be9222"),
        ),
        OKZHETPES,
    ),
    "kz-06": Package(
        "17560893",
        (
            Seed("Отчет по инженерно-геологическим изысканиям +++.pdf", "3b7a369c0071:7299a6fa12"),
            Seed("Теплотехнический расчет +++.pdf", "3b7a369c0071:fae4b24307"),
            Seed("+ Энергетический паспорт закл.pdf", "3b7a369c0071:f234a27513"),
        ),
        OKZHETPES,
    ),
    "kz-07": Package("17571053", (Seed("Том 16 - МОПБ.pdf", "ee4f8730daca:170c86ee77"),)),  # real-2
    "kz-08": Package(
        "17763665",
        (Seed("Энергетический_паспорт_на_Блок_А_be9d5afa4a273c3facd2abae4458b2c2.pdf", "f8e75af10b67:e941ddba81"),),
    ),
    "kz-09": Package(
        "17778737",
        (
            Seed("Геологический отчет с.Тауыш ФАП.pdf", "158c6fd0eef2:cf370f1795"),
            Seed("ПОДПИСАН. Перечень цен по прейскурантам.pdf", "158c6fd0eef2:e38cb08a84"),
        ),
    ),
    # Второй прогон карты — с архивами (12.11). «перечень оборудования, материалов.xls» ↔ «сводная ведомость
    # материальных ресурсов» не взят: старый .xls не прочитать, совпадение не проверено.
    "kz-10": Package(
        "17152405",
        (
            Seed(f"{OV}/Теплотехн. расчет ОВ Явленка (1).pdf", "979d11a81993:cfa42f77e4"),
            Seed(
                f"{OV}/Гидравлический расчет.pdf",
                "979d11a81993:a0e5ac03c4",
                text=r"гидравлическ\w*\s+расч[её]т|расч[её]т\w*[^.]{0,40}гидравлическ",
            ),
        ),
    ),
    "kz-11": Package(
        "17866626",
        (
            Seed(
                "ПСД 1.zip/ПСД 1/ВК_Ф_Расчеты_по_водоснабжению_и_водоотведению (2).pdf",
                "cc53fae1c077:ab1a401566",
                text=r"расч[её]т\w*[^.]{0,60}(?:водоснабжени|водоотведени|канализаци|(?<![а-яё])ВК(?![а-яё]))"
                r"|(?:водоснабжени|водоотведени|канализаци)\w*[^.]{0,40}расч[её]т",
            ),
        ),
    ),
    # Многотомные RAR — распаковывает 7-Zip (установлен 12.11).
    "kz-12": Package(  # ТУ теплоснабжения — единственные ТУ в пакете
        "15648783", (Seed(f"{DOSTYK}/ТУ_ТС_5a836ece65c729f324c0a2d7c584731b.pdf", "0c5a6803e36d:a57644a64f"),)
    ),
    "kz-13": Package(  # остальная ПСД — в томах part1–7; тома 6–7 — хвост набора, чьи первые тома не выложены
        "17018973", (Seed(f"{BOTAKARA}/казсодержание.xlsx", "c00940711371:7212595133"),)
    ),
    "kz-14": Package(
        "17477768",
        (
            Seed(
                "Тамарикс.rar/Тамарикс/расчет дым трубы.pdf",
                "ddab36245dbe:a8d701f6ba",
                text=r"дымов\w*\s+труб|аэродинамическ\w*\s+расч[её]т",
            ),
        ),
    ),
    "kz-15": Package("17611189", (Seed(f"{ZAL}/ПОС_993f6c98c40f2c74bc8e89fbc764fad7.pdf", "26bfa4a47242:1344ec7757"),)),
    "kz-16": Package(  # ПОС — по двум пунктам: «раздела ПОС нет» (70bb1e7fba6c:313e07e181) и «предоставить ПОС»
        "17849240",
        (
            Seed(
                f"{KEMEL}/двг ворд/ПОС.pdf",
                "70bb1e7fba6c:86ec1ce3dd",
                (
                    f"{KEMEL}/двг ворд/1 ПЗ ПОС Благ-тво сквера мкр.Кемель_ г.Алматы 11.02.2026 (1).docx",
                    f"{KEMEL}/ПОС ПЗ Благ-тво сквера мкр.Кемель_ г.Алматы 11.02.2026 (1) том 4.pdf",
                    f"{KEMEL}/ПОС СГП Благоустр. парковой зоны мкр.Кемель Жетысуск.г.Алматы-Стройгенплан"
                    " том 4 книга 13.pdf",
                ),
            ),
        ),
    ),
    # Сбор 13.11: список goszakup — 1413 закупок, 12 085 пунктов; карта — 58 сопоставлений, просмотрены. Отброшены:
    # «гарантийное письмо на землю» ↔ смета электросетей (14534860), казсодержание ↔ сводная ведомость ресурсов
    # (13780218, 13968111), «попикетная ведомость объёмов работ» ↔ основной том (13720653).
    "kz-17": Package("13370053", (Seed("Сводная ресурс.ведомость.pdf", "de57fcc6960a:3332b5bc3e"),)),
    "kz-18": Package("13373454", (Seed("Каз Содержание.pdf", "44375a45a098:8ff1d7c4d2"),)),
    "kz-19": Package("13541669", (Seed(f"{SVMR_}.pdf", "ac74e8c3a4cf:b7c019ada0"),), TEPLO),
    "kz-20": Package("13542151", (Seed(f"{SVMR_}.pdf", "e88f5927ae22:b7c019ada0"),), TEPLO),
    "kz-21": Package("13542193", (Seed(f"{SVMR_}4.pdf", "ba418dbe0017:b7c019ada0"),), TEPLO),
    "kz-22": Package("13649784", (Seed(f"{SVMR}.pdf", "9b1bc54933c9:07f9be9222"),), WATER),
    "kz-23": Package("13650335", (Seed(f"{SVMR}.pdf", "838fc7b83de3:07f9be9222"),), WATER),
    "kz-24": Package("13780218", (Seed(f"{SVMR}.pdf", "f7b7f327829b:3332b5bc3e"),)),
    "kz-25": Package(
        "13928527",
        (
            Seed(
                "расчет продолжительности стр.pdf",
                "a258cb4d233c:2e5ed96cd5",
                text=r"продолжительност\w*\s+строительств|срок\w*\s+строительств",
            ),
        ),
    ),
    "kz-26": Package("13928702", (Seed("Энергетический паспорт.pdf", "88d611c4c34d:3870e5348a"),)),
    "kz-27": Package(
        "13968111",
        (
            Seed("F8550 сводная ведом. материал. ресур. и оборуд..pdf", "a8fc227d196b:3332b5bc3e"),
            Seed("Протокол Астр месторожд1501ЦКН.PDF", "a8fc227d196b:92a510c001"),  # подтверждение запасов воды
        ),
    ),
    "kz-28": Package("14154877", (Seed(f"+ {SVMR}.pdf", "f349acbe70f3:07f9be9222"),)),
    "kz-29": Package(
        "14534860",
        (
            Seed(
                "ПОС - Реконструкция электрических сетей в селе Хан ордасы Бокейординского района.pdf",
                "c7455c6f3c82:6f6456cc5d",
            ),
        ),
    ),
    "kz-30": Package(  # «в составе проекта разработан ПОС» — добавлен; файлов ПОС два
        "14772419",
        (Seed("1.ПОС пр.Назарбаева-испр 23.12.2024.pdf", "600432358b56:816ee36480", ("+++ПОС Назарбаева.pdf",)),),
    ),
    "kz-31": Package(
        "14975193",
        (
            Seed(
                "Сводная ведомость потребности основных материалов, изделий, конструкций и оборудования.pdf",
                "8f592ee237c3:bd7e63abc6",
            ),
        ),
    ),
    "kz-32": Package("15011223", (Seed("Казсодержание_17.04.pdf", "fbd9b5c7fba3:d8577e0517"),)),
    "kz-33": Package("15660545", (Seed("Энергопаспорт-Сельский акимат.pdf", "b489e72e5409:f5a9007491"),)),
    "kz-34": Package(  # капитальный ремонт автодороги
        "15781238",
        (
            Seed("Казахстанское содержание.xlsx", "0cb17163ffd6:2f773f531a"),
            Seed("+Сводная ведомость материальных ресурсов (форма 7).pdf", "0cb17163ffd6:60c363cac5"),
        ),
    ),
    "kz-35": Package(
        "15805375",
        (
            Seed(
                "+Инженерно-геологические изыскания.pdf",
                "83caf2af278c:7c5057fdf4",
                ("+Инженерно-геологические изыскания ИСПР.pdf",),
            ),
        ),
    ),
    "kz-36": Package("15851663", (Seed("каз содерж.pdf", "55ecd3fe3aa0:bdd1b66962"),)),
    "kz-37": Package(
        "15966961",
        (Seed(f"{ARKALYK}.pdf", "bbf8fdc783b7:e941ddba81", (f"{ARKALYK}.doc",)),),
    ),
    "kz-38": Package(
        "16167610", (Seed("каз.содержание_ab739d740ef1be4b34d075e9edae69ef.pdf", "887b88ff48b4:0554c4a4c6"),)
    ),
    "kz-39": Package(  # две школы Шуского района, у каждой своё заключение и свой энергопаспорт (сверено по титулам)
        "16502460",
        (
            Seed("Брошюра 2 _ Энергетический паспорт.pdf", "6256697833fb:cb7a57c690"),  # СШ им. Естемесова
            Seed("Брошюра 2 _ исправ оканч + 20.06.2023.pdf", "2ac6bab93dba:cb7a57c690", kind="energy"),  # СШ им. Абая
        ),
    ),
}

# Виды убранных документов — для полноты по типам (PROTOCOL_V3.md): первое совпадение по имени файла.
# Группы: смета, энергопаспорт, расчёты, ИГИ, ПОС, прочие.
KINDS = (
    ("kazsod", "смета", "ведомость казахстанского содержания", r"каз\w*[\s._-]*содерж|ведомост\w*[\s._-]+потребност"),
    (
        "svmr",
        "смета",
        "сводная ведомость материальных ресурсов",
        r"ресурс\w*[\s._-]*ведом|ведом\w*[\s._-]+материал\w*[\s._-]+ресур",
    ),
    ("pz_smeta", "смета", "пояснительная записка к смете", r"записк\w*[\s._-]+к[\s._-]+смет|^PZ\d"),
    ("prices", "смета", "перечень цен по прейскурантам", r"перечень[\s._-]+цен|прейскурант"),
    ("energy", "энергопаспорт", "энергетический паспорт", r"энерг\w*[\s._-]*паспорт"),
    ("thermal", "расчёты", "теплотехнический расчёт", r"теплотехн"),
    ("calc", "расчёты", "прочие расчёты", r"расч[её]т"),
    ("igi", "ИГИ", "отчёт об инженерно-геологических изысканиях", r"геолог|(?<![а-яё])ИГИ(?![а-яё])"),
    ("pos", "ПОС", "проект организации строительства", r"(?<![а-яё])ПОС(?![а-яё])"),
    ("mopb", "прочие", "мероприятия по пожарной безопасности", r"МОПБ|пожарн"),
    ("tu", "прочие", "технические условия", r"(?<![а-яё])ТУ(?![а-яё])"),
    ("water", "прочие", "протокол утверждения запасов подземных вод", r"месторожд|запас\w*[\s._-]+вод"),
)
KIND_GROUP = {k: g for k, g, _, _ in KINDS}

# Вид объекта: сети, коллекторы и дорога — линейные; парки — благоустройство; остальное — здания и сооружения.
LINEAR = frozenset(
    {"kz-01", "kz-03", "kz-13", "kz-17", "kz-19", "kz-20", "kz-21", "kz-22", "kz-23"}
    | {"kz-27", "kz-28", "kz-29", "kz-30", "kz-31", "kz-34", "kz-38"}
)
LANDSCAPE = frozenset({"kz-04", "kz-16", "kz-18"})

# Разделение (PROTOCOL_V3.md, 17.11): в разработку — пакеты, на которых уже работали движок или прошлые бенчмарки, и их
# группы. Отложенными они быть не могут; на них пилот и отладка движка v2. Остальное — отложенная часть.
SEEN = {
    "kz-03": "real-3 бенчмарка на реальных пакетах (PROTOCOL_REAL)",
    "kz-05": "real-1 того же бенчмарка",
    "kz-07": "real-2 того же бенчмарка",
    "kz-08": "опись и штампы реальных пакетов, шаг 0.11 (REAL_KZ)",
    "kz-09": "Тауыш: на нём писались и проверялись правила kz и ru (CHECK_KZ, CHECK_RU)",
}
# Заключения в папке закупки — как в разборе пунктов (items_from_conclusions): PDF с такими словами в имени
CONCLUSION = re.compile(r"заключ|экспертиз|қорыт", re.I)


def kind(seed: Seed) -> str:
    """Вид убранного документа: явный или по имени файла."""
    if seed.kind:
        return seed.kind
    name = PurePosixPath(seed.file).name
    for k, _, _, pattern in KINDS:
        if re.search(pattern, name, re.I):
            return k
    raise ValueError(f"вид документа не определён: {seed.file}")


def object_class(name: str) -> str:
    return "линейные" if name in LINEAR else "благоустройство" if name in LANDSCAPE else "здания"


def split(packages: dict[str, Package] | None = None) -> dict[str, list[str]]:
    """Разработка — SEEN и пакеты их групп, отложенная часть — остальное."""
    packages = packages or PACKAGES
    groups = {packages[n].group for n in SEEN if n in packages} - {None}
    dev = {n for n, p in packages.items() if n in SEEN or p.group in groups}
    return {"dev": sorted(dev), "heldout": sorted(set(packages) - dev)}


def conclusions_of(real: Path, announces: list[str]) -> set[str]:
    """sha256[:12] заключений закупок — начало ключей их пунктов (remarks.measure.remark_key)."""
    return {
        sha256(p)[:12]
        for a in announces
        for p in (real / a).rglob("*.pdf")
        if CONCLUSION.search(p.name) and "_x" not in p.parts
    }


def excluded_items(items: dict[str, dict], prefixes: set[str]) -> tuple[set[str], set[str]]:
    """Пункты, которых не должно быть в знании движка v2: из заключений отложенных закупок и их копий, приложенных
    к другим закупкам (тот же номер заключения). Возвращает ключи пунктов и номера заключений."""
    numbers = {r.get("conclusion") for k, r in items.items() if k.split(":")[0] in prefixes} - {None, ""}
    keys = {k for k, r in items.items() if k.split(":")[0] in prefixes or r.get("conclusion") in numbers}
    return keys, numbers


def is_hidden(name: str) -> bool:
    """Файл выдаёт итог экспертизы или не относится к ПСД: агенту не показываем."""
    return bool(HIDDEN.search(name)) and not SURVEY.search(name)


def clean_name(name: str) -> str:
    """Имя файла или папки без пометок до и после экспертизы: «+ Том 2. ПЗ закл.pdf» → «Том 2. ПЗ.pdf»."""
    p = PurePosixPath(name)
    suffix = p.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,5}", p.suffix) else ""
    stem = name[: len(name) - len(suffix)]
    for mark in MARKS:
        stem = mark.sub("", stem)
    stem = stem.strip(" _-")
    return (stem or name[: len(name) - len(suffix)]) + suffix


def stage(src: Path, out: Path, scratch: Path) -> tuple[dict[str, Path], list[str]]:
    """Все файлы пакета закупки → out, архивы распакованы в папки с их именами, тома одного RAR — в одну папку.

    Возвращает исходный путь («имя» или «архив/путь внутри архива») → файл в out и потерянное при распаковке.
    """
    files: dict[str, Path] = {}
    lost: list[str] = []
    folders: dict[Path, Path] = {}  # архив без «.partN» → папка в пакете

    def add(path: Path, key: str, dest: Path, depth: int) -> None:
        if is_archive(path.name) and depth < MAX_DEPTH:
            if seven_zip() and path != volumes(path)[0]:
                return  # 7-Zip распаковал весь набор томов с первого
            key = key[: len(key) - len(path.name)] + set_name(path.name)  # «ПСД.rar/…» для всех томов набора
            tmp = scratch / f"x{len(list(scratch.iterdir()))}"
            lost.extend(f"{key}/{x}" for x in extract(path, tmp))
            base = dest.with_name(PurePosixPath(set_name(path.name)).stem)
            if base not in folders:
                target = base
                while target.exists():
                    target = target.with_name(target.name + "_")
                target.mkdir(parents=True)
                folders[base] = target
            # «АР.zip» с единственной папкой «АР/» внутри — без лишнего уровня; ключ — путь как в архиве
            top = list(tmp.iterdir())
            same = len(top) == 1 and top[0].is_dir() and top[0].name.casefold() == base.name.casefold()
            root = top[0] if same else tmp
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    add(p, f"{key}/{p.relative_to(tmp).as_posix()}", folders[base] / p.relative_to(root), depth + 1)
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        files[key] = dest

    scratch.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.iterdir()):
        if p.is_file():
            add(p, p.name, out / p.name, 0)
    return files, lost


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pdf_info(path: Path, head: int = 3) -> tuple[int | None, str]:
    """Листов в PDF (None — не открывается) и текст первых head листов."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        return None, ""
    try:
        text = " ".join(pdf[i].get_textpage().get_text_range() for i in range(min(head, len(pdf))))
        return len(pdf), " ".join(text.split())
    finally:
        pdf.close()


def load_jsonl(path: Path) -> dict[str, dict]:
    rows = (json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    return {r["key"]: r for r in rows}


def build_one(
    name: str, spec: Package, corpus: Path, real: Path, items: dict[str, dict], labels: dict[str, dict]
) -> dict:
    src = real / spec.announce
    out = corpus / name / "package"
    scratch = corpus / name / "_extract"
    for d in (out, scratch):
        if d.exists():
            shutil.rmtree(d)
    files, lost = stage(src / "files", out, scratch)
    shutil.rmtree(scratch)

    hidden = sorted(k for k in files if is_hidden(PurePosixPath(k).name))
    defects = []
    for n, seed in enumerate(spec.seeds, 1):
        for f in (seed.file, *seed.also):
            if f not in files:
                why = " — не распакован" if any(x.startswith(f + ":") for x in lost) else ""
                raise ValueError(f"{name}: нет файла для засева: {f}{why}")
        if seed.remark not in items or seed.remark not in labels:
            raise ValueError(f"{name}: нет пункта {seed.remark} в разметке")
        digest = sha256(files[seed.file])
        own = {seed.file, *seed.also}
        digests = {sha256(files[f]) for f in own}
        copies = sorted(k for k, p in files.items() if k not in own and sha256(p) in digests)
        defects.append(
            {
                "id": f"{name}.{n}",
                "file": seed.file,
                "also": list(seed.also),
                "sha256": digest,
                "copies_removed": copies,
                "remark_key": seed.remark,
                "remark": items[seed.remark]["text"],
                "added": labels[seed.remark].get("added"),
                "category": labels[seed.remark].get("category"),
            }
        )
    removed = set(hidden) | {f for d in defects for f in (d["file"], *d["also"], *d["copies_removed"])}
    for k in removed:
        files.pop(k).unlink()

    renamed = {}
    for k, p in sorted(files.items()):
        new = out.joinpath(*(clean_name(part) for part in p.relative_to(out).parts))
        if new != p and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            p.rename(new)
            files[k] = new
            renamed[k] = new.relative_to(out).as_posix()
    for d in sorted((p for p in out.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()

    gone = {d["sha256"] for d in defects}
    left = [p for p in out.rglob("*") if p.is_file() and sha256(p) in gone]
    if left:
        raise AssertionError(f"{name}: в пакете остались копии убранных документов: {left}")

    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    missing = {f["name"] for f in manifest["files"].values() if f.get("status") != "ok"}
    info = {k: pdf_info(p) for k, p in files.items() if p.suffix.lower() == ".pdf"}
    pages = {k: n for k, (n, _) in info.items()}
    leaks = {
        k: text[max(0, m.start() - 60) : m.end() + 60] for k, (_, text) in info.items() if (m := LEAK.search(text))
    }
    truth = {
        "package": name,
        "announce": spec.announce,
        "title": manifest.get("title"),
        "group": spec.group,
        "files": len(files),
        "formats": dict(Counter(p.suffix.lower() or "—" for p in files.values()).most_common()),
        "pdf_pages": sum(n for n in pages.values() if n),  # знаменатель «ложных тревог на 100 листов»
        "unreadable": sorted(k for k, n in pages.items() if n is None),
        # текст первых листов похож на заключение или пометку «по замечаниям экспертизы»: просмотреть
        "leak_suspects": leaks,
        "hidden": hidden,
        # не скачались с портала: их отсутствие в пакете — не засеянный дефект
        "missing_in_source": sorted(n for n in missing if not is_hidden(n)),
        # в архиве есть, но целиком не распаковалось: тоже не засеянный дефект
        "extract_lost": lost,
        "renamed": renamed,
        "defects": defects,
    }
    (corpus / name / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
    return truth


def build(
    corpus: Path,
    real: Path = REAL,
    items: Path = ITEMS,
    labels: Path = LABELS,
    packages: dict[str, Package] | None = None,
) -> list[dict]:
    items_by_key, labels_by_key = load_jsonl(items), load_jsonl(labels)
    return [
        build_one(name, spec, corpus, real, items_by_key, labels_by_key)
        for name, spec in (packages or PACKAGES).items()
    ]


SPLIT_DATE = "2026-11-17"
SPLIT = Path("docs/bench/data/v3_split.json")


def split_record(corpus: Path, real: Path = REAL, items: Path = ITEMS) -> dict:
    """Разделение со сводкой по частям и списком пунктов, закрытых для знания движка v2."""
    parts = split()
    truth = {n: json.loads((corpus / n / "ground_truth.json").read_text(encoding="utf-8")) for n in PACKAGES}

    def summary(names: list[str]) -> dict:
        defects = [(n, kind(s)) for n in names for s in PACKAGES[n].seeds]
        return {
            "packages": len(names),
            "clusters": len({PACKAGES[n].group or n for n in names}),
            "defects": len(defects),
            "files": sum(truth[n]["files"] for n in names),
            "pdf_pages": sum(truth[n]["pdf_pages"] for n in names),
            "defects_by_group": dict(Counter(KIND_GROUP[k] for _, k in defects).most_common()),
            "defects_by_kind": dict(Counter(k for _, k in defects).most_common()),
            "defects_by_object": dict(Counter(object_class(n) for n, _ in defects).most_common()),
            "packages_by_object": dict(Counter(object_class(n) for n in names).most_common()),
        }

    heldout = parts["heldout"]
    prefixes = conclusions_of(real, [PACKAGES[n].announce for n in heldout])
    keys, numbers = excluded_items(load_jsonl(items), prefixes)
    lost = [s.remark for n in heldout for s in PACKAGES[n].seeds if s.remark not in keys]
    if lost:
        raise AssertionError(f"пункты засева отложенной части не попали в исключение: {lost}")
    return {
        "date": SPLIT_DATE,
        "rule": "разработка — пакеты, на которых уже работали движок или прошлые бенчмарки, и их группы; "
        "остальное — отложенная часть",
        "dev": [
            {"package": n, "announce": PACKAGES[n].announce, "why": SEEN.get(n) or f"группа {PACKAGES[n].group}"}
            for n in parts["dev"]
        ],
        "heldout": [{"package": n, "announce": PACKAGES[n].announce, "group": PACKAGES[n].group} for n in heldout],
        "summary": {"dev": summary(parts["dev"]), "heldout": summary(heldout)},
        # знание движка v2 из замечаний строится без этих пунктов: иначе B′ заранее знает ответы отложенной части
        "knowledge_exclude": {
            "conclusions_sha12": sorted(prefixes),
            "conclusion_numbers": sorted(numbers),
            "items": len(keys),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Датасет V3: пакеты РК с обратным засевом")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("corpus", type=Path)
    b.add_argument("--real", type=Path, default=REAL)
    b.add_argument("--items", type=Path, default=ITEMS)
    b.add_argument("--labels", type=Path, default=LABELS)
    b.add_argument("--only", help="только эти пакеты, через запятую: kz-17,kz-18")
    s = sub.add_parser("split", help="разделение на разработку и отложенную часть → JSON (PROTOCOL_V3.md)")
    s.add_argument("corpus", type=Path)
    s.add_argument("--real", type=Path, default=REAL)
    s.add_argument("--items", type=Path, default=ITEMS)
    s.add_argument("--out", type=Path, default=SPLIT)
    a = ap.parse_args()
    if a.cmd == "split":
        rec = split_record(a.corpus, a.real, a.items)
        a.out.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        for part in ("dev", "heldout"):
            print(part, json.dumps(rec["summary"][part], ensure_ascii=False))
        ex = rec["knowledge_exclude"]
        print(
            f"исключено из знания: {ex['items']} пунктов, заключений {len(ex['conclusions_sha12'])} файлов, "
            f"номеров {len(ex['conclusion_numbers'])}"
        )
        return
    chosen = {n: PACKAGES[n] for n in a.only.split(",")} if a.only else None
    for t in build(a.corpus, a.real, a.items, a.labels, chosen):
        ds = t["defects"]
        copies = sum(len(d["copies_removed"]) for d in ds)
        print(
            f"{t['package']} ({t['announce']}): файлов {t['files']}, листов PDF {t['pdf_pages']}, "
            f"скрыто {len(t['hidden'])}, дефектов {len(ds)}"
            + (f" (+ копий {copies})" if copies else "")
            + f", переименовано {len(t['renamed'])}"
            + (f"; не скачано: {', '.join(t['missing_in_source'])}" if t["missing_in_source"] else "")
            + (f"; не открываются: {', '.join(t['unreadable'])}" if t["unreadable"] else "")
            + (f"; не распаковано: {len(t['extract_lost'])}" if t["extract_lost"] else "")
            + (f"; ответ в тексте? {', '.join(t['leak_suspects'])}" if t["leak_suspects"] else "")
        )


if __name__ == "__main__":
    main()
