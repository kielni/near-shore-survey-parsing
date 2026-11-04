"""Images for the report: whole sheets and crops of rows or regions.

Pages are rendered from the preprocessed PDFs (render_page, scaled so the
long side is 3,300 pixels). Rows are located with the row boxes in
Textract's saved results (data/results/aws_textract/<file>.raw.json), the
only service with boxes for every sheet; this only reads those results.
Whole sheets are downscaled JPEG; crops are full-resolution grayscale PNG,
so marks such as "*" and decimal points stay sharp.
"""

import json
from pathlib import Path

import cv2
import numpy as np
from textractor.entities.document import Document

from comparison.aws_textract.map import (
    bounds,
    find_table,
    is_header_row,
    row_span,
    table_rows,
)
from comparison.util import render_page

TEXTRACT_DIR = Path("data/results/aws_textract")
PDF_DIR = Path("data/preprocessed/Fronds")


def page_image(file: str, page: int) -> np.ndarray:
    """Return a page of a preprocessed PDF as an image array."""
    png = render_page(PDF_DIR / file, page, resize=True)
    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"{file} page {page}: rendered image didn't decode")
    return image


def sheet_jpeg(file: str, page: int, bottom: float = 1.0) -> bytes:
    """Return a page down to bottom (a share of its height) as a 1,200-pixel JPEG.

    For orientation; cutting below the table drops the blank paper under it.
    """
    image = page_image(file, page)
    image = image[: round(min(bottom, 1.0) * image.shape[0])]
    height = round(image.shape[0] * 1200 / image.shape[1])
    small = cv2.resize(image, (1200, height), interpolation=cv2.INTER_AREA)
    return cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()


def crop_png(
    image: np.ndarray,
    top: float,
    bottom: float,
    left: float = 0.0,
    right: float = 1.0,
) -> bytes:
    """Return a region of an image as grayscale PNG (edges as shares of its size)."""
    height, width = image.shape[:2]
    region = image[
        max(0, round(top * height)) : min(height, round(bottom * height)),
        max(0, round(left * width)) : min(width, round(right * width)),
    ]
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return cv2.imencode(".png", gray)[1].tobytes()


def region_png(
    file: str,
    page: int,
    top: float,
    bottom: float,
    left: float = 0.0,
    right: float = 1.0,
) -> bytes:
    """Return a region of a page as grayscale PNG (edges as shares of its size)."""
    return crop_png(page_image(file, page), top, bottom, left, right)


def table_layout(
    file: str, page: int
) -> tuple[list[tuple[float, float]], dict[str, tuple[float, float]]]:
    """Return a page's plant rows (top, bottom) and printed columns (left, right).

    From Textract's table: rows are numbered as sheet_row is, the rows below
    the printed header row; columns are keyed by their header text,
    lowercase without "#" (rep, depth, time, stipes, grn, ..., f1, ..., f12).
    Both empty if Textract found no table.
    """
    path = TEXTRACT_DIR / f"{Path(file).stem}.raw.json"
    document = Document.open(json.loads(path.read_text()))
    table = find_table(document.pages[page - 1])
    if table is None:
        return [], {}
    rows = table_rows(table)
    start = next((i for i, row in enumerate(rows) if is_header_row(row)), 0)
    columns: dict[str, tuple[float, float]] = {}
    for cell in rows[start]:
        if cell.text.strip():
            box = bounds(cell)
            columns[cell.text.strip().lower().lstrip("#")] = (box.x, box.x + box.width)
    return [row_span(row) for row in rows[start + 1 :]], columns


def padded_band(
    spans: list[tuple[float, float]], sheet_row: int
) -> tuple[float, float]:
    """Return a plant row's (top, bottom), padded so marks over a row line show."""
    top, bottom = spans[sheet_row - 1]
    pad = (bottom - top) * 0.15
    return top - pad, bottom + pad


def row_png(file: str, page: int, sheet_row: int) -> bytes | None:
    """Return a crop of one plant row (1-based), or None if it can't be found."""
    spans, _ = table_layout(file, page)
    if not 1 <= sheet_row <= len(spans):
        return None
    return region_png(file, page, *padded_band(spans, sheet_row))


def cell_pngs(file: str, page: int, sheet_row: int) -> dict[str, bytes]:
    """Return one plant row (1-based) cut into a crop per printed column.

    Keyed as table_layout keys the columns; empty if the row can't be found.
    The crops share the row's padded band and the columns' exact edges, so
    side by side they make up the row as on the sheet.
    """
    spans, columns = table_layout(file, page)
    if not 1 <= sheet_row <= len(spans):
        return {}
    top, bottom = padded_band(spans, sheet_row)
    image = page_image(file, page)
    return {
        key: crop_png(image, top, bottom, left, right)
        for key, (left, right) in columns.items()
    }


def table_bottom(file: str, page: int) -> float:
    """Return the bottom of a page's last plant row, or 1.0 if there is no table."""
    spans, _ = table_layout(file, page)
    return spans[-1][1] if spans else 1.0
