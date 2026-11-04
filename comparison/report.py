"""Gather the saved results into one static report: comparison/report/report.html.

Reads each service's scores.json, the mapped
results and the human entry (data/fronds_answers.tsv, a copy of
productivity_survey_FIRST_ENTRY - fronds_pull_down_long.tsv), and renders
report.html.j2. Calls no service. A cell matches if the values agree after
the normalizing scoring uses, and plants are paired as in scoring
(match_rows), so "match" means the same here as in the scores.
"""

import json
import logging
import subprocess
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel

from comparison import crops
from comparison.config import configure_logging, mapped_paths
from comparison.schema import Row, SheetDocument
from comparison.score import (
    ANSWERS_DIR,
    FRONDS,
    match_rows,
    row_values,
    scored_files,
)
from comparison.validate import LOW_CONFIDENCE

log = logging.getLogger("report")

OUTPUT = Path("comparison/report/report.html")
IMAGES = OUTPUT.parent / "img"
RESULTS = Path("data/results")


class Service(BaseModel):
    """A service in the report: its results folder, name, link and description."""

    folder: str
    name: str
    url: str
    description: str


# Services in report order.
SERVICES = [
    Service(
        folder="llm_api/gpt-6-sol",
        name="GPT-6 Sol (OpenAI)",
        url="https://platform.openai.com/docs/models",
        description="OpenAI's lower-cost language model. It reads each page image "
        "and returns every field as written; this project's code applies the "
        "sheet's conventions.",
    ),
    Service(
        folder="llm_api/gpt-6-astra",
        name="GPT-6 Astra (OpenAI)",
        url="https://platform.openai.com/docs/models",
        description="OpenAI's larger model, with the same instructions as Sol; "
        "it costs about 7 times as much. It read the 5 sample sheets only.",
    ),
    Service(
        folder="handwriting_ocr",
        name="Handwriting OCR",
        url="https://www.handwritingocr.com/",
        description="A handwriting transcription service. A custom extractor, set "
        "up in its dashboard, returns the sheet's fields. It read page 1 of the 5 "
        "sample sheets (free trial limit).",
    ),
    Service(
        folder="aws_textract",
        name="AWS Textract",
        url="https://aws.amazon.com/textract/",
        description="Amazon's document OCR. It finds tables and form fields and "
        "returns text with positions and confidence; this project's code turns its "
        "table into the sheet's fields.",
    ),
    Service(
        folder="google_document_ai",
        name="Google Document AI (Form Parser)",
        url="https://cloud.google.com/document-ai",
        description="Google's ready-made processor for tables and form fields.",
    ),
]

# Price per page, and where it comes from.
PRICES = {
    "llm_api/gpt-6-sol": (0.05, "actual, from tokens used on the sample sheets"),
    "llm_api/gpt-6-astra": (0.34, "actual, from tokens used on the sample sheets"),
    "handwriting_ocr": (0.15, "$15 per 100 pages, pay as you go; 5 page free trial"),
    "aws_textract": (0.065, "Tables + Forms, $0.015 + $0.05 per page"),
    "google_document_ai": (0.03, "Form Parser, $30 per 1,000 pages"),
}

# Field groups shown in accuracy by field, as named in scores.json.
FIELD_GROUPS = {
    "F_length": "frond lengths",
    "F_broken": "broken stipe (*)",
    "depth": "depth",
    "depth_unit": "depth unit",
    "time": "time",
    "rep": "rep",
    "tags": "tag counts",
    "stipes": "#stipes",
}

OVERVIEW_FILE = "20261016_SQPT01_FrondPull_1.8.pdf"

# Errors in the human entry of OVERVIEW_FILE, checked against the scan by
# hand: (rep, field) to the value on the sheet.
HUMAN_ERRORS = {("1", "F4"): "11.7"}


class Problem(BaseModel):
    """A common problem shown in the report, with where to find an example."""

    title: str
    kind: str
    file: str
    page: int
    text: str
    change: str
    entry_order: int = 0
    fields: list[str] = []
    band: tuple[float, float] = (0.0, 0.0)


