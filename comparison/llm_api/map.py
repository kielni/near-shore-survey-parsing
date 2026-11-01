"""Map saved OpenAI transcriptions to the sheet schema, by way of the grid.

Reads data/results/llm_api/<model>/<file>.raw.json (one whole response per
page), for every model in config.MODELS, and writes <file>.json next to it. Each page's output parses as schema.Page,
with every value as written; comparison/grid.py applies the conventions.

Columns are rep, depth, time, #stipes, the page's tag columns as written, and
F1-F12. row_text goes in a column with no header, which the grid reads as
free text. Fields the model lists as uncertain get confidence 0.0, the rest
1.0. notes become marginalia.
"""

import json
import logging
from pathlib import Path

from comparison.config import configure_logging
from comparison.grid import Grid, GridPage, Text, grid_to_sheet
from comparison.llm_api.config import MODELS, results_dir
from comparison.llm_api.schema import Page
from comparison.schema import Source

log = logging.getLogger("llm_api")

FRONDS = [f"F{n}" for n in range(1, 13)]


def output_text(response: dict) -> str:
    """Return the text of a saved response's message output."""
    return "".join(
        content["text"]
        for item in response["output"]
        if item["type"] == "message"
        for content in item["content"]
        if content["type"] == "output_text"
    )


def cell(text: str, uncertain: bool) -> Text:
    """Return a grid cell, with confidence 0.0 if the model was unsure."""
    return Text(text=text, confidence=0.0 if uncertain else 1.0)


def map_page(number: int, page: Page) -> GridPage:
    """Build the grid for one transcribed page."""
    header = {
        "observers": Text(text=page.obsbud, confidence=1.0),
        "site": Text(text=page.site, confidence=1.0),
        "survey_date": Text(text=page.date, confidence=1.0),
        "depth_units": Text(text=page.depth_units, confidence=1.0),
    }
    names = ["rep", "depth", "time", "#stipes", *page.tag_columns, *FRONDS, ""]
    rows: list[list[Text]] = []
    for row in page.rows:
        unsure = set(row.uncertain)
        tags = (row.tags + [""] * len(page.tag_columns))[: len(page.tag_columns)]
        if len(row.frond_lengths) > 12:
            log.warning(f"page {number}: {len(row.frond_lengths)} lengths, kept 12")
        lengths = (row.frond_lengths + [""] * 12)[:12]
        rows.append(
            [
                cell(row.rep, "rep" in unsure),
                cell(row.depth, "depth" in unsure),
                cell(row.time, "time" in unsure),
                cell(row.stipes, "stipes" in unsure),
                *(
                    cell(tag, bool({"tags", column} & unsure))
                    for tag, column in zip(tags, page.tag_columns)
                ),
                *(cell(length, "frond_lengths" in unsure) for length in lengths),
                cell(row.row_text, "row_text" in unsure),
            ]
        )
    return GridPage(
        page=number,
        header=header,
        columns=[Text(text=name) for name in names],
        rows=rows,
        lines=[Text(text=note) for note in page.notes],
    )


def map_file(path: Path, model: str) -> Path:
    """Map one raw.json to <file>.json beside it and return its path."""
    stem = path.name.removesuffix(".raw.json")
    source = Source.from_filename(f"{stem}.pdf", service=f"llm_api/{model}")
    responses = json.loads(path.read_text())
    pages = [
        map_page(number, Page.model_validate_json(output_text(response)))
        for number, response in enumerate(responses, start=1)
    ]
    output = path.with_name(f"{stem}.json")
    output.write_text(
        grid_to_sheet(Grid(source=source, pages=pages)).model_dump_json(indent=1)
    )
    return output


def main() -> None:
    """Map every saved raw.json of every model in MODELS."""
    configure_logging()
    for model in MODELS:
        paths = sorted(results_dir(model).glob("*.raw.json"))
        log.info(f"model {model}: mapping {len(paths)} files")
        for path in paths:
            log.info(f"wrote {map_file(path, model)}")


if __name__ == "__main__":
    main()
