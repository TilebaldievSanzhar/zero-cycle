#!/usr/bin/env python3
"""Строит JSON-индекс АГСК-1 из PDF перечня (колонки «Шифр | Наименование | Утверждено | МКС»).

Использование:
    python build_agsk_index.py <AGSK-1.pdf> <out.json>

Каждая запись: code (шифр как в перечне), norm (нормализованный ключ), page (страница PDF с 1),
name, approved, replaces (список нормализованных ключей документов, указанных как «Взамен …»
или «Соответствует … утративший силу»), lang ('ru'|'kk').
Готовый индекс уже лежит в references/agsk1_2025-06_index.json — пересобирать нужно
только при выходе новой редакции АГСК-1.
"""
import json, re, sys
import pdfplumber

sys.path.insert(0, __import__('os').path.dirname(__file__))
from normlib import normalize_code, CODE_RE  # noqa: E402

X_NAME, X_APPR, X_MKS = 160, 370, 475
START = {'ҚР', 'СП', 'СН', 'ВСН', 'СНиП', 'НТП', 'РДС', 'ВНТП', 'ОНТП', 'РД', 'МСН', 'МСП', 'МҚН',
         'МЕЖ', 'МД', 'Пособие', 'Правила', 'Қағидалар', 'Методика', 'Методические', 'Әдістеме',
         'Әдістемелік', 'Рекомендации', 'Ұсынымдар', 'Закон', 'Заң', 'Кодекс', 'Перечень', 'Тізбе',
         'Руководство', 'Инструкция', 'Нұсқаулық', 'Ұлттық', 'Национальное', 'Технический',
         'Техникалық', 'Сборник', 'Жинақ', 'Порядок', 'Тәртіп', 'Р', 'МДС', 'Указания', 'Положение',
         'Ереже', 'Форма', 'Нысан', 'Үлгі', 'Үлгілік', 'Типовой', 'Типовая', 'Типовые', 'Проверочный',
         'Тексеру', 'Постановление', 'Қаулы', 'Приказ', 'Бұйрық', 'ЕНиР', 'ТСН', 'РСН', 'ТР', 'Нормы',
         'Нормалар', 'Классификатор', 'Сыныптауыш', 'Справочник', 'Анықтамалық', 'Стандарт', 'СТ'}
SKIP_HEAD = ('Шифрі', 'нөмірі', 'и номер')


def page_rows(page):
    ws = [w for w in page.extract_words() if w['top'] > 88 and w['bottom'] < page.height - 30]
    rows = {}
    for w in ws:
        key = round(w['top'] / 3)
        rows.setdefault(key, []).append(w)
    out = []
    for k in sorted(rows):
        r = sorted(rows[k], key=lambda w: w['x0'])
        cols = ['', '', '', '']
        for w in r:
            i = 0 if w['x0'] < X_NAME else 1 if w['x0'] < X_APPR else 2 if w['x0'] < X_MKS else 3
            cols[i] = (cols[i] + ' ' + w['text']).strip()
        out.append((r[0]['top'], cols))
    return out


def is_kk(text):
    return bool(re.search('[ӘәҒғҚқҢңӨөҰұҮүҺһІі]', text)) or text.startswith(('ҚР', 'МҚН', 'МЕЖ'))


def main(src, dst):
    entries, cur = [], None
    with pdfplumber.open(src) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            if pno < 9:
                continue
            for top, (code, name, appr, mks) in page_rows(page):
                if code.startswith(SKIP_HEAD) or 'Шифрі' in code:
                    continue
                first = code.split()[0] if code else ''
                if first in START:
                    # новая запись начинается, если в колонке «Шифр» есть начало шифра
                    # и одновременно есть текст в колонке наименования (иначе это заголовок раздела)
                    if name or appr:
                        cur = {'code': code, 'page': pno, 'name': name, 'approved': appr, 'mks': mks}
                        entries.append(cur)
                        continue
                if cur is None:
                    continue
                if code:
                    cur['code'] += ' ' + code
                if name:
                    cur['name'] += ' ' + name
                if appr:
                    cur['approved'] += ' ' + appr
                if mks:
                    cur['mks'] += ' ' + mks
    out = []
    for e in entries:
        code = re.sub(r'\s*\((?:изд\.|по состоянию|\d{2}\.\d{2}\.\d{4}).*$', '', e['code']).strip()
        code = re.sub(r'(\d)\s*-\s+(\d)', r'\1-\2', code)
        code = re.sub(r'-\s+(\d)', r'-\1', code)
        e['code_raw'] = e['code']
        e['code'] = code
        e['lang'] = 'kk' if is_kk(code + ' ' + e['name'][:40]) else 'ru'
        m0 = CODE_RE.search(code)
        if m0 and m0.start() == 0:
            code = m0.group(0)
            e['code'] = code
        e['norm'] = normalize_code(code) if m0 and m0.start() == 0 else ''
        pref = ' '.join(code.split()[:2]) if code.split()[:2] and code.split()[1:2] == ['РК'] else code.split()[0] if code else ''
        reps = []
        for m in re.finditer(r'(?:Взамен|орнына)\s+([^;()]{3,120})', e['name']):
            frag = m.group(1)
            found = [normalize_code(x.group(0)) for x in CODE_RE.finditer(frag)]
            if not found:
                bare = re.match(r'([\dIVX][\w.\-*/]*\d\*?)', frag.strip())
                if bare and pref:
                    found = [normalize_code(pref + ' ' + bare.group(1))]
            reps += found
        for m in re.finditer(r'Соответствует\s+(.{3,160}?)утративш', e['name']):
            reps += [normalize_code(x.group(0)) for x in CODE_RE.finditer(m.group(1))]
        e['replaces'] = sorted(set(r for r in reps if r and r != e['norm']))
        out.append({k: e[k] for k in ('code', 'norm', 'page', 'lang', 'name', 'approved', 'replaces')})
    json.dump(out, open(dst, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'entries: {len(out)}; ru: {sum(e["lang"]=="ru" for e in out)}; with replaces: {sum(bool(e["replaces"]) for e in out)}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
