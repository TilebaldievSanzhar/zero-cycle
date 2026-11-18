"""Inventory and extract evidence, never decide whether a norm is violated."""
import argparse
from datetime import date, datetime, time
import csv
from itertools import zip_longest
import json
from pathlib import Path
import warnings
from zipfile import ZipFile

import fitz
from lxml import etree
from openpyxl import load_workbook
from PIL import Image

from local_io import ROOT, digest, emit, is_link, local, work_dir, write_json

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}
XML_PARSER = lambda: etree.XMLParser(resolve_entities=False, no_network=True)
SUPPORTED = {".pdf", ".xlsx", ".xlsm", ".docx", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".txt", ".csv"}


def value(v):
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, float) and (v != v or abs(v) == float("inf")):
        return str(v)
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def walk(folder, records):
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name.casefold())
    except OSError as error:
        records.append({"document": folder.relative_to(ROOT).as_posix() + "/", "state": "error", "error": str(error)})
        return
    for path in entries:
        name = path.relative_to(ROOT).as_posix()
        try:
            if is_link(path):
                records.append({"document": name, "state": "skipped_link", "warning": "Target not opened; completeness cannot be confirmed"})
            elif path.is_dir():
                yield from walk(path, records)
            elif path.is_file():
                yield local(path, exists=True)
            else:
                records.append({"document": name, "state": "unsupported_entry"})
        except (OSError, ValueError) as error:
            records.append({"document": name, "state": "error", "error": str(error)})


def pdf_units(path, record):
    with fitz.open(path) as doc:
        record["pages"] = len(doc)
        if doc.needs_pass:
            raise ValueError("Encrypted PDF needs a password; no password guessing")
        record["embedded_files"] = list(doc.embfile_names())
        if record["embedded_files"]:
            record["warnings"].append("PDF attachments not extracted or reviewed")
        for n, page in enumerate(doc, 1):
            text = page.get_text("text", sort=True)
            image_count = len(page.get_images())
            flags = []
            if len(text.strip()) < 40:
                flags.append("low_text: render and inspect; not evidence of an empty page")
            if image_count:
                flags.append("contains_images: text layer may omit stamps, signatures or scans")
            yield {"kind": "pdf_page", "page": n, "locator": "PDF page " + str(n),
                   "text": text, "width_pt": page.rect.width, "height_pt": page.rect.height,
                   "rotation": page.rotation, "image_count": image_count, "warnings": flags}


def archive_notes(archive, record):
    members = archive.namelist()
    record["media_members"] = [m for m in members if "/media/" in m and not m.endswith("/")]
    record["embedded_members"] = [m for m in members if "/embeddings/" in m or m.endswith("vbaProject.bin")]
    targets = []
    for member in members:
        if member.endswith(".rels"):
            tree = etree.fromstring(archive.read(member), parser=XML_PARSER())
            for rel in tree:
                if rel.get("TargetMode") == "External":
                    targets.append({"part": member, "target": rel.get("Target")})
    record["external_relationships"] = targets
    if targets:
        record["warnings"].append("External relationships recorded as strings only; targets not opened")
    if record["embedded_members"]:
        record["warnings"].append("Embedded objects/macros not opened or executed")
    if record["media_members"]:
        record["warnings"].append("Embedded pictures require visual inspection; see render_evidence.py --member")


def excel_units(path, record):
    with ZipFile(path) as archive:
        archive_notes(archive, record)
        if any(m.startswith("xl/charts/") for m in archive.namelist()):
            record["warnings"].append("Excel charts are not rendered")
    book = cache = None
    try:
        book = load_workbook(path, read_only=True, data_only=False, keep_links=False)
        cache = load_workbook(path, read_only=True, data_only=True, keep_links=False)
        record["sheets"] = [{"name": s.title, "state": s.sheet_state} for s in book.worksheets]
        record["warnings"].append("Excel print pagination unavailable; cached formula results can be stale; formulas are not recalculated")
        for sheet, cached in zip(book.worksheets, cache.worksheets):
            # Ignore a wrong <dimension> declaration; consume actual worksheet XML rows.
            sheet.reset_dimensions()
            cached.reset_dimensions()
            for row_index, pair in enumerate(zip_longest(sheet.iter_rows(), cached.iter_rows(), fillvalue=()), 1):
                row, cached_row = pair
                cells, display = [], []
                for cell, cc in zip_longest(row, cached_row):
                    v = getattr(cell, "value", None)
                    if v is None:
                        continue
                    formula = getattr(cell, "data_type", None) == "f"
                    address = cell.coordinate
                    cv = value(getattr(cc, "value", None)) if formula else value(v)
                    item = {"cell": address, "value": value(v), "number_format": cell.number_format,
                            "data_type": cell.data_type}
                    if formula:
                        item["cached_value"] = cv
                        item["cache_missing"] = cv is None
                    cells.append(item)
                    display.append(address + "=" + str(value(v)) + (" [cached=" + str(cv) + "]" if formula else ""))
                if cells:
                    yield {"kind": "excel_row", "page": None, "locator": f"Excel {sheet.title}!row {row_index}",
                           "sheet": sheet.title, "sheet_state": sheet.sheet_state, "row": row_index,
                           "cells": cells, "text": " | ".join(display)}
    finally:
        if book is not None:
            book.close()
        if cache is not None:
            cache.close()


