#!/usr/bin/env python3
"""Комплектность пакета: ИРД/документы по приложениям к Правилам № 299, сметная документация,
сверка «Состава проекта» и «Содержания» томов с фактически присутствующими документами.

Использование:
    python inventory.py <out_dir>/package.json [--kind new|reconstruction|capital_repair|smeta_correction|special]
           > inventory.json

Если --kind не задан, вид строительства угадывается по тексту (поле kind_guess) — подтвердите сами.
Для каждого пункта чек-листа: hits_title — совпадения в имени файла / на первых 2 страницах документа
(вероятно, это сам документ), hits_body — упоминания в тексте других документов (документ, возможно,
есть у заказчика, но не приложен). Решение «есть/нет» принимает агент.
"""
import json, os, re, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DESIG_RE = re.compile(
    r'(?<![\w/.\-])((?=[\w./\-]*\d)[0-9A-ZА-ЯЁ][0-9A-Za-zА-ЯЁа-яё/_.]*(?:-[0-9A-ZА-ЯЁ/_.]+)*'
    r'-[А-ЯЁA-Z]{1,5}\d{0,2}(?:\.[А-ЯЁ]{1,3})?)(?![\w\-])')


def pages_of(out_dir, d):
    t = open(os.path.join(out_dir, d['text_file']), encoding='utf-8').read()
    parts = re.split(r'=== PAGE (\d+) ===\n', t)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def snippet(t, m):
    return re.sub(r'\s+', ' ', t[max(0, m.start() - 50):m.end() + 70])


def parse_listing(text, heading):
    """Строки перечня после заголовка ('Состав проекта' / 'Содержание')."""
    m = re.search(heading, text, re.I)
    if not m:
        return []
    rows = []
    for line in text[m.end():].split('\n'):
        ds = DESIG_RE.findall(line)
        vol = re.match(r'\s*(?:Том\s*)?(\d+(?:\.\d+)*)\s', line)
        if ds and not re.search(r'Изм\.|Кол\.\s?уч|№\s?док', line):
            rows.append({'line': line.strip()[:200], 'designations': ds, 'volume': vol.group(1) if vol else None})
    return rows


def main(pkg_json, kind=None):
    out_dir = os.path.dirname(os.path.abspath(pkg_json))
    pkg = json.load(open(pkg_json, encoding='utf-8'))
    ck = json.load(open(os.path.join(HERE, '..', 'references', 'completeness_checklist.json'), encoding='utf-8'))
    docs = [d for d in pkg['documents'] if 'text_file' in d]
    P = {d['path']: pages_of(out_dir, d) for d in docs}
    alltext = '\n'.join('\n'.join(p.values()) for p in P.values())
    # вид строительства
    k = Counter()
    for word, kk in (('реконструкц', 'reconstruction'), ('расширени', 'reconstruction'), ('модернизац', 'reconstruction'),
                     ('техническ\\w+ перевооружени', 'reconstruction'), ('капитальн\\w+ ремонт', 'capital_repair'),
                     ('нов\\w+ строительств', 'new'), ('корректировк\\w+ сметн', 'smeta_correction'),
                     ('экспертн\\w+ сопровождени', 'special')):
        k[kk] += len(re.findall(word, alltext, re.I))
    kind_guess = k.most_common(1)[0][0] if k and k.most_common(1)[0][1] else 'new'
    kind = kind or kind_guess
    items = []
    for it in ck['items']:
        if kind not in it['kinds']:
            continue
        rx = re.compile(it['kw'], re.I)
        ht, hb = [], []
        for d in docs:
            pg = P[d['path']]
            if rx.search(d['path']):
                ht.append({'document': d['path'], 'page': None, 'where': 'имя файла'})
            for n in sorted(pg):
                for m in rx.finditer(pg[n]):
                    rec = {'document': d['path'], 'page': n, 'snippet': snippet(pg[n], m)}
                    (ht if n <= 2 and m.start() < 1500 else hb).append(rec)
                    break
        items.append({'id': it['id'], 'basis': it['basis'], 'text': it['text'], 'req': it['req'],
                      'hits_title': ht[:8], 'hits_body': hb[:8],
                      'verdict_hint': 'найден' if ht else ('только упоминания' if hb else 'НЕ НАЙДЕН')})
    # ТУ по видам (прил. 5 п. 5 — перечень видов)
    tu = {}
    for t in ck['tu_types_app5']:
        rx = re.compile(r'(?:технически\w+\s+услови\w*|\bТУ\b)[^\n]{0,120}(?:' + t + ')', re.I)
        tu[t] = sorted({(d['path'], n) for d in docs for n, txt in P[d['path']].items() if rx.search(txt)})[:6]
    # сметная документация
    smeta = {}
    for name, kw in ck['smeta_docs'].items():
        rx = re.compile(kw, re.I)
        smeta[name] = [(d['path'], n) for d in docs for n, txt in P[d['path']].items() if n <= 3 and rx.search(txt)][:10]
    # обозначения, присутствующие в пакете (основные надписи и первые страницы)
    present = defaultdict(set)
    for d in docs:
        for p in d['pages']:
            for x in (p.get('tb') or {}).get('designations') or []:
                present[x].add((d['path'], p['n']))
        for n in (1, 2):
            for x in DESIG_RE.findall(P[d['path']].get(n, '')):
                present[x].add((d['path'], n))
    # Состав проекта
    sostav = []
    for d in docs:
        for n, txt in P[d['path']].items():
            if re.search(r'(?im)^\s*Состав\s+(?:рабочего\s+)?проекта\s*$', txt):
                for r in parse_listing(txt, r'Состав\s+(?:рабочего\s+)?проекта'):
                    r['listed_in'] = (d['path'], n)
                    r['found'] = {x: sorted(present.get(x, set()) - {(d['path'], n)})[:3] for x in r['designations']}
                    r['missing'] = [x for x, v in r['found'].items() if not v]
                    sostav.append(r)
    # Содержание томов
    contents = []
    for d in docs:
        own = {x for p in d['pages'] for x in (p.get('tb') or {}).get('designations') or []}
        for n, txt in sorted(P[d['path']].items())[:6]:
            if re.search(r'(?im)^\s*Содержание(?:\s+тома)?\s*$', txt):
                for r in parse_listing(txt, r'Содержание(?:\s+тома)?'):
                    miss = [x for x in r['designations'] if x not in own and not re.search(re.escape(x), '\n'.join(v for kk, v in P[d['path']].items() if kk != n))]
                    if miss:
                        contents.append({'document': d['path'], 'page': n, 'line': r['line'], 'not_found_in_volume': miss})
    print(json.dumps({'kind_used': kind, 'kind_guess': kind_guess, 'kind_scores': dict(k), 'items': items,
                      'tu_by_type': {k2: v for k2, v in tu.items()}, 'smeta': smeta,
                      'sostav_proekta': sostav, 'contents_mismatch': contents,
                      'designations_present': sorted(present)}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    a = sys.argv[1:]
    main(a[0], a[a.index('--kind') + 1] if '--kind' in a else None)
