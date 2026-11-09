"""Реальный публичный слой КР: закупки строительных работ на zakupki.gov.kg и их файлы (docs/bench/REAL_KG.md).

Портал на PrimeFaces: список объявлений — таблица с AJAX-пагинацией (все 1,1 млн объявлений, свежие первыми),
карточка — view.xhtml?id=…; файлы карточки — download?key=…. Часть файлов отдаётся без входа, часть — только
зарегистрированному поставщику: такие помечаем и не трогаем.

    python -I -X utf8 scripts/zakupki_kg.py orders <orders.jsonl> --pages 400 [--rows 100] [--start 0]
    python -I -X utf8 scripts/zakupki_kg.py cards  <orders.jsonl> <var/real/zakupki_kg> [--min-som 10000000]
                                                   [--match RE] [--limit 200]
    python -I -X utf8 scripts/zakupki_kg.py fetch  <var/real/zakupki_kg> [id …] [--only RE]

Только публичные страницы, пауза между запросами, повтор при обрыве; скачанное заново не качается. Страницы и файлы —
недоверенные данные: страницы разбираются регулярными выражениями, файлы не открываются и не распаковываются здесь.
Документы третьих лиц храним в var/ (вне git).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import http.cookiejar
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://zakupki.gov.kg/popp"
LIST = BASE + "/view/order/list.xhtml"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) zerocycle-research/0.1"
DELAY = 1.5
MAX_FILE = 300 * 1024**2
TABLE = "j_idt124:j_idt125:table"
FORM = "j_idt124"
# строительство и ремонт зданий и сооружений; названия бывают на кыргызском: курулуш — строительство, оңдоо — ремонт
CONSTRUCTION = (
    r"строител|реконструк|капитальн\w*\s+ремонт|ремонт\w*\s+(?:здани|школ|больниц|детск|корпус)|курулуш|оңдоо|ондоо"
)

_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))
_opener.addheaders = [("User-Agent", UA), ("Accept-Language", "ru")]
_last = 0.0


def _open(req, tries: int = 4):
    global _last
    for k in range(tries):
        pause = DELAY - (time.monotonic() - _last)
        if pause > 0:
            time.sleep(pause)
        _last = time.monotonic()
        try:
            return _opener.open(req, timeout=120)
        except (urllib.error.URLError, TimeoutError) as e:
            if k == tries - 1:
                raise
            print(f"  повтор через {10 * (k + 1)} с: {e}", file=sys.stderr)
            time.sleep(10 * (k + 1))
    raise RuntimeError("unreachable")


def get(url: str) -> str:
    with _open(url) as r:
        return r.read().decode("utf-8", errors="replace")


def text(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()


def _viewstate(page: str) -> str:
    form = re.search(rf'<form id="{FORM}".*?</form>', page, re.S)
    m = re.search(r'name="javax\.faces\.ViewState"[^>]*value="([^"]*)"', form.group(0) if form else page)
    return html.unescape(m.group(1)) if m else ""


LABELS = (
    ("number", "№"),
    ("org", "Наименование организации"),
    ("type", "Вид закупок"),
    ("name", "Наименование закупки"),
    ("method", "Метод закупок"),
    ("amount", "Планируемая сумма"),
    ("published", "Дата опубликования"),
    ("deadline", "Срок подачи предложений поставщиков"),
)


def _rows(fragment: str) -> list[dict]:
    out = []
    for r in re.findall(r"<tr[^>]*data-ri[^>]*>(.*?)</tr>", fragment, re.S):
        ids = re.findall(r"view\.xhtml\?id=(\d+)", r)
        rec: dict = {"id": ids[0] if ids else None}
        for c in (text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)):
            for key, label in LABELS:
                if c.startswith(label + " "):
                    rec[key] = c[len(label) + 1 :].strip()
        digits = re.sub(r"[^\d,.]", "", rec.get("amount", "")).replace(",", ".")
        rec["amount_som"] = float(digits) if re.fullmatch(r"\d+(?:\.\d+)?", digits or "") else None
        out.append(rec)
    return out


def cmd_orders(out: Path, pages: int, rows: int, start: int) -> None:
    first = get(LIST)
    vs = _viewstate(first)
    total = re.search(r"rowCount:(\d+)", first)
    print("всего объявлений в таблице:", total.group(1) if total else "?", flush=True)
    seen = set()
    if out.exists():
        seen = {json.loads(x)["id"] for x in out.read_text(encoding="utf-8").splitlines() if x.strip()}
    with out.open("a", encoding="utf-8") as f:
        for p in range(start, start + pages):
            data = {
                "javax.faces.partial.ajax": "true",
                "javax.faces.source": TABLE,
                "javax.faces.partial.execute": TABLE,
                "javax.faces.partial.render": TABLE,
                "javax.faces.behavior.event": "page",
                "javax.faces.partial.event": "page",
                f"{TABLE}_pagination": "true",
                f"{TABLE}_first": str(p * rows),
                f"{TABLE}_rows": str(rows),
                f"{TABLE}_skipChildren": "true",
                f"{TABLE}_encodeFeature": "true",
                FORM: FORM,
                "javax.faces.ViewState": vs,
            }
            req = urllib.request.Request(
                LIST,
                data=urllib.parse.urlencode(data).encode(),
                headers={
                    "Faces-Request": "partial/ajax",
                    "X-Requested-With": "XMLHttpRequest",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            try:
                with _open(req) as r:
                    found = _rows(r.read().decode("utf-8", "replace"))
            except Exception as e:
                print(f"страница {p}: ошибка {e}", flush=True)
                continue
            new = [r for r in found if r["id"] and r["id"] not in seen]
            for r in new:
                seen.add(r["id"])
                r["page"] = p
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            works = sum(1 for r in found if r.get("type") == "Работы")
            print(f"страница {p}: строк {len(found)}, новых {len(new)}, работ {works}", flush=True)
            if not found:
                break


def card_files(page: str) -> list[dict]:
    """Файлы карточки: имя, ссылка и открыт ли файл без входа (пустой key — «нужна регистрация»)."""
    files = []
    pattern = r"<a[^>]*(href=\"[^\"]*download\?key=([^\"]*)\"|onclick=\"alert\('[^']*'\)[^\"]*\")[^>]*>(.*?)</a>"
    for m in re.finditer(pattern, page, re.S):
        key = m.group(2)
        before = text(page[max(0, m.start() - 300) : m.start()])
        files.append(
            {
                "name": text(m.group(3)) or before[-80:],
                "url": f"{BASE}/download?key={key}" if key else None,
                "open": bool(key),
            }
        )
    return files


def cmd_cards(orders: Path, root: Path, min_som: float, match: str, limit: int) -> None:
    rx = re.compile(match, re.I)
    recs = [json.loads(x) for x in orders.read_text(encoding="utf-8").splitlines() if x.strip()]
    chosen = [
        r
        for r in recs
        if r.get("type") == "Работы" and (r.get("amount_som") or 0) >= min_som and rx.search(r.get("name") or "")
    ]
    chosen.sort(key=lambda r: -(r.get("amount_som") or 0))
    print(f"работ по строительству от {min_som:,.0f} сом: {len(chosen)}; смотрим {min(limit, len(chosen))}", flush=True)
    for r in chosen[:limit]:
        d = root / r["id"]
        man_path = d / "manifest.json"
        if man_path.exists():
            continue
        page = get(f"{BASE}/view/order/view.xhtml?id={r['id']}")
        files = card_files(page)
        d.mkdir(parents=True, exist_ok=True)
        manifest = {**r, "source": f"{BASE}/view/order/view.xhtml?id={r['id']}", "files": files}
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        opened = [f["name"] for f in files if f["open"]]
        print(
            f"{r['id']} | {r.get('amount', '')[:16]:>16} | открыто {len(opened)}, по входу "
            f"{len(files) - len(opened)} | {(r.get('name') or '')[:70]} | {opened[:4]}",
            flush=True,
        )


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
        if length and size != int(length):
            raise ValueError(f"оборвано: {size} из {length} байт")
        rec.update({"file": f"files/{dest.name}", "size": size, "sha256": h.hexdigest(), "status": "ok"})
        print(f"  {size >> 10:>8} КБ  {rec['name']}", flush=True)
    except Exception as e:
        dest.unlink(missing_ok=True)
        rec["status"] = f"ошибка: {e}"[:200]
        print(f"  ошибка: {rec['name']}: {e}", flush=True)


def cmd_fetch(root: Path, ids: list[str], only: str | None) -> None:
    rx = re.compile(only, re.I) if only else None
    for d in sorted(p for p in root.iterdir() if p.is_dir() and (not ids or p.name in ids)):
        man_path = d / "manifest.json"
        if not man_path.exists():
            continue
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        wanted = [f for f in manifest["files"] if f["open"] and (not rx or rx.search(f["name"]))]
        if not wanted:
            continue
        (d / "files").mkdir(exist_ok=True)
        print(f"== {d.name}: {manifest.get('name', '')[:90]} — берём {len(wanted)}", flush=True)
        for f in wanted:
            dest = d / "files" / _safe(f["name"])
            if not (dest.exists() and f.get("sha256")):
                _download(f["url"], dest, f)
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Закупки строительных работ на zakupki.gov.kg и их файлы")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("orders")
    p.add_argument("out", type=Path)
    p.add_argument("--pages", type=int, default=10)
    p.add_argument("--rows", type=int, default=100)
    p.add_argument("--start", type=int, default=0)
    p = sub.add_parser("cards")
    p.add_argument("orders", type=Path)
    p.add_argument("root", type=Path)
    p.add_argument("--min-som", type=float, default=10_000_000)
    p.add_argument("--match", default=CONSTRUCTION)
    p.add_argument("--limit", type=int, default=200)
    p = sub.add_parser("fetch")
    p.add_argument("root", type=Path)
    p.add_argument("ids", nargs="*")
    p.add_argument("--only")
    a = ap.parse_args()
    if a.cmd == "orders":
        cmd_orders(a.out, a.pages, a.rows, a.start)
    elif a.cmd == "cards":
        cmd_cards(a.orders, a.root, a.min_som, a.match, a.limit)
    else:
        cmd_fetch(a.root, a.ids, a.only)


if __name__ == "__main__":
    main()
