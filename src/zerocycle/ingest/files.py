"""Файлы пакета: папка или zip → пути, sha256 и тип по содержимому.

Пакет приходит из чужих рук, поэтому zip распаковывается с ограничениями: без абсолютных путей и «..»,
не больше MAX_FILES файлов и MAX_BYTES распакованного объёма. Вложенные архивы не раскрываются.
"""

from __future__ import annotations

import contextlib
import hashlib
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from zerocycle.model import Media

MAX_FILES = 20_000
MAX_BYTES = 20 * 1024**3  # 20 ГБ — пакет ПД с моделями бывает большим, но не бесконечным
SKIP = {"thumbs.db", ".ds_store", "desktop.ini"}


class PackageError(Exception):
    """Пакет нельзя принять: путь не найден, архив опасен или повреждён."""


@dataclass(frozen=True)
class SourceFile:
    path: str  # внутри пакета через «/»
    abs_path: Path
    sha256: str
    size: int
    media: Media


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sniff(path: Path) -> Media:
    """Тип файла по первым байтам; для zip — по содержимому (xlsx, docx или просто архив)."""
    with path.open("rb") as f:
        head = f.read(64)
    if head.startswith(b"%PDF-"):
        return Media.pdf
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
        except zipfile.BadZipFile:
            return Media.other
        if "xl/workbook.xml" in names:
            return Media.xlsx
        if "word/document.xml" in names:
            return Media.docx
        return Media.zip
    if head.startswith(b"ISO-10303-21"):
        return Media.ifc
    if head.removeprefix(b"\xef\xbb\xbf").lstrip().startswith(b"<"):
        return Media.xml
    return Media.other


def safe_extract(archive: Path, target: Path) -> Path:
    """Распаковать zip в target с проверкой путей и объёма."""
    try:
        z = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as e:
        raise PackageError(f"архив повреждён: {archive.name}") from e
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) > MAX_FILES:
            raise PackageError(f"в архиве больше {MAX_FILES} файлов")
        if sum(i.file_size for i in infos) > MAX_BYTES:
            raise PackageError("распакованный объём архива больше допустимого")
        root = target.resolve()
        for info in infos:
            name = _member_name(info)
            rel = PurePosixPath(name)
            if rel.is_absolute() or ".." in rel.parts or ":" in rel.parts[0]:
                raise PackageError(f"опасный путь в архиве: {name}")
            dest = (root / Path(*rel.parts)).resolve()
            if not dest.is_relative_to(root):
                raise PackageError(f"опасный путь в архиве: {name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, dest.open("wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
    return root


def _member_name(info: zipfile.ZipInfo) -> str:
    """Имя файла в zip: без флага UTF-8 кириллица из Windows-архиваторов записана в cp866."""
    name = info.filename
    if not info.flag_bits & 0x800:
        with contextlib.suppress(UnicodeEncodeError, UnicodeDecodeError):
            name = name.encode("cp437").decode("cp866")
    return name.replace("\\", "/")


def open_package(path: Path, workdir: Path | None = None) -> tuple[Path, list[SourceFile]]:
    """Папка или zip → корень пакета на диске и список файлов (пути — от корня, через «/»)."""
    path = Path(path)
    if path.is_dir():
        root = path
    elif path.is_file() and zipfile.is_zipfile(path):
        target = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="zerocycle-"))
        root = safe_extract(path, target)
    else:
        raise PackageError(f"не папка и не zip: {path}")
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.name.lower() in SKIP:
            continue
        files.append(
            SourceFile(
                path=p.relative_to(root).as_posix(),
                abs_path=p,
                sha256=sha256_file(p),
                size=p.stat().st_size,
                media=sniff(p),
            )
        )
    return root, files
