"""Реальный публичный слой (PLAN, шаг 0.11): ПСД из вложений закупок строительных работ на goszakup.gov.kz.

ПСД лежит в объявлениях во вкладке «Документация», в виде документа «Приложение 14 (Техническая спецификация
закупаемых работ в сфере строительства)». Только публичные страницы и только GET, пауза между запросами,
повтор при обрыве; уже скачанное не качается заново.

    python -I -X utf8 scripts/goszakup_kz_psd.py list  <out.json> [--pages 1]          кандидаты: объявления и их файлы
    python -I -X utf8 scripts/goszakup_kz_psd.py fetch <var/real/goszakup_kz> <id> ... файлы объявлений + manifest.json
        --pick   только срез по разделам (заключение, ОПЗ, паспорт, АР, АС/КЖ, ОВ, ВК, ЭОМ, ГП, ПОС)
        --workers N   параллельных загрузок (по умолчанию 1; файловый сервер отдаёт ~50 КБ/с на поток)

Документы третьих лиц: храним локально в var/ (вне git); в них фамилии и подписи — в корпус для разметки
только обезличенными.
"""

from __future__ import annotations

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
KEEP = {"pdf", "zip"}  # rar и 7z без внешних утилит не распаковать; сметы (xls, kenml) — позже
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


def spec_files(announce_id: str) -> list[tuple[str, str]]:
    """Файлы «Технической спецификации» объявления: [(url, имя)]."""
    html = get(f"/ru/announce/index/{announce_id}", {"tab": "documents"})
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if SPEC in tr and (m := re.search(r"actionModalShowFiles\((\d+),\s*(\d+)\)", tr)):
            modal = get(f"/ru/announce/actionAjaxModalShowFiles/{m.group(1)}/{m.group(2)}")
            return [(u, " ".join(n.split())) for u, n in FILE_HREF.findall(modal)]
    return []


def title(announce_id: str) -> str:
    html = get(f"/ru/announce/index/{announce_id}")
    m = re.search(r'Наименование объявления</label>\s*<div[^>]*>\s*<input[^>]*value="([^"]*)"', html)
    return (m.group(1) if m else "").replace("&quot;", '"')


def cmd_list(out: str, pages: int = 1) -> None:
    ids: list[str] = []
    for kw in ["строительство", "капитальный ремонт", "реконструкция"]:
        for page in range(1, pages + 1):
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
            ids += [i for i in re.findall(r"/ru/announce/index/(\d+)", html) if i not in ids]
    result = {}
    for aid in ids:
        files = spec_files(aid)
        exts: dict[str, int] = {}
        for _, n in files:
            e = n.rsplit(".", 1)[-1].lower() if "." in n else "?"
            exts[e] = exts.get(e, 0) + 1
        result[aid] = {"title": title(aid), "files": files, "exts": exts}
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
        rec.update({"file": f"files/{dest.name}", "size": size, "sha256": h.hexdigest(), "status": "ok"})
        print(f"  {size >> 10:>8} КБ  {rec['name']}", flush=True)
    except Exception as e:  # файл удалён или слишком велик — фиксируем и идём дальше
        dest.unlink(missing_ok=True)
        rec["status"] = f"ошибка: {e}"[:200]
        print(f"  ошибка: {rec['name']}: {e}", flush=True)


def cmd_fetch(root: str, ids: list[str], only_pick: bool = False, workers: int = 1) -> None:
    for aid in ids:
        d = Path(root) / aid
        (d / "files").mkdir(parents=True, exist_ok=True)
        man_path = d / "manifest.json"
        manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
        manifest.update({"announce_id": aid, "source": f"{BASE}/ru/announce/index/{aid}?tab=documents"})
        manifest.setdefault("title", title(aid))
        files = manifest.setdefault("files", {})
        listed = spec_files(aid)
        wanted = pick(listed) if only_pick else listed
        print(f"== {aid}: {manifest['title'][:100]} — файлов {len(listed)}, берём {len(wanted)}", flush=True)
        jobs = []
        for url, name in wanted:
            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            rec = files.setdefault(url, {"name": name})
            if ext not in KEEP:
                rec["status"] = f"пропущен: .{ext}"
                continue
            dest = d / "files" / _safe(name)
            if not (dest.exists() and rec.get("sha256")):
                jobs.append((url, dest, rec))
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(lambda job: _download(*job), jobs))
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    if cmd == "list":
        pages = int(rest[rest.index("--pages") + 1]) if "--pages" in rest else 1
        cmd_list(rest[0], pages)
    else:
        workers = int(rest[rest.index("--workers") + 1]) if "--workers" in rest else 1
        args = [a for i, a in enumerate(rest) if not a.startswith("--") and (i == 0 or rest[i - 1] != "--workers")]
        cmd_fetch(args[0], args[1:], only_pick="--pick" in rest, workers=workers)
