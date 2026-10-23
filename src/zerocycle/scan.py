"""Опись пакета: файлы → документы с листами, основными надписями, обозначениями и данными info.xml.

Обозначение документа берётся по убыванию надёжности: из info.xml (атрибут Code), из графы 1 первой
основной надписи (у содержания тома «-С» отбрасывается), из имени файла. Расхождения между ними — не здесь,
а в правилах: опись только собирает факты.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath

from zerocycle.extract.designation import interpret, parse
from zerocycle.extract.titleblock import find_title_block
from zerocycle.ingest.files import SourceFile, open_package
from zerocycle.ingest.pde import read_info_xml
from zerocycle.ingest.pdf import iter_pdf
from zerocycle.model import Designation, Document, Media, Package, Page
from zerocycle.packs import codebook
from zerocycle.units import paper_format


def scan(path: Path, workdir: Path | None = None, jurisdiction: str = "ru") -> Package:
    cb = codebook(jurisdiction)
    _root, files = open_package(Path(path), workdir)
    pkg = Package(root=Path(path).name, jurisdiction=jurisdiction)

    info_file = next((f for f in files if f.path.lower() == "info.xml"), None)
    if info_file:
        pkg.pde = read_info_xml(info_file.abs_path, info_file.path, info_file.sha256)

    for f in files:
        if f is info_file:
            continue
        pkg.documents.append(_document(f))

    _link_pde(pkg)
    for doc in pkg.documents:
        if doc.designation is None:
            doc.designation = _designation_from_pages(doc) or _designation_from_name(doc)
        if doc.designation is not None:
            doc.section = interpret(doc.designation, cb)
        if doc.title is None:
            doc.title = _title_from_pages(doc)

    bases = Counter(d.designation.base for d in pkg.documents if d.designation and d.designation.base)
    if bases:
        pkg.base_designation = bases.most_common(1)[0][0]
    return pkg


def _document(f: SourceFile) -> Document:
    doc = Document(path=f.path, sha256=f.sha256, size=f.size, media=f.media)
    if f.media != Media.pdf:
        return doc
    try:
        for p in iter_pdf(f.abs_path):
            doc.pages.append(
                Page(
                    number=p.number,
                    width=round(p.width, 2),
                    height=round(p.height, 2),
                    paper=paper_format(p.width, p.height),
                    landscape=p.width > p.height,
                    has_text=p.has_text,
                    title_block=find_title_block(p) if p.has_text else None,
                )
            )
    except Exception as e:  # повреждённый файл не должен ронять опись пакета
        doc.issues.append(f"PDF не читается: {type(e).__name__}: {e}")
    return doc


def _link_pde(pkg: Package) -> None:
    """Документы info.xml → файлы пакета: GUID, обозначение и наименование; потерянные — в замечания."""
    if pkg.pde is None:
        return
    by_path = {d.path.lower(): d for d in pkg.documents}
    for node in pkg.pde.nodes:
        if node.kind != "document" or not node.file_name:
            continue
        doc = by_path.get(node.file_name.lower())
        if doc is None:
            pkg.pde.issues.append(f"файла из info.xml нет в пакете: {node.file_name}")
            continue
        doc.guid = node.guid
        if code := node.attrs.get("Code"):
            doc.designation = parse(code)
        doc.title = node.attrs.get("DocumentName") or doc.title
    listed = {n.file_name.lower() for n in pkg.pde.nodes if n.kind == "document" and n.file_name}
    for d in pkg.documents:
        if d.path.lower() not in listed:
            pkg.pde.issues.append(f"файл не описан в info.xml: {d.path}")


def _designation_from_pages(doc: Document) -> Designation | None:
    for page in doc.pages:
        tb = page.title_block
        if tb and (d := parse(tb.get(1))):
            if d.suffix == "С":  # первым листом тома часто идёт содержание «<том>-С»
                d = d.model_copy(update={"suffix": None})
            return d
    return None


def _designation_from_name(doc: Document) -> Designation | None:
    return parse(PurePosixPath(doc.path).stem.split(" ")[0])


def _title_from_pages(doc: Document) -> str | None:
    """Наименование из графы 5 первого заглавного листа; содержание тома («Содержание тома N») пропускаем."""
    for page in doc.pages:
        tb = page.title_block
        if tb and tb.form in (4, 5) and tb.get(5) and not tb.get(5).lower().startswith("содержание тома"):
            return tb.get(5)
    return None
