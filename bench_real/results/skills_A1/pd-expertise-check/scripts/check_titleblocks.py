#!/usr/bin/env python3
"""Проверки оформления по ГОСТ 21.101-97 по результатам extract_package.py.

Использование:
    python check_titleblocks.py <out_dir>/package.json [--stage РП] > titleblock_candidates.json

Выдаёт КАНДИДАТЫ в замечания (поле suggested_status). Каждый кандидат нужно подтвердить
просмотром страницы (render_page.py --crop tb / Read) — особенно отсутствие подписей и дат.
"""
import json, re, sys
from collections import Counter, defaultdict

LIMITS = {'A4': 250, 'A3': 150, 'A2': 75, 'A1': 50}  # ГОСТ 21.101-97 п. 4.1.4
G = 'ГОСТ 21.101-97'


def cand(doc, page, check, detail, basis, status='нарушение'):
    return {'document': doc, 'page': page, 'check': check, 'detail': detail, 'basis': basis,
            'suggested_status': status}


def page_text(out_dir, doc):
    try:
        t = open(f"{out_dir}/{doc['text_file']}", encoding='utf-8').read()
    except Exception:
        return {}
    parts = re.split(r'=== PAGE (\d+) ===\n', t)
    return {int(parts[i]): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def is_cover_or_title(p, txt):
    tb = p.get('tb') or {}
    has_tb = tb.get('has_sheet_label') or tb.get('has_stage_label') or re.search(r'\bИзм\.', tb.get('raw', ''))
    return not has_tb and p['n'] <= 3


def main(pkg_json, expected_stage=None):
    out_dir = pkg_json.rsplit('/', 1)[0] if '/' in pkg_json else '.'
    pkg = json.load(open(pkg_json, encoding='utf-8'))
    C = []
    stage_counter = Counter()
    stage_pages = defaultdict(list)
    for d in pkg['documents']:
        if 'error' in d:
            C.append(cand(d['path'], None, 'pdf_open', f"PDF не открывается: {d['error']}",
                          'Комплектность пакета (документ нечитаем)', 'проверить вручную'))
            continue
        texts = page_text(out_dir, d)
        pages = d['pages']
        # --- форматы и объём тома
        fm = Counter(p['format'] for p in pages)
        for p in pages:
            if p['format'] == 'нестандартный':
                C.append(cand(d['path'], p['n'], 'format', f"Размер листа {p['w_mm']}×{p['h_mm']} мм не соответствует "
                              f"основным/дополнительным форматам (допуск ±4 мм)",
                              f'{G}, п. 4.1.4 (форматы по ГОСТ 2.301, текста которого в источниках нет)',
                              'проверить вручную'))
        for f, lim in LIMITS.items():
            if fm.get(f, 0) > lim:
                C.append(cand(d['path'], None, 'volume_size', f'В томе {fm[f]} листов формата {f} (> {lim})', f'{G}, п. 4.1.4'))
        # --- скан-страницы
        scans = [p['n'] for p in pages if p.get('scan_suspect')]
        if scans:
            C.append(cand(d['path'], None, 'scan', f'Страницы без текстового слоя (скан): {scans[:30]}',
                          'Требует визуального просмотра (Read/render_page.py)', 'проверить вручную'))
        # --- основная надпись по страницам
        for p in pages:
            if p.get('scan_suspect') or p['chars'] < 20:
                continue
            tb = p.get('tb') or {}
            txt = texts.get(p['n'], '')
            if is_cover_or_title(p, txt):
                continue
            if not tb.get('designations') and (tb.get('has_sheet_label') or tb.get('has_stage_label')):
                C.append(cand(d['path'], p['n'], 'designation_missing', 'В основной надписи не найдено обозначение документа (графа 1)',
                              f'{G}, п. 5.27, прил. Д, перечисление а); п. 4.1.3'))
            if not (tb.get('has_sheet_label') or tb.get('has_stage_label') or tb.get('designations')):
                C.append(cand(d['path'], p['n'], 'titleblock_missing', 'Не найдена основная надпись в правом нижнем углу листа',
                              f'{G}, п. 5.27, прил. Е', 'проверить вручную'))
            if tb.get('has_stage_label'):
                st = tb.get('stage')
                if not st:
                    C.append(cand(d['path'], p['n'], 'stage_missing', 'Графа «Стадия» (графа 6) не заполнена',
                                  f'{G}, прил. Д, перечисление е)'))
                else:
                    stage_counter[st] += 1
                    stage_pages[st].append((d['path'], p['n']))
        # --- нумерация листов внутри документа с одним обозначением (графы 7, 8)
        runs = defaultdict(list)
        for p in pages:
            tb = p.get('tb') or {}
            des = (tb.get('designations') or [None])[0]
            if des:
                runs[des].append(p)
        for des, ps in runs.items():
            nums = [(p['n'], int(p['tb']['sheet'])) for p in ps if (p.get('tb') or {}).get('sheet')]
            seq = [s for _, s in nums]
            if seq:
                dup = [s for s, c in Counter(seq).items() if c > 1]
                if dup:
                    C.append(cand(d['path'], None, 'sheet_dup', f'{des}: повторяющиеся номера листов {dup}', f'{G}, п. 9.3, прил. Д, перечисление ж)'))
                exp = list(range(min(seq), min(seq) + len(seq)))
                if sorted(seq) != exp or seq != sorted(seq):
                    gaps = sorted(set(range(min(seq), max(seq) + 1)) - set(seq))
                    C.append(cand(d['path'], None, 'sheet_seq', f'{des}: номера листов {seq} (стр. PDF {[n for n, _ in nums]}); пропуски {gaps}',
                                  f'{G}, п. 9.3 (порядковая нумерация листов в пределах документа), прил. Д, перечисление ж)'))
                no_num = [p['n'] for p in ps if not (p.get('tb') or {}).get('sheet') and (p.get('tb') or {}).get('has_sheet_label')]
                if no_num and len(ps) > 1:
                    C.append(cand(d['path'], no_num[0], 'sheet_empty', f'{des}: графа «Лист» пуста на стр. PDF {no_num}',
                                  f'{G}, прил. Д, перечисление ж)', 'проверить вручную'))
            total = [(p['n'], int(p['tb']['sheets'])) for p in ps if (p.get('tb') or {}).get('sheets')]
            if total:
                n0, t0 = total[0]
                if t0 != len(ps):
                    C.append(cand(d['path'], n0, 'sheets_total', f'{des}: в графе «Листов» указано {t0}, фактически листов с этим обозначением в файле {len(ps)}',
                                  f'{G}, прил. Д, перечисление и)'))
                if len(total) > 1:
                    C.append(cand(d['path'], total[1][0], 'sheets_total_repeated', f'{des}: графа «Листов» заполнена не только на первом листе (стр. {[n for n, _ in total]})',
                                  f'{G}, прил. Д, перечисление и)', 'проверить вручную'))
            # подписи на первом листе (графы 10–13)
            first = ps[0]['tb']
            roles = first.get('roles') or []
            if first.get('has_stage_label') or first.get('has_sheets_label'):
                if not roles:
                    C.append(cand(d['path'], ps[0]['n'], 'roles_missing', f'{des}: в основной надписи первого листа не найдены строки «Разраб./Пров./Н.контр.»',
                                  f'{G}, прил. Д, перечисления л), м)', 'проверить вручную'))
                for r in roles:
                    miss = [k for k, v in (('фамилия', r['surname']), ('дата', r['date'])) if not v]
                    if miss:
                        C.append(cand(d['path'], ps[0]['n'], 'role_incomplete', f"{des}: строка «{r['role']}» — нет: {', '.join(miss)}; объектов-подписей в строке: {r['sig_objects']}",
                                      f'{G}, прил. Д, перечисление м)', 'проверить вручную'))
                    elif r['sig_objects'] == 0:
                        C.append(cand(d['path'], ps[0]['n'], 'signature_check', f"{des}: строка «{r['role']}» ({r['surname']}, {r['date']}) — подпись не найдена как графический объект, проверить визуально",
                                      f'{G}, прил. Д, перечисление м)', 'проверить вручную'))
        # --- сквозная нумерация страниц тома (правый верхний угол)
        tr = [(p['n'], int(p['top_right_num'])) for p in pages if p.get('top_right_num')]
        if len(tr) >= 3:
            off = Counter(num - n for n, num in tr).most_common(1)[0][0]
            bad = [(n, num) for n, num in tr if num - n != off]
            if bad:
                C.append(cand(d['path'], bad[0][0], 'page_numbering', f'Сквозная нумерация страниц нарушена: (стр. PDF, номер) {bad[:10]}; ожидаемое смещение {off}',
                              f'{G}, п. 9.3'))
        # --- структура тома
        all_t = '\n'.join(texts.get(i, '') for i in sorted(texts)[:6])
        n_des = len(runs)
        if n_des >= 2 and not re.search(r'(?im)^\s*Содержание(\s+тома)?\s*$|Содержание тома', all_t):
            C.append(cand(d['path'], None, 'contents_missing', f'В файле {n_des} документов с разными обозначениями, но лист «Содержание» в начале не найден',
                          f'{G}, п. 9.4', 'проверить вручную'))
        t1 = '\n'.join(texts.get(i, '') for i in (1, 2))
        if runs and re.search(r'(?i)(рабоч\w+ проект|проектная документация|том\s+\d)', t1):
            if not re.search(r'(?i)(Главный инженер проекта|ГИП|Главный архитектор проекта|ГАП)', t1):
                C.append(cand(d['path'], 1, 'title_signatures', 'На титульном листе не найдена подпись главного инженера (архитектора) проекта',
                              f'{G}, п. 9.5', 'проверить вручную'))
            if not re.search(r'(?i)(Директор|Генеральный директор|Главный инженер(?! проекта)|Руководитель)', t1):
                C.append(cand(d['path'], 1, 'title_signatures', 'На титульном листе не найдена подпись руководителя или главного инженера организации',
                              f'{G}, п. 9.5', 'проверить вручную'))
    # --- стадия по пакету
    if stage_counter:
        main_stage = expected_stage or stage_counter.most_common(1)[0][0]
        for st, lst in stage_pages.items():
            if st != main_stage:
                C.append(cand(lst[0][0], lst[0][1], 'stage_mismatch', f'Стадия «{st}» отличается от стадии пакета «{main_stage}»; листы: {lst[:15]}',
                              f'{G}, прил. Д, перечисление е)', 'проверить вручную'))
    print(json.dumps({'stage_counts': dict(stage_counter), 'candidates': C}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    a = sys.argv[1:]
    st = None
    if '--stage' in a:
        st = a[a.index('--stage') + 1]
    main(a[0], st)
