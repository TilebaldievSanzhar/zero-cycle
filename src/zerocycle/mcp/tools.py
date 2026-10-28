"""Инструменты агента над прогонами проверки: запустить проверку, листать находки, объяснить, показать фрагмент листа.

Сессия живёт, пока жив процесс сервера: прогоны — в памяти, отчёты — по желанию на диск (`runs_dir`). Проверка
большого пакета идёт минуты, поэтому она запускается в фоне (`start`), а ждут её с пределом (`wait`): клиент MCP
не упирается в таймаут вызова, агент спрашивает статус ещё раз.

Находка для агента — в схеме ответа рук бенчмарка: категория, документ, страница, описание, основание
(docs/bench/PROTOCOL.md, «Руки») — плюс id, статус, тяжесть и правило.
"""

from __future__ import annotations

import io
import json
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zerocycle.model import Anchor, Finding, Package, Status
from zerocycle.norms.designation import find_designations
from zerocycle.packs import available
from zerocycle.rules.cards import FAMILY, KIND, SEVERITY, STATUS
from zerocycle.rules.engine import Report, bases, load_rules, run

MAX_LIST = 200  # находок за один вызов list_findings
SUMMARY_VIOLATED = 25  # нарушений в сводке check_package; остальные — list_findings
RENDER_SCALE = 2.0  # 144 dpi: читаются графы основной надписи
RENDER_MAX_PX = 2000  # длинная сторона картинки
RENDER_MARGIN = 36.0  # поле вокруг рамки находки, пт (0,5 дюйма)
VERDICT = {
    "current": "действует",
    "outdated_edition": "действует более новая редакция",
    "replaced": "документ заменён",
    "not_listed": "нет в индексе",
    "not_covered": "вид документа индекс не перечисляет",
    "no_year": "год не указан — действующая редакция по индексу",
}


@dataclass
class RunRecord:
    id: str
    path: Path
    jurisdiction: str
    work_kind: str | None
    started: datetime
    future: Future
    package: Package | None = None
    report: Report | None = None
    files: dict[str, Path] = field(default_factory=dict)
    seconds: float | None = None
    error: str | None = None

    @property
    def state(self) -> str:
        if not self.future.done():
            return "running"
        return "failed" if self.error else "done"


def finding_view(fid: str, f: Finding) -> dict[str, Any]:
    """Находка в схеме ответа рук бенчмарка: категория, документ, страница, описание, основание."""
    first = f.anchors[0] if f.anchors else None
    pages = sorted({a.page for a in f.anchors if a.page})
    return {
        "id": fid,
        "status": f.status.value,
        "severity": f.severity.value,
        "category": FAMILY[f.family.value],
        "document": first.file if first else None,
        "page": first.page if first else None,
        "pages": pages,
        "description": f.message,
        "basis": f.basis,
        "rule": f.rule,
    }


def _anchor_view(a: Anchor) -> dict[str, Any]:
    return {k: v for k, v in a.model_dump().items() if v not in (None, "") and k != "sha256"}


