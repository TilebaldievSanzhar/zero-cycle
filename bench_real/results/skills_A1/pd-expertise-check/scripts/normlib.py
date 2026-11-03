"""Общие функции: поиск и нормализация шифров нормативных документов."""
import re

_LAT2CYR = str.maketrans({'A': 'А', 'B': 'В', 'C': 'С', 'E': 'Е', 'H': 'Н', 'K': 'К', 'M': 'М',
                          'O': 'О', 'P': 'Р', 'T': 'Т', 'X': 'Х', 'y': 'у', 'c': 'с', 'p': 'р'})

# Префиксы (длинные раньше коротких). Латиница внутри префикса допускается (типичная ошибка набора).
_P = r'[СC]'
PREFIX = (r'(?:'
          r'[СC]Н[иu]П\s*Р[КK]|[СC]Н[иu]П|'
          r'[СC][ПP]\s*Р[КK]\s*(?:EN|ЕН|ЕN)|[СC][ПP]\s*Р[КK]|[СC]Н\s*Р[КK]|'
          r'МСН|МСП|МҚН|МЕЖ|ҚР\s*ҚН|ҚР\s*ЕЖ|ҚНжЕ|'
          r'НДЦС\s*Р[КK]|ЕСН\s*Р[КK]|РДС\s*Р[КK]|НТП\s*Р[КK]|МД\s*Р[КK]|ВСН|ВНТП|ОНТП|МДС|РД|'
          r'[СC]Т\s*Р[КK]\s*(?:ГОСТ\s*Р|ISO|EN|IEC|СТБ|ИСО)?|'
          r'ГОСТ\s*Р\s*(?:ИСО|ISO|МЭК|IEC)?|ГОСТ\s*(?:ISO|IEC|EN|ИСО|МЭК)?|'
          r'ТР\s*ТС|ПУЭ|[СC]Н|[СC]П'
          r')')
_SEG = r'(?:\d+|[IVXІ]{1,4}|[AА]\d+)(?:\(\d+(?:\.\d+)*\))?'
NUM = rf'{_SEG}(?:\s?[.\-–—:/+]\s?{_SEG})+\*{{0,2}}'
CODE_RE = re.compile(rf'(?<![\wА-Яа-я]){PREFIX}\s*{NUM}')

KK2RU = [(r'^ҚРҚН', 'СНРК'), (r'^ҚРЕЖ', 'СПРК'), (r'^МҚН', 'МСН'), (r'^МЕЖ', 'МСП'), (r'^ҚНжЕ', 'СНиП')]

# Вне области АГСК-1 (см. Введение АГСК-1, с. 4–5 PDF): стандарты ГОСТ, СТ РК и т.п.
OUT_OF_SCOPE = ('ГОСТ', 'СТРК', 'ТРТС', 'ПУЭ')
# Ценообразование и сметы (раздел 8 АГСК-1: «включены в АГСК-5») — АГСК-5 в источниках нет
PRICING_PREFIX = ('НДЦС', 'ЕСН')


def is_pricing(norm):
    pre, num = norm.split(' ', 1)
    return pre.startswith(PRICING_PREFIX) or (pre in ('СНРК', 'СПРК', 'МДРК', 'РДСРК') and num.startswith('8.'))


def split_code(code):
    m = re.match(rf'\s*({PREFIX})\s*(.*)$', code)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def normalize_code(code):
    """'СП РК 2.04-01-2017*' -> 'СПРК 2.04-01-2017'. Возвращает '' если не шифр."""
    pre, num = split_code(code)
    if pre is None:
        return ''
    pre = re.sub(r'\s+', '', pre).translate(_LAT2CYR).replace('СНuП', 'СНиП').replace('СНИП', 'СНиП')
    pre = pre.replace('ЕН', 'EN').replace('ЕN', 'EN') if 'РК' in pre and pre.startswith('СП') else pre
    for a, b in KK2RU:
        pre = re.sub(a, b, pre)
    num = num.strip().replace('–', '-').replace('—', '-').replace('І', 'I')
    num = re.sub(r'\s+', '', num).rstrip('*').rstrip('.,;')
    num = num.replace('А', 'A')
    return f'{pre} {num}'


def has_star(code):
    return code.strip().endswith('*')


def prefix_of(norm):
    return norm.split(' ')[0]


def dehyphenate(text):
    """Склеивает переносы внутри шифров: 'СП РК 2.04-01-\\n2017' -> 'СП РК 2.04-01-2017'."""
    text = re.sub(r'([.\-–])[ \t]*\n[ \t]*(?=[\dIVX])', r'\1', text)
    return text


def find_codes(text):
    """[(raw, norm, start, end)] — все шифры НД в тексте."""
    text2 = dehyphenate(text)
    out = []
    for m in CODE_RE.finditer(text2):
        raw = m.group(0)
        n = normalize_code(raw)
        if n:
            out.append((raw, n, m.start(), m.end()))
    return out
