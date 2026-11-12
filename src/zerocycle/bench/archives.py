"""Архивы пакетов ПСД: имена файлов внутри и распаковка.

tar.exe Windows (bsdtar) переводит имена в кодовую страницу ANSI (cp1251): файлы с казахскими буквами («Қ», «ө»,
«і») он пропускает с ошибкой «unreadable filename» — и при распаковке, и в списке. Поэтому:
- ZIP — zipfile: имена в UTF-8 по флагу, без флага — OEM (cp866, как пишут архиваторы на русской Windows);
- RAR — имена прямо из заголовков (RAR4 и RAR5, по одному тому): тома многотомного архива читаются по отдельности, в
  каждом есть заголовки файлов, которые в нём начинаются или продолжаются;
- TAR — tarfile (имена в UTF-8);
- распаковка RAR и 7z — 7-Zip (установлен 12.11): многотомный набор целиком, с первого тома, имена в Unicode. Без
  7-Zip — bsdtar по одному тому с копией архива под латинским именем: файл, который переходит из тома в том, так не
  собрать. RAR в обоих случаях сверяется с заголовками: чего нет или что обрезано — в потерянных.
"""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

RAR4 = b"Rar!\x1a\x07\x00"
RAR5 = b"Rar!\x1a\x07\x01\x00"
SUFFIXES = {".zip", ".rar", ".7z", ".tar"}
VOLUME = re.compile(r"(?:^|\.)part(\d+)\.rar$", re.I)  # «ПСД.part01.rar» и просто «part1.rar»


def seven_zip() -> str | None:
    """7z.exe: многотомный RAR целиком и имена в Unicode. Нет — распаковка через bsdtar по одному тому."""
    installed = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "7-Zip" / "7z.exe"
    return str(installed) if installed.exists() else shutil.which("7z")


def volumes(path: Path) -> list[Path]:
    """Тома набора, к которому относится path, по номеру: «ПСД.part1.rar», «ПСД.part2.rar»…; не том — [path]."""
    if not (m := VOLUME.search(path.name)):
        return [path]
    base = path.name[: m.start()].casefold()
    found = [(int(v.group(1)), p) for p in path.parent.iterdir() if (v := VOLUME.search(p.name))]
    return [p for _, p in sorted(found) if p.name[: VOLUME.search(p.name).start()].casefold() == base]


def set_name(name: str) -> str:
    """Имя набора томов: «ПСД.part01.rar» → «ПСД.rar», «part1.rar» → «part.rar»; у обычного архива — как есть."""
    if not (m := VOLUME.search(name)):
        return name
    return (name[: m.start()] or "part") + ".rar"


def tar_exe() -> str:
    """bsdtar: на Windows — System32\\tar.exe (tar из Git — GNU tar, он не читает RAR и ZIP)."""
    system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "tar.exe"
    return str(system) if system.exists() else "tar"


def zip_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & 0x800:
        return info.filename
    try:  # zipfile без флага UTF-8 читает cp437; русские архиваторы пишут OEM cp866
        return info.filename.encode("cp437").decode("cp866")
    except UnicodeError:
        return info.filename


def _rar4_unicode(std: bytes, enc: bytes) -> str:
    """Имя RAR4 с флагом LHD_UNICODE: OEM-имя + сжатый UTF-16 (алгоритм unrar, EncodeFileName::Decode)."""
    out = bytearray()
    pos = encpos = 0

    def enc_byte() -> int:
        nonlocal encpos
        if encpos >= len(enc):
            return 0
        encpos += 1
        return enc[encpos - 1]

    def put(lo: int, hi: int) -> None:
        nonlocal pos
        out.extend((lo, hi))
        pos += 1

    hi = enc_byte()
    flags = flagbits = 0
    while encpos < len(enc):
        if flagbits == 0:
            flags, flagbits = enc_byte(), 8
        flagbits -= 2
        kind = (flags >> flagbits) & 3
        if kind == 0:
            put(enc_byte(), 0)
        elif kind == 1:
            put(enc_byte(), hi)
        elif kind == 2:
            lo = enc_byte()
            put(lo, enc_byte())
        else:
            n = enc_byte()
            if n & 0x80:
                c = enc_byte()
                for _ in range((n & 0x7F) + 2):
                    put(((std[pos] if pos < len(std) else 0x3F) + c) & 0xFF, hi)
            else:
                for _ in range(n + 2):
                    put(std[pos] if pos < len(std) else 0x3F, 0)
    return out.decode("utf-16-le", "replace")


