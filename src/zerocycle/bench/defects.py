"""Подсаживаемые дефекты засеянного корпуса и их эталон (docs/bench/PROTOCOL.md).

Каталог зафиксирован до правил проверки: D-коды — из bim-data/taxonomies/remark_and_defect_taxonomy.json,
F-коды — формальные дефекты ГОСТ Р 21.101-2020 и ПДЭ. Каждый дефект портит описание пакета до рисования
и возвращает запись эталона: семейство, файл, графа, ожидаемое и фактическое значение. Страница
проставляется при рисовании по метке листа. Один том — не больше одного дефекта, чтобы эталон был однозначным.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable

from zerocycle.bench import content as C
from zerocycle.bench.generator import PackageSpec, SheetSpec, TomSpec

Inject = Callable[[PackageSpec, random.Random, set[str]], dict | None]


def _free(spec: PackageSpec, used: set[str], pred: Callable[[TomSpec], bool] = lambda t: True) -> list[TomSpec]:
    return [t for t in spec.toms if t.present and t.designation not in used and pred(t)]


def _sheets(tom: TomSpec, kind: str) -> list[SheetSpec]:
    return [s for s in tom.sheets if s.kind == kind and s.keep]


def _swap_last_digits(s: str) -> str | None:
    """«2026-017» → «2026-071»: классическая опечатка в базовом обозначении."""
    m = re.search(r"(\d)(\d)(\D*)$", s)
    if not m or m.group(1) == m.group(2):
        return None
    return s[: m.start()] + m.group(2) + m.group(1) + m.group(3)


def _tag(sheet: SheetSpec, code: str) -> str:
    tag = f"{code}-{len(sheet.tags)}"
    sheet.tags.append(tag)
    return tag


def _rec(defect: str, tom: TomSpec | None, **kw) -> dict:
    family, title = CATALOG[defect][:2]
    rec = {"id": defect, "family": family, "title": title}
    if tom is not None:
        rec["file"] = tom.path
        rec["designation"] = tom.designation
    rec.update(kw)
    return rec


def _d01(spec, rng, used):
    """ТЭП в томе АР или ПЗУ не совпадает с ПЗ: переставлены цифры или изменено значение."""
    cands = [t for t in _free(spec, used, lambda t: t.code in ("АР", "ПЗУ"))]
    for tom in rng.sample(cands, len(cands)):
        for sh in _sheets(tom, "text"):
            for b in sh.blocks:
                if b[0] == "table" and b[4] == "tep":
                    row = rng.choice([r for r in b[3] if r[0] in ("Площадь застройки", "Общая площадь здания")])
                    expected = row[1]
                    digits = re.sub(r"\D", "", expected)
                    i = rng.randrange(len(digits) - 2)
                    swapped = digits[:i] + digits[i + 1] + digits[i] + digits[i + 2 :]
                    if swapped == digits:
                        swapped = digits[:i] + str((int(digits[i]) + 1) % 10) + digits[i + 1 :]
                    it = iter(swapped)
                    row[1] = re.sub(r"\d", lambda _m, it=it: next(it), expected)
                    used.add(tom.designation)
                    pz = spec.tom("ПЗ")
                    return _rec(
                        "D01",
                        tom,
                        tag=_tag(sh, "D01"),
                        key=row[0],
                        expected=expected,
                        actual=row[1],
                        reference=pz.path if pz else None,
                    )
    return None


def _d02(spec, rng, used):
    """Лист графической части выброшен, а в ведомости, в графе «Листов» и в нумерации он остался."""
    cands = _free(spec, used, lambda t: len(_sheets(t, "drawing")) >= 3)
    if not cands:
        return None
    tom = rng.choice(cands)
    drawings = _sheets(tom, "drawing")
    k = rng.randrange(1, len(drawings) - 1)
    drawings[k].keep = False
    used.add(tom.designation)
    return _rec(
        "D02",
        tom,
        tag=_tag(drawings[0], "D02"),
        missing_sheet=int(drawings[k].graphs[7]),  # при сквозной нумерации тома — не k + 1
        sheets_declared=len(drawings),
        missing_name=drawings[k].drawing,
    )


def _d03(spec, rng, used):
    """В графе 1 одного листа — чужое обозначение: переставлены цифры, чужой шифр или нет хвоста документа."""
    cands = _free(spec, used, lambda t: bool(_sheets(t, "text")))
    if not cands:
        return None
    tom = rng.choice(cands)
    sh = rng.choice(_sheets(tom, "text") + _sheets(tom, "drawing"))
    expected = sh.graphs[1]
    variants = []
    if swapped := _swap_last_digits(spec.base):
        variants.append(expected.replace(spec.base, swapped, 1))
    other = rng.choice([c for c in ("АР", "КР", "ПБ", "ООС") if c != tom.code])
    variants.append(expected.replace(f"-{tom.code}", f"-{other}", 1))
    if not expected.endswith((".ТЧ", ".ГЧ")):
        variants.append(expected + "1")
    elif len(_sheets(tom, sh.kind)) > 1:
        # хвост — практика организации, не ГОСТ: снятый хвост виден, только когда рядом есть другие листы части
        variants.append(expected.rsplit(".", 1)[0])
    actual = rng.choice([v for v in variants if v != expected])
    sh.graphs[1] = actual
    used.add(tom.designation)
    return _rec("D03", tom, tag=_tag(sh, "D03"), graph=1, expected=expected, actual=actual)


def _d07(spec, rng, used):
    """В перечне норм — устаревшая редакция вместо действующей (по реестру «Стройкомплекс.РФ»)."""
    swaps = {new: old for old, new in C.OUTDATED.items()}
    cands = []
    for tom in _free(spec, used):
        for sh in _sheets(tom, "text"):
            for b in sh.blocks:
                if b[0] == "table" and b[4] == "norms":
                    cands += [(tom, sh, row) for row in b[3] if row[1] in swaps]
    if cands:
        tom, sh, row = rng.choice(cands)
        expected, row[1] = row[1], swaps[row[1]]
        used.add(tom.designation)
        return _rec("D07", tom, tag=_tag(sh, "D07"), expected=expected, actual=row[1], detectable_by="registry")
    return None


def _d07_off_registry(spec, rng, used):
    """Устаревший добровольный стандарт: реестр обязательных требований его не покажет."""
    tom = spec.tom("ПЗ")
    if tom is None or tom.designation in used:
        return None
    for sh in _sheets(tom, "text"):
        for b in sh.blocks:
            if b[0] == "table" and b[4] == "norms":
                for row in b[3]:
                    if row[1] == "ГОСТ Р 21.101-2020":
                        row[1] = "ГОСТ Р 21.1101-2013"
                        used.add(tom.designation)
                        return _rec(
                            "D07R",
                            tom,
                            tag=_tag(sh, "D07R"),
                            expected="ГОСТ Р 21.101-2020",
                            actual=row[1],
                            detectable_by="curated_replacements",
                        )
    return None


def _d09(spec, rng, used):
    """Обязательного раздела нет ни в пакете, ни в «Составе ПД»."""
    cands = _free(spec, used, lambda t: t.code in ("ОДИ", "ТБЭ", "ООС", "ПБ"))
    if not cands:
        return None
    tom = rng.choice(cands)
    tom.present = False
    tom.in_composition = False
    used.add(tom.designation)
    return _rec("D09", None, section=tom.section, code=tom.code, designation=tom.designation, title=tom.title)


def _d10(spec, rng, used):
    """На первом листе документа нет фамилии нормоконтролёра или даты разработчика."""
    cands = _free(spec, used)
    if not cands:
        return None
    tom = rng.choice(cands)
    firsts = [s for s in tom.sheets if s.keep and s.form in (3, 5) and s.signatures]
    sh = rng.choice(firsts)
    # строка нормоконтроля — предпоследняя (bench.generator._signs): подпись роли у шаблонов разная
    i, field_ = rng.choice([(len(sh.signatures) - 2, "name"), (0, "date")])
    role, name, date = sh.signatures[i]
    sh.signatures[i] = (role, "", date) if field_ == "name" else (role, name, "")
    used.add(tom.designation)
    return _rec("D10", tom, tag=_tag(sh, "D10"), role=role, field=field_)


def _f01(spec, rng, used):
    """Пропуск в нумерации листов документа: текстовой или графической части тома, где листов три и больше.

    Текстовые части в генераторе короткие (один-два листа), поэтому дефект чаще ложится на графическую часть.
    """

    def parts(t: TomSpec) -> list[list[SheetSpec]]:
        return [p for p in (_sheets(t, "text"), _sheets(t, "drawing")) if len(p) >= 3]

    cands = _free(spec, used, lambda t: bool(parts(t)))
    if not cands:
        return None
    tom = rng.choice(cands)
    sheets = rng.choice(parts(tom))
    k = rng.randrange(1, len(sheets))
    for sh in sheets[k:]:
        sh.graphs[7] = str(int(sh.graphs[7]) + 1)
    used.add(tom.designation)
    return _rec("F01", tom, tag=_tag(sheets[k], "F01"), after_sheet=k, jumps_to=int(sheets[k].graphs[7]))


def _f02(spec, rng, used):
    """В графе 8 первого листа — не то число листов."""
    cands = _free(spec, used, lambda t: len(_sheets(t, "text")) >= 2)
    if not cands:
        return None
    tom = rng.choice(cands)
    sh = _sheets(tom, "text")[0]
    expected = sh.graphs[8]
    sh.graphs[8] = str(int(expected) + rng.choice([-1, 1, 2]))
    used.add(tom.designation)
    return _rec("F02", tom, tag=_tag(sh, "F02"), graph=8, expected=expected, actual=sh.graphs[8])


def _f03(spec, rng, used):
    """Стадия «Р» на листе проектной документации."""
    cands = _free(spec, used)
    if not cands:
        return None
    tom = rng.choice(cands)
    sh = rng.choice([s for s in tom.sheets if s.keep and 6 in s.graphs])
    sh.graphs[6] = "Р"
    used.add(tom.designation)
    return _rec("F03", tom, tag=_tag(sh, "F03"), graph=6, expected="П", actual="Р")


def _f04(spec, rng, used):
    """Наименование здания (графа 3) в одном томе отличается от остальных."""
    cands = _free(spec, used, lambda t: bool(_sheets(t, "drawing")))
    if not cands:
        return None
    tom = rng.choice(cands)
    drawings = _sheets(tom, "drawing")
    expected = drawings[0].graphs[3]
    actual = re.sub(r"№ (\d)", lambda m: f"№ {int(m.group(1)) % 9 + 1}", expected)
    if actual == expected:
        actual = expected.split(" со ")[0].split(" с ")[0] + " (корп. 1)"
    for sh in drawings:
        sh.graphs[3] = actual
    used.add(tom.designation)
    return _rec("F04", tom, tag=_tag(drawings[0], "F04"), graph=3, expected=expected, actual=actual)


def _f05(spec, rng, used):
    """Том есть в пакете, но не указан в «Составе ПД»."""
    cands = _free(spec, used)
    if not cands:
        return None
    tom = rng.choice(cands)
    tom.in_composition = False
    used.add(tom.designation)
    return _rec("F05", tom)


def _f06(spec, rng, used):
    """Имя файла не соответствует обозначению (ГОСТ Р 21.101-2020, п. 8.2.6)."""
    cands = _free(spec, used)
    if not cands:
        return None
    tom = rng.choice(cands)
    expected = tom.path
    tom.file_name = f"{tom.code}{''.join(map(str, tom.numbers))}_{rng.choice(['финал', 'изм1', 'v2', 'новый'])}.pdf"
    used.add(tom.designation)
    return _rec("F06", tom, expected=expected)


def _f07(spec, rng, used):
    """Два документа в info.xml с одним GUID (п. 8.2.4)."""
    cands = _free(spec, used)
    if len(cands) < 2:
        return None
    a, b = rng.sample(cands, 2)
    b.guid = a.guid
    used.update({a.designation, b.designation})
    return _rec("F07", None, file="info.xml", guid=a.guid, designations=[a.designation, b.designation])


def _f08(spec, rng, used):
    """Формат в графе 26 не совпадает с размером листа."""
    cands = [
        (t, s)
        for t in _free(spec, used)
        for s in _sheets(t, "drawing")
        if s.paper == "A3" and not (s.page or s.rotate or s.sideways)  # лист 1:1 без поворота
    ]
    if not cands:
        return None
    tom, sh = rng.choice(cands)
    sh.paper = "A2"
    used.add(tom.designation)
    return _rec("F08", tom, tag=_tag(sh, "F08"), graph=26, written=sh.graphs[26], actual_paper="A2")


def _f09(spec, rng, used):
    """Том указан в «Составе ПД», а файла в пакете нет."""
    cands = _free(spec, used, lambda t: t.code not in ("ПЗ",))
    if not cands:
        return None
    tom = rng.choice(cands)
    tom.present = False
    used.add(tom.designation)
    return _rec("F09", None, designation=tom.designation, title=tom.title)


CATALOG: dict[str, tuple[str, str, Inject]] = {
    "D01": ("consistency", "ТЭП в разных томах не совпадают", _d01),
    "D02": ("completeness", "лист указан в ведомости, но отсутствует", _d02),
    "D03": ("formatting", "неверное обозначение в графе 1", _d03),
    "D07": ("norms", "ссылка на недействующую редакцию нормы", _d07),
    "D07R": ("norms", "ссылка на заменённый стандарт вне реестра требований", _d07_off_registry),
    "D09": ("completeness", "обязательный раздел не представлен", _d09),
    "D10": ("formatting", "нет фамилии или даты в основной надписи", _d10),
    "F01": ("formatting", "пропуск в нумерации листов", _f01),
    "F02": ("formatting", "«Листов» не равно числу листов документа", _f02),
    "F03": ("formatting", "стадия «Р» в проектной документации", _f03),
    "F04": ("consistency", "наименование здания в томе отличается", _f04),
    "F05": ("completeness", "том не указан в «Составе ПД»", _f05),
    "F06": ("submission_format", "имя файла не по обозначению", _f06),
    "F07": ("submission_format", "повтор GUID в info.xml", _f07),
    "F08": ("formatting", "формат в графе 26 не совпадает с размером листа", _f08),
    "F09": ("completeness", "том из «Состава ПД» отсутствует в пакете", _f09),
}


def inject(spec: PackageSpec, rng: random.Random, codes: list[str]) -> list[dict]:
    """Испортить пакет дефектами из списка; вернуть записи эталона (неприменимые дефекты пропускаются)."""
    used: set[str] = set()
    records = []
    for code in codes:
        rec = CATALOG[code][2](spec, rng, used)
        if rec is not None:
            records.append(rec)
    return records
