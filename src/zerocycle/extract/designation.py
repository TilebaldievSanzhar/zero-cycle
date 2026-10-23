"""Обозначения документов по ГОСТ Р 21.101-2020: разбор, место в составе ПД, вложенность.

- Раздел ПД — базовое обозначение организации, дефис и шифр раздела: «2345-ПЗ» (п. 4.1.2).
- Часть раздела — номер после шифра («2345-ПЗУ1»), книга части — через точку («2345-ПЗУ1.2»); подраздел —
  номер подраздела («2345-ИОС4»), его часть и книга — через точку («2345-ИОС4.1.2») (п. 4.1.3).
- Содержание тома — «-С» (п. 8.1.5); состав ПД — базовое обозначение и «-СП» (п. 8.1.6).
- Рабочая документация — базовое обозначение и марка («2345-12-АР»); документ комплекта — через точку
  («2345-12-ЭО.1») (п. 4.2.4–4.2.5).

Базовое обозначение организация задаёт сама — номер договора, код объекта, через дефисы и точки, —
поэтому шифр ищем справа налево по справочнику юрисдикции, а не по позиции в строке. Разбор нейтральный:
что означают номера после шифра, решает `interpret` по стадии и виду объекта.
"""

from __future__ import annotations

import re

from zerocycle.model import Designation, SectionRef
from zerocycle.packs import Codebook, codebook

_DASHES = str.maketrans(dict.fromkeys("‐‑‒–—―−", "-"))
_LAT2CYR = str.maketrans("ABCEHKMOPTXY", "АВСЕНКМОРТХУ")
_LATIN = re.compile(r"[A-Z]")
_CYRILLIC = re.compile(r"[А-ЯЁ]")
_TOKEN = re.compile(
    r"^(?P<letters>[А-ЯЁA-Z]+)"  # шифр раздела или марка
    r"(?P<nums>\d+(?:\.\d+)*)?"  # подраздел, часть, книга / комплект и документ
    r"(?:\.(?P<dot>\d+))?"  # документ через точку сразу после шифра: ЭО.1
    r"(?:\.(?P<tail>[А-ЯЁA-Z]+\d*))?$"  # хвост организации: АР.ГЧ
)


def _clean(raw: str) -> str:
    """Тире → дефис, пробелы у дефисов убрать, остальные пробелы разделяют части: «39-2024 ОВ» → «39-2024-ОВ»."""
    t = re.sub(r"\s*-\s*", "-", raw.translate(_DASHES).upper().strip())
    return re.sub(r"\s+", "-", t).strip("-.")


def _mixed(token: str) -> bool:
    return bool(_LATIN.search(token)) and bool(_CYRILLIC.search(token))


def _match_known(token: str, known: frozenset[str]) -> tuple[re.Match[str] | None, str | None]:
    """Совпадение токена с шифром из справочника; вторым — замечание, если шифр пришлось поправить.

    Классика опечаток — цифра вместо похожей буквы: «П3» вместо «ПЗ», «П0С» вместо «ПОС».
    """
    m = _TOKEN.match(token)
    if m and m.group("letters").translate(_LAT2CYR) in known:
        return m, None
    # правим только начало токена, номера после шифра не трогаем: «П0С3» → «ПОС» и часть 3
    for k in range(len(token), 1, -1):
        head = token[:k].replace("3", "З").replace("0", "О")
        if head != token[:k] and head.translate(_LAT2CYR) in known:
            m = _TOKEN.match(head + token[k:])
            if m and m.group("letters") == head:
                return m, "цифра вместо буквы в шифре"
    return None, None


