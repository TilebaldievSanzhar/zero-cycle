"""Local-only paths shared by the offline review utilities (Python 3)."""
from pathlib import Path
import hashlib
import json
import os
import stat

ROOT = Path.cwd().resolve()


def is_link(path):
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def local(path, *, exists=False):
    """Reject lexical escape AND any existing symlink/junction component."""
    p = Path(os.path.abspath(str(path)))
    try:
        relative = p.relative_to(ROOT)
    except ValueError:
        raise ValueError("Path must stay inside the current working directory")
    current = ROOT
    for part in relative.parts:
        current /= part
        if current.exists() or current.is_symlink():
            if is_link(current):
                raise ValueError("Symlink/junction/reparse point is not followed: " + str(current))
    resolved = p.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError("Resolved path leaves the current working directory")
    if exists and not p.exists():
        raise FileNotFoundError(p)
    return p


def package_file(path):
    p = local(path, exists=True)
    package = local(ROOT / "package", exists=True)
    if not p.is_relative_to(package) or not p.is_file():
        raise ValueError("Input must be a file inside package/")
    return p


def reference_dir():
    return local(Path(__file__).parent.parent / "references", exists=True)


def work_dir(path="review_work"):
    p = local(path)
    if p == ROOT or any(p.is_relative_to(ROOT / x) for x in ("package", ".agents", "skills_out", "sources")):
        raise ValueError("Use a separate scratch directory inside cwd, outside package/ and skills")
    marker = p / ".kz-review-work.json"
    if p.exists() and any(p.iterdir()):
        if not marker.is_file() or read_json(marker).get("owner") != "kz-rp-review-v1":
            raise ValueError("Output directory is not empty and was not created by this utility")
    p.mkdir(parents=True, exist_ok=True)
    write_json(marker, {"owner": "kz-rp-review-v1"})
    return p


def write_json(path, value):
    p = local(path)
    p.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(local(path, exists=True).read_text(encoding="utf-8-sig"))


def json_lines(path):
    with local(path, exists=True).open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def digest(path):
    result = hashlib.sha256()
    with local(path, exists=True).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
