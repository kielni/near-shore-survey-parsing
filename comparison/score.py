"""Score one service's mapped sheets against the verified answers.

Reads data/results/<service>/<file>.json and the matching
data/answers/fronds/<file>.json, pairs extracted rows with answer plants
(match_rows), and compares cell by cell. Writes, next to the results:

- scores.json: cell and row accuracy, flag recall and rate, rare-case
  accuracy, and validation-flag precision per rule;
- errors.csv: every wrong cell, with how its row was paired.

Scoring is per row: each extracted row is compared with the answer plant it
pairs with, and an extracted row with no plant counts as wrong. An answer
plant with no extracted row is not scored. It is counted as missed only on
sheets where every page was extracted: the answers don't record pages, so
on a partial sheet (Handwriting OCR gets only page 1 of some) a plant on a
page that wasn't sent can't be told from a real miss. Pages not sent don't
affect any score. A cell blank in both the answer and the extraction is not
scored, so the empty F9-F12 of small plants don't pad accuracy. Only sheets
marked scored in data/split.tsv are scored: version 1.8 sheets with answers.
"""

import argparse
import csv
import json
import logging
from pathlib import Path

from pydantic import BaseModel

from comparison.config import configure_logging, mapped_paths, results_dirs
from comparison.schema import Cell, Flag, Row, SheetDocument

log = logging.getLogger("score")

ANSWERS_DIR = Path("data/answers/fronds")
SPLIT = Path("data/split.tsv")

FRONDS = [f"F{n}" for n in range(1, 13)]

# Cells compared when pairing leftover rows by agreement, and how many must
# agree for a pair.
AGREEMENT_FIELDS = ["depth", "time", "stipes", "F1", "F2", "F3"]
MIN_AGREEMENT = 3


class ScoredCell(BaseModel):
    """One compared cell: what was read, what the answer says, and flags."""

    file: str
    page: int | None
    sheet_row: int | None
    entry_order: int | None
    rep: int | None
    field: str
    predicted: str
    expected: str
    correct: bool
    rules: list[str]
    # How the row was paired: rep, cells or extra; header for the
    # page header.
    match: str
    # Answer facts for rare-case accuracy.
    broken: bool = False
    feet: bool = False


def normalized(value: object) -> str:
    """Return a value as comparable text: numbers rounded, None blank."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float | int):
        return f"{float(value):g}"
    return str(value)


def row_values(row: Row, colors: list[str], with_stipes: bool) -> dict[str, str]:
    """Return a row's scored fields as comparable text."""
    cells: dict[str, Cell] = {
        "rep": row.rep,
        "depth": row.depth,
        "depth_unit": row.depth_unit,
        "time": row.time,
    }
    if with_stipes:
        cells["stipes"] = row.stipes
    for color in colors:
        cells[f"tags.{color}"] = row.tags.get(color, Cell())
    for name in FRONDS:
        cells[name] = getattr(row, name)
        cells[f"{name}_broken"] = getattr(row, f"{name}_broken")
    return {name: normalized(cell.value) for name, cell in cells.items()}


def has_values(row: Row) -> bool:
    """Return True if any value cell of the row was read (not just row_text)."""
    values = row_values(row, list(row.tags), with_stipes=True)
    return any(value for value in values.values())


def agreement(extracted: Row, answer: Row) -> int:
    """Count the pairing fields and tag counts where two rows agree."""
    left = row_values(extracted, list(answer.tags), with_stipes=True)
    right = row_values(answer, list(answer.tags), with_stipes=True)
    fields = AGREEMENT_FIELDS + [f"tags.{color}" for color in answer.tags]
    return sum(1 for f in fields if left[f] and left[f] == right[f])