# Common problems, each shown with a crop and what each service read. A
# "row" example names the human entry's plant (entry_order) and the fields
# to show; the crop is that plant's row, which on these sheets is in entry
# order. A "header" example crops the top of the page (as shares of its
# height) and shows the header fields each service read.
PROBLEMS: list[Problem] = [
    Problem(
        title="* for a broken stipe, written anywhere around the length",
        kind="row",
        file=OVERVIEW_FILE,
        page=1,
        entry_order=2,
        fields=["F4", "F5", "F7", "F9", "F10", "F12"],
        text="Divers write the * above, after or across the number; services miss "
        "it or read it as part of a neighboring value.",
        change="A broken-stipe column or checkbox per frond, instead of a mark "
        "on the length.",
    ),
    Problem(
        title="E for 11",
        kind="row",
        file=OVERVIEW_FILE,
        page=1,
        entry_order=8,
        fields=["F7", "F10"],
        text="E means 11 (E.6 is 11.6), a lab convention no OCR service knows; "
        "this project's code converts it when a service reads the E.",
        change="Write 11 in full.",
    ),
    Problem(
        title="Dropped decimal points",
        kind="row",
        file=OVERVIEW_FILE,
        page=1,
        entry_order=7,
        fields=["F5", "F11", "F12"],
        text="Services lose small decimal points and read 2.5 as 25. This "
        "project's code fixes lengths over 31 m, but not smaller ones.",
        change="Printed decimal boxes, or a printed decimal point in each cell.",
    ),
    Problem(
        title="Hand-added tag column (Blk)",
        kind="row",
        file=OVERVIEW_FILE,
        page=1,
        entry_order=3,
        fields=["tags.blk"],
        text="Divers add new tag colors by hand to the right of F12, outside the "
        "printed table, where table detection doesn't look.",
        change="Print blank extra tag columns on the form.",
    ),
    Problem(
        title="Circled depth unit, and units mixed within a sheet",
        kind="row",
        file=OVERVIEW_FILE,
        page=1,
        entry_order=2,
        fields=["depth", "depth_unit"],
        text="Divers circle the unit once in the header (Meters here), but write "
        "some rows in feet (29ft). OCR services don't report which word the diver "
        "circled.",
        change="A unit column (m / ft) on each row.",
    ),
    Problem(
        title="Site written in the OBS/BUD box",
        kind="header",
        file=OVERVIEW_FILE,
        page=1,
        band=(0.04, 0.16),
        text="Here the site code is in the OBS/BUD box and the SITE box is empty.",
        change="Data-entry guidance: site in the SITE box only.",
    ),
    Problem(
        title="Tick marks beside reps",
        kind="row",
        file="20260731_KF01S_FrondPull_1.8.pdf",
        page=1,
        entry_order=1,
        fields=["rep"],
        text="Divers tick plants off; services read the tick as a letter (J 1, V 2).",
        change="Data-entry guidance, or a separate checkbox for ticking off.",
    ),
    Problem(
        title="Transferred pages",
        kind="row",
        file="20260921_BR01D_FrondPull_1.8.pdf",
        page=2,
        entry_order=2,
        text="Page 2 repeats plants from page 1 (here rep 3), copied onto a clean page. "
        "Scoring ignores the copies, but a production system has to tell "
        "copies from new plants.",
        change="Scanning convention: scan the clean copy only, or mark copies.",
    ),
]


def write_image(content: bytes, name: str) -> str:
    """Write an image to comparison/report/img and return its path from the report."""
    (IMAGES / name).write_bytes(content)
    return f"{IMAGES.name}/{name}"


def commit() -> str:
    """Return the short hash of the latest commit, or a note if there isn't one."""
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "no commit"


def load_sheet(folder: str, file: str) -> tuple[SheetDocument, SheetDocument] | None:
    """Return a service's mapped sheet and its human entry, if both exist."""
    path = RESULTS / folder / f"{Path(file).stem}.json"
    answer = ANSWERS_DIR / path.name
    if not path.exists() or not answer.exists():
        return None
    return (
        SheetDocument.model_validate_json(path.read_text()),
        SheetDocument.model_validate_json(answer.read_text()),
    )


def results_by_service() -> list[dict[str, object]]:
    """Return the results table: one row per service, from its scores.json."""
    allowed = scored_files()
    rows: list[dict[str, object]] = []
    for service in SERVICES:
        folder = service.folder
        scores = json.loads((RESULTS / folder / "scores.json").read_text())
        pages = sum(
            len(SheetDocument.model_validate_json(p.read_text()).pages)
            for p in mapped_paths(RESULTS / folder)
            if p.name.replace(".json", ".pdf") in allowed
        )
        price, source = PRICES[folder]
        cells, sheets = scores["cells"], scores["sheets"]
        rows.append(
            {
                "name": service.name,
                "sheets": sheets,
                "pages": pages,
                "cells": cells,
                "matching": round(cells * scores["cell_accuracy"]),
                "accuracy": scores["cell_accuracy"],
                "rows": scores["rows"],
                "rows_matching": round(scores["rows"] * scores["row_accuracy"]),
                "mismatches_per_sheet": cells * (1 - scores["cell_accuracy"]) / sheets,
                "missed": scores["missed_plants"],
                "price": price,
                "price_source": source,
            }
        )
    return rows


