"""Write every service's results to a Google Sheet.

Write results for each service to a single sheet.
Clear and rewrite on every run; switch to append for ongoing use.
Use the same layout as the human entry sheet
(productivity_survey_FIRST_ENTRY - fronds_pull_down_long.tsv): one row per frond,
only what the service read, in sheet order (file, page, row, frond).
Copy site type and zone from the site list in the answers file; leave observer and 
buddy blank. Highlight low confidence cells, if available.

Set GOOGLE_SHEET_ID in local.env and share the sheet to the service account email
in in google-service-account.json.
"""

import csv
import logging
import os

import gspread
from gspread.utils import rowcol_to_a1
from gspread.worksheet import CellFormat

from comparison.config import configure_logging, mapped_paths
from comparison.report import RESULTS, SERVICES
from comparison.schema import Cell, Header, Row, SheetDocument
from comparison.score import FRONDS, normalized, scored_files
from comparison.validate import LOW_CONFIDENCE

log = logging.getLogger("sheet")

# Columns of the human entry, in its order.
COLUMNS = [
    "name_of_data_enterer",
    "site",
    "site_type",
    "zone",
    "date",
    "observer",
    "buddy",
    "depth_units",
    "plant_ID",
    "depth",
    "time",
    "total_frond",
    "number_grn",
    "number_wht",
    "number_ylw",
    "number_blk",
    "frond",
    "frond_length",
    "damaged_stipe",
    "notes",
]


def site_details() -> dict[str, tuple[str, str]]:
    """Return each site's type (FOR/BAR) and zone (Shallow/Deep), from the answers."""
    details: dict[str, tuple[str, str]] = {}
    with open("data/fronds_answers.tsv", newline="") as file:
        lines = list(csv.reader(file, delimiter="\t"))
    start = next(i for i, line in enumerate(lines) if line[0] == "name_of_data_enterer")
    for line in lines[start + 1 :]:
        if len(line) > 3 and line[1].strip():
            details[line[1].strip()] = (line[2].strip(), line[3].strip())
    return details


def is_low(cell: Cell | None) -> bool:
    """Return True if a cell was read with low confidence."""
    return (
        cell is not None
        and cell.confidence is not None
        and cell.confidence < LOW_CONFIDENCE
    )


def frond_rows(
    row: Row,
    header: Header,
    enterer: str,
    notes: str,
    sites: dict[str, tuple[str, str]],
) -> list[tuple[dict[str, str], set[str]]]:
    """Return one plant as rows in the human entry's layout, one per frond.

    Each row comes with its columns read with low confidence. A plant with
    no lengths gets one row with frond blank.
    """
    read: dict[str, Cell | None] = {
        "site": header.site,
        "date": header.survey_date,
        "depth_units": row.depth_unit,
        "plant_ID": row.rep,
        "depth": row.depth,
        "time": row.time,
        "total_frond": row.stipes,
    }
    for color in ["grn", "wht", "ylw", "blk"]:
        read[f"number_{color}"] = row.tags.get(color)
    low = {column for column, cell in read.items() if is_low(cell)}
    site = header.site.value or ""
    site_type, zone = sites.get(site, ("", ""))
    survey_date = header.survey_date.value
    plant = {
        "name_of_data_enterer": enterer,
        "site": site,
        "site_type": site_type,
        "zone": zone,
        "date": survey_date.strftime("%Y%m%d") if survey_date else "",
        "observer": "",
        "buddy": "",
        "depth_units": normalized(row.depth_unit.value),
        "plant_ID": normalized(row.rep.value),
        "depth": normalized(row.depth.value),
        "time": normalized(row.time.value),
        "total_frond": normalized(row.stipes.value),
        "notes": notes,
    }
    for color in ["grn", "wht", "ylw", "blk"]:
        cell = row.tags.get(color)
        plant[f"number_{color}"] = normalized(cell.value) if cell else ""
    fronds: list[tuple[dict[str, str], set[str]]] = []
    for n, name in enumerate(FRONDS, start=1):
        length_cell = getattr(row, name)
        broken_cell = getattr(row, f"{name}_broken")
        length, broken = length_cell.value, broken_cell.value
        if length is None and broken is None:
            continue
        values = plant | {
            "frond": str(n),
            "frond_length": normalized(length),
            "damaged_stipe": "yes" if broken else "no" if broken is False else "",
        }
        frond_low = {
            column
            for column, cell in [
                ("frond_length", length_cell),
                ("damaged_stipe", broken_cell),
            ]
            if is_low(cell)
        }
        fronds.append((values, low | frond_low))
    blank = {"frond": "", "frond_length": "", "damaged_stipe": ""}
    return fronds or [(plant | blank, low)]


def build_tab(
    folder: str, label: str, sites: dict[str, tuple[str, str]]
) -> tuple[list[list[str]], list[tuple[int, int]]]:
    """Return a service tab's rows (header first, in sheet order) and low cells.

    Low cells are (row, column), 0-based.
    """
    allowed = scored_files()
    values: list[list[str]] = [COLUMNS]
    low_cells: list[tuple[int, int]] = []
    for path in mapped_paths(RESULTS / folder):
        document = SheetDocument.model_validate_json(path.read_text())
        if document.source.file not in allowed:
            continue
        for page in document.pages:
            for row in page.rows:
                notes = row.row_text.value or ""
                for r, low in frond_rows(row, page.header, label, notes, sites):
                    low_cells += [
                        (len(values), i) for i, c in enumerate(COLUMNS) if c in low
                    ]
                    values.append([r[c] for c in COLUMNS])
    return values, low_cells


def write_tab(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    values: list[list[str]],
    low_cells: list[tuple[int, int]],
) -> None:
    """Clear a tab (creating it if needed), write it and highlight low cells.

    Clearing removes values and formatting, so no highlight outlives its run.
    """
    try:
        worksheet = spreadsheet.worksheet(title)
        worksheet.clear()
        # clear() keeps formatting, such as highlights from earlier runs.
        spreadsheet.batch_update(
            {
                "requests": [
                    {
                        "repeatCell": {
                            "range": {"sheetId": worksheet.id},
                            "cell": {},
                            "fields": "userEnteredFormat",
                        }
                    }
                ]
            }
        )
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title, rows=len(values), cols=len(COLUMNS)
        )
    worksheet.update(values, "A1")
    header = f"A1:{rowcol_to_a1(1, len(COLUMNS))}"
    formats: list[CellFormat] = [
        {"range": header, "format": {"textFormat": {"bold": True}}}
    ]
    formats += [
        {
            "range": rowcol_to_a1(row + 1, column + 1),
            # Light yellow.
            "format": {"backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.8}},
        }
        for row, column in low_cells
    ]
    worksheet.batch_format(formats)


def main() -> None:
    """Write one tab per service to the demo spreadsheet."""
    configure_logging()
    client = gspread.service_account(
        filename="google-service-account.json",
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    spreadsheet = client.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    sites = site_details()
    for service in SERVICES:
        values, low_cells = build_tab(service.folder, service.name, sites)
        title = service.folder.replace("/", " ")
        write_tab(spreadsheet, title, values, low_cells)
        log.info(f"{title}: {len(values) - 1} rows, {len(low_cells)} low confidence")


if __name__ == "__main__":
    main()
