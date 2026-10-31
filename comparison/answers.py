"""Parse the verified answers into the sheet schema, with QA issues and features.

Reads data/fronds_answers.tsv (never edited) and writes, under data/answers/:

- fronds/<file>.json: one schema document per date+site group that matches
  a PDF, as service "answers". Answers carry no page or sheet row, so all
  plants go on one page, numbered by entry order; the header applies to
  every page of the PDF.
- unmatched.tsv: date+site groups with no PDF.
- issues.tsv: check failures for a person to resolve against the scan.
- features.tsv: per-sheet features, copied into data/split.tsv.

Each document also gets the validation flags, to calibrate the rules: a
rule that fires on verified data is too strict or found an answer error.

Fixes go in corrections.tsv (file, entry_order, field, value, reason),
applied last. Observers stay as entered (surnames, with typos) until there
is a surveyor key file.
"""

import csv
import logging
import re
from datetime import date
from pathlib import Path

import pymupdf

from comparison.config import configure_logging
from comparison.grid import parse_number, parse_time
from comparison.schema import (
    Cell,
    DepthUnit,
    Header,
    Page,
    Row,
    SheetDocument,
    Source,
)
from comparison.validate import validate

log = logging.getLogger("answers")

ANSWERS = Path("data/fronds_answers.tsv")
PDF_DIR = Path("data/Fronds")
OUTPUT_DIR = Path("data/answers")

# Plant-level fields that should be the same on every frond row of a plant.
PLANT_FIELDS = ["depth", "depth_units", "time", "total_frond"]


def read_answers() -> tuple[list[str], list[dict[str, str]]]:
    """Return the tag colors and the answer rows, with whitespace stripped.

    The file starts with blank lines before its header row; blank rows are
    dropped. Every column named number_<color> is a tag color.
    """
    with ANSWERS.open(newline="") as file:
        lines = list(csv.reader(file, delimiter="\t"))
    start = next(i for i, line in enumerate(lines) if line[0] == "name_of_data_enterer")
    header = lines[start]
    rows: list[dict[str, str]] = []
    for line in lines[start + 1 :]:
        values = [value.strip() for value in line]
        if any(values):
            rows.append(dict(zip(header, values)))
    colors = [
        name.removeprefix("number_") for name in header if name.startswith("number_")
    ]
    return colors, rows


def first_uses(rows: list[dict[str, str]], colors: list[str]) -> dict[str, str]:
    """Return the first date (YYYYMMDD) each tag color has a non-zero count."""
    first: dict[str, str] = {}
    for row in rows:
        for color in colors:
            count = row.get(f"number_{color}", "")
            if count not in {"", "0"} and row["date"] < first.get(color, "99999999"):
                first[color] = row["date"]
    return first


def sheet_colors(
    group: list[dict[str, str]], colors: list[str], first: dict[str, str]
) -> list[str]:
    """Return the tag colors a sheet has a column for.

    A color that is 0 on every row of a sheet dated before its first
    non-zero use anywhere means "no column on that sheet", not zero tags.
    """
    stamp = group[0]["date"]
    present = []
    for color in colors:
        counts = {row.get(f"number_{color}", "") for row in group}
        if counts <= {"", "0"} and stamp < first.get(color, "99999999"):
            continue
        present.append(color)
    return present


def runs(group: list[dict[str, str]]) -> list[list[dict[str, str]]]:
    """Split a sheet's rows into plants: runs of consecutive equal plant_ID.

    Runs, not grouping by plant_ID, so plants entered under a duplicated ID
    stay apart. Plants can also be entered back to back under one ID
    (20260923_KF01S: five plants as plant 4), so a run also ends where the
    frond number restarts at 1 and a plant-level field changes.
    """
    plants: list[list[dict[str, str]]] = []
    for row in group:
        if plants and plants[-1][0]["plant_ID"] == row["plant_ID"]:
            start = plants[-1][0]
            fields = [*PLANT_FIELDS, *(k for k in row if k.startswith("number_"))]
            changed = any(row[field] != start[field] for field in fields)
            if not (row["frond"] == "1" and changed):
                plants[-1].append(row)
                continue
        plants.append([row])
    return plants