def by_field() -> list[dict[str, object]]:
    """Return each service's accuracy per field group, from its scores.json."""
    rows: list[dict[str, object]] = []
    for service in SERVICES:
        scores = json.loads((RESULTS / service.folder / "scores.json").read_text())
        groups = scores["cell_accuracy_by_group"]
        rows.append(
            {"name": service.name, "groups": [groups.get(g) for g in FIELD_GROUPS]}
        )
    return rows


def column_order(name: str) -> int:
    """Return a field's place in the printed column order, rep..F12.

    Fields not printed on the form (depth unit, hand-added tag colors) go last.
    """
    printed = ["rep", "depth", "time", "stipes", "tags.grn", "tags.ylw", "tags.wht"]
    printed += FRONDS
    return printed.index(name) if name in printed else len(printed)


def columns(human: dict[str, str], service: dict[str, str]) -> list[dict[str, object]]:
    """Return a row's fields side by side, with lengths and their * together.

    A field matches if the normalized values agree, as in scoring.
    """
    shown: list[dict[str, object]] = []
    names = sorted(
        (n for n in human | service if not n.endswith("_broken")), key=column_order
    )
    for name in names:
        left, right = human.get(name, ""), service.get(name, "")
        match = left == right
        if name in FRONDS:
            broken = f"{name}_broken"
            left += "*" if human.get(broken) == "true" else ""
            right += "*" if service.get(broken) == "true" else ""
            match = match and human.get(broken, "") == service.get(broken, "")
        shown.append({"name": name, "human": left, "service": right, "match": match})
    return shown


def paired_rows(
    document: SheetDocument, answer: SheetDocument
) -> list[tuple[Row | None, Row | None]]:
    """Return (service row, human row) pairs as scoring pairs them.

    Extra rows have no human row, missed plants no service row.
    """
    extracted = [(page.page, row) for page in document.pages for row in page.rows]
    pairs, extra, missed = match_rows(extracted, answer.pages[0].rows)
    return [
        *((found[1], plant) for found, plant, _ in pairs),
        *((row, None) for _, row in extra),
        *((None, plant) for plant in missed),
    ]


def row_dicts(
    service: Row | None, human: Row | None, with_stipes: bool
) -> tuple[dict[str, str], dict[str, str]]:
    """Return a pair's human and service values as scoring normalizes them."""
    colors = list(
        dict.fromkeys(
            [*(human.tags if human else []), *(service.tags if service else [])]
        )
    )
    return (
        row_values(human, colors, with_stipes) if human else {},
        row_values(service, colors, with_stipes) if service else {},
    )


def overview(cell_crops: dict[int, dict[str, str]]) -> dict[str, object]:
    """Return the end-to-end example: one sheet, each service's mismatched rows.

    Each column carries its crop of the scan, where the sheet has that
    column printed.
    """
    services: list[dict[str, object]] = []
    for entry in SERVICES:
        folder, name = entry.folder, entry.name
        loaded = load_sheet(folder, OVERVIEW_FILE)
        if loaded is None:
            continue
        document, answer = loaded
        with_stipes = any(r.stipes.value is not None for r in answer.pages[0].rows)
        rows: list[dict[str, object]] = []
        matching = 0
        for service, human in paired_rows(document, answer):
            left, right = row_dicts(service, human, with_stipes)
            shown = columns(left, right)
            if service and human and all(c["match"] for c in shown):
                matching += 1
                continue
            # On this sheet the human entry is in sheet order, so a plant's
            # entry order is its row on the page.
            cells = cell_crops.get(human.entry_order or 0, {}) if human else {}
            for column in shown:
                # Header keys drop the "tags." prefix and are lowercase (f1).
                key = str(column["name"]).removeprefix("tags.").lower()
                column["crop"] = cells.get(key)
                column["on_sheet"] = HUMAN_ERRORS.get(
                    (left.get("rep", ""), str(column["name"]))
                )
            source = human or service
            rows.append(
                {
                    "rep": source.rep.value if source else None,
                    "how": "missed" if not service else "extra" if not human else "",
                    "cropped": bool(cells),
                    "columns": shown,
                }
            )
        services.append(
            {
                "id": folder.replace("/", "-"),
                "name": name,
                "matching": matching,
                "plants": len(answer.pages[0].rows),
                "rows": rows,
            }
        )
    return {"file": OVERVIEW_FILE, "services": services}


