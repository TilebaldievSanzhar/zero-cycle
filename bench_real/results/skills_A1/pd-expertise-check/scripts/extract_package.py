#!/usr/bin/env python3
"""Инвентаризация пакета PDF: текст по страницам, форматы листов, поля основной надписи.

Использование:
    python extract_package.py <package_dir> <out_dir>

Результат:
    <out_dir>/package.json     — сводка по каждому PDF и каждой странице
    <out_dir>/text/<имя>.txt   — полный текст документа с маркерами "=== PAGE n ===" (для grep)

Эвристики основной надписи (ГОСТ 21.101-97, прил. Д, графы 1, 6, 7, 8, 10–13) — это КАНДИДАТЫ.
Перед тем как записывать нарушение, подтвердите его визуально (render_page.py + Read).
"""
import json, os, re, sys
from collections import Counter

import pdfplumber

MM = 72 / 25.4
BASE = {'A0': (841, 1189), 'A1': (594, 841), 'A2': (420, 594), 'A3': (297, 420), 'A4': (210, 297),
        'A5': (148, 210)}
TOL = 4  # мм

DESIG_RE = re.compile(
    r'(?<![\w/.\-])((?=[\w./\-]*\d)[0-9A-ZА-ЯЁ][0-9A-Za-zА-ЯЁа-яё/_.]*(?:-[0-9A-ZА-ЯЁ/_.]+)*'
    r'-[А-ЯЁA-Z]{1,5}\d{0,2}(?:\.[А-ЯЁ]{1,3})?)(?![\w\-])')
DATE_RE = re.compile(r'^(?:\d{1,2}[./]\d{1,2}[./]\d{2,4}|\d{1,2}[./]\d{2,4}|\d{2}\.\d{2})$')
ROLE_RE = re.compile(r'^(Разраб|Разработал|Выполнил|Пров|Проверил|Н\.?\s?контр|Нормоконтр|ГИП|Утв|Утвердил|'
                     r'Гл\.?\s?спец|Нач\.?\s?отд|Рук\.?|Исполн)', re.I)
STAGE_VALUES = ('РП', 'П', 'Р', 'ЭП', 'ТЭО', 'РД')


def fmt_name(w_mm, h_mm):
    a, b = sorted((w_mm, h_mm))
    for name, (s, l) in BASE.items():
        if abs(a - s) <= TOL and abs(b - l) <= TOL:
            return name
    # дополнительные форматы: сторона = длинная сторона базового, другая = n × короткая сторона
    for name, (s, l) in BASE.items():
        for n in range(2, 10):
            for x, y in ((l, s * n), (s * n, l)):
                if abs(a - min(x, y)) <= TOL and abs(b - max(x, y)) <= TOL:
                    return f'{name}x{n}'
    return 'нестандартный'


def words_in(words, x0, top0, x1, top1):
    return [w for w in words if w['x0'] >= x0 and w['x1'] <= x1 and w['top'] >= top0 and w['bottom'] <= top1]


def text_of(ws):
    lines = {}
    for w in sorted(ws, key=lambda w: (round(w['top'] / 3), w['x0'])):
        lines.setdefault(round(w['top'] / 3), []).append(w['text'])
    return '\n'.join(' '.join(v) for _, v in sorted(lines.items()))


def value_under(label, ws, max_dy=12 * MM):
    """Значение в ячейке под меткой (или справа в той же строке)."""
    cx0, cx1 = label['x0'] - 4 * MM, label['x1'] + 4 * MM
    cands = [w for w in ws if w is not label and w['top'] > label['top'] + 1 and w['top'] - label['bottom'] < max_dy
             and w['x1'] > cx0 and w['x0'] < cx1]
    cands.sort(key=lambda w: (w['top'], abs((w['x0'] + w['x1']) / 2 - (label['x0'] + label['x1']) / 2)))
    return cands[0]['text'] if cands else None


