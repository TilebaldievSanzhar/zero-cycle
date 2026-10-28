"""Шаблоны «организаций» для генератора v1: как организация оформляет пакет ПД.

Шаблон задаёт:
- основную надпись — отдельно для текстовых документов (Word) и для чертежей (САПР);
- подписи ролей и даты подписей;
- хвосты обозначений частей тома и нумерацию листов;
- папки ПДЭ;
- банк формулировок текста, вид перечня норм, названия показателей ТЭП;
- дополнительные графы на поле для подшивки.

Варианты взяты из реальных ПСД (docs/bench/REAL_KZ.md) и практики оформления в РФ.

Отложенная часть слоя S (docs/bench/PROTOCOL.md) — пакеты на шаблонах из bench/heldout.py: их нет в разработке,
правила и опись на них не настраиваются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache

from zerocycle.bench.draw import StampStyle

ESKD = ("Изм.", "Лист", "№ докум.", "Подп.", "Дата")  # ГОСТ 2.104, форма 2а: без «Кол.уч»


@dataclass(frozen=True)
class Template:
    id: str
    split: str  # dev | heldout
    text_stamp: StampStyle  # основная надпись текстовых документов
    drawing_stamp: StampStyle  # основная надпись чертежей
    roles: tuple[str, str, str, str]  # разработал, проверил, нормоконтроль, ГИП
    # хвосты текстовой и графической частей тома; None — части без хвостов, нумерация листов сквозная
    tails: tuple[str, str] | None
    folders: tuple[str, str]  # папка раздела и подраздела ПДЭ: поля n (раздел), s (подраздел), code
    wording: str  # банк формулировок: dev | heldout
    norms_header: tuple[str, str, str] = ("№", "Обозначение", "Наименование")
    norms_code: str = "{code}"  # как организация пишет обозначение нормы в перечне
    tep_labels: dict[str, str] = field(default_factory=dict)  # показатель ТЭП → как его называет организация
    margin: bool = False  # дополнительные графы 20–22 на поле для подшивки
    staggered: bool = False  # подписи в разные дни: проверил — позже разработчика, ГИП — последним

    def folder(self, section: int, code: str, sub: int | None = None) -> str:
        top = self.folders[0].format(n=section, code=code)
        return top if sub is None else f"{top}/{self.folders[1].format(n=section, s=sub, code=code)}"


DEV: list[Template] = [
    Template(
        id="gost",
        split="dev",
        text_stamp=StampStyle(),
        drawing_stamp=StampStyle(),
        roles=("Разраб.", "Пров.", "Н. контр.", "ГИП"),
        tails=(".ТЧ", ".ГЧ"),
        folders=("{n:02d} {code}", "{n:02d}.{s} {code}{s}"),
        wording="dev",
        margin=True,
    ),
    Template(
        id="word-eskd",
        split="dev",
        text_stamp=StampStyle(header=("Изм.", "Кол.уч", "Лист", "№док", "Подпись", "Дата"), gap=0.5),
        drawing_stamp=StampStyle(columns=(7, 10, 23, 15, 10), header=ESKD),
        roles=("Разраб.", "Пров.", "Н.контр.", "ГИП"),
        tails=None,
        folders=("Раздел {n}. {code}", "Подраздел {n}.{s}. {code}{s}"),
        wording="dev",
        margin=True,
        staggered=True,
    ),
    Template(
        id="cad-long",
        split="dev",
        text_stamp=StampStyle(),
        drawing_stamp=StampStyle(row=5.5, grid="long"),
        roles=("Разработал", "Проверил", "Н. контр.", "ГИП"),
        tails=(".ТЧ", ".ГЧ"),
        folders=("{n:02d}_{code}", "{n:02d}.{s}_{code}{s}"),
        wording="dev",
        norms_header=("№ п/п", "Обозначение документа", "Наименование документа"),
    ),
    Template(
        id="compact",
        split="dev",
        text_stamp=StampStyle(
            row=4.5,
            columns=(9.6, 9.6, 9.6, 12.5, 14.0, 9.6),
            header=("Изм.", "Код", "Лист", "Nдок.", "Подп.", "Дата"),
        ),
        drawing_stamp=StampStyle(row=4.5, columns=(9.6, 9.6, 9.6, 12.5, 14.0, 9.6)),
        roles=("Исполн.", "Пров.", "Н. контр.", "ГИП"),
        tails=None,
        folders=("{n:02d} {code}", "{n:02d}.{s} {code}{s}"),
        wording="dev",
        margin=True,
        staggered=True,
    ),
]


@cache
def catalog() -> dict[str, Template]:
    """Все шаблоны: разработочные — здесь, отложенные — в bench/heldout.py."""
    from zerocycle.bench import heldout

    return {t.id: t for t in [*DEV, *heldout.TEMPLATES]}


def templates(split: str) -> list[Template]:
    return [t for t in catalog().values() if t.split == split]
