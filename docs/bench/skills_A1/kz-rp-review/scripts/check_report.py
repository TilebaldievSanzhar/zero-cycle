"""Validate report structure and package locators, not normative conclusions."""
import argparse
from pathlib import PurePosixPath

from local_io import emit, read_json


CATEGORIES = ("комплектность", "оформление", "увязка", "нормы и ссылки на них",
              "расчёты", "смета", "формат подачи")
FINDING_FIELDS = ("category", "status", "document", "page", "description", "basis")


def validate(report, inventory):
    errors = []
    files = {r["document"]: r for r in inventory["files"]}
    if not isinstance(report, dict):
        return ["Top level must be an object"]
    for field in ("findings", "summary"):
        if field not in report:
            errors.append("Top level missing " + field)
    for field in report:
        if field not in ("findings", "summary"):
            errors.append("Top level unexpected field " + field)
    if not isinstance(report.get("summary"), str):
        errors.append("summary must be a string")
    if not isinstance(report.get("findings"), list):
        return errors + ["findings must be an array"]
    for i, finding in enumerate(report["findings"], 1):
        label = f"findings[{i}]"
        if not isinstance(finding, dict):
            errors.append(label + " must be an object")
            continue
        for field in FINDING_FIELDS:
            if field not in finding:
                errors.append(label + " missing " + field)
        for field in finding:
            if field not in FINDING_FIELDS:
                errors.append(label + " unexpected field " + field)
        if finding.get("category") not in CATEGORIES:
            errors.append(label + " invalid category")
        if finding.get("status") not in ("нарушение", "проверить вручную"):
            errors.append(label + " invalid status")
        for field in ("description", "basis"):
            if not isinstance(finding.get(field), str) or not finding[field].strip():
                errors.append(label + " requires nonempty " + field)
        document, page = finding.get("document"), finding.get("page")
        if document == "package/":
            if page is not None:
                errors.append(label + " package/ requires page=null")
            continue
        if (not isinstance(document, str) or not document or "\\" in document or ":" in document
                or document.startswith("package/") or PurePosixPath(document).is_absolute()
                or any(part in ("", ".", "..") for part in document.split("/"))):
            errors.append(label + " invalid path inside package/ (omit the package/ prefix)")
            continue
        record = files.get("package/" + document)
        if record is None:
            errors.append(label + " document not in inventory")
            continue
        if document.lower().endswith(".pdf"):
            if page is not None and (type(page) is not int or page < 1 or page > record.get("pages", 0)):
                errors.append(label + " PDF page must be null or an existing page from 1")
        elif page is not None:
            errors.append(label + " non-PDF requires page=null; put the locator in description")
    return errors


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", required=True)
    p.add_argument("--inventory", default="review_work/inventory.json")
    args = p.parse_args()
    errors = validate(read_json(args.report), read_json(args.inventory))
    emit({"valid_structure_and_locators": not errors, "errors": errors,
          "warning": "Does not verify quotes, absence searches, applicability, norms, or truth of a finding."})
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
