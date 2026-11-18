"""Literal normalized search with original evidence; no inferred norm status."""
import argparse
import re
import unicodedata

from local_io import emit, json_lines, local, reference_dir

DASHES = str.maketrans({c: "-" for c in "‐‑‒–—−﹘﹣－"})


def normalized(text):
    return "".join(unicodedata.normalize("NFKC", text).translate(DASHES).casefold().split())


def excerpt(text, query, width):
    # Reconstruct raw positions; works for common line breaks and hyphen variants.
    mapped, positions = [], []
    for i, c in enumerate(text):
        for char in normalized(c):
            mapped.append(char)
            positions.append(i)
    at = "".join(mapped).find(query)
    if at < 0:
        return text[:width]
    start = max(0, positions[at] - width // 3)
    end = min(len(text), positions[min(at + len(query) - 1, len(positions)-1)] + 1 + width * 2 // 3)
    return text[start:end]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", choices=("package", "agsk", "gost", "299"), default="package")
    p.add_argument("--query", required=True)
    p.add_argument("--work", default="review_work")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--context", type=int, default=1200)
    args = p.parse_args()
    if args.limit < 1 or args.context < 50:
        p.error("--limit must be positive and --context >= 50")
    if args.source == "package":
        records = json_lines(local(args.work) / "units.jsonl")
    elif args.source == "agsk":
        records = json_lines(reference_dir() / "agsk_pages.jsonl")
    else:
        paths = ([reference_dir() / "gost_full.txt"] if args.source == "gost" else
                 sorted(reference_dir().glob("order_299_appendix_*.txt")))
        records = ({"document": str(path.relative_to(reference_dir())), "page": None,
                    "locator": "full source text", "text": local(path, exists=True).read_text("utf-8")} for path in paths)
    query = normalized(args.query)
    if not query:
        p.error("empty query")
    total, hits = 0, []
    for item in records:
        if query in normalized(item["text"]):
            total += 1
            if len(hits) < args.limit:
                hit = {k: item[k] for k in ("document", "page", "locator", "unit_id") if k in item}
                hit["excerpt"] = excerpt(item["text"], query, args.context)
                hits.append(hit)
    emit({"source": args.source, "query": args.query, "matching_units": total, "shown": len(hits),
          "truncated": total > len(hits), "hits": hits,
          "warning": "Candidates only; inspect the full source and table row. No hit does not prove absence or repeal."})


if __name__ == "__main__":
    main()
