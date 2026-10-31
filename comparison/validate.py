"""Check mapped sheets against the validation rules and record flags.

Each rule adds flags; none of them change values. Rewrites every mapped
data/results/<service>/<file>.json with its flags list filled in.

Depths are checked against a fixed limit, not the site's earlier depths,
so a few sheets don't constrain them.
Times are only checked for being readable: rows aren't in time order
on verified sheets (20261016_SQPT01: 10:45, 10:45, 10:34, 11:00, ...).
"""

import argparse
import logging
from pathlib import Path

from comparison.config import configure_logging, mapped_paths
from comparison.schema import METERS_PER_FOOT, Cell, Flag, Page, Row, SheetDocument

log = logging.getLogger("validate")

FRONDS = [f"F{n}" for n in range(1, 13)]

# Longest frond length accepted, in meters; the answers reach 30.4 m.
MAX_LENGTH = 50.0

# Cells below this confidence (0-1) flag their row for review.
LOW_CONFIDENCE = 0.8


def value_cells(row: Row) -> dict[str, Cell]:
    """Return the row's value cells by field name, tag counts as tags.<color>."""
    cells: dict[str, Cell] = {
        name: getattr(row, name) for name in ["rep", "depth", "time", "stipes"]
    }
    cells.update({f"tags.{color}": cell for color, cell in row.tags.items()})
    cells.update({name: getattr(row, name) for name in FRONDS})
    return cells


def is_empty(row: Row) -> bool:
    """Return True if nothing was read in the row."""
    cells = value_cells(row).values()
    return not row.row_text.value and not any(cell.raw for cell in cells)


def check_row(page: int, row: Row) -> list[Flag]:
    """Apply the per-row rules to one row."""
    # Rule, field (None for the whole row) and message of each rule that fires.
    found: list[tuple[str, str | None, str]] = []
    cells = value_cells(row)
    for name, cell in cells.items():
        if cell.raw and cell.raw.strip() and cell.value is None:
            found.append(("unreadable", name, f"{cell.raw!r} has no value"))
    low = [
        name
        for name, cell in cells.items()
        if cell.confidence is not None and cell.confidence < LOW_CONFIDENCE
    ]
    if low:
        found.append(("low_confidence", None, f"low confidence in {', '.join(low)}"))
    lengths = [getattr(row, name) for name in FRONDS]
    for name, length in zip(FRONDS, lengths):
        if length.value is not None and not 0 <= length.value <= MAX_LENGTH:
            message = f"{length.value} m is outside 0-{MAX_LENGTH}"
            found.append(("length_range", name, message))
    depth = row.depth.value
    if depth is not None:
        # Recreational scuba limit, 130 ft. With no unit (the circled unit
        # often isn't read), a depth passes if it fits read as feet.
        if row.depth_m is not None:
            over = row.depth_m > 130 * METERS_PER_FOOT
        else:
            over = depth > 130
        if depth <= 0 or over:
            unit = row.depth_unit.value or "no unit"
            message = f"depth {depth} ({unit}) is outside 0-130 ft"
            found.append(("depth_range", "depth", message))
    measured = [bool(length.raw and length.raw.strip()) for length in lengths]
    if True in measured:
        last = len(measured) - measured[::-1].index(True)
        if not all(measured[:last]):
            found.append(
                ("length_gaps", None, "frond lengths are not filled left to right")
            )
    stipes = row.stipes.value
    count = sum(measured)
    if stipes is not None:
        # Fewer than 12 is not flagged: divers sometimes run out of time
        # ("Not all 10 sampled, only time for 5" in the answers).
        if count > min(stipes, 12):
            found.append(
                ("frond_count", None, f"{count} fronds measured, {stipes} stipes")
            )
        tags = [cell.value for cell in row.tags.values() if cell.value is not None]
        if sum(tags) > stipes:
            message = f"tag counts sum to {sum(tags)}, {stipes} stipes"
            found.append(("tag_sum", None, message))
    return [
        Flag(
            rule=rule, page=page, sheet_row=row.sheet_row, field=field, message=message
        )
        for rule, field, message in found
    ]


def check_page(page: Page, document: SheetDocument) -> list[Flag]:
    """Apply the per-page rules, then the per-row rules to each row."""
    flags: list[Flag] = []
    source = document.source
    header = page.header
    if header.site.value != source.filename_site:
        flags.append(
            Flag(
                rule="header_filename",
                page=page.page,
                field="site",
                message=f"site {header.site.value} ({header.site.raw!r}),"
                f" filename {source.filename_site}",
            )
        )
    if header.survey_date.value != source.filename_date:
        flags.append(
            Flag(
                rule="header_filename",
                page=page.page,
                field="survey_date",
                message=f"date {header.survey_date.value}"
                f" ({header.survey_date.raw!r}), filename {source.filename_date}",
            )
        )
    rows = [row for row in page.rows if not is_empty(row)]
    reps = [row.rep.value for row in rows if row.rep.value is not None]
    for row in rows:
        if row.rep.value is not None and reps.count(row.rep.value) > 1:
            flags.append(
                Flag(
                    rule="rep_unique",
                    page=page.page,
                    sheet_row=row.sheet_row,
                    field="rep",
                    message=f"rep {row.rep.value} appears more than once",
                )
            )
        flags.extend(check_row(page.page, row))
    return flags


def validate(document: SheetDocument) -> list[Flag]:
    """Return every flag the rules raise for one sheet."""
    return [flag for page in document.pages for flag in check_page(page, document)]


def main() -> None:
    """Parse arguments and validate every mapped sheet of one service."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, help="e.g. aws_textract")
    args = parser.parse_args()
    paths = mapped_paths(Path("data/results", args.service))
    log.info(f"{args.service}: validating {len(paths)} files")
    for path in paths:
        document = SheetDocument.model_validate_json(path.read_text())
        document.flags = validate(document)
        path.write_text(document.model_dump_json(indent=1))
        log.info(f"{path.name}: {len(document.flags)} flags")


if __name__ == "__main__":
    main()