def match_rows(
    extracted: list[tuple[int, Row]], answers: list[Row]
) -> tuple[list[tuple[tuple[int, Row], Row, str]], list[tuple[int, Row]], list[Row]]:
    """Pair extracted rows (page, row) with answer plants for one sheet.

    1. By rep: the k-th occurrence of a rep in page-then-row order pairs
       with its k-th occurrence in entry order.
    2. Leftovers by the most agreeing cells, at least MIN_AGREEMENT, which
       catches misread reps.
    3. What remains: a row whose rep already paired on another page is a
       transferred copy and is dropped; so is a row with row_text, since
       the answers skip dead and missing plants ("MISSING", "Dead?");
       other extracted rows are extra; answer plants are missed.

    Returns the pairs with how each was made, the extra rows and the
    missed plants.
    """
    rows = [item for item in extracted if has_values(item[1])]
    pairs: list[tuple[tuple[int, Row], Row, str]] = []
    left_rows = list(rows)
    left_answers = list(answers)
    for answer in answers:
        rep = answer.rep.value
        partner = next(
            (r for r in left_rows if rep is not None and r[1].rep.value == rep), None
        )
        if partner is not None:
            pairs.append((partner, answer, "rep"))
            left_rows.remove(partner)
            left_answers.remove(answer)
    candidates = sorted(
        (
            (agreement(row[1], answer), index, answer_index)
            for index, row in enumerate(left_rows)
            for answer_index, answer in enumerate(left_answers)
        ),
        key=lambda item: -item[0],
    )
    used_rows: set[int] = set()
    used_answers: set[int] = set()
    for count, index, answer_index in candidates:
        if count < MIN_AGREEMENT:
            break
        if index in used_rows or answer_index in used_answers:
            continue
        pairs.append((left_rows[index], left_answers[answer_index], "cells"))
        used_rows.add(index)
        used_answers.add(answer_index)
    paired = {(row[1].rep.value, row[0]) for row, _, _ in pairs}
    reps = {rep for rep, _ in paired}
    extra = [
        row
        for i, row in enumerate(left_rows)
        if i not in used_rows
        and not row[1].row_text.value
        and not (row[1].rep.value in reps and (row[1].rep.value, row[0]) not in paired)
    ]
    missed = [a for i, a in enumerate(left_answers) if i not in used_answers]
    return pairs, extra, missed


def flag_rules(
    flags: list[Flag], page: int, sheet_row: int | None, field: str
) -> list[str]:
    """Return the rules flagging one cell: its own flags, or its row's."""
    rules = []
    for flag in flags:
        if flag.page != page or flag.sheet_row != sheet_row:
            continue
        if flag.field is None or flag.field == field:
            rules.append(flag.rule)
    return rules


def score_sheet(
    document: SheetDocument, answer: SheetDocument
) -> tuple[list[ScoredCell], int]:
    """Compare one extracted sheet with its answer, row by row.

    Returns the compared cells and the number of answer plants with no
    extracted row, which are not scored.
    """
    file = document.source.file
    flags = document.flags
    answer_page = answer.pages[0]
    answer_header = answer_page.header
    cells: list[ScoredCell] = []
    for page in document.pages:
        for name in ("site", "survey_date"):
            predicted = normalized(getattr(page.header, name).value)
            expected = normalized(getattr(answer_header, name).value)
            cells.append(
                ScoredCell(
                    file=file,
                    page=page.page,
                    sheet_row=None,
                    entry_order=None,
                    rep=None,
                    field=name,
                    predicted=predicted,
                    expected=expected,
                    correct=predicted == expected,
                    rules=flag_rules(flags, page.page, None, name),
                    match="header",
                )
            )
    with_stipes = any(row.stipes.value is not None for row in answer_page.rows)
    extracted = [(page.page, row) for page in document.pages for row in page.rows]
    pairs, extra, missed = match_rows(extracted, answer_page.rows)
    compared: list[tuple[tuple[int, Row] | None, Row | None, str]] = [
        *pairs,
        *((row, None, "extra") for row in extra),
    ]
    for found, plant, how in compared:
        page_number, row = found if found else (None, None)
        colors = list(
            dict.fromkeys([*(plant.tags if plant else []), *(row.tags if row else [])])
        )
        predicted_values = row_values(row, colors, with_stipes) if row else {}
        expected_values = row_values(plant, colors, with_stipes) if plant else {}
        for field in expected_values or predicted_values:
            predicted = predicted_values.get(field, "")
            expected = expected_values.get(field, "")
            if not predicted and not expected:
                continue
            frond = field.removesuffix("_broken")
            cells.append(
                ScoredCell(
                    file=file,
                    page=page_number,
                    sheet_row=row.sheet_row if row else None,
                    entry_order=plant.entry_order if plant else None,
                    rep=plant.rep.value if plant else (row.rep.value if row else None),
                    field=field,
                    predicted=predicted,
                    expected=expected,
                    correct=predicted == expected,
                    rules=(
                        flag_rules(flags, page_number, row.sheet_row, field)
                        if row and page_number is not None
                        else []
                    ),
                    match=how,
                    broken=bool(plant and getattr(plant, f"{frond}_broken").value)
                    if frond in FRONDS
                    else False,
                    feet=bool(plant and plant.depth_unit.value == "Feet"),
                )
            )
    return cells, len(missed)


