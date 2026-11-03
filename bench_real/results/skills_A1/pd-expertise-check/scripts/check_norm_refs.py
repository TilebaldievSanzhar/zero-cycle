#!/usr/bin/env python3
"""Проверка ссылок на нормативные документы по АГСК-1 (июнь 2025).

Использование:
    python check_norm_refs.py <out_dir>/package.json [--index ../references/agsk1_2025-06_index.json]
           [--agsk-text ../references/agsk1_2025-06_text.txt] > norm_refs.json

Для каждой найденной в пакете ссылки (шифр + документ + страницы) выдаёт класс:
  valid         — шифр есть в АГСК-1 как действующий (страница АГСК указана)
  replaced      — шифр указан в АГСК-1 как заменённый («Взамен …» / «Соответствует … утративший силу»);
                  приводится действующий документ → кандидат в «нарушение»
  not_listed    — шифр из области АГСК-1 (СН РК, СП РК, СНиП РК, МСН, ВСН, …) не найден ни в индексе,
                  ни в сыром тексте АГСК-1 → кандидат в «нарушение» (в перечне действующих отсутствует);
                  перед записью проверьте grep по agsk1_2025-06_text.txt вручную
  raw_only      — номер найден только в сыром тексте АГСК-1 (индекс не распознал) → посмотрите страницу
  out_of_scope  — ГОСТ, СТ РК, ТР ТС, ПУЭ (АГСК-1 их не содержит, Введение) и сметные нормативы
                  (НДЦС РК, СН/СП РК 8.xx — раздел 8 АГСК-1 отсылает к АГСК-5) → «проверить вручную»
Также выдаёт same_number — действующие документы с тем же номером, но другим годом/префиксом.
"""
import json, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from normlib import find_codes, prefix_of, OUT_OF_SCOPE, is_pricing  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def load_pages(out_dir, doc):
    t = open(os.path.join(out_dir, doc['text_file']), encoding='utf-8').read()
    parts = re.split(r'=== PAGE (\d+) ===\n', t)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def stem(norm):
    """'СПРК 2.04-01-2017' -> '2.04-01' (номер без года)."""
    num = norm.split(' ', 1)[1]
    return re.sub(r'[-:]\d{2,4}(?:/\d{4})?$', '', num)


def main(pkg_json, index, agsk_text):
    out_dir = os.path.dirname(os.path.abspath(pkg_json))
    pkg = json.load(open(pkg_json, encoding='utf-8'))
    idx = json.load(open(index, encoding='utf-8'))
    valid = {}
    for e in sorted(idx, key=lambda e: e['lang'] != 'ru'):  # русские записи приоритетнее казахских
        if e['norm']:
            valid.setdefault(e['norm'], e)
    replaced = defaultdict(list)
    for e in idx:
        for r in e['replaces']:
            replaced[r].append(e)
    by_stem = defaultdict(list)
    for n, e in valid.items():
        by_stem[(prefix_of(n), stem(n))].append(e)
    raw = re.sub(r'\s+', '', open(agsk_text, encoding='utf-8').read()) if agsk_text and os.path.exists(agsk_text) else ''

    refs = {}
    for d in pkg['documents']:
        if 'text_file' not in d:
            continue
        for pno, txt in load_pages(out_dir, d):
            for rawc, norm, s, e in find_codes(txt):
                r = refs.setdefault(norm, {'norm': norm, 'as_written': set(), 'where': defaultdict(set), 'context': None})
                r['as_written'].add(rawc.replace('\n', ' '))
                r['where'][d['path']].add(pno)
                if r['context'] is None:
                    r['context'] = re.sub(r'\s+', ' ', txt[max(0, s - 60):e + 40])
    out = []
    for norm, r in sorted(refs.items()):
        pre = prefix_of(norm)
        item = {'ref': norm, 'as_written': sorted(r['as_written']),
                'where': {k: sorted(v) for k, v in r['where'].items()}, 'context': r['context']}
        if norm in valid:
            e = valid[norm]
            item.update(cls='valid', agsk_page=e['page'], agsk_code=e['code'], agsk_name=e['name'][:160])
            if norm in replaced:  # одновременно и действующий, и упомянут «взамен» (частичная замена)
                item['also_replaced_by'] = [(x['code'], x['page']) for x in replaced[norm]]
        elif norm in replaced:
            item.update(cls='replaced', replaced_by=[{'code': x['code'], 'page': x['page'], 'name': x['name'][:160]}
                                                    for x in replaced[norm] if x['lang'] == 'ru'] or
                        [{'code': x['code'], 'page': x['page']} for x in replaced[norm]])
        elif pre.startswith(OUT_OF_SCOPE):
            item.update(cls='out_of_scope', note='ГОСТ/СТ РК/ТР ТС/ПУЭ не включены в АГСК-1 (Введение, с. 4–5 PDF)')
        elif is_pricing(norm):
            item.update(cls='out_of_scope', note='Ценообразование и сметы: раздел 8 АГСК-1 — «включены в АГСК-5» (с. 146 PDF); АГСК-5 в источниках нет')
        else:
            num = norm.split(' ', 1)[1]
            item['cls'] = 'raw_only' if raw and num in raw else 'not_listed'
        if item['cls'] != 'valid' and not pre.startswith(OUT_OF_SCOPE):
            same = by_stem.get((pre, stem(norm)), [])
            if same:
                item['same_number_valid'] = [(x['code'], x['page']) for x in same if x['lang'] == 'ru'][:5]
        out.append(item)
    summary = defaultdict(int)
    for i in out:
        summary[i['cls']] += 1
    print(json.dumps({'summary': summary, 'refs': out}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    a = sys.argv[1:]
    index = os.path.join(HERE, '..', 'references', 'agsk1_2025-06_index.json')
    text = os.path.join(HERE, '..', 'references', 'agsk1_2025-06_text.txt')
    if '--index' in a:
        index = a[a.index('--index') + 1]
    if '--agsk-text' in a:
        text = a[a.index('--agsk-text') + 1]
    main(a[0], index, text)
