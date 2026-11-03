#!/usr/bin/env python3
"""Генератор тестового пакета ПСД с заранее заложенными дефектами (для проверки скриптов).

    python make_test_package.py <out_package_dir>

Мини-писатель PDF без внешних шрифтов: Helvetica + /Differences + /ToUnicode (кириллица
извлекается pdfplumber корректно; при рендере глифы кириллицы могут не отображаться — для
тестов текстового слоя это неважно). Ожидаемые дефекты перечислены в EXPECTED внизу файла.
"""
import os, sys, zlib

MM = 72 / 25.4
EXTRA = ('АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдежзийклмнопрстуфхцчшщъыьэюяЁё'
         'ӘәҒғҚқҢңӨөҰұҮүҺһІі«»№–—²³')
CODE = {ch: 128 + i for i, ch in enumerate(EXTRA)}


def enc(s):
    out = bytearray()
    for ch in s:
        if ch in CODE:
            out.append(CODE[ch])
        elif 32 <= ord(ch) < 127:
            out.append(ord(ch))
        else:
            out.append(ord('?'))
    return out.hex()


def tounicode():
    lines = ['/CIDInit /ProcSet findresource begin', '12 dict begin', 'begincmap',
             '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
             '/CMapName /Adobe-Identity-UCS def', '/CMapType 2 def',
             '1 begincodespacerange <00> <FF> endcodespacerange']
    pairs = [(c, c) for c in range(32, 127)] + [(code, ord(ch)) for ch, code in CODE.items()]
    for i in range(0, len(pairs), 100):
        chunk = pairs[i:i + 100]
        lines.append(f'{len(chunk)} beginbfchar')
        lines += [f'<{c:02X}> <{u:04X}>' for c, u in chunk]
        lines.append('endbfchar')
    lines += ['endcmap', 'CMapName currentdict /CMap defineresource pop', 'end', 'end']
    return '\n'.join(lines).encode()


class Page:
    def __init__(self, w_mm, h_mm):
        self.w, self.h = w_mm * MM, h_mm * MM
        self.ops = []
        self.image = False

    def text(self, x_mm, y_mm, s, size=9):
        """x, y — от левого ВЕРХНЕГО угла, мм (y — базовая линия)."""
        self.ops.append(f'BT /F1 {size} Tf {x_mm * MM:.2f} {self.h - y_mm * MM:.2f} Td <{enc(s)}> Tj ET')

    def lines(self, x_mm, y_mm, s, size=9, step=5):
        for i, ln in enumerate(s.split('\n')):
            self.text(x_mm, y_mm + i * step, ln, size)

    def rect(self, x_mm, y_mm, w_mm, h_mm, lw=0.5):
        self.ops.append(f'{lw} w {x_mm * MM:.2f} {self.h - (y_mm + h_mm) * MM:.2f} {w_mm * MM:.2f} {h_mm * MM:.2f} re S')

    def hline(self, x0, y, x1, lw=0.3):
        self.ops.append(f'{lw} w {x0 * MM:.2f} {self.h - y * MM:.2f} m {x1 * MM:.2f} {self.h - y * MM:.2f} l S')

    def signature(self, x_mm, y_mm):
        x, y = x_mm * MM, self.h - y_mm * MM
        self.ops.append(f'0.4 w {x:.1f} {y:.1f} m {x + 8:.1f} {y + 6:.1f} {x + 14:.1f} {y - 4:.1f} {x + 30:.1f} {y + 3:.1f} c S')

    def scan(self):
        self.image = True
        self.ops.append(f'q {self.w - 40:.1f} 0 0 {self.h - 40:.1f} 20 20 cm /Im1 Do Q')


