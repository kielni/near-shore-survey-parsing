"""Map saved Textract responses to the sheet schema, by way of the grid.

Reads data/results/aws_textract/<file>.raw.json with Textractor,
builds a grid (comparison/grid.py) and writes <file>.json next to it.

What the Textract output looks like (checked on the sample sheets):

- Each page has one table with `rep` ... `F12` as its header row, then the 8
  plant rows. Textract sometimes adds a row above the header (empty, or the
  SITE box), so the header is the row holding rep and F12. Columns are matched by header text in the grid, since one page
  had an extra empty first column and a "feet time" header.
- FORMS key-values pair header labels with the wrong value (the date came
  back as the SITE value), so header fields are read from the label lines
  (OBS/BUD, SITE, Date) instead: either the rest of the label's own line
  ("SITE BROID") or the nearest other line beside or below it.
- The circled depth unit isn't reported (both printed words come back), so
  the header's depth_units is left out.
- A hand-added tag column (Blk) comes back as separate lines to the right
  of the table, with stray marks among them, and handwritten notes can sit
  there too ("1 frond exactly").
- Textract marks some handwritten notes as printed, so notes are found by
  leaving out the form's own printed text, not by text type.
"""

import json
import logging
from math import hypot
from pathlib import Path

from textractor.entities.document import Document
from textractor.entities.line import Line
from textractor.entities.page import Page
from textractor.entities.table import Table

from comparison.config import configure_logging
from comparison.grid import (
    GUESS_FACTOR,
    Box,
    Grid,
    GridPage,
    Text,
    grid_to_sheet,
    scaled,
)
from comparison.schema import Source

log = logging.getLogger("aws_textract")

RESULTS_DIR = Path("data/results/aws_textract")

# Printed header labels, keyed by the Header field their value fills.
LABELS = {"observers": "OBS/BUD", "site": "SITE", "survey_date": "Date"}

# Other printed text on the form, and the start of the instructions
# paragraph's two lines (OCR reads "Count all" as "Count ail").
FORM_TEXT = {"meters", "feet", "depth units (circle one)"}
FORM_TEXT_STARTS = ("count a", "estimate should")

# Farthest a header value can be from its label's right end, as a share of
# page size; notes farther away are marginalia.
LABEL_REACH = 0.1

# Largest horizontal gap, as a share of page width, between lines of one
# hand-added column to the right of the table. Printed columns are about
# 0.045 wide.
COLUMN_GAP = 0.03

# Longest text read as a value in a hand-added tag column; longer text there
# is a note.
TAG_VALUE_LENGTH = 3


def bounds(text: Text) -> Box:
    """Return a Text's box; every Text built here has one."""
    assert text.box is not None
    return text.box


def middle(b: Box) -> tuple[float, float]:
    """Return the center point of a box."""
    return b.x + b.width / 2, b.y + b.height / 2


def inside(b: Box, area: Box) -> bool:
    """Return True if the center of b lies within area."""
    x, y = middle(b)
    return area.x <= x <= area.x + area.width and area.y <= y <= area.y + area.height


def is_form_text(text: Text) -> bool:
    """Return True if a line is printed form text, such as a header label."""
    lower = text.text.strip().lower()
    return (
        lower in FORM_TEXT
        or lower.startswith(FORM_TEXT_STARTS)
        or any(lower.startswith(label.lower()) for label in LABELS.values())
    )


def to_text(entity: Line | Table, text: str, word_confidences: list[float]) -> Text:
    """Return an entity as grid Text, with its lowest confidence."""
    bbox = entity.bbox
    return Text(
        text=text,
        confidence=min([entity.confidence, *word_confidences]),
        box=Box(x=bbox.x, y=bbox.y, width=bbox.width, height=bbox.height),
    )


def is_header_row(row: list[Text]) -> bool:
    """Return True if a table row is the printed header: it has rep and F12."""
    texts = {cell.text.strip().lower() for cell in row}
    return "rep" in texts and "f12" in texts


def find_table(page: Page) -> Table | None:
    """Return the frond table: the one with a row holding rep and F12."""
    for table in page.tables:
        if any(is_header_row(row) for row in table_rows(table)):
            return table
    return max(page.tables, key=lambda t: t.row_count, default=None)


def table_rows(table: Table) -> list[list[Text]]:
    """Return each table row as a list of cell texts, in column order."""
    rows: list[list[Text]] = []
    for index in range(1, table.row_count + 1):
        cells = sorted(
            (c for c in table.table_cells if c.row_index == index),
            key=lambda c: c.col_index,
        )
        rows.append(
            [to_text(c, c.text.strip(), [w.confidence for w in c.words]) for c in cells]
        )
    return rows


def row_span(row: list[Text]) -> tuple[float, float]:
    """Return the top and bottom edge of a table row."""
    return (
        min(bounds(t).y for t in row),
        max(bounds(t).y + bounds(t).height for t in row),
    )


def label_distance(line: Text, label: Text) -> float:
    """Return how far a line's left end is from a label's right end."""
    box, label_box = bounds(line), bounds(label)
    return hypot(
        box.x - (label_box.x + label_box.width),
        middle(box)[1] - middle(label_box)[1],
    )


