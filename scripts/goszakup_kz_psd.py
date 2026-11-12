"""Реальный публичный слой (PLAN, шаг 0.11): ПСД из вложений закупок строительных работ на goszakup.gov.kz.

ПСД лежит в объявлениях во вкладке «Документация», в виде документа «Приложение 14 (Техническая спецификация
закупаемых работ в сфере строительства)». Только публичные страницы и только GET, пауза между запросами,
повтор при обрыве; уже скачанное не качается заново.

    python -I -X utf8 scripts/goszakup_kz_psd.py list  <out.json> [--pages 1]          кандидаты: объявления и их файлы
    python -I -X utf8 scripts/goszakup_kz_psd.py fetch <var/real/goszakup_kz> <id> ... файлы объявлений + manifest.json
        --pick   только срез по разделам (заключение, ОПЗ, паспорт, АР, АС/КЖ, ОВ, ВК, ЭОМ, ГП, ПОС)
        --only RE     только файлы, чьё имя подходит под выражение, например заключения: --only "заключ|қорытынд"
        --from-list F объявления и их файлы — из вывода list (без повторных запросов страниц)
        --workers N   параллельных загрузок (по умолчанию 1; файловый сервер отдаёт ~50 КБ/с на поток)
        --all-ext     файлы любых расширений (пакет V3 целиком: архивы, сметы, xlsx), а не только pdf и zip

Документы третьих лиц: храним локально в var/ (вне git); в них фамилии и подписи — в корпус для разметки
только обезличенными.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://old.goszakup.gov.kz"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/129.0 Safari/537.36 zerocycle-research/0.1"
DELAY = 1.2
KEEP = {"pdf", "zip"}  # по умолчанию; --all-ext — пакет целиком (RAR и 7z распаковывает tar.exe Windows)
MAX_FILE = 300 * 1024**2
SPEC = "Техническая спецификация"
FILE_HREF = re.compile(r'href="(https://v3bl\.goszakup\.gov\.kz/files/download_file/[^"]+)"[^>]*>\s*([^<]*)<')

# срез пакета по разделам: первое совпадение по каждому образцу, в этом порядке
PICK = [
    r"заключени",
    r"общая.пояснит|\bОПЗ\b",
    r"паспорт.проекта",
    r"архитектурные.решения|\bАР\b",
    r"архитектурно.строит|\bАС\b|\bКЖ\b|конструкц",
    r"отоплени|\bОВ\b",
    r"водопровод|водоснабжени|\bВК\b",
    r"электроосвещ|электрическое.освещ|\bЭОМ\b",
    r"генеральный.план|генплан|\bГП\b",
    r"организации.строительства|\bПОС\b",
]

_last = 0.0
_lock = threading.Lock()


def _wait() -> None:
    global _last
    with _lock:
        pause = DELAY - (time.monotonic() - _last)
        if pause > 0:
            time.sleep(pause)
        _last = time.monotonic()


def pick(files: list[tuple[str, str]]) -> list[tuple[str, str]]:
    chosen: list[tuple[str, str]] = []
    for pattern in PICK:
        rx = re.compile(pattern, re.IGNORECASE)
        for url, name in files:
            if rx.search(name.replace("_", " ")) and name.lower().endswith(".pdf") and (url, name) not in chosen:
                chosen.append((url, name))
                break
    return chosen


def _open(url: str, tries: int = 4):
    for k in range(tries):
        _wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
            return urllib.request.urlopen(req, timeout=90)
        except (urllib.error.URLError, TimeoutError) as e:
            if k == tries - 1:
                raise
            print(f"  повтор через {10 * (k + 1)} с: {e}", file=sys.stderr)
            time.sleep(10 * (k + 1))
    raise RuntimeError("unreachable")


def get(path: str, params: dict | None = None) -> str:
    url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    with _open(url) as r:
        return r.read().decode("utf-8", errors="replace")


def documents(announce_id: str) -> tuple[str, list[tuple[str, str]]]:
    """Название объявления и файлы «Технической спецификации» [(url, имя)] — с одной вкладки «Документация»."""
    html = get(f"/ru/announce/index/{announce_id}", {"tab": "documents"})
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if SPEC in tr and (m := re.search(r"actionModalShowFiles\((\d+),\s*(\d+)\)", tr)):
            modal = get(f"/ru/announce/actionAjaxModalShowFiles/{m.group(1)}/{m.group(2)}")
            return _title(html), [(u, " ".join(n.split())) for u, n in FILE_HREF.findall(modal)]
    return _title(html), []


def spec_files(announce_id: str) -> list[tuple[str, str]]:
    return documents(announce_id)[1]


def _title(html: str) -> str:
    m = re.search(r'Наименование объявления</label>\s*<div[^>]*>\s*<input[^>]*value="([^"]*)"', html)
    return (m.group(1) if m else "").replace("&quot;", '"')


def title(announce_id: str) -> str:
    return _title(get(f"/ru/announce/index/{announce_id}"))


def cmd_list(out: str, pages: int = 1) -> None:
    ids: list[str] = []
    for kw in ["строительство", "капитальный ремонт", "реконструкция"]:
        for page in range(1, pages + 1):
            try:  # портал отвечает с перебоями: страницу, что не отдалась, пропускаем — следующий запуск её доберёт
                html = get(
                    "/ru/search/announce",
                    {
                        "filter[name]": kw,
                        "filter[trade_type]": "r",
                        "filter[status][]": ["350"],
                        "filter[amount_from]": "150000000",
                        "page": str(page),
                    },
                )
            except (urllib.error.URLError, TimeoutError) as e:
                print(f"«{kw}», страница {page}: не отдалась ({e})", flush=True)
                continue
            ids += [i for i in re.findall(r"/ru/announce/index/(\d+)", html) if i not in ids]
    result = json.loads(Path(out).read_text(encoding="utf-8")) if Path(out).exists() else {}
    print(f"объявлений в выдаче: {len(ids)}, новых: {sum(1 for i in ids if i not in result)}", flush=True)
    for aid in ids:
        if aid in result:  # уже в списке с прошлого запуска
            continue
        try:
            name, files = documents(aid)
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"{aid}: не отдалось ({e})", flush=True)
            continue
        exts: dict[str, int] = {}
        for _, n in files:
            e = n.rsplit(".", 1)[-1].lower() if "." in n else "?"
            exts[e] = exts.get(e, 0) + 1
        result[aid] = {"title": name, "files": files, "exts": exts}
        print(f"{aid}: файлов {len(files):>3} {exts} | {result[aid]['title'][:90]}", flush=True)
        Path(out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" .")[:150] or "file"


def _download(url: str, dest: Path, rec: dict) -> None:
    try:
        h, size = hashlib.sha256(), 0
        with _open(url) as r, dest.open("wb") as f:
            while chunk := r.read(1 << 20):
                size += len(chunk)
                if size > MAX_FILE:
                    raise ValueError(f"больше {MAX_FILE >> 20} МБ")
                h.update(chunk)
                f.write(chunk)
            length = r.headers.get("Content-Length")
        if length and size != int(length):  # сервер обрывает поток без ошибки
            raise ValueError(f"оборвано: {size} из {length} байт")
        if dest.suffix.lower() == ".pdf" and b"%%EOF" not in dest.read_bytes()[-4096:]:
            raise ValueError(f"оборвано: {size} байт, в конце PDF нет %%EOF")
        rec.update({"file": f"files/{dest.name}", "size": size, "sha256": h.hexdigest(), "status": "ok"})
        print(f"  {size >> 10:>8} КБ  {rec['name']}", flush=True)
    except Exception as e:  # файл удалён или слишком велик — фиксируем и идём дальше
        dest.unlink(missing_ok=True)
        rec["status"] = f"ошибка: {e}"[:200]
        print(f"  ошибка: {rec['name']}: {e}", flush=True)


def select(files: list[tuple[str, str]], only_pick: bool = False, only: str | None = None) -> list[tuple[str, str]]:
    if only:
        rx = re.compile(only, re.IGNORECASE)
        files = [(u, n) for u, n in files if rx.search(n.replace("_", " "))]
    return pick(files) if only_pick else files


def cmd_fetch(
    root: str,
    ids: list[str],
    only_pick: bool = False,
    workers: int = 1,
    only: str | None = None,
    listed_by_id: dict[str, dict] | None = None,
    all_ext: bool = False,
) -> None:
    listed_by_id = listed_by_id or {}
    for aid in ids:
        known = listed_by_id.get(aid)
        listed = [tuple(f) for f in known["files"]] if known else spec_files(aid)
        wanted = select(listed, only_pick, only)
        if not wanted:
            print(f"== {aid}: подходящих файлов нет", flush=True)
            continue
        d = Path(root) / aid
        (d / "files").mkdir(parents=True, exist_ok=True)
        man_path = d / "manifest.json"
        manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
        manifest.update({"announce_id": aid, "source": f"{BASE}/ru/announce/index/{aid}?tab=documents"})
        if "title" not in manifest:
            manifest["title"] = known["title"] if known else title(aid)
        files = manifest.setdefault("files", {})
        print(f"== {aid}: {manifest['title'][:100]} — файлов {len(listed)}, берём {len(wanted)}", flush=True)
        jobs = []
        for url, name in wanted:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            rec = files.setdefault(url, {"name": name})
            if not all_ext and ext not in KEEP:
                rec["status"] = f"пропущен: .{ext}"
                continue
            dest = d / "files" / _safe(name)
            if not (dest.exists() and rec.get("sha256")):
                jobs.append((url, dest, rec))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(lambda job: _download(*job), jobs))
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="ПСД из вложений закупок СМР на goszakup.gov.kz")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("out")
    p.add_argument("--pages", type=int, default=1)
    p = sub.add_parser("fetch")
    p.add_argument("root")
    p.add_argument("ids", nargs="*")
    p.add_argument("--pick", action="store_true")
    p.add_argument("--only")
    p.add_argument("--from-list")
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--all-ext", action="store_true")
    a = ap.parse_args()
    if a.cmd == "list":
        cmd_list(a.out, a.pages)
        return
    listed = json.loads(Path(a.from_list).read_text(encoding="utf-8")) if a.from_list else {}
    ids = a.ids or [aid for aid, rec in listed.items() if select([tuple(f) for f in rec["files"]], a.pick, a.only)]
    cmd_fetch(a.root, ids, only_pick=a.pick, workers=a.workers, only=a.only, listed_by_id=listed, all_ext=a.all_ext)


if __name__ == "__main__":
    main()