def write_pdf(path, pages):
    objs = []

    def add(b):
        objs.append(b)
        return len(objs)

    cmap = tounicode()
    tu = add(b'<< /Length %d >>stream\n' % len(cmap) + cmap + b'\nendstream')
    diffs = ' '.join(f'/uni{ord(ch):04X}' for ch in EXTRA)
    widths = ' '.join(['556'] * (256 - 32))
    font = add(f'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /FirstChar 32 /LastChar 255 '
               f'/Widths [{widths}] /Encoding << /Type /Encoding /BaseEncoding /WinAnsiEncoding '
               f'/Differences [128 {diffs}] >> /ToUnicode {tu} 0 R >>'.encode())
    raw = bytes((i * 7 + j * 3) % 256 for i in range(60) for j in range(40))
    imgz = zlib.compress(raw)
    img = add(b'<< /Type /XObject /Subtype /Image /Width 40 /Height 60 /ColorSpace /DeviceGray '
              b'/BitsPerComponent 8 /Filter /FlateDecode /Length %d >>stream\n' % len(imgz) + imgz + b'\nendstream')
    pages_id = len(objs) + 1
    add(b'')  # placeholder for Pages
    kids = []
    for p in pages:
        content = '\n'.join(p.ops).encode()
        c = add(b'<< /Length %d >>stream\n' % len(content) + content + b'\nendstream')
        res = f'/Font << /F1 {font} 0 R >>' + (f' /XObject << /Im1 {img} 0 R >>' if p.image else '')
        kids.append(add(f'<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {p.w:.2f} {p.h:.2f}] '
                        f'/Resources << {res} >> /Contents {c} 0 R >>'.encode()))
    objs[pages_id - 1] = f'<< /Type /Pages /Kids [{" ".join(f"{k} 0 R" for k in kids)}] /Count {len(kids)} >>'.encode()
    cat = add(f'<< /Type /Catalog /Pages {pages_id} 0 R >>'.encode())
    out = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offs = []
    for i, b in enumerate(objs, 1):
        offs.append(len(out))
        out += f'{i} 0 obj\n'.encode() + b + b'\nendobj\n'
    xref = len(out)
    out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode()
    out += b''.join(f'{o:010d} 00000 n \n'.encode() for o in offs)
    out += f'trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'wb').write(out)


# ---------- основные надписи ----------
def stamp_form3_5(p, des, obj, bld, title, stage, sheet, sheets, roles, org='ТОО «ПроектСтрой»', h=55):
    W, H = p.w / MM, p.h / MM
    x0, y0 = W - 5 - 185, H - 5 - h
    p.rect(x0, y0, 185, h)
    p.text(x0 + 2, y0 + 4, 'Изм. Кол.уч Лист №док. Подп. Дата', 6)
    p.text(x0 + 70, y0 + 6, des, 10)
    p.text(x0 + 70, y0 + 14, obj, 7)
    p.text(x0 + 70, y0 + 20, bld, 7)
    for i, (role, name, date, sig) in enumerate(roles):
        y = y0 + 16 + i * 5
        p.text(x0 + 2, y, role, 6)
        if name:
            p.text(x0 + 19, y, name, 6)
        if sig:
            p.signature(x0 + 33, y - 1)
        if date:
            p.text(x0 + 57, y, date, 6)
    p.text(x0 + 70, y0 + h - 15, title, 7)
    p.text(x0 + 140, y0 + h - 22, 'Стадия', 6)
    p.text(x0 + 157, y0 + h - 22, 'Лист', 6)
    p.text(x0 + 170, y0 + h - 22, 'Листов', 6)
    if stage:
        p.text(x0 + 142, y0 + h - 16, stage, 8)
    if sheet is not None:
        p.text(x0 + 159, y0 + h - 16, str(sheet), 8)
    if sheets is not None:
        p.text(x0 + 173, y0 + h - 16, str(sheets), 8)
    p.text(x0 + 142, y0 + h - 5, org, 7)


def stamp_form6(p, des, sheet):
    W, H = p.w / MM, p.h / MM
    x0, y0 = W - 5 - 185, H - 5 - 15
    p.rect(x0, y0, 185, 15)
    p.text(x0 + 2, y0 + 12, 'Изм. Кол.уч Лист №док. Подп. Дата', 6)
    p.text(x0 + 80, y0 + 9, des, 10)
    p.text(x0 + 176, y0 + 4, 'Лист', 6)
    if sheet is not None:
        p.text(x0 + 177, y0 + 11, str(sheet), 8)


def pageno(p, n):
    p.text(p.w / MM - 15, 10, str(n), 9)


OBJ = 'Строительство детского сада на 280 мест в г. Косшы Акмолинской области'
OBJ_BAD = 'Строительство детского сада на 260 мест в г. Косшы'
BLD = 'Здание детского сада'
ROLES_OK = [('Разраб.', 'Иванов', '05.24', True), ('Пров.', 'Петров', '05.24', True),
            ('Н.контр.', 'Ким', '05.24', True), ('ГИП', 'Сидоров', '05.24', True)]


