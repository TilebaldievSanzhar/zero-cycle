"""Реальный публичный слой РФ: проектная документация и заключения экспертизы из закупок строительных работ в ЕИС.

По 44-ФЗ (ст. 33, ч. 1, п. 8) проектная документация входит в описание объекта закупки на строительство, поэтому
в документах закупки часто лежат ПД (PDF по разделам или архивы) и заключение экспертизы. В заключении по ПД и
результатам изысканий есть разделы «Сведения об оперативных изменениях, внесенных заявителем … в процессе проведения
экспертизы» (4.1.3, 4.2.3) — след замечаний, как «Дополнения и изменения» в заключениях РК.

Сертификаты сайтов госсектора РФ выпускает НУЦ Минцифры; Windows ему по умолчанию не доверяет. Корневой сертификат
передаётся путём (--ca), в систему не ставится: TLS проверяется, только с этим корнем.

    python -I -X utf8 scripts/eis_ru.py search <list.json> --ca <pem> -q "строительство школы" [-q …] [--pages 1]
    python -I -X utf8 scripts/eis_ru.py docs   <list.json> --ca <pem> [--limit 50]
    python -I -X utf8 scripts/eis_ru.py fetch  <list.json> <var/real/eis_ru> --ca <pem> [--only RE] [reg …]

Только публичные страницы, пауза между запросами, повтор при обрыве; скачанное заново не качается. Ответы и файлы —
недоверенные данные: страницы разбираются регулярными выражениями, файлы не открываются и не распаковываются здесь.
Документы третьих лиц храним в var/ (вне git).
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://zakupki.gov.ru"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) zerocycle-research/0.1"
DELAY = 1.5
MAX_FILE = 400 * 1024**2
CONCLUSION = r"заключ|экспертиз"
_last = 0.0
_ctx: ssl.SSLContext | None = None


def _context(ca: str) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cafile=ca)
    return ctx


def _open(url: str, tries: int = 4):
    global _last
    for k in range(tries):
        pause = DELAY - (time.monotonic() - _last)
        if pause > 0:
            time.sleep(pause)
        _last = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ru"})
            return urllib.request.urlopen(req, context=_ctx, timeout=120)
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


def search(query: str, page: int, min_price: int) -> list[dict]:
    params = {
        "searchString": query,
        "morphology": "on",
        "fz44": "on",
        "pc": "on",
        "pa": "on",
        "sortBy": "UPDATE_DATE",
        "sortDirection": "false",
        "recordsPerPage": "_50",
        "pageNumber": str(page),
        "priceFromGeneral": str(min_price),
    }
    s = get(BASE + "/epz/order/extendedsearch/results.html?" + urllib.parse.urlencode(params))
    out = []
    blocks = re.findall(
        r'<div class="search-registry-entry-block.*?(?=<div class="search-registry-entry-block|$)', s, re.S
    )
    for block in blocks:
        link = re.search(r'href="(/epz/order/notice/[^"]*?/view/common-info\.html\?regNumber=(\d+))"', block)
        if not link:
            continue
        obj = re.search(r"Объект закупки</div>\s*<div[^>]*>(.*?)</div>", block, re.S)
        price = re.search(r"price-block__value[^>]*>(.*?)</div>", block, re.S)
        status = re.search(r"registry-entry__header-mid__title[^>]*>(.*?)</div>", block, re.S)
        out.append(
            {
                "reg": link.group(2),
                "common": link.group(1),
                "object": text(obj.group(1)) if obj else "",
                "price": text(price.group(1)) if price else "",
                "status": text(status.group(1)) if status else "",
                "query": query,
            }
        )
    return out


def documents(common: str) -> list[dict]:
    s = get(BASE + common.replace("common-info.html", "documents.html"))
    files, seen = [], set()
    for m in re.finditer(
        r'<a[^>]*href="(https?://zakupki\.gov\.ru/44fz/filestore/public/[^"]+)"[^>]*>(.*?)</a>', s, re.S
    ):
        url = html.unescape(m.group(1))
        title = re.search(r'title="([^"]*)"', m.group(0))
        name = html.unescape(title.group(1)) if title else text(m.group(2))
        if url not in seen and name:
            seen.add(url)
            files.append({"url": url, "name": name})
    return files


def _load(path: Path) -> dict[str, dict]:
    return {r["reg"]: r for r in json.loads(path.read_text(encoding="utf-8"))} if path.exists() else {}


def _save(path: Path, recs: dict[str, dict]) -> None:
    path.write_text(json.dumps(list(recs.values()), ensure_ascii=False, indent=1), encoding="utf-8")


def cmd_search(out: Path, queries: list[str], pages: int, min_price: int) -> None:
    recs = _load(out)
    for q in queries:
        for p in range(1, pages + 1):
            new = [r for r in search(q, p, min_price) if r["reg"] not in recs]
            for r in new:
                recs[r["reg"]] = r
            print(f"«{q}», страница {p}: новых {len(new)}, всего {len(recs)}", flush=True)
            _save(out, recs)


def cmd_docs(path: Path, limit: int) -> None:
    recs = _load(path)
    todo = [r for r in recs.values() if "files" not in r][:limit]
    for r in todo:
        try:
            r["files"] = documents(r["common"])
        except Exception as e:
            r["files"], r["error"] = [], str(e)[:200]
        names = [f["name"] for f in r["files"]]
        concl = [n for n in names if re.search(CONCLUSION, n, re.I)]
        print(
            f"{r['reg']} | {r['price'][:18]} | файлов {len(names):>3} | заключений {len(concl)} | {r['object'][:70]}",
            flush=True,
        )
        _save(path, recs)


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
        if dest.suffix.lower() == ".pdf" and b"%%EOF" not in dest.read_bytes()[-4096:]:
            raise ValueError(f"оборвано: {size} байт, в конце PDF нет %%EOF")
        rec.update({"file": f"files/{dest.name}", "size": size, "sha256": h.hexdigest(), "status": "ok"})
        print(f"  {size >> 10:>8} КБ  {rec['name']}", flush=True)
    except Exception as e:
        dest.unlink(missing_ok=True)
        rec["status"] = f"ошибка: {e}"[:200]
        print(f"  ошибка: {rec['name']}: {e}", flush=True)


def cmd_fetch(path: Path, root: Path, only: str | None, regs: list[str]) -> None:
    recs = _load(path)
    rx = re.compile(only, re.I) if only else None
    for reg in regs or [r for r, v in recs.items() if v.get("files")]:
        rec = recs[reg]
        wanted = [f for f in rec.get("files", []) if not rx or rx.search(f["name"])]
        if not wanted:
            continue
        d = root / reg
        (d / "files").mkdir(parents=True, exist_ok=True)
        man_path = d / "manifest.json"
        manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.exists() else {}
        manifest.update({k: rec[k] for k in ("reg", "object", "price", "status")})
        manifest["source"] = BASE + rec["common"].replace("common-info.html", "documents.html")
        files = manifest.setdefault("files", {})
        print(f"== {reg}: {rec['object'][:90]} — берём {len(wanted)}", flush=True)
        for f in wanted:
            entry = files.setdefault(f["url"], {"name": f["name"]})
            dest = d / "files" / _safe(f["name"])
            if not (dest.exists() and entry.get("sha256")):
                _download(f["url"], dest, entry)
        man_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    global _ctx
    ap = argparse.ArgumentParser(description="ПД и заключения экспертизы из закупок строительных работ в ЕИС")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search")
    p.add_argument("out", type=Path)
    p.add_argument("-q", "--query", action="append", required=True)
    p.add_argument("--pages", type=int, default=1)
    p.add_argument("--min-price", type=int, default=50_000_000)
    p = sub.add_parser("docs")
    p.add_argument("list", type=Path)
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("fetch")
    p.add_argument("list", type=Path)
    p.add_argument("root", type=Path)
    p.add_argument("regs", nargs="*")
    p.add_argument("--only")
    for p in sub.choices.values():
        p.add_argument("--ca", required=True, help="корневой сертификат НУЦ Минцифры (PEM)")
    a = ap.parse_args()
    _ctx = _context(a.ca)
    if a.cmd == "search":
        cmd_search(a.out, a.query, a.pages, a.min_price)
    elif a.cmd == "docs":
        cmd_docs(a.list, a.limit)
    else:
        cmd_fetch(a.list, a.root, a.only, a.regs)


if __name__ == "__main__":
    main()