def accuracy(cells: list[ScoredCell]) -> float | None:
    """Return the share of cells that are correct, or None with no cells."""
    return sum(c.correct for c in cells) / len(cells) if cells else None


def precision(flagged: list[ScoredCell]) -> float | None:
    """Return the share of flagged cells that are wrong, or None with none."""
    return sum(not c.correct for c in flagged) / len(flagged) if flagged else None


def metrics(cells: list[ScoredCell], missed: int) -> dict[str, object]:
    """Compute the scores for the compared cells; missed plants are reported."""
    wrong = [c for c in cells if not c.correct]
    flagged = [c for c in cells if c.rules]
    by_field: dict[str, list[ScoredCell]] = {}
    for cell in cells:
        by_field.setdefault(cell.field, []).append(cell)
    rows: dict[tuple[str, int | None, int | None, int | None], list[ScoredCell]] = {}
    for cell in cells:
        if cell.match != "header":
            key = (cell.file, cell.page, cell.sheet_row, cell.entry_order)
            rows.setdefault(key, []).append(cell)
    rules = sorted({rule for c in cells for rule in c.rules})
    return {
        "cells": len(cells),
        "cell_accuracy": accuracy(cells),
        "cell_accuracy_by_field": {f: accuracy(c) for f, c in sorted(by_field.items())},
        "rows": len(rows),
        "row_accuracy": (
            sum(all(c.correct for c in r) for r in rows.values()) / len(rows)
            if rows
            else None
        ),
        "extra_rows": len({k for k, r in rows.items() if r[0].match == "extra"}),
        # Answer plants with no extracted row; not in the accuracies.
        "missed_plants": missed,
        "flag_recall": sum(bool(c.rules) for c in wrong) / len(wrong)
        if wrong
        else None,
        "flag_rate": len(flagged) / len(cells) if cells else None,
        "rare_case_accuracy": {
            "broken": accuracy(
                [c for c in cells if c.broken and c.field.endswith("_broken")]
            ),
            "feet_rows": accuracy([c for c in cells if c.feet]),
        },
        "flag_precision_by_rule": {
            rule: precision([c for c in cells if rule in c.rules]) for rule in rules
        },
    }


def scored_files() -> dict[str, int]:
    """Return the files marked scored in data/split.tsv, with their page counts."""
    with SPLIT.open(newline="") as file:
        return {
            row["file"]: int(row["pages"])
            for row in csv.DictReader(file, delimiter="\t")
            if row["split"] == "scored"
        }


def score_results(results_dir: Path) -> None:
    """Score every scored sheet in one results folder and write the results."""
    label = str(results_dir.relative_to("data/results"))
    paths = mapped_paths(results_dir)
    allowed = scored_files()
    cells: list[ScoredCell] = []
    missed = 0
    scored = 0
    for path in paths:
        document = SheetDocument.model_validate_json(path.read_text())
        answer_path = ANSWERS_DIR / path.name
        if document.source.file not in allowed:
            log.info(f"{path.name}: not a scored sheet")
            continue
        if not answer_path.exists():
            log.info(f"{path.name}: no answers, not scored")
            continue
        answer = SheetDocument.model_validate_json(answer_path.read_text())
        sheet_cells, sheet_missed = score_sheet(document, answer)
        cells.extend(sheet_cells)
        if len(document.pages) < allowed[document.source.file]:
            log.info(
                f"{path.name}: not every page extracted, missed plants not counted"
            )
        else:
            missed += sheet_missed
        scored += 1
    scores = {
        "service": label,
        "sheets": scored,
    }
    scores.update(metrics(cells, missed))
    (results_dir / "scores.json").write_text(json.dumps(scores, indent=1))
    with (results_dir / "errors.csv").open("w", newline="") as file:
        columns = [
            "file",
            "page",
            "sheet_row",
            "entry_order",
            "rep",
            "field",
            "predicted",
            "expected",
            "rules",
            "match",
        ]
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for cell in cells:
            if not cell.correct:
                writer.writerow(cell.model_dump() | {"rules": " ".join(cell.rules)})
    log.info(
        f"{label}: {scored} sheets, {len(cells)} cells,"
        f" cell accuracy {scores['cell_accuracy']:.3f} -> {results_dir / 'scores.json'}"
    )


def main() -> None:
    """Parse arguments and score each of a service's results folders."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, help="e.g. aws_textract")
    args = parser.parse_args()
    for results_dir in results_dirs(args.service):
        score_results(results_dir)


if __name__ == "__main__":
    main()
