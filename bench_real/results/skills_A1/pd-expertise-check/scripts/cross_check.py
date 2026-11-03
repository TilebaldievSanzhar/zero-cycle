#!/usr/bin/env python3
"""Увязка между томами: наименование объекта/здания и технико-экономические показатели (ТЭП).

Использование:
    python cross_check.py <out_dir>/package.json > cross_check.json

object_names — варианты наименования объекта (титульные листы, основные надписи) с местами,
               нормализованные (регистр, кавычки, пробелы); несколько вариантов → смотреть вручную.
tep          — по каждому показателю все найденные значения с документом/страницей;
               conflict=true, если в разных местах разные числа (при одинаковой единице).
Это кандидаты: перед записью нарушения откройте страницы и убедитесь, что речь об одном и том же
показателе (общая площадь здания vs. площадь помещения и т.п.).
"""
import json, os, re, sys
from collections import defaultdict

TEP = {
    'Площадь земельного участка': r'площадь\s+(?:земельного\s+)?участка',
    'Площадь застройки': r'площадь\s+застройки',
    'Общая площадь здания': r'общая\s+площадь(?:\s+здания)?',
    'Полезная площадь': r'полезная\s+площадь',
    'Расчётная площадь': r'расч[её]тная\s+площадь',
    'Жилая площадь': r'жилая\s+площадь',
    'Площадь квартир': r'площадь\s+квартир',
    'Строительный объём': r'строительный\s+объ[её]м(?:\s+здания)?',
    'Этажность': r'этажность|количество\s+этажей|число\s+этажей',
    'Количество квартир': r'количество\s+квартир',
    'Вместимость': r'вместимость|количество\s+(?:мест|учащихся|посещений)',
    'Площадь озеленения': r'площадь\s+озеленения',
    'Площадь покрытий': r'площадь\s+(?:тв[её]рдых\s+)?покрыти[йя]',
    'Продолжительность строительства': r'продолжительность\s+строительства|нормативн\w+\s+срок\s+строительства',
    'Сметная стоимость': r'(?:сметная|общая\s+сметная)\s+стоимость(?:\s+строительства)?|стоимость\s+строительства',
    'Уровень ответственности': r'уровень\s+ответственности',
    'Степень огнестойкости': r'степень\s+огнестойкости',
}
NUM = r'(\d{1,3}(?:[  ]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)'
UNIT = r'\s*(м2|м²|кв\.?\s?м|м3|м³|куб\.?\s?м|га|эт\w*|мес\w*|тыс\.?\s?тенге|млн\.?\s?тенге|тенге|чел\w*|мест\w*|кварт\w*|%)?'
OBJ_RX = re.compile(r'(?:(?:Строительство|Реконструкция|Капитальный\s+ремонт|Расширение|Модернизация|Техническое\s+перевооружение)'
                    r'\s+[^\n]{5,250})', re.I)


def pages_of(out_dir, d):
    t = open(os.path.join(out_dir, d['text_file']), encoding='utf-8').read()
    parts = re.split(r'=== PAGE (\d+) ===\n', t)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def norm_name(s):
    s = s.replace('“', '"').replace('”', '"').replace('«', '"').replace('»', '"').replace('ё', 'е')
    s = re.sub(r'\s+', ' ', s).strip(' .,;:').lower()
    s = re.split(r'\s(?:том|раздел|книга|альбом|рабочий проект|стадия|изм\.|лист)\b', s)[0]
    return s[:200]


def to_float(x):
    return float(x.replace(' ', '').replace(' ', '').replace(',', '.'))


def main(pkg_json):
    out_dir = os.path.dirname(os.path.abspath(pkg_json))
    pkg = json.load(open(pkg_json, encoding='utf-8'))
    names = defaultdict(set)
    tep = defaultdict(list)
    for d in pkg['documents']:
        if 'text_file' not in d:
            continue
        P = pages_of(out_dir, d)
        srcs = [(n, P.get(n, '')) for n in (1, 2, 3)]
        srcs += [(p['n'], p['tb']['raw']) for p in d['pages'] if p.get('tb')]
        for n, t in srcs:
            for m in OBJ_RX.finditer(t):
                names[norm_name(m.group(0))].add((d['path'], n))
            for q in re.findall(r'«([^»]{8,200})»', t):
                names['«' + norm_name(q) + '»'].add((d['path'], n))
        for n, t in P.items():
            for line in t.split('\n'):
                for key, rx in TEP.items():
                    m = re.search(rx, line, re.I)
                    if not m:
                        continue
                    rest = line[m.end():m.end() + 80]
                    v = re.search(NUM + UNIT, rest)
                    if v:
                        try:
                            val = to_float(v.group(1))
                        except ValueError:
                            continue
                        unit = (v.group(2) or '').replace('²', '2').replace('³', '3').replace(' ', '')
                        tep[key].append({'value': val, 'unit': unit, 'document': d['path'], 'page': n,
                                         'line': line.strip()[:160]})
                    break
    tep_out = {}
    for key, vals in tep.items():
        by_unit = defaultdict(set)
        for v in vals:
            by_unit[v['unit']].add(v['value'])
        conflict = any(len(s) > 1 for u, s in by_unit.items())
        docs = {v['document'] for v in vals}
        tep_out[key] = {'conflict': conflict, 'documents': len(docs), 'values': vals[:30]}
    obj = sorted(({'name': k, 'where': sorted(v)[:12], 'count': len(v)} for k, v in names.items()),
                 key=lambda x: -x['count'])
    # кластеры: строки, одна из которых — начало другой (перенос длинного наименования), считаем одним вариантом
    variants = []
    for o in obj:
        if o['name'].startswith('«') or len(o['name']) < 15:
            continue
        for v in variants:
            if v['name'].startswith(o['name'][:40]) or o['name'].startswith(v['name'][:40]):
                v['forms'].append(o['name'])
                v['where'] += o['where']
                break
        else:
            variants.append({'name': o['name'], 'forms': [o['name']], 'where': list(o['where'])})
    for v in variants:
        v['where'] = sorted(set(map(tuple, v['where'])))[:20]
        v['forms'] = sorted(set(v['forms']))
    print(json.dumps({'object_name_variants': variants[:20], 'quoted_names': [o for o in obj if o['name'].startswith('«')][:20],
                      'tep': tep_out}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main(sys.argv[1])