class Session:
    def __init__(self, root: Path | None = None, runs_dir: Path | None = None) -> None:
        self.root = Path(root).resolve() if root else None
        self.runs_dir = Path(runs_dir) if runs_dir else None
        self.runs: dict[str, RunRecord] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="zerocycle-check")
        self._tmp: list[tempfile.TemporaryDirectory] = []  # распакованные zip — пока жива сессия
        self._lock = threading.Lock()

    # --- проверка пакета -----------------------------------------------------------------------------------------

    def resolve(self, path: str) -> Path:
        """Путь пакета от агента: относительный — от корня сессии; вне корня — отказ (рука видит только пакет)."""
        p = Path(path).expanduser()
        if not p.is_absolute() and self.root is not None:
            p = self.root / p
        p = p.resolve()
        if self.root is not None and not p.is_relative_to(self.root):
            raise ValueError(f"путь вне разрешённой папки {self.root}: {p}")
        if not p.exists():
            raise FileNotFoundError(f"нет такого пакета: {p}")
        return p

    def start(self, path: str, jurisdiction: str = "ru", work_kind: str | None = None) -> str:
        if jurisdiction not in available():
            raise ValueError(f"нет пакета юрисдикции «{jurisdiction}»; есть: {', '.join(available())}")
        if work_kind not in (None, "new", "reconstruction", "capital_repair"):
            raise ValueError("вид работ: new, reconstruction или capital_repair")
        target = self.resolve(path)
        with self._lock:
            run_id = f"r{len(self.runs) + 1}"
            fut: Future = Future()
            rec = RunRecord(run_id, target, jurisdiction, work_kind, datetime.now(UTC), fut)
            self.runs[run_id] = rec
        rec.future = self._pool.submit(self._check, rec)
        return run_id

    def _check(self, rec: RunRecord) -> None:
        from zerocycle.packs import context as pack_context
        from zerocycle.rules.context import Context
        from zerocycle.scan import scan

        t0 = time.perf_counter()
        try:
            if rec.path.is_dir():
                workdir, root = None, rec.path
            else:
                tmp = tempfile.TemporaryDirectory(prefix="zerocycle-mcp-")
                self._tmp.append(tmp)
                workdir = root = Path(tmp.name)
            pkg = scan(rec.path, pack_context(rec.jurisdiction), workdir=workdir)
            files = {d.path: root / d.path for d in pkg.documents}
            params = {"work_kind": rec.work_kind} if rec.work_kind else {}
            ctx = Context(pkg, jurisdiction=rec.jurisdiction, files=files, params=params)
            rec.package, rec.files, rec.report = pkg, files, run(ctx)
            if self.runs_dir is not None:
                self.runs_dir.mkdir(parents=True, exist_ok=True)
                out = self.runs_dir / f"{rec.started:%Y%m%dT%H%M%S}-{rec.id}-{rec.path.name}.json"
                out.write_text(rec.report.model_dump_json(indent=1), encoding="utf-8")
        except Exception as e:  # ошибка проверки — в статус прогона, а не обрыв сервера
            rec.error = f"{type(e).__name__}: {e}"
        finally:
            rec.seconds = round(time.perf_counter() - t0, 1)

    def wait(self, run_id: str, timeout: float | None = None) -> bool:
        rec = self._run(run_id)
        try:
            rec.future.result(timeout=timeout)
        except TimeoutError:
            return False
        return True

    def _run(self, run_id: str | None) -> RunRecord:
        if not self.runs:
            raise ValueError("проверок ещё не было: начните с check_package")
        rec = self.runs.get(run_id) if run_id else self.runs[list(self.runs)[-1]]
        if rec is None:
            raise ValueError(f"нет прогона {run_id}; есть: {', '.join(self.runs)}")
        return rec

    def _done(self, run_id: str | None) -> RunRecord:
        rec = self._run(run_id)
        if rec.state == "running":
            raise ValueError(f"прогон {rec.id} ещё идёт — спросите run_status")
        if rec.error:
            raise ValueError(f"прогон {rec.id} упал: {rec.error}")
        return rec

    def _findings(self, rec: RunRecord) -> list[tuple[str, Finding]]:
        return [(f"F{i}", f) for i, f in enumerate(rec.report.findings, 1)]

    def summary(self, run_id: str | None = None) -> dict[str, Any]:
        """Сводка прогона: что найдено, что движок проверить не смог, какие правила не применялись."""
        rec = self._run(run_id)
        head: dict[str, Any] = {"run_id": rec.id, "state": rec.state, "path": str(rec.path)}
        if rec.state == "running":
            elapsed = (datetime.now(UTC) - rec.started).total_seconds()
            return {**head, "elapsed_seconds": round(elapsed), "next": f"run_status(run_id='{rec.id}')"}
        if rec.error:
            return {**head, "error": rec.error}
        report, pkg = rec.report, rec.package
        found = self._findings(rec)
        counts = {s.value: sum(f.status == s for _, f in found) for s in Status}
        by_family: dict[str, int] = {}
        for _, f in found:
            if f.status == Status.violated:
                by_family[FAMILY[f.family.value]] = by_family.get(FAMILY[f.family.value], 0) + 1
        pages = [p for d in pkg.documents for p in d.pages]
        return {
            **head,
            "package": pkg.root,
            "jurisdiction": report.jurisdiction,
            "standard": report.edition,
            "work_kind": report.work_kind,
            "rule_packs": report.packs,
            "documents": len(pkg.documents),
            "pages": len(pages),
            "pages_without_text": sum(not p.has_text for p in pages),
            "pages_with_title_block": sum(p.title_block is not None for p in pages),
            "seconds": rec.seconds,
            "counts": counts,
            "violated_by_category": by_family,
            "violated": [finding_view(i, f) for i, f in found if f.status == Status.violated][:SUMMARY_VIOLATED],
            "not_checked": [
                {"id": i, "rule": f.rule, "description": f.message} for i, f in found if f.status == Status.cannot_check
            ],
            "rules_not_applicable": [{"rule": c.rule, "reason": c.reason} for c in report.coverage if not c.applicable],
            "rule_errors": [{"rule": c.rule, "error": c.error} for c in report.coverage if c.error],
            "withheld_by_gate": len(report.withheld),
            "next": (
                f"list_findings(run_id='{rec.id}', status='manual') — что проверить человеку; "
                "explain_finding(finding_id) — основание и формулировка эксперта; "
                "render_fragment(finding_id) — лист с рамкой находки"
            ),
        }

    # --- находки -------------------------------------------------------------------------------------------------

    def list_findings(
        self,
        run_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
        rule: str | None = None,
        document: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        rec = self._done(run_id)
        rows = []
        for fid, f in self._findings(rec):
            if status and f.status.value != status:
                continue
            if category and category not in (f.family.value, FAMILY[f.family.value]):
                continue
            if rule and not f.rule.startswith(rule):
                continue
            if document and not any(document in a.file for a in f.anchors):
                continue
            rows.append(finding_view(fid, f))
        limit = max(1, min(limit, MAX_LIST))
        return {"run_id": rec.id, "total": len(rows), "offset": offset, "findings": rows[offset : offset + limit]}

    def explain_finding(self, finding_id: str, run_id: str | None = None) -> dict[str, Any]:
        rec = self._done(run_id)
        fid, f = self._finding(rec, finding_id)
        rules = {r.id: r for r in load_rules(rec.jurisdiction)}
        r = rules.get(f.rule)
        known = bases()
        basis = []
        for b in f.basis:
            doc = b.split(",", 1)[0].strip()
            info = known.get(doc, {})
            basis.append({"text": b, "document": doc, "title": info.get("title"), "verified": info.get("verified")})
        on_sheet = any(a.page for a in f.anchors)
        out = {
            **finding_view(fid, f),
            "values": f.values,
            "anchors": [_anchor_view(a) for a in f.anchors],
            "basis_documents": basis,
            "next": f"render_fragment(finding_id='{fid}', run_id='{rec.id}')" if on_sheet else None,
        }
        if r is not None:
            out["rule_card"] = {
                "id": r.id,
                "version": r.version,
                "title": r.title,
                "kind": KIND[r.kind],
                "why": r.why,
                "expert_wording": r.remark,
                "evidence": list(r.evidence),
                "review": "черновик — не проверено практиком" if r.review == "draft" else r.review,
            }
        return out

    def _finding(self, rec: RunRecord, finding_id: str) -> tuple[str, Finding]:
        found = dict(self._findings(rec))
        key = finding_id.strip().upper()
        key = key if key.startswith("F") else f"F{key}"
        if key not in found:
            raise ValueError(f"нет находки {finding_id} в прогоне {rec.id}: id от F1 до F{len(found)}")
        return key, found[key]

    # --- фрагмент листа ------------------------------------------------------------------------------------------

    def render_fragment(
        self,
        finding_id: str | None = None,
        run_id: str | None = None,
        file: str | None = None,
        page: int | None = None,
        anchor: int = 0,
    ) -> tuple[bytes, dict[str, Any]]:
        """PNG фрагмента листа: рамка находки (красным) с полем вокруг; без рамки — лист целиком."""
        import pypdfium2 as pdfium
        from PIL import ImageDraw

        rec = self._done(run_id)
        bbox = None
        if finding_id:
            fid, f = self._finding(rec, finding_id)
            paged = [a for a in f.anchors if a.page]
            if not paged:
                where = _anchor_view(f.anchors[0]) if f.anchors else "нет"
                raise ValueError(f"у находки {fid} нет листа: якорь — {where}")
            a = paged[min(anchor, len(paged) - 1)]
            file, page, bbox = a.file, a.page, a.bbox
        if not file or not page:
            raise ValueError("нужен finding_id или file и page")
        path = rec.files.get(file)
        if path is None:
            raise ValueError(f"нет файла {file} в пакете прогона {rec.id}")
        pdf = pdfium.PdfDocument(path)
        try:
            if not 1 <= page <= len(pdf):
                raise ValueError(f"в файле {file} страниц {len(pdf)}, а не {page}")
            img = pdf[page - 1].render(scale=RENDER_SCALE).to_pil().convert("RGB")
        finally:
            pdf.close()
        crop = (0, 0, img.width, img.height)
        if bbox:
            s = RENDER_SCALE
            box = [round(v * s) for v in bbox]
            ImageDraw.Draw(img).rectangle(box, outline=(220, 30, 30), width=max(2, round(s * 1.5)))
            m = round(RENDER_MARGIN * s)
            crop = (max(0, box[0] - m), max(0, box[1] - m), min(img.width, box[2] + m), min(img.height, box[3] + m))
        img = img.crop(crop)
        if max(img.size) > RENDER_MAX_PX:
            k = RENDER_MAX_PX / max(img.size)
            img = img.resize((round(img.width * k), round(img.height * k)))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), {"file": file, "page": page, "bbox": bbox, "size": list(img.size)}

    # --- правила и нормы -----------------------------------------------------------------------------------------

    def list_rules(self, jurisdiction: str = "ru") -> dict[str, Any]:
        if jurisdiction not in available():
            raise ValueError(f"нет пакета юрисдикции «{jurisdiction}»; есть: {', '.join(available())}")
        from zerocycle.packs import context as pack_context

        edition = pack_context(jurisdiction).standard
        rules = []
        for r in load_rules(jurisdiction):
            applies = {}
            if r.applies.jurisdictions:
                applies["jurisdictions"] = list(r.applies.jurisdictions)
            if r.applies.work_kinds:
                applies["work_kinds"] = list(r.applies.work_kinds)
            rules.append(
                {
                    "id": r.id,
                    "title": r.title,
                    "category": FAMILY[r.family.value],
                    "kind": KIND[r.kind],
                    "severity": SEVERITY[r.severity.value],
                    "basis": list(r.basis_for(edition)),
                    "applies": applies or "везде",
                    "review": r.review,
                }
            )
        return {
            "jurisdiction": jurisdiction,
            "standard": edition,
            "rules": rules,
            "scope": (
                "Только формальные проверки: комплектность, оформление основных надписей и листов, увязка "
                "наименований, обозначений и ТЭП, ссылки на нормы. Содержание решений, расчёты и графику движок "
                "не оценивает — это остаётся агенту и ГИПу."
            ),
        }

    def norm_status(self, designation: str, jurisdiction: str = "ru") -> dict[str, Any]:
        """Действует ли документ по индексу юрисдикции: все обозначения норм в строке."""
        if jurisdiction == "ru":
            from zerocycle.packs.ru.rules import INDEX_BASE, registry

            index, base = registry(), INDEX_BASE
        elif jurisdiction == "kz":
            from zerocycle.packs.kz.rules import INDEX_BASE, agsk1

            index, base = agsk1(), INDEX_BASE
        else:
            raise ValueError(f"индекса норм для «{jurisdiction}» нет: есть ru и kz")
        found = find_designations(designation)
        if not found:
            raise ValueError(f"в строке нет обозначения нормы: «{designation}»")
        out = []
        for d, _, _ in found:
            v = index.verdict(d)
            cur = v.current
            row: dict[str, Any] = {
                "designation": d.code,
                "as_written": d.text,
                "status": v.status,
                "meaning": VERDICT[v.status],
            }
            if d.issues:
                row["issues"] = list(d.issues)
            if cur is not None:
                row["current"] = {"code": cur.code, "title": cur.title, "since": cur.since}
                row["basis"] = cur.basis or (f"{base}, с. {cur.page}" if cur.page else base)
            if v.status == "not_listed" and not index.exhaustive:
                row["note"] = "реестр перечисляет только документы с обязательными требованиями: «нет» ≠ «не действует»"
            out.append(row)
        return {
            "index": index.source,
            "as_of": index.as_of.isoformat(),
            "exhaustive": index.exhaustive,
            "results": out,
        }

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        for tmp in self._tmp:
            tmp.cleanup()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=str)


__all__ = ["STATUS", "Session", "finding_view"]
