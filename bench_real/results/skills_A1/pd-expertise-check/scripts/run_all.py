#!/usr/bin/env python3
"""Запускает все автоматические проверки подряд.

    python run_all.py <package_dir> <work_dir> [--kind new|reconstruction|capital_repair|smeta_correction|special] [--stage РП]

Создаёт в <work_dir>: package.json, text/*.txt, titleblock.json, norm_refs.json, inventory.json, cross_check.json
и печатает краткую сводку. <work_dir> должен быть внутри текущей рабочей папки.
"""
import json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def run(args, out):
    with open(out, 'w', encoding='utf-8') as f:
        r = subprocess.run([sys.executable] + args, stdout=f, stderr=subprocess.PIPE, text=True)
    if r.returncode:
        print(f'!! {args[0]} завершился с ошибкой:\n{r.stderr[-2000:]}')
    return r.returncode == 0


def main():
    a = sys.argv[1:]
    pkg, work = a[0], a[1]
    os.makedirs(work, exist_ok=True)
    extra_kind = ['--kind', a[a.index('--kind') + 1]] if '--kind' in a else []
    extra_stage = ['--stage', a[a.index('--stage') + 1]] if '--stage' in a else []
    subprocess.run([sys.executable, os.path.join(HERE, 'extract_package.py'), pkg, work], check=True)
    pj = os.path.join(work, 'package.json')
    run([os.path.join(HERE, 'check_titleblocks.py'), pj] + extra_stage, os.path.join(work, 'titleblock.json'))
    run([os.path.join(HERE, 'check_norm_refs.py'), pj], os.path.join(work, 'norm_refs.json'))
    run([os.path.join(HERE, 'inventory.py'), pj] + extra_kind, os.path.join(work, 'inventory.json'))
    run([os.path.join(HERE, 'cross_check.py'), pj], os.path.join(work, 'cross_check.json'))
    try:
        tb = json.load(open(os.path.join(work, 'titleblock.json'), encoding='utf-8'))
        nr = json.load(open(os.path.join(work, 'norm_refs.json'), encoding='utf-8'))
        inv = json.load(open(os.path.join(work, 'inventory.json'), encoding='utf-8'))
        cc = json.load(open(os.path.join(work, 'cross_check.json'), encoding='utf-8'))
    except Exception as e:
        print('Не удалось прочитать результаты:', e)
        return
    print('\n== Оформление: кандидатов', len(tb['candidates']), '; стадии', tb['stage_counts'])
    print('== Ссылки на НД:', dict(nr['summary']))
    print('== Вид строительства:', inv['kind_used'], '(угадано:', inv['kind_guess'], ')')
    print('   не найдено пунктов чек-листа:', [i['id'] for i in inv['items'] if i['verdict_hint'] == 'НЕ НАЙДЕН'])
    print('   Состав проекта — отсутствуют:', [m for r in inv['sostav_proekta'] for m in r['missing']])
    print('== Наименование объекта — вариантов:', len(cc['object_name_variants']))
    print('   ТЭП с расхождениями:', [k for k, v in cc['tep'].items() if v['conflict']])


if __name__ == '__main__':
    main()
