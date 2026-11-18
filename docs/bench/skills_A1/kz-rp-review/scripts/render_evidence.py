"""Render a PDF page, raster frame, or an embedded OOXML picture locally."""
import argparse
import io
from zipfile import ZipFile

import fitz
from PIL import Image, ImageOps

from local_io import ROOT, emit, local, package_file, reference_dir, work_dir


def main():
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--document", help="A path inside package/")
    group.add_argument("--agsk", action="store_true", help="Render the bundled original AGSK PDF")
    p.add_argument("--page", type=int, default=1, help="PDF page from 1 (not printed sheet number)")
    p.add_argument("--frame", type=int, default=1, help="Raster frame from 1")
    p.add_argument("--member", help="Exact image member inside a DOCX/XLSX archive, no extraction to disk")
    p.add_argument("--dpi", type=int, default=140)
    p.add_argument("--crop", nargs=4, type=float, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
                   help="Fractions 0..1 of the rotated visible page/image; default is the entire page")
    p.add_argument("--work", default="review_work")
    args = p.parse_args()
    if not 36 <= args.dpi <= 400 or args.page < 1 or args.frame < 1:
        p.error("DPI must be 36..400, page/frame >= 1")
    if args.crop:
        l, t, r, b = args.crop
        if not (0 <= l < r <= 1 and 0 <= t < b <= 1):
            p.error("Crop fractions must satisfy 0 <= left < right <= 1 and 0 <= top < bottom <= 1")
    source = reference_dir() / "AGSK-1_2025-06.pdf" if args.agsk else package_file(args.document)
    source = local(source, exists=True)
    if args.member:
        if args.agsk or source.suffix.lower() not in (".docx", ".xlsx", ".xlsm"):
            p.error("--member requires a DOCX/XLSX/XLSM package file")
        prefix = "word/media/" if source.suffix.lower() == ".docx" else "xl/media/"
        if not args.member.startswith(prefix) or ".." in args.member.split("/"):
            p.error("Only embedded pictures under word/media/ or xl/media/ are allowed")
        with ZipFile(source) as archive:
            with Image.open(io.BytesIO(archive.read(args.member))) as pic:
                picture = ImageOps.exif_transpose(pic).convert("RGB")
    elif source.suffix.lower() == ".pdf":
        with fitz.open(source) as doc:
            if doc.needs_pass:
                raise ValueError("Encrypted PDF")
            if args.page > len(doc):
                p.error("PDF page is out of range")
            page = doc[args.page - 1]
            # get_pixmap clip uses the visible, rotated page coordinate system.
            clip = None
            if args.crop:
                l, t, r, b = args.crop
                rect = page.rect
                clip = fitz.Rect(rect.width*l, rect.height*t, rect.width*r, rect.height*b)
            pixmap = page.get_pixmap(dpi=args.dpi, clip=clip, colorspace=fitz.csRGB, alpha=False)
            picture = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    else:
        with Image.open(source) as pic:
            if args.frame > getattr(pic, "n_frames", 1):
                p.error("Image frame is out of range")
            pic.seek(args.frame - 1)
            picture = ImageOps.exif_transpose(pic).convert("RGB")
    if args.crop and (args.member or source.suffix.lower() != ".pdf"):
        l, t, r, b = args.crop
        w, h = picture.size
        picture = picture.crop((round(l*w), round(t*h), round(r*w), round(b*h)))
    output_dir = work_dir(args.work) / "renders"
    local(output_dir).mkdir(exist_ok=True)
    # Number output files without overwriting previously inspected evidence.
    index = 1
    while (output_dir / f"render_{index:04d}.png").exists():
        index += 1
    target = local(output_dir / f"render_{index:04d}.png")
    picture.save(target)
    emit({"image": target.relative_to(ROOT).as_posix(), "source": source.relative_to(ROOT).as_posix(),
          "page": args.page if source.suffix.lower() == ".pdf" else None, "frame": args.frame,
          "member": args.member, "crop": args.crop, "dpi": args.dpi, "size_px": list(picture.size)})


if __name__ == "__main__":
    main()
