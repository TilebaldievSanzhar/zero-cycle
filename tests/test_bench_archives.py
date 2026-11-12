"""Архивы пакетов ПСД: имена из заголовков RAR4 и RAR5, ZIP без флага UTF-8."""

import struct
import zipfile
from pathlib import Path

from zerocycle.bench.archives import RAR4, RAR5, _rar4_unicode, extract, members, rar_entries


def vint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def rar5_block(fields: bytes, data: bytes = b"") -> bytes:
    return b"\0\0\0\0" + vint(len(fields)) + fields + data  # CRC не проверяем


def rar5(files: list[tuple[str, bytes, int]]) -> bytes:
    """Том RAR5 без сжатия: (имя, данные, флаги заголовка — 0x10 «продолжается в следующем томе»)."""
    out = RAR5 + rar5_block(vint(1) + vint(0) + vint(0))  # главный заголовок
    for name, data, flags in files:
        raw = name.encode("utf-8")
        head = vint(2) + vint(0x02 | flags) + vint(len(data)) + vint(0) + vint(len(data)) + vint(0)
        head += vint(0) + vint(0) + vint(len(raw)) + raw
        out += rar5_block(head, data)
    return out + rar5_block(vint(5) + vint(0) + vint(0))  # конец архива


def test_rar5_names_sizes_and_split(tmp_path: Path):
    p = tmp_path / "ПСД.part1.rar"
    p.write_bytes(rar5([("Том 1/ОПЗ.pdf", b"%PDF opz", 0), ("Том 2/Қастеев ӨЖ.pdf", b"%PDF half", 0x10)]))
    entries = rar_entries(p)
    assert [(e.name, e.size, e.split) for e in entries] == [
        ("Том 1/ОПЗ.pdf", 8, False),
        ("Том 2/Қастеев ӨЖ.pdf", 9, True),
    ]
    assert members(p) == ["Том 1/ОПЗ.pdf", "Том 2/Қастеев ӨЖ.pdf"]


def encode_rar4_unicode(name: str) -> bytes:
    """Простейшая запись имени RAR4: каждый символ — два байта UTF-16 (вид 2)."""
    units = name.encode("utf-16-le")
    out = bytearray([0])  # старший байт по умолчанию
    pairs = [units[i : i + 2] for i in range(0, len(units), 2)]
    for i in range(0, len(pairs), 4):
        out.append(0xAA)
        for pair in pairs[i : i + 4]:
            out += pair
    return bytes(out)


def test_rar4_unicode_name():
    name = "СД парк Жетісу.pdf"
    std = name.encode("cp866", "replace")
    assert _rar4_unicode(std, encode_rar4_unicode(name)) == name


def test_rar4_file_header(tmp_path: Path):
    name = "Смета Қ.pdf"
    raw = name.encode("cp866", "replace") + b"\0" + encode_rar4_unicode(name)
    data = b"%PDF"
    body = struct.pack("<IIBIIBBHI", len(data), len(data), 2, 0, 0, 29, 0x30, len(raw), 0x20) + raw
    head = struct.pack("<HBHH", 0, 0x74, 0x8000 | 0x200, 7 + len(body)) + body
    main = struct.pack("<HBHH", 0, 0x73, 0, 13) + b"\0" * 6
    p = tmp_path / "a.rar"
    p.write_bytes(RAR4 + main + head + data + struct.pack("<HBHH", 0, 0x7B, 0, 7))
    [entry] = rar_entries(p)
    assert (entry.name, entry.size, entry.split) == (name, 4, False)


def test_zip_without_utf8_flag_is_cp866(tmp_path: Path):
    """Архиваторы на русской Windows пишут имена в OEM cp866 без флага UTF-8."""
    name = "Том 1 ОПЗ.pdf"
    placeholder = "X" * len(name)
    p = tmp_path / "a.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr(placeholder, b"%PDF")
    p.write_bytes(p.read_bytes().replace(placeholder.encode(), name.encode("cp866")))
    assert members(p) == [name]
    assert extract(p, tmp_path / "out") == []
    assert (tmp_path / "out" / name).read_bytes() == b"%PDF"


def test_tar_names_in_utf8(tmp_path: Path):
    import io
    import tarfile

    p = tmp_path / "Том 6- ОВОС.tar"
    with tarfile.open(p, "w", format=tarfile.GNU_FORMAT, encoding="utf-8") as t:
        info = tarfile.TarInfo("Том 6- ОВОС/РООС Қазақ.pdf")
        info.size = 4
        t.addfile(info, io.BytesIO(b"%PDF"))
    assert members(p) == ["Том 6- ОВОС/РООС Қазақ.pdf"]
    assert extract(p, tmp_path / "out") == []
    assert (tmp_path / "out" / "Том 6- ОВОС" / "РООС Қазақ.pdf").read_bytes() == b"%PDF"
