"""Архивы пакетов ПСД: имена из заголовков RAR4 и RAR5, многотомный RAR, ZIP без флага UTF-8, TAR."""

import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from zerocycle.bench import archives
from zerocycle.bench.archives import (
    RAR4,
    RAR5,
    _rar4_unicode,
    extract,
    members,
    rar_entries,
    set_name,
    seven_zip,
    volumes,
)

SPLIT_BEFORE, SPLIT_AFTER = 0x08, 0x10  # флаги заголовка RAR5: продолжение из прошлого тома, продолжается в следующем


def vint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def rar5_block(fields: bytes, data: bytes = b"") -> bytes:
    body = vint(len(fields)) + fields
    return zlib.crc32(body).to_bytes(4, "little") + body + data


def rar5(files: list[tuple[str, bytes, int, int]], volume: int | None = None, last: bool = True) -> bytes:
    """RAR5 без сжатия: (имя, данные этого тома, полный размер, флаги заголовка); volume — номер тома с 0."""
    flags = 0 if volume is None else 0x0001 | (0x0002 if volume else 0)
    out = RAR5 + rar5_block(vint(1) + vint(0) + vint(flags) + (vint(volume) if volume else b""))
    for name, data, size, head_flags in files:
        raw = name.encode("utf-8")
        head = vint(2) + vint(0x02 | head_flags) + vint(len(data)) + vint(0) + vint(size) + vint(0x20)
        head += vint(0) + vint(2) + vint(len(raw)) + raw  # сжатие 0 — хранение
        out += rar5_block(head, data)
    return out + rar5_block(vint(5) + vint(0) + vint(0 if last else 1))


AR = b"%PDF-1.4 AR album"


def two_volumes(folder: Path) -> None:
    """«ПСД.part1.rar» + «ПСД.part2.rar»: АР переходит из тома в том, у ГП казахская буква в имени."""
    folder.mkdir(parents=True, exist_ok=True)
    part1 = [("ПСД/ОПЗ.pdf", b"%PDF opz", 8, 0), ("ПСД/АР.pdf", AR[:7], len(AR), SPLIT_AFTER)]
    part2 = [("ПСД/АР.pdf", AR[7:], len(AR), SPLIT_BEFORE), ("ПСД/Қ ГП.pdf", b"%PDF gp", 7, 0)]
    (folder / "ПСД.part1.rar").write_bytes(rar5(part1, volume=0, last=False))
    (folder / "ПСД.part2.rar").write_bytes(rar5(part2, volume=1))


def test_rar5_names_sizes_and_split(tmp_path: Path):
    two_volumes(tmp_path)
    p = tmp_path / "ПСД.part1.rar"
    assert [(e.name, e.size, e.split) for e in rar_entries(p)] == [
        ("ПСД/ОПЗ.pdf", 8, False),
        ("ПСД/АР.pdf", len(AR), True),
    ]
    assert members(tmp_path / "ПСД.part2.rar") == ["ПСД/АР.pdf", "ПСД/Қ ГП.pdf"]
    assert volumes(tmp_path / "ПСД.part2.rar") == [p, tmp_path / "ПСД.part2.rar"]
    assert set_name("ПСД.part01.rar") == "ПСД.rar" and set_name("ПСД.zip") == "ПСД.zip"
    assert set_name("part7.rar") == "part.rar" and set_name("Тамарикс.part3.rar") == "Тамарикс.rar"


@pytest.mark.skipif(not seven_zip(), reason="нужен 7-Zip")
def test_seven_zip_extracts_whole_volume_set(tmp_path: Path):
    two_volumes(tmp_path / "src")
    assert extract(tmp_path / "src" / "ПСД.part1.rar", tmp_path / "out") == []
    assert (tmp_path / "out" / "ПСД" / "АР.pdf").read_bytes() == AR
    assert (tmp_path / "out" / "ПСД" / "Қ ГП.pdf").read_bytes() == b"%PDF gp"


def test_without_seven_zip_split_file_is_lost(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(archives, "seven_zip", lambda: None)
    two_volumes(tmp_path / "src")
    lost = extract(tmp_path / "src" / "ПСД.part1.rar", tmp_path / "out")
    assert "ПСД/АР.pdf: из тома в том" in lost
    assert not (tmp_path / "out" / "ПСД" / "АР.pdf").exists()  # обрезок не остаётся


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