def docx_units(path, record):
    with ZipFile(path) as archive:
        archive_notes(archive, record)
        record["warnings"].append("Word has no reliable PDF pagination here; text, fields, tables and pictures do not reproduce page layout")
        members = sorted(m for m in archive.namelist() if m == "word/document.xml" or
                         (m.startswith("word/") and m.endswith(".xml") and
                          Path(m).name.startswith(("header", "footer", "footnotes", "endnotes", "comments"))))
        for member in members:
            tree = etree.fromstring(archive.read(member), parser=XML_PARSER())
            if tree.xpath("//w:ins | //w:del", namespaces=NS):
                record["warnings"].append(f"Tracked changes in {member}: inserted text included, deleted text excluded; review versions manually")
            for n, paragraph in enumerate(tree.iter("{" + W + "}p"), 1):
                chunks = []
                for node in paragraph.iter():
                    # Textbox paragraphs can be nested: do not count their text twice.
                    if next(node.iterancestors("{" + W + "}p"), None) is not paragraph:
                        continue
                    if any(a.tag == "{" + W + "}del" for a in node.iterancestors()):
                        continue
                    if node.tag == "{" + W + "}t":
                        chunks.append(node.text or "")
                    elif node.tag == "{" + W + "}tab":
                        chunks.append("\t")
                    elif node.tag in ("{" + W + "}br", "{" + W + "}cr"):
                        chunks.append("\n")
                text = "".join(chunks)
                if not text.strip():
                    continue
                xpath = tree.getroottree().getpath(paragraph)
                nums = paragraph.xpath("./w:pPr/w:numPr/*/@w:val", namespaces=NS)
                yield {"kind": "word_paragraph", "page": None, "locator": f"Word {member}, paragraph {n}, {xpath}",
                       "part": member, "paragraph": n, "xml_path": xpath, "numbering_properties": nums,
                       "text": text}
        record["warnings"].append("Automatic Word list labels are not synthesized; numbering_properties are only raw XML values")


def image_units(path, record):
    with Image.open(path) as picture:
        frames = getattr(picture, "n_frames", 1)
        record["frames"] = frames
        record["warnings"].append("Raster image: no OCR, inspect every relevant frame visually")
        for n in range(frames):
            picture.seek(n)
            yield {"kind": "image_frame", "page": None, "locator": f"Image frame {n+1}", "frame": n+1,
                   "width_px": picture.width, "height_px": picture.height, "text": "",
                   "warnings": ["visual_review_required"]}


def text_units(path, record):
    raw = path.read_bytes()
    # Explicit fallback, not silent character replacement.
    try:
        text = raw.decode("utf-8-sig")
        record["encoding"] = "utf-8-sig"
    except UnicodeDecodeError:
        text = raw.decode("cp1251")
        record["encoding"] = "cp1251 (fallback; verify visually)"
        record["warnings"].append("Legacy encoding guessed as cp1251")
    if path.suffix.lower() == ".csv":
        import io
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
            record["warnings"].append("CSV delimiter could not be inferred; comma fallback")
        for n, row in enumerate(csv.reader(io.StringIO(text), dialect), 1):
            yield {"kind": "csv_row", "page": None, "locator": f"CSV record {n}", "row": n,
                   "values": row, "text": " | ".join(row)}
    else:
        for n, line in enumerate(text.splitlines(), 1):
            yield {"kind": "text_line", "page": None, "locator": f"Text line {n}", "text": line}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="review_work")
    args = parser.parse_args()
    package = local(ROOT / "package", exists=True)
    if not package.is_dir():
        raise ValueError("package/ must be a directory")
    out = work_dir(args.out)
    records, count = [], 0
    # Check generated filenames separately too, in case an old output contains a link.
    units_path = local(out / "units.jsonl")
    search_path = local(out / "search_index.txt")
    with units_path.open("w", encoding="utf-8") as units, search_path.open("w", encoding="utf-8") as search:
        for path in walk(package, records):
            suffix = path.suffix.lower()
            record = {"document": path.relative_to(ROOT).as_posix(), "suffix": suffix,
                      "state": "unsupported", "warnings": [], "unit_count": 0}
            records.append(record)
            try:
                record["bytes"] = path.stat().st_size
                record["sha256"] = digest(path)
                if suffix not in SUPPORTED:
                    record["warnings"].append("Unsupported format: not inspected; absence conclusions may be blocked")
                    continue
                extractor = (pdf_units if suffix == ".pdf" else excel_units if suffix in (".xlsx", ".xlsm") else
                             docx_units if suffix == ".docx" else text_units if suffix in (".txt", ".csv") else image_units)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    try:
                        for unit in extractor(path, record):
                            count += 1
                            record["unit_count"] += 1
                            unit.update(document=record["document"], unit_id=f"u{count:07d}")
                            units.write(json.dumps(unit, ensure_ascii=False, allow_nan=False) + "\n")
                            search.write(f"\n=== {unit['unit_id']} | {unit['document']} | {unit['locator']} ===\n{unit['text']}\n")
                    finally:
                        record["warnings"].extend(str(w.message) for w in caught)
                record["state"] = "extracted"
            except Exception as error:
                record["state"] = "partial_error" if record["unit_count"] else "error"
                record["error"] = f"{type(error).__name__}: {error}"
    write_json(out / "inventory.json", {"package": "package/", "files": records})
    summary = {"entries": len(records), "units": count,
               "incomplete_entries": [r["document"] for r in records if r["state"] != "extracted"],
               "warning": "Extraction is not review coverage; no findings or absence claims are generated."}
    write_json(out / "extraction_summary.json", summary)
    emit(summary)


if __name__ == "__main__":
    main()