def problem_readings(
    problem: Problem, crop_ids: dict[tuple[str, int, int], str]
) -> dict[str, object]:
    """Return one common problem with its crop and what each service read."""
    file, page = problem.file, problem.page
    readings: list[dict[str, object]] = []
    # An example with no fields (transferred pages) shows its crop only.
    services = SERVICES if problem.kind == "header" or problem.fields else []
    for entry in services:
        name = entry.name
        loaded = load_sheet(entry.folder, file)
        if loaded is None:
            continue
        document, answer = loaded
        if problem.kind == "header":
            pages = [p for p in document.pages if p.page == page]
            if not pages:
                continue
            header = pages[0].header
            read = [
                {
                    "name": "OBS/BUD",
                    "service": header.observers.raw or "",
                    "match": True,
                },
                {
                    "name": "site used",
                    "service": header.site.value or "",
                    "match": True,
                },
            ]
            readings.append({"name": name, "columns": read})
            continue
        with_stipes = any(r.stipes.value is not None for r in answer.pages[0].rows)
        for service, human in paired_rows(document, answer):
            if human is None or human.entry_order != problem.entry_order:
                continue
            left, right = row_dicts(service, human, with_stipes)
            shown = [c for c in columns(left, right) if c["name"] in problem.fields]
            readings.append({"name": name, "columns": shown, "missed": service is None})
    if problem.kind == "header":
        crop = crop_ids.get((file, page, 0))
    else:
        crop = crop_ids.get((file, page, problem.entry_order))
    return problem.model_dump() | {"crop": crop, "readings": readings}


def build_crops() -> dict[tuple[str, int, int], str]:
    """Render every common problem's crop, once, to comparison/report/img.

    Returns the image path for each (file, page, row), row 0 being the
    header band.
    """
    wanted: set[tuple[str, int, int]] = set()
    for problem in PROBLEMS:
        row = problem.entry_order if problem.kind == "row" else 0
        wanted.add((problem.file, problem.page, row))
    ids: dict[tuple[str, int, int], str] = {}
    headers = {p.file: p.band for p in PROBLEMS if p.kind == "header"}
    for file, page, row in sorted(wanted):
        png: bytes | None
        if row == 0:
            png = crops.region_png(file, page, *headers[file])
        else:
            png = crops.row_png(file, page, row)
        if png is None:
            log.warning(f"{file} page {page} row {row}: no crop")
            continue
        ids[(file, page, row)] = write_image(png, f"crop-{len(ids)}.png")
    return ids


def build_cell_crops() -> dict[int, dict[str, str]]:
    """Render the end-to-end sheet's plant rows, one crop per printed column.

    Returns the image paths by row (1-based) and column, keyed as
    crops.table_layout keys the columns.
    """
    paths: dict[int, dict[str, str]] = {}
    for row in range(1, 9):  # the sheet's 8 plants
        cells = crops.cell_pngs(OVERVIEW_FILE, 1, row)
        if not cells:
            log.warning(f"{OVERVIEW_FILE} row {row}: no crop")
        paths[row] = {
            key: write_image(png, f"cell-{row}-{key}.png") for key, png in cells.items()
        }
    return paths


def main() -> None:
    """Gather the results and write comparison/report/report.html."""
    configure_logging()
    IMAGES.mkdir(parents=True, exist_ok=True)
    crop_ids = build_crops()
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent),
        autoescape=select_autoescape(["html", "j2"]),
    )
    html = environment.get_template("report.html.j2").render(
        generated=date.today().isoformat(),
        commit=commit(),
        low_confidence=LOW_CONFIDENCE,
        service_list=SERVICES,
        services=results_by_service(),
        field_names=list(FIELD_GROUPS.values()),
        fields=by_field(),
        overview=overview(build_cell_crops()),
        problems=[problem_readings(p, crop_ids) for p in PROBLEMS],
    )
    OUTPUT.write_text(html)
    log.info(f"wrote {OUTPUT} ({OUTPUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
