"""Заключения экспертизы РК: подраздел «Дополнения и изменения, внесённые в процессе экспертизы» → пункты.

Письма замечаний экспертизы не публикуются, но их след остаётся в заключении: в разделе 7 перечислено, что
проектировщик изменил и дополнил по замечаниям, — по разделам проекта, пунктами. Заключения приложены к закупкам
строительных работ на goszakup.gov.kz, поэтому корпус «пакет → замечания» для Казахстана собирается без партнёра
(PLAN, шаг 0.11b).

Разбор по тексту страниц: убрать колонтитулы, найти заголовок подраздела и его конец, разбить тело на группы
(разделы проекта) и пункты. Скан без текстового слоя — честное «нет текста»; OCR — позже.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import pdfplumber
from pydantic import BaseModel, Field

from zerocycle.ingest.files import sha256_file
from zerocycle.ingest.pdf import MIN_TEXT_CHARS

MAX_BODY_LINES = 600  # подраздел длиннее — значит, конец не найден: дальше не читаем
MAX_GROUP_CHARS = 90  # заголовок группы — короткая строка: «Раздел ВК. Пояснительная записка»
MAX_INTRO_LINES = 12  # вводная фраза «В процессе рассмотрения … внесены …:» — с адресами бывает в 8 строк


class Remark(BaseModel):
    """Пункт подраздела: что изменено или дополнено по замечанию экспертизы."""

    n: int  # порядковый номер в заключении, с 1
    number: str | None = None  # как пронумерован: «27», «7.2.1», «а»; None — маркер «-» или без номера
    group: str | None = None  # заголовок группы как написан: «Раздел ВК. Пояснительная записка»
    discipline: str | None = None  # раздел проекта по заголовку группы: АР, КР, ОВ, ВК, ЭОМ, СС, ГП, ПОС, СД…
    text: str
    page: int
    category: str | None = None  # категория кодбука (`zerocycle.remarks.classify`)
    rule: str | None = None  # правило кодбука, давшее категорию; «manual» — разметка человеком


Status = Literal["ok", "empty", "no_section", "no_text", "unreadable"]


class Conclusion(BaseModel):
    """Заключение экспертизы и пункты подраздела «Дополнения и изменения»."""

    file: str
    sha256: str
    pages: int
    text_pages: int
    number: str | None = None  # «SE-0085/26»
    issued: date | None = None
    verdict: str | None = None  # «положительное» или «отрицательное»
    object: str | None = None  # наименование объекта из заголовка заключения
    expert_org: str | None = None
    heading: str | None = None  # заголовок подраздела как написан
    status: Status
    remarks: list[Remark] = Field(default_factory=list)


# Раздел проекта по заголовку группы. Порядок важен: «архитектурно-строительные» раньше «архитектурных»,
# «пожарная сигнализация» (СС) раньше «пожарной безопасности», «энергетический паспорт» — не паспорт проекта.
DISCIPLINES: list[tuple[str, re.Pattern[str]]] = [
    (code, re.compile(rx, re.IGNORECASE))
    for code, rx in [
        ("ИРД", r"исходно-разрешит|\bирд\b"),
        ("ИИ", r"изыскани|\bиги\b|\bигди\b|геолог|геодез|топограф"),
        ("ОПЗ", r"общ\w*\s+пояснит|\bопз\b|паспорт\w*\s+(?:рабочего\s+)?проект"),
        ("ГП", r"генеральн\w*\s+план|генплан|\bгп\b|благоустр|озеленен|вертикальн\w*\s+планир"),
        ("АС", r"архитектурно-строит|\bас\b"),
        ("АР", r"архитектур|\bар\b"),
        ("КР", r"конструктив|конструкци|\bкж\d*\b|\bкм\d*\b|\bкр\b|\bкд\b|фундамент"),
        ("ТС", r"теплов\w*\s+сет|теплоснабж|\bн?тс\b"),
        ("ОВ", r"отоплен|вентиляц|кондиционир|\bовик\b|\bов\b|\bитп\b|тепловой\s+пункт|тепломехан"),
        ("ВК", r"водопровод|водоснабж|канализ|водоотвед|\bн?вк\b|\bнв\b|\bнк\b"),
        ("ГС", r"газоснабж|газопровод|\bгсн?\b"),
        (
            "СС",
            r"слаботоч|\bсс\b|\bскс\b|\bвн\b|\bапс\b|\bпс\b|\bос\b|\bсоуэ\b|\bсорс\b|сигнализац|видеонабл|"
            r"связ[иь]|диспетчер|автоматиз",
        ),
        ("ЭОМ", r"электр|\bэом\b|\bэо\b|\bэм\b|\bэсн?\b|\bнэс\b|освещен|молниезащ"),
        ("ТХ", r"технолог|\bтх\b"),
        ("ПБ", r"пожарн\w*\s+безопасн|пожаротуш|\bпб\b|\bапт\b"),
        ("ИТМ", r"гражданск\w*\s+оборон|чрезвычайн|\bитм\b|\bго\s*(?:и\s*)?чс\b"),
        ("ООС", r"окружающ\w*\s+сред|\bоос\b|\bовос\b|\bроос\b|эколог"),
        ("ЭЭ", r"энергетическ\w*\s+паспорт|энергосбер|энергоэффект"),
        ("СЭ", r"санитарн|эпидемиолог"),
        ("ПОС", r"организац\w*\s+строит|\bпос\b|\bппр\b"),
        ("СД", r"смет|\bсд\b"),
        ("АД", r"автомобильн\w*\s+дорог|\bад\b|дорожн"),
    ]
]


def discipline_of(text: str | None) -> str | None:
    """Раздел проекта по заголовку группы: «Раздел ВК. Пояснительная записка» → ВК."""
    if not text:
        return None
    return next((code for code, rx in DISCIPLINES if rx.search(text)), None)


@dataclass(frozen=True)
class Line:
    page: int  # с 1
    text: str


_NOISE = re.compile(
    r"^(?:\d{1,3}|-\s*\d{1,3}\s*-|Страница\s+\d+\s+из\s+\d+)$|сформирован\w*\s+информационн\w+\s+систем",
    re.IGNORECASE,
)


def lines_of(pages: list[str]) -> list[Line]:
    """Строки текста без колонтитулов и номеров страниц.

    Колонтитул — строка, которая повторяется на трёх страницах и больше (и не реже чем на трети страниц):
    «Заключение № … от … по рабочему проекту», строки наименования объекта под ним.
    """
    split = [[" ".join(s.split()) for s in (text or "").splitlines()] for text in pages]
    seen = Counter(s for page in split for s in set(page) if s)
    limit = max(3, len(pages) // 3)
    return [
        Line(no, s)
        for no, page in enumerate(split, start=1)
        for s in page
        if s and seen[s] < limit and not _NOISE.search(s)
    ]


def _norm(s: str) -> str:
    return s.lower().replace("ё", "е")


# «7.1 Дополнения и изменения, внесённые в рабочий проект в процессе проведения экспертизы»,
# «7.2. Изменения и дополнения, внесенные в рабочий проект в процессе экспертизы.»,
# «7.1. Дополнения по исходно-разрешительным документам и изменения, внесенные в рабочий проект …»
_HEAD = re.compile(
    r"(?P<num>\d+(?:\.\d+)*)?\.?\s*(?:дополнени|изменени)\w*\b[^:]{0,140}?внесенн\w*[^:]{0,120}?"
    r"в\s+процессе(?:\s+(?:\S+\s+){0,3}?экспертиз\w*)?\.?"
)
_INTRO = re.compile(r"внесены\s+следующие\s+(?:изменения|дополнения)")
_NONE = re.compile(r"(?:изменени|дополнени)\w*[^.]{0,120}?не\s+(?:вносил|внесен|требовал)")
_INTRO_START = re.compile(r"^(?:в\s+процессе|в\s+ходе|по\s+замечани|по\s+результатам)|рассмотрени|внесены\s+следующ")
_INTRO_END = re.compile(r":$|(?:изменени|дополнени)\w*\s*\.$")
_END_PHRASE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s*)?(?:экспертн\w+\s+оценк|оценк\w*\s+(?:\S+\s+){0,2}решени|выводы?\b|общие\s+выводы|"
    r"рекомендаци|с\s+учетом\s+внесенн|после\s+внесения\s+изменени|основные\s+(?:технико\s*-\s*)?экономическ)"
)
_END_UNNUMBERED = re.compile(r"^(?:примечани[ея]\b|таблица\s*(?:№\s*)?\d)")
_LEAD = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?\.?\s*")
# «1. Представлен …», «1) выполнены …» (после скобки — и со строчной), «4.1 Площадь …», «а) …», «- …»
_ITEM = re.compile(
    r"^(?:(?P<num>\d{1,3}(?:\.\d{1,3}){0,3})\s*(?:\.\s*(?=[А-ЯЁA-Z«\"(])|\)\s*)"
    r"|(?P<num2>\d{1,3}(?:\.\d{1,3}){1,3})\s+(?=[А-ЯЁA-Z«\"(])"
    r"|(?P<let>[а-яa-z])\)\s*"
    r"|(?P<dash>[-–—•·▪●*])\s*)"
    r"(?P<rest>[^\W\d_].*|[«\"(].*)$"
)
_GROUP_HINT = re.compile(r"^(?:раздел|подраздел|по\s|часть|том\b|книга|альбом|марка)", re.IGNORECASE)
# в заголовке группы нет действия: «Генеральный план.» — заголовок, «Строй генплан предоставлен» — пункт
_CHANGE_VERB = re.compile(
    r"\b(?:представлен|предоставлен|откорректирован|исправлен|выполнен|добавлен|дополнен|указан|приведен|"
    r"предусмотрен|разработан|внесен|учтен|заменен|исключен|принят|уточнен|согласован|оформлен|переработан|"
    r"доработан|обновил|добавил|представил|предоставил|откорректировал|указал|выполнил|разработал|дополнил|"
    r"исправил|привел|заменил|исключил)\w*"
)
_INLINE_SECTION = re.compile(r"^(?:Раздел|Подраздел|Марка|Комплект)\s+([^:]{1,40}):\s*")
_TERMINAL = (".", ";", ":", "!", "?", "»", '"', ")")
_ABBREV = re.compile(r"(?:^|[\s(])(?:п|пп|табл|рис|стр|ст|см|т|г|гг|ул|д|прил|разд|прим|ч|им|кв|мкр)\.$", re.IGNORECASE)
PARAGRAPH_END = 0.75  # строка короче 3/4 полной — последняя строка абзаца


@dataclass(frozen=True)
class _Head:
    body: int  # индекс первой строки тела в lines
    number: str | None  # номер заголовка: «7.1»
    text: str


def _headings(lines: list[Line]) -> list[_Head]:
    found = []
    for i in range(len(lines)):
        window = [ln.text for ln in lines[i : i + 3]]
        joined = " ".join(window)
        m = _HEAD.match(_norm(joined))
        if not m:
            continue
        used, pos = 0, 0
        for k, t in enumerate(window):  # сколько строк занял заголовок
            if pos < m.end():
                used = k + 1
            pos += len(t) + 1
        found.append(_Head(i + used, m.group("num"), joined[: m.end()].strip()))
    if not found:  # заголовка нет — начало по вводной фразе «внесены следующие изменения и дополнения:»
        found = [_Head(i, None, "") for i, ln in enumerate(lines) if _INTRO.search(_norm(ln.text))]
    return found


def _is_end(text: str, head_number: str | None, last_top: int | None) -> bool:
    t = _norm(text)
    if _END_PHRASE.match(t):
        return True
    m = _LEAD.match(t)
    if not m:
        return bool(_END_UNNUMBERED.match(t))
    top, sub, sub2 = m.groups()
    if head_number and "." in head_number:
        h_top, h_sub = head_number.split(".")[:2]
        if top == h_top and sub is not None and sub2 is None and sub != h_sub:
            return True  # следующий подраздел: 7.1 → 7.2
    rest = text[m.end() :]
    letters = [c for c in rest if c.isalpha()]
    caps = len(letters) >= 5 and all(c.isupper() for c in letters)
    # «8. ВЫВОДЫ:» — следующая глава; «8. АРХИТЕКТУРНЫЕ РЕШЕНИЯ» — заголовок группы, «8. Добавлен …» — пункт
    return sub is None and caps and discipline_of(rest) is None and (last_top is None or int(top) != last_top + 1)


def _is_group(text: str, next_text: str | None) -> bool:
    """Заголовок группы: «Раздел ВК. Пояснительная записка», «Отопление и вентиляция», «Общие замечания»."""
    if len(text) > MAX_GROUP_CHARS or not text[:1].isupper() or text.endswith((";", ",")):
        return False
    if _ITEM.match(text) and not _GROUP_HINT.match(text):
        return False
    if text.endswith(":"):
        return True
    if _CHANGE_VERB.search(_norm(text)):
        return False
    words = len(text.split())
    next_is_item = next_text is not None and bool(_ITEM.match(next_text))
    next_upper = next_text is not None and next_text[:1].isupper()
    hint, discipline = _GROUP_HINT.match(text) is not None, discipline_of(text) is not None
    if text.endswith("."):  # «Генеральный план.», «По сметной части.»
        return words <= 4 and (next_is_item or hint or (discipline and next_upper))
    if hint:
        return next_is_item or words <= 8
    if discipline and words <= 6:
        return next_is_item or next_upper
    return words <= 5 and next_is_item


def _last_number(number: str | None) -> int | None:
    if number is None or not number.replace(".", "").isdigit():
        return None
    return int(number.rsplit(".", 1)[-1])


@dataclass
class _Item:
    number: str | None
    kind: Literal["num", "let", "dash", "text"]
    group: str | None
    text: str
    page: int


def _parse_body(lines: list[Line], head: _Head) -> list[_Item]:
    body: list[Line] = []
    last_top: int | None = None
    for ln in lines[head.body : head.body + MAX_BODY_LINES]:
        if _is_end(ln.text, head.number, last_top):
            break
        m = _ITEM.match(ln.text)
        if m and (m.group("num") or m.group("num2")):
            last_top = _last_number(m.group("num") or m.group("num2"))
        body.append(ln)

    start = _skip_intro(body)
    blocks: list[tuple[str | None, list[Line]]] = [(None, [])]  # группы: заголовок и его строки
    for k in range(start, len(body)):
        nxt = body[k + 1].text if k + 1 < len(body) else None
        if _is_group(body[k].text, nxt):
            blocks.append((body[k].text.rstrip(":.").strip(), []))
        else:
            blocks[-1][1].append(body[k])
    widths = sorted(len(ln.text) for ln in body[start:])
    full = widths[int(0.8 * (len(widths) - 1))] if widths else 0  # длина полной строки абзаца
    items = [it for group, block in blocks for it in _parse_block(block, group, full)]
    return _lift_parents(items)


def _skip_intro(body: list[Line]) -> int:
    """Вводная фраза «В процессе рассмотрения … внесены следующие изменения и дополнения:» — бывает и дважды."""
    start = 0
    while start < len(body) and _INTRO_START.search(_norm(body[start].text)) and not _ITEM.match(body[start].text):
        end = next(
            (k for k in range(start, min(start + MAX_INTRO_LINES, len(body))) if _INTRO_END.search(body[k].text)),
            None,
        )
        if end is None:
            break
        start = end + 1
    return start


def _parse_block(block: list[Line], group: str | None, full: int) -> list[_Item]:
    numbered = any((m := _ITEM.match(ln.text)) and (m.group("num") or m.group("num2")) for ln in block)
    items: list[_Item] = []
    current: _Item | None = None
    prev = ""
    for ln in block:
        m = _ITEM.match(ln.text)
        if m:
            number = m.group("num") or m.group("num2") or m.group("let")
            kind = "num" if (m.group("num") or m.group("num2")) else "let" if m.group("let") else "dash"
            last = _last_number(current.number) if current is not None and current.kind == "num" else None
            seq = last is not None and _last_number(number) == last + 1
            # «…таблицей 4 п.» + «2.4.1 СН РК …» — продолжение, а не пункт: новый пункт — после конца фразы
            # (не сокращения) или по порядку; номер без точки («4.1 Площадь») — только по порядку
            ends = prev.endswith(_TERMINAL) and not _ABBREV.search(prev)
            if current is None or seq or (ends and not m.group("num2")):
                current = _Item(number if kind != "dash" else None, kind, group, m.group("rest"), ln.page)
                items.append(current)
                prev = ln.text
                continue
        # без нумерации пункт начинается с прописной после конца предложения или после короткой строки абзаца
        paragraph_end = prev.endswith((".", ";", "!")) or len(prev) < PARAGRAPH_END * full
        if current is None or (not numbered and paragraph_end and ln.text[:1].isupper()):
            current = _Item(None, "text", group, ln.text, ln.page)
            items.append(current)
        else:
            joiner = "" if current.text.endswith("-") and not current.text.endswith(" -") else " "
            current.text = f"{current.text}{joiner}{ln.text}"
        prev = ln.text
    return items


def _lift_parents(items: list[_Item]) -> list[_Item]:
    """«1. Внесены изменения в раздел АР:» с подпунктами «а) …», «- …» — это заголовок группы, а не пункт."""
    out: list[_Item] = []
    for i, it in enumerate(items):
        nxt_item = items[i + 1] if i + 1 < len(items) else None
        if it.text.endswith(":") and nxt_item is not None and nxt_item.kind in ("let", "dash"):
            label = it.text.rstrip(":").strip()
            for child in items[i + 1 :]:
                if child.kind not in ("let", "dash"):
                    break
                child.group = label if discipline_of(label) or not child.group else child.group
            continue
        out.append(it)
    return out


_TITLE = re.compile(
    r"ЗАКЛЮЧЕНИЕ\s*\(\s*(?P<verdict>\w+)\s*\)\s*№\s*(?P<number>\S+)\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})\s*г?\.?"
    r"\s*(?P<rest>.{0,400}?)\s*(?:ЗАКАЗЧИК|ГЕНПРОЕКТИРОВЩИК|ГЕНЕРАЛЬНЫЙ\s+ПРОЕКТИРОВЩИК)",
)
_RUNNING = re.compile(r"Заключени[ея]\s*№\s*(?P<number>\S+)\s+от\s+(?P<date>\d{2}\.\d{2}\.\d{4})")
_ORG_KK = re.compile(r"[\"«“]([^\"«»“”]{2,80})[\"»”]\s*(?:ЖШС|[Жж]ауапкершілігі\s+шектеулі|АҚ\b|[Аа]кционерлік)")
_ORG_RU = re.compile(
    r"(?:ТОО|АО|[Тт]оварищество\s+с\s+ограниченной\s+ответственностью)\s*[\"«“]([^\"«»“”]{2,80})[\"»”]"
)


def _date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%d.%m.%Y").date()
    except ValueError:
        return None


def _object(rest: str) -> str | None:
    """«по рабочему проекту «Строительство …» (привязка)» → «Строительство …»."""
    a = min((i for i in (rest.find("«"), rest.find('"')) if i >= 0), default=-1)
    b = max(rest.rfind("»"), rest.rfind('"'))
    return rest[a + 1 : b].strip() or None if 0 <= a < b else None


def _meta(pages: list[str], c: dict) -> None:
    head = " ".join(" ".join(p.split()) for p in pages[:6])
    if m := _TITLE.search(head):
        verdict = m.group("verdict").lower()
        c["verdict"] = (
            "положительное" if verdict.startswith("полож") else "отрицательное" if "отриц" in verdict else None
        )
        c["number"], c["issued"], c["object"] = m.group("number"), _date(m.group("date")), _object(m.group("rest"))
    elif m := _RUNNING.search(head):
        c["number"], c["issued"] = m.group("number"), _date(m.group("date"))
    for marker, rx in (("АЛҒЫ СӨЗ", _ORG_KK), ("ПРЕДИСЛОВИЕ", _ORG_RU)):
        at = head.find(marker)
        if at >= 0 and (m := rx.search(head, at, at + 900)):
            c["expert_org"] = " ".join(m.group(1).split())
            break
    else:
        if "Госэкспертиза" in head:  # РГП «Госэкспертиза»: имя — в колонтитуле, предисловия нет
            c["expert_org"] = "Госэкспертиза"


def parse_conclusion(pages: list[str], text_pages: int, file: str, sha256: str) -> Conclusion:
    """Заключение по тексту страниц (как отдаёт pdfplumber) → метаданные и пункты «Дополнений и изменений»."""
    c: dict = {"file": file, "sha256": sha256, "pages": len(pages), "text_pages": text_pages}
    if text_pages == 0:
        return Conclusion(**c, status="no_text")
    _meta(pages, c)
    lines = lines_of(pages)
    heads = _headings(lines)
    if not heads:
        # «Изменения и дополнения в процессе экспертизы не вносились» — подраздел есть, пунктов нет
        none = next((ln.text for ln in lines if _NONE.search(_norm(ln.text))), None)
        return Conclusion(**c, heading=none, status="empty" if none else "no_section")
    parsed = [(h, _parse_body(lines, h)) for h in heads]
    head, items = max(parsed, key=lambda hi: (len(hi[1]), hi[0].body))  # оглавление — раньше и без пунктов
    if not c.get("expert_org"):
        intro = " ".join(ln.text for ln in lines[head.body : head.body + MAX_INTRO_LINES])
        if m := _ORG_RU.search(intro):
            c["expert_org"] = " ".join(m.group(1).split())
    remarks = []
    for i, it in enumerate(items, start=1):
        inline = _INLINE_SECTION.match(it.text)  # «Раздел КЖ: Дополнены …» — раздел точнее группы
        discipline = (discipline_of(inline.group(1)) if inline else None) or discipline_of(it.group)
        remarks.append(
            Remark(n=i, number=it.number, group=it.group, discipline=discipline, text=it.text.strip(), page=it.page)
        )
    return Conclusion(**c, heading=head.text or None, status="ok" if remarks else "empty", remarks=remarks)


def read_pages(path: Path) -> tuple[list[str], int]:
    """Текст страниц PDF и число страниц с текстовым слоем."""
    texts, with_text = [], 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
            with_text += len(page.chars) >= MIN_TEXT_CHARS
            page.close()
    return texts, with_text


def read_conclusion(path: Path, name: str | None = None) -> Conclusion:
    pages, text_pages = read_pages(path)
    return parse_conclusion(pages, text_pages, name or path.name, sha256_file(path))