def parse(raw: str, cb: Codebook | None = None) -> Designation | None:
    """«2345-ИОС4.1.2-С» → Designation; None — строка не похожа на обозначение."""
    cb = cb or codebook()
    text = _clean(raw)
    if not text:
        return None
    tokens = text.split("-")
    issues: list[str] = []
    if raw.translate(_DASHES) != raw:
        issues.append("вместо дефиса — тире")
    if re.search(r"[^\s-]\s+[^\s-]", raw.translate(_DASHES).strip()):
        issues.append("пробел вместо дефиса")
    if re.match(r"\s*[№N]\s*\d", raw):
        issues.append("знак № в обозначении")
        text = re.sub(r"^[№N]-?", "", text)
        tokens = text.split("-")

    suffix = None
    last = tokens[-1].translate(_LAT2CYR)
    if len(tokens) > 1 and last in ("С", "СП"):
        suffix = last
        tokens = tokens[:-1]

    # шифр — самый правый токен, чьи буквы есть в справочнике; иначе самый правый буквенный токен
    known = cb.all_codes
    found: tuple[int, re.Match[str]] | None = None
    fallback: tuple[int, re.Match[str]] | None = None
    for i in range(len(tokens) - 1, -1, -1):
        m, fix = _match_known(tokens[i], known)
        if m:
            found = (i, m)
            if fix:
                issues.append(fix)
            break
        m = _TOKEN.match(tokens[i])
        if m and fallback is None and i > 0 and m.group("letters").translate(_LAT2CYR) not in cb.tails:
            fallback = (i, m)

    if found is None and suffix == "СП":
        base = "-".join(tokens)
        if any(_mixed(t) for t in tokens):
            issues.append("в базовом обозначении смешаны латиница и кириллица")
        return Designation(raw=raw, base=base, suffix="СП", issues=tuple(issues)) if base else None

    if found is None:
        # незнакомый шифр принимаем, только если перед ним похожее на код базовое обозначение — с цифрами;
        # иначе «Пояснительная записка» стала бы обозначением
        if fallback is None or not any(re.search(r"\d", t) for t in tokens[: fallback[0]]):
            return None
        found = fallback
        issues.append("шифр не из справочника юрисдикции")

    i, m = found
    letters_raw = m.group("letters")
    if _LATIN.search(letters_raw):
        issues.append("латиница в шифре" if not _mixed(letters_raw) else "в шифре смешаны латиница и кириллица")
    tail = m.group("tail").translate(_LAT2CYR) if m.group("tail") else None
    rest = tokens[i + 1 :]
    if len(rest) == 1 and tail is None and rest[0].translate(_LAT2CYR) in cb.tails:
        tail = rest[0].translate(_LAT2CYR)  # «2345-АР-ТЧ» — хвост организации через дефис
    elif rest:
        issues.append("после шифра лишние части: " + "-".join(rest))
    base_tokens = tokens[:i]
    if not base_tokens:
        issues.append("нет базового обозначения")
    elif not any(re.search(r"\d", t) for t in base_tokens):
        if any(len(re.sub(r"[^А-ЯЁA-Z]", "", t)) >= 6 for t in base_tokens):
            return None  # «Заказчик: ГУ … ВК» — проза с шифром в конце, а не обозначение
        issues.append("в базовом обозначении нет цифр")
    if any(_mixed(t) for t in base_tokens):
        issues.append("в базовом обозначении смешаны латиница и кириллица")

    nums = tuple(int(n) for n in m.group("nums").split(".")) if m.group("nums") else ()
    return Designation(
        raw=raw,
        base="-".join(base_tokens),
        code=letters_raw.translate(_LAT2CYR),
        numbers=nums,
        doc_number=int(m.group("dot")) if m.group("dot") else None,
        tail=tail,
        suffix=suffix,
        issues=tuple(issues),
    )


def interpret(d: Designation, cb: Codebook | None = None, linear: bool = False) -> SectionRef | None:
    """Место документа стадии «П» в составе ПД: раздел по ПП 87, подраздел, часть, книга."""
    cb = cb or codebook()
    if d.code is None:
        return None
    sec = cb.section(d.code, linear)
    if sec is None:
        return SectionRef(code=d.code)
    n = d.numbers
    if sec.subsections:  # раздел 5: ИОС<подраздел>.<часть>.<книга>
        return SectionRef(
            number=sec.number,
            code=d.code,
            subsection=n[0] if n else None,
            part=n[1] if len(n) > 1 else None,
            book=n[2] if len(n) > 2 else None,
        )
    return SectionRef(
        number=sec.number,
        code=d.code,
        part=n[0] if n else None,
        book=n[1] if len(n) > 1 else None,
    )


def belongs_to(child: Designation, parent: Designation) -> bool:
    """Документ входит в том или раздел: «2345-ИОС4.1.2» ⊂ «2345-ИОС4»; «2345-АР.ГЧ» ⊂ «2345-АР»."""
    if child.base != parent.base or child.code != parent.code or parent.code is None:
        return False
    if child.numbers[: len(parent.numbers)] != parent.numbers:
        return False
    if parent.doc_number is not None and child.doc_number != parent.doc_number:
        return False
    return parent.tail is None or child.tail == parent.tail
