"""Map saved Handwriting OCR results to the sheet schema, by way of the grid.

Reads data/results/handwriting_ocr/<file>.raw.json, builds a grid
(comparison/grid.py) and writes <file>.json next to it.

The custom extractor returns named fields, not a table: the header fields
(obsbud, site, date, depth_units) and frond_measurements_table, a list of
rows whose fields are named like the printed columns (rep, #stipes, #grn,
..., Blk). Frond lengths come back as one string ("2.0, 7.7, 2.5*, ..."),
split here into F1-F12. There is no confidence and no position, so cells
have confidence None and there is no marginalia. Tag columns are the
extractor's fields, so a new tag color needs the extractor updated.

Results come in two shapes: the API download ({"results": [{"page_number",
"extractions": [[fields]]}]}) and the dashboard export, a list of field
lists, one per page.
"""

import json
import logging
import re
from pathlib import Path

from comparison.config import configure_logging
from comparison.grid import Grid, GridPage, Text, grid_to_sheet
from comparison.schema import Source

log = logging.getLogger("handwriting_ocr")

RESULTS_DIR = Path("data/results/handwriting_ocr")

# Header fields of the extractor, keyed by the Header field they fill.
HEADER_FIELDS = {
    "observers": "obsbud",
    "site": "site",
    "survey_date": "date",
    "depth_units": "depth_units",
}


def pages(data: object) -> list[tuple[int, list[dict]]]:
    """Return (page number, extractor fields) for each page of a result."""
    if isinstance(data, list):
        return [(number, fields) for number, fields in enumerate(data, start=1)]
    assert isinstance(data, dict)
    return [(page["page_number"], page["extractions"][0]) for page in data["results"]]


def split_lengths(text: str) -> list[str]:
    """Split the frond lengths string into one length per frond.

    Lengths are separated by commas or spaces. A "*" on its own marks the
    length before it as broken (the one after it, if it comes first).
    """
    tokens = [token for token in re.split(r"[,\s]+", text) if token]
    lengths: list[str] = []
    pending = ""
    for token in tokens:
        if token == "*":
            if lengths:
                lengths[-1] += "*"
            else:
                pending = "*"
            continue
        lengths.append(pending + token)
        pending = ""
    return lengths


def map_page(number: int, fields: list[dict]) -> GridPage:
    """Build the grid for one page of extractor fields."""
    by_key = {field["key"]: field for field in fields}
    header = {
        name: Text(text=str(by_key[key]["value"] or ""))
        for name, key in HEADER_FIELDS.items()
        if key in by_key
    }
    table = next(field for field in fields if isinstance(field["value"], list))
    rows: list[list[Text]] = []
    names: list[str] = []
    for row_fields in table["value"]:
        cells: list[Text] = []
        names = []
        for field in row_fields:
            if field["key"] == "frond_lengths":
                lengths = split_lengths(str(field["value"] or ""))
                if len(lengths) > 12:
                    log.warning(f"page {number}: {len(lengths)} lengths, kept 12")
                lengths = (lengths + [""] * 12)[:12]
                names += [f"F{n}" for n in range(1, 13)]
                cells += [Text(text=length) for length in lengths]
            else:
                names.append(field["name"])
                cells.append(Text(text=str(field["value"] or "")))
        rows.append(cells)
    # Every row has the same extractor fields, so any row gives the columns.
    columns = [Text(text=name) for name in names]
    return GridPage(page=number, header=header, columns=columns, rows=rows)


def map_file(path: Path) -> Path:
    """Map one raw.json to <file>.json beside it and return its path."""
    stem = path.name.removesuffix(".raw.json")
    source = Source.from_filename(f"{stem}.pdf", service="handwriting_ocr")
    data = json.loads(path.read_text())
    grid = Grid(source=source, pages=[map_page(n, f) for n, f in pages(data)])
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