def parse_titleblock(page, words):
    W, H = page.width, page.height
    x0, top0 = W - 200 * MM, H - 65 * MM
    tb = words_in(words, x0, top0, W, H)
    res = {'raw': text_of(tb)}
    labels = {w['text'].strip(':.').lower(): w for w in tb}
    stage_l = labels.get('стадия')
    res['stage'] = value_under(stage_l, tb) if stage_l else None
    lst = [w for w in tb if w['text'].strip(':.') == 'Лист']
    lsts = [w for w in tb if w['text'].strip(':.') == 'Листов']

    def pick(labels, pool):
        # «Лист» встречается и в шапке таблицы изменений (Изм. Кол.уч. Лист №док.) — берём правую метку
        # с числовым значением под ней
        for lab in sorted(labels, key=lambda w: -w['x0']):
            v = value_under(lab, pool)
            if v and re.fullmatch(r'\d{1,4}', v):
                return v
        return value_under(max(labels, key=lambda w: w['x0']), pool) if labels else None
    res['sheet'] = pick(lst, [w for w in tb if w not in lsts])
    res['sheets'] = pick(lsts, tb)
    for k in ('sheet', 'sheets'):
        if res[k] is not None and not re.fullmatch(r'\d{1,4}', res[k]):
            res[k + '_raw'] = res[k]
            res[k] = None
    if res['stage'] and res['stage'] not in STAGE_VALUES and len(res['stage']) > 4:
        res['stage_raw'], res['stage'] = res['stage'], None
    res['has_stage_label'] = bool(stage_l)
    res['has_sheet_label'] = bool(lst)
    res['has_sheets_label'] = bool(lsts)
    des = Counter(m.group(1) for m in DESIG_RE.finditer(res['raw']))
    res['designations'] = [d for d, _ in des.most_common()]
    # роли / фамилии / даты (графы 10–13)
    rows = {}
    for w in tb:
        rows.setdefault(round(w['top'] / 4), []).append(w)
    roles = []
    for _, r in sorted(rows.items()):
        r.sort(key=lambda w: w['x0'])
        for i, w in enumerate(r):
            if ROLE_RE.match(w['text']):
                rest = [v for v in r[i + 1:] if v['x0'] - w['x1'] < 70 * MM]
                surname = next((v['text'] for v in rest if re.match(r'^[А-ЯЁA-Z][а-яёa-z\-]+', v['text'])
                                and not ROLE_RE.match(v['text'])), None)
                date = next((v['text'] for v in rest if DATE_RE.match(v['text'])), None)
                ymid = (w['top'] + w['bottom']) / 2
                sig = 0
                for obj in page.curves + page.images + [l for l in page.lines if abs(l['top'] - l['bottom']) > 1]:
                    oy = (obj['top'] + obj['bottom']) / 2
                    if abs(oy - ymid) < 4 * MM and obj['x0'] > w['x1'] + 15 * MM and obj['x1'] < w['x1'] + 60 * MM:
                        sig += 1
                roles.append({'role': w['text'], 'surname': surname, 'date': date, 'sig_objects': sig})
                break
    res['roles'] = roles
    return res


def top_right_number(page, words):
    W = page.width
    ws = words_in(words, W - 35 * MM, 0, W, 22 * MM)
    nums = [w['text'] for w in ws if re.fullmatch(r'\d{1,4}', w['text'])]
    return nums[-1] if nums else None


def process(pdf_path, rel, out_text_dir):
    doc = {'path': rel, 'pages': []}
    try:
        pdf = pdfplumber.open(pdf_path)
    except Exception as e:  # повреждён/зашифрован
        doc['error'] = repr(e)
        return doc
    with pdf:
        doc['n_pages'] = len(pdf.pages)
        doc['metadata'] = {k: str(v) for k, v in (pdf.metadata or {}).items() if k in ('Title', 'Author', 'Creator', 'Producer', 'CreationDate', 'ModDate')}
        chunks = []
        for i, page in enumerate(pdf.pages, 1):
            try:
                words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
                text = page.extract_text() or ''
            except Exception as e:
                words, text = [], ''
                doc.setdefault('page_errors', []).append((i, repr(e)))
            w_mm, h_mm = page.width / MM, page.height / MM
            p = {'n': i, 'w_mm': round(w_mm), 'h_mm': round(h_mm), 'format': fmt_name(w_mm, h_mm),
                 'orient': 'альбомная' if w_mm > h_mm else 'книжная', 'chars': len(text.strip()),
                 'images': len(page.images), 'rotation': page.rotation}
            p['scan_suspect'] = p['chars'] < 20 and p['images'] > 0
            p['first_line'] = text.strip().split('\n')[0][:120] if text.strip() else ''
            if words:
                p['tb'] = parse_titleblock(page, words)
                p['top_right_num'] = top_right_number(page, words)
            doc['pages'].append(p)
            chunks.append(f'=== PAGE {i} ===\n{text}\n')
        safe = re.sub(r'[\\/]+', '__', rel)
        with open(os.path.join(out_text_dir, safe + '.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(chunks))
        doc['text_file'] = os.path.join('text', safe + '.txt')
    # группировка по обозначению (графа 1)
    groups = []
    for p in doc['pages']:
        d = (p.get('tb') or {}).get('designations') or []
        key = d[0] if d else None
        if groups and groups[-1]['designation'] == key:
            groups[-1]['pages'].append(p['n'])
        else:
            groups.append({'designation': key, 'pages': [p['n']]})
    doc['designation_runs'] = groups
    return doc


def main(pkg, out):
    os.makedirs(os.path.join(out, 'text'), exist_ok=True)
    docs, others = [], []
    for root, _, files in os.walk(pkg):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, pkg)
            if fn.lower().endswith('.pdf'):
                print('…', rel, file=sys.stderr, flush=True)
                docs.append(process(full, rel, os.path.join(out, 'text')))
            else:
                others.append(rel)
    docs.sort(key=lambda d: d['path'])
    res = {'package': os.path.abspath(pkg), 'documents': docs, 'non_pdf_files': sorted(others)}
    with open(os.path.join(out, 'package.json'), 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    # краткий обзор в stdout
    for d in docs:
        if 'error' in d:
            print(f"!! {d['path']}: {d['error']}")
            continue
        fm = Counter(p['format'] for p in d['pages'])
        scans = sum(p['scan_suspect'] for p in d['pages'])
        des = [g['designation'] for g in d['designation_runs'] if g['designation']]
        print(f"{d['path']}: {d['n_pages']} стр.; форматы {dict(fm)}; скан-страниц {scans}; обозначения {sorted(set(des))[:6]}")
    if others:
        print('Не-PDF файлы:', others)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
