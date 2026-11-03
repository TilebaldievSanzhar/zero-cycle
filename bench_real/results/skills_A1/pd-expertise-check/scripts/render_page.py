#!/usr/bin/env python3
"""Рендер страницы PDF в PNG для визуальной проверки (подписи, даты, штампы, сканы).

Использование:
    python render_page.py <file.pdf> <page_from_1> <out.png> [--crop tb|tr|title|full] [--scale 2]

  --crop tb    — правый нижний угол 200×70 мм (основная надпись)
  --crop tr    — правый верхний угол 40×25 мм (номер страницы)
  --crop full  — вся страница (по умолчанию)
Затем откройте PNG инструментом Read.
"""
import sys
import pypdfium2 as pdfium

MM = 72 / 25.4


def main(path, pno, out, crop='full', scale=2.0):
    pdf = pdfium.PdfDocument(path)
    page = pdf[pno - 1]
    w, h = page.get_size()
    img = page.render(scale=scale).to_pil()
    if crop == 'tb':
        box = (w - 200 * MM, h - 70 * MM, w, h)
    elif crop == 'tr':
        box = (w - 40 * MM, 0, w, 25 * MM)
    else:
        box = (0, 0, w, h)
    box = tuple(max(0, int(v * scale)) for v in box)
    img.crop(box).save(out)
    print(out, img.size, '->', box)


if __name__ == '__main__':
    a = sys.argv[1:]
    crop = a[a.index('--crop') + 1] if '--crop' in a else 'full'
    scale = float(a[a.index('--scale') + 1]) if '--scale' in a else 2.0
    main(a[0], int(a[1]), a[2], crop, scale)
