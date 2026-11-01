"""Helpers shared by the comparison services, which never import each other."""

from pathlib import Path

import pymupdf


def render_page(pdf: Path, page: int, resize: bool = False) -> bytes:
    """Return one page (1-based) of a PDF as PNG bytes, as a reader sees it.

    Pages render at 300 dpi. With resize, the page is instead scaled so its
    long side is 3,300 pixels, about a letter-size page at 300 dpi, for
    services with an image size limit: one scan is stored at about three
    times the usual page size (20261016_SQPT02 renders at 9,834 x 7,575).
    """
    with pymupdf.open(pdf) as doc:
        source = doc[page - 1]
        # get_pixmap applies /Rotate, so the image matches the preprocessed page.
        if not resize:
            return source.get_pixmap(dpi=300).tobytes("png")
        zoom = 3300 / max(source.rect.width, source.rect.height)
        return source.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")
