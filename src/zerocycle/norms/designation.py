"""Обозначение нормативного документа: «СН РК 1.02-03-2022» → вид «СН РК», номер «1.02-03», год 2022.

Один разбор для ссылок в тексте пакета и для записей индекса: ссылка и запись сравниваются по ключу (вид, номер),
а редакция — по году. Звёздочка («СП РК 2.04-01-2017*» — документ с изменениями) снимается; тире и латиница-двойник
(«CH PK» латиницей) приводятся к одному написанию с сохранением позиций — якорь ссылки указывает в исходный текст.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Виды документов: длинные раньше коротких («СНиП РК» раньше «СНиП», «ГОСТ Р» раньше «ГОСТ»)
KINDS = (
    "СНиП РК",
    "СН РК",
    "СП РК",
    "НТП РК",
    "РДС РК",
    "РД РК",
    "МД РК",
    "МДС РК",
    "ЭСН РК",
    "НДЦС РК",
    "СТ РК",
    "ГОСТ Р",
    "ГОСТ",
    "СНиП",
    "МСН",
    "МСП",
    "ВСН",
    "СанПиН",
    "СП",
    "СН",
    "РД",
    "МДС",
)
# виды, у которых двузначный хвост через дефис — всегда год: «ГОСТ 21.101-97», «ВСН 41-85»
_SHORT_YEAR_KINDS = frozenset({"ГОСТ", "ГОСТ Р", "СТ РК", "ВСН", "СН"})
_LATIN = "ACEHKMOPTXYacekopxy"
_CYRILLIC_TWINS = "АСЕНКМОРТХУасекорху"
_LOOKALIKE = str.maketrans(_LATIN, _CYRILLIC_TWINS)
_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—―−", "-"))
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_HAS_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_ONLY_TWINS = re.compile(rf"[{_LATIN}]{{2,4}}")

_KIND_RX = "|".join(re.escape(k).replace(r"\ ", r"\s*") for k in KINDS)
# номер: «1.02-03-2022», «21.101-97», «3.05.04-85», «01-01-3.1(4.1)-2017», «EN 1991-1-5:2003/2011», «III-23-76»;
# после дефиса бывает пробел или перенос строки: «4.02- 104-2013», «1.02-03-\r\n2022»
_NUMBER = r"(?:EN\s*)?[0-9IVX](?:[0-9IVX.:/()]|-\s{0,3}(?=[0-9IVX]))*[0-9IVX)]"
DESIGNATION = re.compile(rf"(?<![А-ЯЁA-Zа-яё])(?P<kind>{_KIND_RX})\s*(?:№\s*)?(?P<body>{_NUMBER})(?P<star>\*)?")
# виды РК с номером «A.BB-CC(C)-ГГГГ»: опечатки «1.03.00-2011», «4.01.-02-2009», «2.02-052009» — в каноническую запись
_KZ_KINDS = frozenset({"СН РК", "СП РК", "СНиП РК", "РДС РК", "МСН", "МСП"})
_KZ_TYPOS = (
    (re.compile(r"^(\d)\.(\d{2})\.-?(\d{2,3})-(\d{4})$"), "точка вместо дефиса"),
    (re.compile(r"^(\d)\.(\d{2})-(\d{2,3})((?:19|20)\d{2})$"), "год без дефиса"),
)
# продолжение номера после переноса строки: «СН РК 5.01-02-201\r\n3», «СП РК 1.02\r\n-102-2014»
_YEAR_REST = re.compile(r"\s{1,3}(\d{1,3})(?!\d)")
_DASH_REST = re.compile(rf"\s{{1,3}}-\s{{0,3}}({_NUMBER})")
# «НТП РК 01-0» + «1-3.1(4.1)-2017», «НТП РК 01-01-3.1» + «(4.1)-2017»: продолжение — только если оно кончается годом
_TAIL_REST = re.compile(r"\s{1,3}([(0-9][0-9IVX.()]*(?:-[0-9IVX.()]+)*-(?:19|20)\d{2})(?!\d)")
# номер российского СП без года: «54.13330», «6.13130», «256.1325800», «484.1311500»
_RU_SP = re.compile(r"\d+\.\d{5,7}")


@dataclass(frozen=True)
class NormDesignation:
    kind: str  # канонический вид: «СН РК», «ГОСТ»
    number: str  # без года: «1.02-03», «21.101»
    year: int | None  # год редакции; None — не указан
    text: str  # как написано
    issues: tuple[str, ...] = ()  # что исправлено при разборе: «точка вместо дефиса», «год без дефиса»

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.number

    @property
    def code(self) -> str:
        """Каноническая запись: год до 2000 — двумя цифрами, как принято («ГОСТ 21.101-97»), позже — четырьмя."""
        if self.year is None:
            return f"{self.kind} {self.number}"
        if self.kind == "СП" and _RU_SP.fullmatch(self.number):  # СП 54.13330.2016, СП 256.1325800.2016
            return f"{self.kind} {self.number}.{self.year}"
        short = self.year < 2000 or self.kind == "СанПиН"  # «СанПиН 2.1.3684-21» — год двумя цифрами и после 2000
        year = f"{self.year % 100:02d}" if short else str(self.year)
        return f"{self.kind} {self.number}-{year}"


def twins_to_cyrillic(text: str) -> str:
    """Латинские двойники в кириллическом слове («Cведения») и слова из одних двойников («CH», «PK») → кириллица.

    Замена посимвольная: длина и позиции текста не меняются.
    """

    def fix(m: re.Match[str]) -> str:
        w = m.group(0)
        return w.translate(_LOOKALIKE) if _HAS_CYRILLIC.search(w) or _ONLY_TWINS.fullmatch(w) else w

    return _WORD.sub(fix, text)


def to_cyrillic(text: str) -> str:
    """Все латинские двойники → кириллица: для полей, где латиницы не бывает (стадия «P.П» → «Р.П»)."""
    return text.translate(_LOOKALIKE)


def _canon_kind(raw: str) -> str:
    compact = raw.replace(" ", "").upper()
    return next((k for k in KINDS if k.replace(" ", "").upper() == compact), " ".join(raw.split()))


def split_year(kind: str, body: str) -> tuple[str, int | None]:
    """Номер и год редакции: «1.02-03-2022» → («1.02-03», 2022), «1.02-03» → без года, «3.05.04-85» → 1985."""
    if kind == "СП" and (m := re.fullmatch(rf"({_RU_SP.pattern})\.(\d{{4}})", body)):  # «54.13330.2016»
        return m.group(1), int(m.group(2))
    head, sep, tail = body.rpartition("-")
    if not sep or not tail.isdigit():
        return body, None
    if len(tail) == 4:
        year = int(tail)
        return (head, year) if 1950 <= year <= 2100 else (body, None)
    if len(tail) != 2:
        return body, None
    # двузначный хвост — год у ГОСТ и ВСН, при трёх частях («2.03-01-95») и у старых СНиП («3.05.04-85»);
    # у «1.02-03» это номер документа в группе
    if kind in _SHORT_YEAR_KINDS or "-" in head or head.count(".") >= 2:
        y = int(tail)
        return head, (1900 + y if y >= 30 else 2000 + y)
    return body, None


def _make(kind_raw: str, body_raw: str, text: str) -> NormDesignation:
    kind = _canon_kind(kind_raw)
    body = re.sub(r"\s+", "", body_raw)
    issues: list[str] = []
    if kind in ("ГОСТ", "ГОСТ Р") and (m := re.match(r"^(21)-(\d{3})(?=-|$)", body)):
        body = f"{m.group(1)}.{m.group(2)}" + body[m.end() :]  # «ГОСТ 21-501-2018» — опечатка в ГОСТ 21.501
        issues.append("дефис вместо точки")
    if kind in _KZ_KINDS:
        for rx, issue in _KZ_TYPOS:
            if m := rx.match(body):
                body = f"{m.group(1)}.{m.group(2)}-{m.group(3)}-{m.group(4)}"
                issues.append(issue)
                break
    number, year = split_year(kind, body)
    return NormDesignation(kind, number, year, text, tuple(issues))


def parse_designation(text: str) -> NormDesignation | None:
    """Первое обозначение в тексте или None."""
    found = find_designations(text)
    return found[0][0] if found else None


def _extend(t: str, body: str, end: int) -> tuple[str, int]:
    """Номер, разорванный переносом строки, — склеить: «…-201» + «3»; «1.02» + «-102-2014»."""
    tail = re.search(r"-(\d{1,3})$", body)
    if tail and (m := _YEAR_REST.match(t, end)) and len(tail.group(1)) + len(m.group(1)) == 4:
        return body + m.group(1), m.end()
    if "-" not in body and (m := _DASH_REST.match(t, end)):
        return f"{body}-{m.group(1)}", m.end()
    if not re.search(r"-(?:19|20)\d{2}$", body) and (m := _TAIL_REST.match(t, end)):
        return body + m.group(1), m.end()
    return body, end


def find_designations(text: str) -> list[tuple[NormDesignation, int, int]]:
    """Все обозначения в тексте: (обозначение, начало, конец) — позиции в исходном тексте."""
    t = twins_to_cyrillic(text.translate(_DASHES))
    out = []
    for m in DESIGNATION.finditer(t):
        body, end = m.group("body"), m.end("body")
        while body.endswith(")") and body.count(")") > body.count("("):  # «(по СП РК 2.04-01-2017)» — скобка не номер
            body, end = body[:-1], end - 1
        if m.group("star") and end == m.end("body"):
            end = m.end()  # звёздочка — часть записи: документ с изменениями
        else:
            body, end = _extend(t, body, end)
        out.append((_make(m.group("kind"), body, text[m.start() : end]), m.start(), end))
    return out