@dataclass(frozen=True)
class Entry:
    name: str  # путь через «/»
    size: int  # распакованный размер
    split: bool  # файл переходит из тома в том: из одного тома его не распаковать


def _rar4(f) -> list[Entry]:
    out = []
    f.seek(len(RAR4))
    while len(head := f.read(7)) == 7:
        _, kind, flags, size = struct.unpack("<HBHH", head)
        if size < 7:
            break
        body = f.read(size - 7)
        add = 0
        if kind in (0x74, 0x7A) and len(body) >= 25:  # файл; служебный блок
            add, unp = struct.unpack("<II", body[0:8])
            start = 25
            if flags & 0x100:  # размеры больше 4 ГБ
                hi_add, hi_unp = struct.unpack("<II", body[25:33])
                add, unp, start = add | hi_add << 32, unp | hi_unp << 32, 33
            if kind == 0x74 and flags & 0xE0 != 0xE0:  # 0xE0 — папка
                raw = body[start : start + struct.unpack("<H", body[19:21])[0]]
                if flags & 0x200:
                    std, _, enc = raw.partition(b"\0")
                    name = _rar4_unicode(std, enc) if enc else raw.decode("utf-8", "replace")
                else:
                    name = raw.decode("cp866", "replace")
                out.append(Entry(name.replace("\\", "/"), unp, bool(flags & 0x03)))
        elif (kind == 0x73 and flags & 0x80) or kind == 0x7B:  # заголовки зашифрованы; конец архива
            break
        elif flags & 0x8000:
            add = struct.unpack("<I", body[0:4])[0]
        f.seek(add, 1)
    return out


