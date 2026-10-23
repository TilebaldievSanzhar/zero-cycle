"""Реквизитная часть ПДЭ — info.xml (ГОСТ Р 21.101-2020, п. 8.2.3–8.2.6, прил. Ф).

Структура: Package → Project (атрибуты ProjectName, Code) → Children → Folder (Section, Subsection, Set;
атрибуты Number, Code, Title) → Children → Document (FileName; атрибуты Code, DocumentName, Sheet).
У Project, Folder и Document — GUID; GUID документа не меняется между версиями пакета (п. 8.2.4).

XML приходит из чужих рук: парсер без DTD, внешних сущностей и сети.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from lxml import etree

from zerocycle.model import PdeInfo, PdeNode

GUID = re.compile(r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$")
FOLDER_TYPES = {"Section", "Subsection", "Set"}


def _parser() -> etree.XMLParser:
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)


def _local(el) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _child(el, name: str):
    return next((c for c in el if _local(c) == name), None)


def _attrs(el) -> dict[str, str]:
    box = _child(el, "Attributes")
    if box is None:
        return {}
    return {a.get("Name", ""): a.get("Value", "") for a in box if _local(a) == "Attribute" and a.get("Name")}


def read_info_xml(path: Path, rel: str, sha256: str) -> PdeInfo:
    info = PdeInfo(file=rel, sha256=sha256)
    try:
        tree = etree.parse(str(path), _parser())
    except etree.XMLSyntaxError as e:
        info.issues.append(f"info.xml не разбирается: {e.msg}")
        return info
    root = tree.getroot()
    if _local(root) != "Package":
        info.issues.append(f"корневой элемент «{_local(root)}», а не Package")
        return info
    info.created = root.get("Created")
    info.organization = root.get("Organization")
    project = _child(root, "Project")
    if project is None:
        info.issues.append("нет элемента Project")
        return info
    info.project_guid = project.get("Id")
    attrs = _attrs(project)
    info.project_name = attrs.get("ProjectName")
    info.project_code = attrs.get("Code")
    if info.project_guid and not GUID.match(info.project_guid):
        info.issues.append(f"Id объекта не GUID: {info.project_guid}")

    def walk(el, parent: int | None) -> None:
        children = _child(el, "Children")
        if children is None:
            return
        for c in children:
            name = _local(c)
            if name not in ("Folder", "Document"):
                continue
            is_folder = name == "Folder"
            file_name = None if is_folder else (c.get("FileName") or "").replace("\\", "/").lstrip("/") or None
            node = PdeNode(
                kind="folder" if is_folder else "document",
                guid=c.get("Id"),
                type=c.get("Type") if is_folder else None,
                attrs=_attrs(c),
                file_name=file_name,
                created=c.get("Created"),
                parent=parent,
                xpath=tree.getpath(c),
            )
            info.nodes.append(node)
            if is_folder:
                walk(c, len(info.nodes) - 1)

    walk(project, None)
    _check(info)
    return info


def _check(info: PdeInfo) -> None:
    """Замечания к самому info.xml; сверка с файлами пакета — в описи (`zerocycle.scan`)."""
    for n in info.nodes:
        where = n.attrs.get("Code") or n.file_name or n.xpath
        if not n.guid:
            info.issues.append(f"нет Id у {where}")
        elif not GUID.match(n.guid):
            info.issues.append(f"Id не GUID у {where}: {n.guid}")
        if n.kind == "folder" and n.type not in FOLDER_TYPES:
            info.issues.append(f"тип папки «{n.type}» не из {sorted(FOLDER_TYPES)}: {where}")
        if n.kind == "document" and not n.file_name:
            info.issues.append(f"нет FileName у документа {where}")
    dup = [g for g, k in Counter(n.guid for n in info.nodes if n.guid).items() if k > 1]
    for g in dup:
        info.issues.append(f"повтор GUID: {g}")