def build(out):
    # --- ИРД
    p = Page(210, 297)
    p.lines(25, 30, 'УТВЕРЖДАЮ\nРуководитель ГУ «Управление строительства»\n_________ Ахметов А.А.\n«10» января 2024 г.')
    p.lines(25, 70, f'ЗАДАНИЕ НА ПРОЕКТИРОВАНИЕ\nОбъект: {OBJ}\nВид строительства: новое строительство\nСтадия: рабочий проект')
    write_pdf(f'{out}/01_ИРД/Задание на проектирование.pdf', [p])
    p = Page(210, 297)
    p.lines(25, 30, f'АРХИТЕКТУРНО-ПЛАНИРОВОЧНОЕ ЗАДАНИЕ (АПЗ) № KZ12VUA00012345\nна проектирование объекта: {OBJ}\nЭтажность: 2')
    write_pdf(f'{out}/01_ИРД/АПЗ.pdf', [p])
    p = Page(210, 297)
    p.lines(25, 30, f'ТЕХНИЧЕСКИЕ УСЛОВИЯ № 45 на электроснабжение\nобъекта: {OBJ}\nРазрешенная мощность 250 кВт')
    write_pdf(f'{out}/01_ИРД/ТУ_электроснабжение.pdf', [p])
    p = Page(210, 297)
    p.scan()
    write_pdf(f'{out}/01_ИРД/Лицензия_скан.pdf', [p])

    # --- Том 1 ПЗ (A4): титул, содержание, состав проекта, ПЗ 4 листа
    pages = []
    p = Page(210, 297)  # титульный лист — без основной надписи, без номера
    p.lines(30, 30, 'ТОО «ПроектСтрой»\nГосударственная лицензия ГСЛ № 01234 от 01.02.2015')
    p.lines(30, 90, f'{OBJ}\nРАБОЧИЙ ПРОЕКТ\nТом 1\nОбщая пояснительная записка\n2024-15-ПЗ', 11, 8)
    p.lines(30, 200, 'Директор ____________ Нурланов Б.К.\nГлавный инженер проекта ____________ Сидоров В.В.')
    pages.append(p)
    p = Page(210, 297)
    pageno(p, 2)
    p.lines(25, 25, 'Содержание\n2024-15-ПЗ.С Содержание тома 2\n2024-15-СП Состав проекта 3\n2024-15-ПЗ Пояснительная записка 4\n2024-15-ПЗ.Р Расчеты 9', 9, 6)
    stamp_form3_5(p, '2024-15-ПЗ.С', OBJ, BLD, 'Содержание тома', 'РП', None, None, ROLES_OK, h=40)
    pages.append(p)
    p = Page(210, 297)
    pageno(p, 3)
    p.lines(25, 25, 'Состав проекта\n1 2024-15-ПЗ Общая пояснительная записка\n2 2024-15-АР Архитектурные решения\n'
                    '3 2024-15-КЖ Конструкции железобетонные\n4 2024-15-СМ Сметная документация', 9, 6)
    stamp_form3_5(p, '2024-15-СП', OBJ, BLD, 'Состав проекта', 'РП', None, None, ROLES_OK, h=40)
    pages.append(p)
    body = [
        f'1. Общие сведения\nОбъект: {OBJ}\nОснование: задание на проектирование, АПЗ № KZ12VUA00012345,\nтехнические условия № 45 на электроснабжение.',
        'Проект разработан в соответствии с СН РК 1.02-03-2011* и СН РК 1.02-03-2022,\nСП РК 2.04-01-2017, СН РК 2.04-01-\n2011, ГОСТ 21.101-97, СТ РК 1.5-2008.',
        'Пожарная безопасность: СН РК 2.02-01-2019, СП РК 2.02-101-2014.\nОтопление: СНиП РК 4.02-42-2006.',
        'Технико-экономические показатели\nПлощадь застройки 1 520,4 м2\nОбщая площадь здания 3 850,0 м2\n'
        'Строительный объем 18 600 м3\nЭтажность 2\nСметная стоимость 2 405 000 тыс. тенге',
    ]
    sheet_nums = [1, 2, 4, 5]          # дефект: пропущен лист 3
    tr_nums = [4, 5, 7, 8]             # дефект: сквозная нумерация прыгает 5 -> 7
    for i, txt in enumerate(body):
        p = Page(210, 297)
        pageno(p, tr_nums[i])
        p.lines(25, 25, txt, 9, 6)
        if i == 0:   # дефект: Листов 5 при 4 фактических; Н.контр. без фамилии и даты
            stamp_form3_5(p, '2024-15-ПЗ', OBJ, BLD, 'Пояснительная записка', 'РП', 1, 5,
                          [ROLES_OK[0], ROLES_OK[1], ('Н.контр.', None, None, False), ROLES_OK[3]], h=40)
        else:
            stamp_form6(p, '2024-15-ПЗ', sheet_nums[i])
        pages.append(p)
    write_pdf(f'{out}/Том 1 ПЗ.pdf', pages)

    # --- Том 2 АР (A3 альбомный), лист 4 — нестандартный 400×297
    pages = []
    specs = [('Общие данные', 'РП', 1, 4), ('План 1 этажа', 'РП', 2, None), ('Фасады', 'П', 3, None), ('Разрез 1-1', None, 4, None)]
    for i, (title, st, sh, shs) in enumerate(specs):
        p = Page(420 if i < 3 else 400, 297)
        if i == 0:
            p.lines(25, 25, 'Ведомость рабочих чертежей основного комплекта\nТехнико-экономические показатели\n'
                            'Площадь застройки 1 520,4 м2\nОбщая площадь здания 3 905,0 м2\nЭтажность 2', 9, 6)
        p.text(25, 150, title, 12)
        stamp_form3_5(p, '2024-15-АР', OBJ_BAD if i == 0 else OBJ, BLD, title, st, sh, shs,
                      ROLES_OK if i else [ROLES_OK[0], ('Пров.', 'Петров', None, True), ROLES_OK[2], ROLES_OK[3]])
        pages.append(p)
    write_pdf(f'{out}/Том 2 АР.pdf', pages)

    # --- Том 4 СМ (A4 альбомный)
    pages = []
    p = Page(297, 210)
    p.lines(20, 20, 'Пояснительная записка к сметной документации\nСметная стоимость строительства 2 450 000 тыс. тенге\n'
                    'Сметная документация составлена в соответствии с НДЦС РК 8.01-08-2022.', 9, 6)
    stamp_form3_5(p, '2024-15-СМ', OBJ, BLD, 'Сметная документация', 'РП', 1, 2, ROLES_OK, h=40)
    pages.append(p)
    p = Page(297, 210)
    p.lines(20, 20, 'Сводный сметный расчет стоимости строительства\nВсего по сводному расчету 2 450 000 тыс. тенге', 9, 6)
    stamp_form6(p, '2024-15-СМ', 2)
    pages.append(p)
    write_pdf(f'{out}/Том 4 СМ.pdf', pages)


