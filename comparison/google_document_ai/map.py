"""Map saved Document AI Form Parser results to the sheet schema, by way of the grid.

Reads data/results/google_document_ai/<file>.raw.json and writes <file>.json
next to it. Form Parser's output is used as returned, to measure it as it is:

- the table: the frond table's rows, each row's cells sorted by x (the JSON
  lists them out of order). A cell spanning several columns (colSpan) fills
  the first and leaves the rest blank, so later cells stay under their own
  headers. The header row is the first row with rep and F12; body cells
  often merge several columns' values, which then read as unreadable.
- the header: Form Parser's form fields (OBS/BUD, SITE, Date, Depth Units),
  with their confidences.

Form Parser gives every table cell confidence 0, meaning it doesn't score
them, so cells get no confidence rather than 0.
"""

import json
import logging
from pathlib import Path

from comparison.config import configure_logging
from comparison.grid import Grid, GridPage, Text, grid_to_sheet
from comparison.schema import Source

log = logging.getLogger("google_document_ai")

RESULTS_DIR = Path("data/results/google_document_ai")

# Form field labels, keyed by the Header field their value fills.
LABELS = {
    "observers": "OBS/BUD",
    "site": "SITE",
    "survey_date": "Date",
    "depth_units": "Depth Units",
}


def layout_text(document: dict, layout: dict) -> str:
    """Return the text a layout points to, with whitespace collapsed."""
    segments = layout.get("textAnchor", {}).get("textSegments", [])
    text = "".join(
        document["text"][int(s.get("startIndex", 0)) : int(s["endIndex"])]
        for s in segments
    )
    return " ".join(text.split())


def left_edge(layout: dict) -> float:
    """Return a layout's left edge, as a share of page width."""
    return min(p.get("x", 0.0) for p in layout["boundingPoly"]["normalizedVertices"])


def row_texts(document: dict, row: dict) -> list[Text]:
    """Return a table row as one Text per column, cells in x order.

    A cell spanning n columns gives its text and n - 1 blanks.
    """
    texts: list[Text] = []
    for cell in sorted(row["cells"], key=lambda c: left_edge(c["layout"])):
        confidence = cell["layout"].get("confidence") or None
        texts.append(
            Text(text=layout_text(document, cell["layout"]), confidence=confidence)
        )
        texts += [Text() for _ in range(cell.get("colSpan", 1) - 1)]
    return texts


def is_header_row(row: list[Text]) -> bool:
    """Return True if a row is the printed header: it has the words rep and F12.

    Words, not cells, since Form Parser merges header cells ("BIK F11 F12").
    """
    words = {word for cell in row for word in cell.text.lower().split()}
    return "rep" in words and "f12" in words


def map_page(document: dict, page: dict) -> GridPage:
    """Build the grid for one Form Parser page."""
    header: dict[str, Text] = {}
    for field in page.get("formFields", []):
        name = layout_text(document, field["fieldName"])
        for key, label in LABELS.items():
            if name.lower().startswith(label.lower()) and key not in header:
                value = field["fieldValue"]
                header[key] = Text(
                    text=layout_text(document, value),
                    confidence=value.get("confidence"),
                )
    for table in page.get("tables", []):
        rows = [
            row_texts(document, row)
            for row in table.get("headerRows", []) + table.get("bodyRows", [])
        ]
        start = next((i for i, row in enumerate(rows) if is_header_row(row)), None)
        if start is not None:
            return GridPage(
                page=page["pageNumber"],
                header=header,
                columns=rows[start],
                rows=rows[start + 1 :],
            )
    return GridPage(page=page["pageNumber"], header=header)


def map_file(path: Path) -> Path:
    """Map one raw.json to <file>.json beside it and return its path."""
    stem = path.name.removesuffix(".raw.json")
    source = Source.from_filename(f"{stem}.pdf", service="google_document_ai")
    document = json.loads(path.read_text())
    grid = Grid(source=source, pages=[map_page(document, p) for p in document["pages"]])
    output = path.with_name(f"{stem}.json")
    output.write_text(grid_to_sheet(grid).model_dump_json(indent=1))
    return output


def main() -> None:
    """Map every saved raw.json."""
    configure_logging()
    paths = sorted(RESULTS_DIR.glob("*.raw.json"))
    log.info(f"mapping {len(paths)} files")
    for path in paths:
        log.info(f"wrote {map_file(path)}")


if __name__ == "__main__":
    main()
