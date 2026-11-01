"""Transcribe preprocessed survey PDFs with OpenAI models and save each response.

One request per page: the page is rendered from data/preprocessed/ to PNG and
sent with prompt.txt, at detail "original" with high reasoning effort and
high verbosity, with strict structured output (schema.Page). A sheet is
saved only if every page's output parses, as a list of the pages' whole
responses in page order, to data/results/llm_api/<model>/<file>.raw.json, so
each model's results are kept apart. A file whose raw.json already exists for
a model is skipped; to run it again, clear the results
(make clean-results SERVICE=llm_api, or SERVICE=llm_api/<model> for one model).

Token usage is logged per page and written to tokens.json in the same folder,
keyed by file, for every sheet sent, saved or not, since every page is billed.

Every model in config.MODELS is run, one after another; settings follow
[Getting the Most out of GPT-5.4 for Vision and Document Understanding](https://developers.openai.com/cookbook/examples/multimodal/document_and_multimodal_understanding_tips)
"""

import argparse
import base64
import json
import logging
from pathlib import Path

import pymupdf
from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from comparison.config import configure_logging
from comparison.llm_api.config import MODELS, results_dir
from comparison.llm_api.schema import Page
from comparison.util import render_page

log = logging.getLogger("llm_api")

PROMPT = Path(__file__).with_name("prompt.txt").read_text()


def transcribe_page(
    client: OpenAI, model: str, png: bytes, usage: list[dict[str, int]]
) -> dict:
    """Send one page image and return the whole response as a dict.

    The page's token usage is appended to usage before the output is checked,
    so a page that is billed but doesn't parse is still counted.
    """
    image = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    response = client.responses.create(
        model=model,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": PROMPT},
                    {"type": "input_image", "image_url": image, "detail": "original"},
                ],
            }
        ],
        reasoning={"effort": "high"},
        text={
            "verbosity": "high",
            "format": {
                "type": "json_schema",
                "name": "frond_sheet_page",
                "schema": Page.model_json_schema(),
                "strict": True,
            },
        },
    )
    tokens = {"input_tokens": 0, "output_tokens": 0}
    if response.usage is not None:
        tokens = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        }
    usage.append(tokens)
    log.info(
        f"page {len(usage)}: {tokens['input_tokens']} input, {tokens['output_tokens']} output tokens"
    )
    # Raises on a refusal, an incomplete response or output that doesn't
    # match the schema, so the sheet isn't saved.
    Page.model_validate_json(response.output_text)
    return response.model_dump(mode="json")


def extract_model(client: OpenAI, model: str, files: list[str]) -> None:
    """Transcribe every page of each file with one model and save the responses.

    A page that fails (an API error, or output that doesn't parse) is logged
    with its file and page, nothing is saved for that file, and the run moves
    on to the next file; failed files are listed at the end.
    """
    output_dir = results_dir(model)
    output_dir.mkdir(parents=True, exist_ok=True)
    tokens_path = output_dir / "tokens.json"
    tokens = json.loads(tokens_path.read_text()) if tokens_path.exists() else {}
    failed: list[str] = []
    for key in files:
        output = output_dir / f"{Path(key).stem}.raw.json"
        if output.exists():
            log.info(f"{key}: {output} exists, skipped")
            continue
        pdf = Path("data/preprocessed") / key
        with pymupdf.open(pdf) as doc:
            page_count = doc.page_count
        usage: list[dict[str, int]] = []
        pages: list[dict] = []
        for n in range(1, page_count + 1):
            try:
                pages.append(
                    transcribe_page(
                        client, model, render_page(pdf, n, resize=True), usage
                    )
                )
            except (OpenAIError, ValidationError) as error:
                log.error(f"{key} page {n} ({model}): {error}")
                break
        # A rerun of a file (after clearing its results) replaces its entry.
        tokens[Path(key).stem] = usage
        tokens_path.write_text(json.dumps(tokens, indent=1))
        if len(pages) < page_count:
            log.error(f"{key} ({model}): failed, nothing saved")
            failed.append(key)
            continue
        output.write_text(json.dumps(pages, indent=1))
        log.info(f"{key}: {page_count} pages ({pages[0]['model']}) -> {output}")
    if failed:
        log.error(f"{model}: {len(failed)} files failed: {', '.join(failed)}")


def extract(files: list[str]) -> None:
    """Transcribe the files with every model in MODELS."""
    client = OpenAI()
    for model in MODELS:
        log.info(f"model {model}")
        extract_model(client, model, files)


def main() -> None:
    """Parse arguments and transcribe the listed files."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files",
        type=Path,
        required=True,
        help="text file listing one Fronds/<file>.pdf per line",
    )
    args = parser.parse_args()
    files = args.files.read_text().split()
    log.info(f"extracting {len(files)} files")
    extract(files)


if __name__ == "__main__":
    main()