EXPECTED = """
Том 1 ПЗ.pdf: лист 3 пропущен (seq 1,2,4,5); Листов=5 при 4; Н.контр. без фамилии/даты; сквозная нумерация 5->7;
  ссылки: СН РК 1.02-03-2011 (заменён на 1.02-03-2022), СН РК 2.02-01-2019 (заменён), СП РК 2.02-101-2014 (заменён),
  СН РК 2.04-01-2011 (перенос строки, действует), СНиП РК 4.02-42-2006 (проверить по АГСК), ГОСТ/СТ РК — вне АГСК;
  Содержание ссылается на 2024-15-ПЗ.Р — отсутствует; Состав проекта: Том 3 2024-15-КЖ отсутствует в пакете.
Том 2 АР.pdf: стадия «П» на листе 3, пустая стадия на листе 4, формат 400×297 нестандартный, наименование объекта
  «на 260 мест» на листе 1; Пров. без даты; общая площадь 3 905,0 против 3 850,0 в ПЗ.
Том 4 СМ.pdf: сметная стоимость 2 450 000 против 2 405 000 в ПЗ.
ИРД: нет правоустанавливающего документа на землю, нет инженерных изысканий, нет ТУ на воду/тепло и т.д.,
  нет решения об инвестировании, нет расчёта стоимости ПИР, нет прайс-листов, нет сводной ведомости потребности;
  лицензия — скан без текста.
"""

if __name__ == '__main__':
    build(sys.argv[1])
    print('ok', sys.argv[1])
