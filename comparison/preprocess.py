"""Turn the scanned survey PDFs upright and build contact sheets to check them.

Rotation only changes each page's /Rotate flag, so the scanned image is not
re-encoded.
"""

import logging
from pathlib import Path

import pymupdf

from comparison.config import configure_logging

log = logging.getLogger("preprocess")

DATA_DIR = Path("data")
PREPROCESSED_DIR = DATA_DIR / "preprocessed"

# Clockwise rotation, in degrees, added to each page's existing /Rotate flag.
DEFAULT_ROTATION = 270

# Exceptions found on the contact sheets, keyed by file name.
ROTATION_OVERRIDES = {
    "20260827_KF03S_frondpull_1.6.pdf": 90,
}


def rotate_pdf(source: Path, destination: Path, degrees: int) -> None:
    """Write a copy of source with every page rotated clockwise by degrees."""
    with pymupdf.open(source) as doc:
        for page in doc:
            page.set_rotation((page.rotation + degrees) % 360)
        destination.parent.mkdir(parents=True, exist_ok=True)
        doc.save(destination)


def rotate_form(form: str) -> None:
    """Write rotated copy of a form's PDFs under data/preprocessed/<form>/."""
    sources = sorted((DATA_DIR / form).glob("*.pdf"))
    for source in sources:
        degrees = ROTATION_OVERRIDES.get(source.name, DEFAULT_ROTATION)
        rotate_pdf(source, PREPROCESSED_DIR / form / source.name, degrees)
    log.info(f"{form}: rotated {len(sources)} files")


def build_contact_sheet(form: str) -> Path:
    """Lay out every page of a form's upright PDFs as labelled thumbnails."""
    page_width, page_height = pymupdf.paper_size("a3-l")
    columns, rows = 6, 5
    cell_width = page_width / columns
    cell_height = page_height / rows
    sheet = pymupdf.open()
    slot = 0
    for path in sorted((PREPROCESSED_DIR / form).glob("*.pdf")):
        with pymupdf.open(path) as source:
            # Low-resolution renders keep the sheet small; get_pixmap applies
            # /Rotate, so each thumbnail shows the page as a reader sees it.
            thumbnails = [page.get_pixmap(dpi=40) for page in source]
        for number, pixmap in enumerate(thumbnails):
            if slot % (columns * rows) == 0:
                sheet.new_page(width=page_width, height=page_height)
            page = sheet[sheet.page_count - 1]
            x0 = (slot % columns) * cell_width
            y0 = (slot // columns % rows) * cell_height
            label = f"{path.stem} p{number + 1}"
            page.insert_text((x0 + 4, y0 + 10), label, fontsize=7)
            thumbnail = pymupdf.Rect(
                x0 + 4, y0 + 14, x0 + cell_width - 4, y0 + cell_height - 4
            )
            page.insert_image(thumbnail, pixmap=pixmap)
            slot += 1
    output = PREPROCESSED_DIR / f"contact_sheet_{form}.pdf"
    sheet.save(output, deflate=True)
    log.info(f"wrote contact sheet to {output}")
    return output


def main() -> None:
    """Rotate PDFs, then build a contact sheet."""
    configure_logging()
    form = "Fronds"
    rotate_form(form)
    build_contact_sheet(form)


if __name__ == "__main__":
    main()