def read_header(
    lines: list[Text], table_top: float
) -> tuple[dict[str, Text], set[int]]:
    """Read the header fields from the label lines above the table.

    A label line may carry its value ("SITE BROID"); otherwise each
    other line within reach goes to the nearest label without a
    value, measured from the label's right end. Returns the fields and the
    ids of the lines used.
    """
    above = [line for line in lines if bounds(line).y < table_top]
    header: dict[str, Text] = {}
    labels: dict[str, Text] = {}
    for line in above:
        for field, label in LABELS.items():
            if line.text.strip().upper().startswith(label.upper()):
                labels[field] = line
                rest = line.text.strip()[len(label) :].strip()
                if rest:
                    header[field] = line.model_copy(update={"text": rest})
    used = {id(line) for line in labels.values()}
    open_labels = {f: line for f, line in labels.items() if f not in header}
    values: dict[str, list[Text]] = {}
    for line in above:
        if id(line) in used or is_form_text(line) or not open_labels:
            continue
        nearest = min(open_labels, key=lambda f: label_distance(line, open_labels[f]))
        if label_distance(line, open_labels[nearest]) <= LABEL_REACH:
            values.setdefault(nearest, []).append(line)
            used.add(id(line))
    for field, found in values.items():
        found.sort(key=lambda t: bounds(t).x)
        confidences = [t.confidence for t in found if t.confidence is not None]
        header[field] = Text(
            text=" ".join(t.text for t in found),
            confidence=min(confidences) if confidences else None,
            box=found[0].box,
        )
    return header, used


def tag_columns(
    lines: list[Text], table: Box, spans: list[tuple[float, float]]
) -> tuple[list[Text], list[list[Text]], set[int]]:
    """Find hand-added tag columns to the right of the table.

    Lines right of the table are grouped into columns by position. A group
    is a tag column only if it has short values in the data rows; each value
    goes to the row whose span contains its center, and when a row gets
    more than one, the one nearest the row's center wins, with lower
    confidence. Returns the column headers, each data row's values, and the
    ids of the lines used.
    """
    right = table.x + table.width
    top, bottom = table.y, table.y + table.height
    beside = sorted(
        (
            line
            for line in lines
            if middle(bounds(line))[0] > right
            and top <= middle(bounds(line))[1] <= bottom
        ),
        key=lambda line: bounds(line).x,
    )
    groups: list[list[Text]] = []
    for line in beside:
        if groups and bounds(line).x - bounds(groups[-1][-1]).x <= COLUMN_GAP:
            groups[-1].append(line)
        else:
            groups.append([line])
    header_span, data_spans = spans[0], spans[1:]
    headers: list[Text] = []
    values: list[list[Text]] = [[] for _ in data_spans]
    used: set[int] = set()
    for group in groups:
        header = Text()
        cells: list[list[Text]] = [[] for _ in data_spans]
        for line in group:
            y = middle(bounds(line))[1]
            if header_span[0] <= y <= header_span[1]:
                header = line
                continue
            for index, (row_top, row_bottom) in enumerate(data_spans):
                short = len(line.text.strip()) <= TAG_VALUE_LENGTH
                if row_top <= y <= row_bottom and short:
                    cells[index].append(line)
        if not any(cells):
            continue
        headers.append(header)
        used.add(id(header))
        for index, found in enumerate(cells):
            if not found:
                values[index].append(Text())
                continue
            center = sum(data_spans[index]) / 2
            found.sort(key=lambda t: abs(middle(bounds(t))[1] - center))
            best = found[0]
            used.add(id(best))
            if len(found) > 1:
                confidence = scaled(best.confidence, GUESS_FACTOR)
                best = best.model_copy(update={"confidence": confidence})
            values[index].append(best)
    return headers, values, used


def map_page(page: Page) -> GridPage:
    """Build the grid for one Textract page."""
    lines = [
        to_text(line, line.text, [w.confidence for w in line.words])
        for line in page.lines
    ]
    table = find_table(page)
    if table is None:
        notes = [line for line in lines if not is_form_text(line)]
        return GridPage(page=page.page_num, lines=notes)
    table_box = to_text(table, "", []).box
    assert table_box is not None
    # Textract sometimes adds a row above the printed header (empty, or the
    # SITE box), so the header is the first row with rep and F12.
    rows = table_rows(table)
    start = next((i for i, row in enumerate(rows) if is_header_row(row)), 0)
    rows = rows[start:]
    header, header_used = read_header(lines, table_box.y)
    outside = [
        line
        for line in lines
        if not is_form_text(line) and not inside(bounds(line), table_box)
    ]
    spans = [row_span(row) for row in rows]
    tag_headers, tag_values, tag_used = tag_columns(outside, table_box, spans)
    used = header_used | tag_used
    marginalia = [line for line in outside if id(line) not in used]
    return GridPage(
        page=page.page_num,
        header=header,
        columns=rows[0] + tag_headers,
        rows=[cells + extra for cells, extra in zip(rows[1:], tag_values)],
        table_box=table_box,
        lines=marginalia,
    )


def map_file(path: Path) -> Path:
    """Map one raw.json to <file>.json beside it and return its path."""
    stem = path.name.removesuffix(".raw.json")
    source = Source.from_filename(f"{stem}.pdf", service="aws_textract")
    document = Document.open(json.loads(path.read_text()))
    grid = Grid(source=source, pages=[map_page(page) for page in document.pages])
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