def number_cell(raw: str) -> Cell[float]:
    """Return a number cell read with the sheet conventions."""
    return Cell(value=parse_number(raw)[0], raw=raw or None)


def count_cell(raw: str) -> Cell[int]:
    """Return a whole-number cell."""
    return Cell(value=int(raw) if raw.isdigit() else None, raw=raw or None)


def unit_cell(raw: str) -> Cell[DepthUnit]:
    """Return a depth unit cell; anything but Meters or Feet has no value."""
    unit: DepthUnit | None = None
    if raw == "Meters":
        unit = "Meters"
    elif raw == "Feet":
        unit = "Feet"
    return Cell(value=unit, raw=raw or None)


def build_row(plant: list[dict[str, str]], entry_order: int, colors: list[str]) -> Row:
    """Build one schema row from a plant's frond rows."""
    first = plant[0]
    row = Row(entry_order=entry_order)
    row.rep = count_cell(first["plant_ID"])
    row.depth = number_cell(first["depth"])
    row.depth_unit = unit_cell(first["depth_units"])
    row.time = Cell(value=parse_time(first["time"]), raw=first["time"] or None)
    if first["total_frond"]:
        row.stipes = count_cell(first["total_frond"])
    row.tags = {color: count_cell(first[f"number_{color}"]) for color in colors}
    for frond in plant:
        if frond["frond"].isdigit() and 1 <= int(frond["frond"]) <= 12:
            name = f"F{int(frond['frond'])}"
            setattr(row, name, number_cell(frond["frond_length"]))
            broken = frond["damaged_stipe"]
            setattr(
                row,
                f"{name}_broken",
                Cell(value=broken == "yes" if broken else None, raw=broken or None),
            )
    notes = list(dict.fromkeys(frond["notes"] for frond in plant if frond["notes"]))
    row.answer_notes = "; ".join(notes) or None
    return row


def build_header(group: list[dict[str, str]], colors: list[str]) -> Header:
    """Build the header from the group's observer, buddy, site and date."""
    first = group[0]
    raw = "; ".join(part for part in (first["observer"], first["buddy"]) if part)
    names = [name.strip() for name in re.split(r"[;,]", raw) if name.strip()]
    stamp = first["date"]
    survey_date = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:]))
    return Header(
        observers=Cell(value=names or None, raw=raw or None),
        site=Cell(value=first["site"], raw=first["site"]),
        survey_date=Cell(value=survey_date, raw=stamp),
        tag_colors=colors,
    )