def _vint(buf: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            break
    return value, pos


def _rar5(f) -> list[Entry]:
    out = []
    f.seek(len(RAR5))
    while len(f.read(4)) == 4:  # CRC32 заголовка
        size_bytes = bytearray()
        while b := f.read(1):
            size_bytes.append(b[0])
            if not b[0] & 0x80:
                break
        size, _ = _vint(bytes(size_bytes), 0)
        head = f.read(size)
        if len(head) < size or size == 0:
            break
        kind, pos = _vint(head, 0)
        flags, pos = _vint(head, pos)
        data = 0
        if flags & 1:
            _, pos = _vint(head, pos)  # размер extra
        if flags & 2:
            data, pos = _vint(head, pos)
        if kind == 2:  # файл
            fflags, pos = _vint(head, pos)
            unp, pos = _vint(head, pos)
            _, pos = _vint(head, pos)  # атрибуты
            pos += 4 * bool(fflags & 2) + 4 * bool(fflags & 4)  # время, CRC
            _, pos = _vint(head, pos)  # сжатие
            _, pos = _vint(head, pos)  # ОС
            n, pos = _vint(head, pos)
            if not fflags & 1:  # 1 — папка
                name = head[pos : pos + n].decode("utf-8", "replace")
                out.append(Entry(name.replace("\\", "/"), unp, bool(flags & 0x18)))  # 0x08, 0x10 — из тома в том
        elif kind in (4, 5):  # заголовки зашифрованы; конец архива
            break
        f.seek(data, 1)
    return out


def rar_entries(path: Path) -> list[Entry]:
    """Файлы тома RAR — из заголовков, без распаковки."""
    with path.open("rb") as f:
        sig = f.read(8)
        if sig.startswith(RAR5):
            return _rar5(f)
        if sig.startswith(RAR4):
            return _rar4(f)
    raise ValueError(f"{path.name}: не RAR")


def rar_members(path: Path) -> list[str]:
    return list(dict.fromkeys(e.name for e in rar_entries(path)))


def members(path: Path) -> list[str]:
    """Пути файлов внутри архива (через «/»); у многотомного RAR — только этого тома."""
    suffix = path.suffix.lower()
    if suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            return [zip_name(i) for i in z.infolist() if not i.is_dir()]
    if suffix == ".rar":
        return rar_members(path)
    if suffix == ".tar":
        with tarfile.open(path, encoding="utf-8", errors="replace") as t:
            return [m.name for m in t.getmembers() if m.isfile()]
    out = _bsdtar(path, "-tf")  # 7z: имена — в cp1251, казахские буквы теряются
    return [x for x in out.stdout.decode("cp1251", "replace").splitlines() if x and not x.endswith("/")]


def _bsdtar(path: Path, *args: str) -> subprocess.CompletedProcess:
    """bsdtar получает путь в ANSI: архив с казахскими буквами в имени он не откроет — даём копию с латинским именем."""
    if str(path).isascii():
        return subprocess.run([tar_exe(), *args, str(path)], capture_output=True)
    tmp = Path(tempfile.mkdtemp(prefix="zc_arch_"))
    try:
        copy = tmp / ("archive" + path.suffix.lower())
        shutil.copyfile(path, copy)
        return subprocess.run([tar_exe(), *args, str(copy)], capture_output=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _seven_zip_x(path: Path, into: Path) -> subprocess.CompletedProcess:
    # пароль-заглушка: зашифрованный архив даст ошибку, а не вопрос в консоли
    cmd = [seven_zip(), "x", "-y", "-bd", "-bso0", "-bsp0", "-pzc-no-password", f"-o{into}", str(path)]
    return subprocess.run(cmd, capture_output=True, stdin=subprocess.DEVNULL)


def _check_rar(entries: list[Entry], into: Path, whole: bool) -> list[str]:
    """Сверка распакованного с заголовками RAR: нет файла или не тот размер — потерян, обрезок удаляется.

    whole — набор томов распакован целиком (7-Zip); иначе файл, который переходит из тома в том, потерян всегда.
    """
    lost = []
    base = into.resolve()
    for e in {e.name: e for e in entries}.values():
        p = (into / e.name).resolve()
        if not p.is_relative_to(base):  # «..» в имени: такое не распаковывается, и мы не трогаем
            lost.append(f"{e.name}: путь вне архива")
        elif (e.split and not whole) or not p.is_file() or p.stat().st_size != e.size:
            p.unlink(missing_ok=True)
            lost.append(e.name + (": из тома в том" if e.split and not whole else ": не распакован"))
    return lost


def extract(path: Path, into: Path) -> list[str]:
    """Распаковать в новую пустую папку into. Возвращает потерянное: файлы архива, которые целиком не распаковались.

    Многотомный RAR распаковывается с первого тома: 7-Zip сам находит остальные. Распакованное сверяется с заголовками
    всех томов набора: чего нет (тома не выложены, казахские буквы у bsdtar) или что обрезано — в потерянных.
    """
    into.mkdir(parents=True)
    lost: list[str] = []
    suffix = path.suffix.lower()
    if suffix in (".rar", ".7z") and seven_zip():
        r = _seven_zip_x(path, into)
        if suffix == ".rar":
            lost += _check_rar([e for v in volumes(path) for e in rar_entries(v)], into, whole=True)
        elif r.returncode:
            lost += [x for x in r.stderr.decode("utf-8", "replace").splitlines() if x.strip()]
    elif suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                info.filename = zip_name(info)  # zipfile сам отбрасывает «..» и абсолютные пути
                try:
                    z.extract(info, into)
                except (RuntimeError, zipfile.BadZipFile, OSError) as e:  # зашифрован, повреждён
                    lost.append(f"{info.filename}: {e}")
    elif suffix == ".tar":  # bsdtar прочёл бы имена в UTF-8 как cp1251
        with tarfile.open(path, encoding="utf-8", errors="replace") as t:
            for m in t.getmembers():
                try:
                    t.extract(m, into, filter="data")  # фильтр отбрасывает «..», абсолютные пути и ссылки наружу
                except (tarfile.TarError, OSError) as e:
                    lost.append(f"{m.name}: {e}")
    else:
        r = _bsdtar(path, "-C", str(into), "-xf")  # без 7-Zip: по одному тому
        if suffix == ".rar":
            lost += _check_rar(rar_entries(path), into, whole=False)
        elif r.returncode:
            lost += [x for x in r.stderr.decode("cp1251", "replace").splitlines() if "Error exit" not in x]
    base = into.resolve()
    for p in list(into.rglob("*")):
        if p.is_symlink():
            p.unlink()
        elif not p.resolve().is_relative_to(base):
            raise ValueError(f"{path.name}: путь вне папки распаковки: {p}")
    return list(dict.fromkeys(lost))


def is_archive(name: str) -> bool:
    return PurePosixPath(name).suffix.lower() in SUFFIXES