def check_plants(file: str, plants: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    """Return QA issues for one sheet's plants."""
    issues: list[dict[str, str]] = []
    ids = [plant[0]["plant_ID"] for plant in plants]
    for order, plant in enumerate(plants, start=1):
        first = plant[0]
        found: list[tuple[str, str]] = []
        if ids.count(first["plant_ID"]) > 1:
            found.append(("duplicate_plant_id", f"{ids.count(first['plant_ID'])} runs"))
        fronds = [int(row["frond"]) for row in plant if row["frond"].isdigit()]
        if fronds and fronds != list(range(1, len(fronds) + 1)):
            found.append(("frond_numbering", " ".join(map(str, fronds))))
        for field in [*PLANT_FIELDS, *(k for k in first if k.startswith("number_"))]:
            values = list(dict.fromkeys(row[field] for row in plant))
            if len(values) > 1:
                found.append(
                    ("plant_field_changes", f"{field}: {' then '.join(values)}")
                )
        if first["time"] and parse_time(first["time"]) is None:
            found.append(("unreadable", f"time {first['time']!r}"))
        values = [first["depth"], *(row["frond_length"] for row in plant)]
        for value in values:
            if value and parse_number(value)[0] is None:
                found.append(("unreadable", f"number {value!r}"))
        for check, detail in found:
            issues.append(
                {
                    "file": file,
                    "entry_order": str(order),
                    "plant_ID": first["plant_ID"],
                    "check": check,
                    "detail": detail,
                    "resolution": "",
                }
            )
    return issues


def features(
    file: str, group: list[dict[str, str]], plants: list[list[dict[str, str]]]
) -> dict[str, str]:
    """Return the per-sheet features recorded in data/split.tsv."""
    first = group[0]
    with pymupdf.open(PDF_DIR / file) as pdf:
        pages = pdf.page_count
    no_lengths = sum(1 for plant in plants if not any(r["frond_length"] for r in plant))
    return {
        "file": file,
        "site": first["site"],
        "site_type": first["site_type"],
        "zone": first["zone"],
        "template": "new" if any(row["total_frond"] for row in group) else "old",
        "pages": str(pages),
        "mixed_units": str(len({row["depth_units"] for row in group}) > 1).lower(),
        "plants": str(len(plants)),
        "plants_without_lengths": str(no_lengths),
        "recorder": re.split(r"[;,]", first["observer"])[0].strip(),
    }


def corrected(row: Row, field: str, value: str) -> None:
    """Set one field of a row from a corrections.tsv value."""
    if field.startswith("tags."):
        row.tags[field.removeprefix("tags.")] = count_cell(value)
    elif field.endswith("_broken"):
        setattr(row, field, Cell(value=value == "yes" if value else None, raw=value))
    elif field in {"rep", "stipes"}:
        setattr(row, field, count_cell(value))
    elif field == "time":
        row.time = Cell(value=parse_time(value), raw=value)
    elif field == "depth_unit":
        row.depth_unit = unit_cell(value)
    else:
        setattr(row, field, number_cell(value))


def apply_corrections(documents: dict[str, SheetDocument]) -> None:
    """Apply data/answers/corrections.tsv, if it exists, to the documents."""
    path = OUTPUT_DIR / "corrections.tsv"
    if not path.exists():
        return
    with path.open(newline="") as file:
        for fix in csv.DictReader(file, delimiter="\t"):
            rows = documents[fix["file"]].pages[0].rows
            row = next(r for r in rows if r.entry_order == int(fix["entry_order"]))
            corrected(row, fix["field"], fix["value"])


def write_tsv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    """Write rows to a tab-separated file with the given columns."""
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    """Parse the answers and write the documents, issues and features."""
    configure_logging()
    colors, rows = read_answers()
    first = first_uses(rows, colors)
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        groups.setdefault((row["date"], row["site"]), []).append(row)
    pdfs = {
        tuple(path.name.split("_")[:2]): path.name for path in PDF_DIR.glob("*.pdf")
    }
    documents: dict[str, SheetDocument] = {}
    unmatched: list[dict[str, str]] = []
    issues: list[dict[str, str]] = []
    sheet_features: list[dict[str, str]] = []
    for (stamp, site), group in sorted(groups.items()):
        plants = runs(group)
        file = pdfs.get((stamp, site))
        issues.extend(check_plants(file or f"{stamp}_{site}", plants))
        if file is None:
            unmatched.append({"date": stamp, "site": site, "rows": str(len(group))})
            continue
        present = sheet_colors(group, colors, first)
        source = Source.from_filename(file, service="answers")
        rows_out = [
            build_row(plant, n, present) for n, plant in enumerate(plants, start=1)
        ]
        page = Page(page=1, header=build_header(group, present), rows=rows_out)
        documents[file] = SheetDocument(source=source, pages=[page])
        sheet_features.append(features(file, group, plants))
    apply_corrections(documents)
    output = OUTPUT_DIR / "fronds"
    output.mkdir(parents=True, exist_ok=True)
    for file, document in documents.items():
        # Rules that fire on verified data are too strict or found an answer error.
        document.flags = validate(document)
        path = output / f"{file.removesuffix('.pdf')}.json"
        path.write_text(document.model_dump_json(indent=1))
    write_tsv(OUTPUT_DIR / "unmatched.tsv", unmatched, ["date", "site", "rows"])
    issue_columns = ["file", "entry_order", "plant_ID", "check", "detail", "resolution"]
    write_tsv(OUTPUT_DIR / "issues.tsv", issues, issue_columns)
    write_tsv(OUTPUT_DIR / "features.tsv", sheet_features, list(sheet_features[0]))
    log.info(
        f"{len(documents)} sheets matched, {len(unmatched)} unmatched,"
        f" {len(issues)} issues -> {OUTPUT_DIR}"
    )


if __name__ == "__main__":
    main()
