"""Run the Handwriting OCR custom extractor on single pages and save each result.

Each line of the --files list is `Fronds/<file>.pdf <page>`, one page per
sheet. The page is rendered from the preprocessed PDF in data/preprocessed/ to
PNG, uploaded with action=extractor, polled until processed, and the JSON
result saved unmodified to data/results/handwriting_ocr/<file>.raw.json.

A page whose raw.json already exists is skipped, as for every service (and
every upload spends a page credit); to run it again, clear the results
(make clean-results SERVICE=handwriting_ocr).
"""

import argparse
import logging
import os
import time
from pathlib import Path

import httpx

from comparison.config import configure_logging
from comparison.util import render_page

log = logging.getLogger("handwriting_ocr")

API = "https://api.handwritingocr.com/v3"
RESULTS_DIR = Path("data/results/handwriting_ocr")

# Seconds between requests: the API allows 2 requests per second.
REQUEST_GAP = 0.6


def request(
    client: httpx.Client,
    method: str,
    url: str,
    data: dict[str, str] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
) -> httpx.Response:
    """Send a request, keeping under the rate limit and waiting out a 429."""
    while True:
        time.sleep(REQUEST_GAP)
        response = client.request(method, url, data=data, files=files)
        if response.status_code != 429:
            return response
        wait = float(response.headers.get("Retry-After", 5))
        log.info(f"rate limited, waiting {wait:.0f} s")
        time.sleep(wait)


def upload(client: httpx.Client, name: str, png: bytes, extractor: str) -> str:
    """Upload one page image to the custom extractor and return the document ID."""
    response = request(
        client,
        "POST",
        "/documents",
        data={"action": "extractor", "extractor_id": extractor},
        files={"file": (f"{name}.png", png, "image/png")},
    )
    response.raise_for_status()
    return response.json()["id"]


def wait_for_result(client: httpx.Client, document_id: str) -> bytes | None:
    """Poll a document until it is processed and return its JSON result.

    Returns None if processing failed. Processing usually takes 15-30 s.
    """
    while True:
        response = request(client, "GET", f"/documents/{document_id}")
        response.raise_for_status()
        status = response.json()["status"]
        if status == "processed":
            break
        if status == "failed":
            return None
        time.sleep(5)
    result = request(client, "GET", f"/documents/{document_id}.json")
    result.raise_for_status()
    return result.content


def extract(lines: list[str]) -> None:
    """Upload every listed page not already saved, then save each result."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    headers = {
        "Authorization": f"Bearer {os.environ['HANDWRITING_OCR_API_KEY']}",
        "Accept": "application/json",
    }
    extractor = os.environ["HANDWRITING_OCR_EXTRACTOR"]
    with httpx.Client(base_url=API, headers=headers, timeout=60) as client:
        # Upload every page before waiting on any, so they process together.
        jobs: dict[Path, str] = {}
        for line in lines:
            key, page = line.split()
            stem = Path(key).stem
            output = RESULTS_DIR / f"{stem}.raw.json"
            if output.exists():
                log.info(f"{key}: {output} exists, skipped")
                continue
            png = render_page(Path("data/preprocessed") / key, int(page))
            jobs[output] = upload(client, f"{stem}_p{page}", png, extractor)
            log.info(f"{key} page {page}: uploaded as {jobs[output]}")
        for output, document_id in jobs.items():
            result = wait_for_result(client, document_id)
            if result is None:
                log.error(f"{document_id}: processing failed, nothing saved")
                continue
            output.write_bytes(result)
            log.info(f"{document_id} -> {output}")


def main() -> None:
    """Parse arguments and run the extractor on the listed pages."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files",
        type=Path,
        required=True,
        help="text file listing `Fronds/<file>.pdf <page>` per line",
    )
    args = parser.parse_args()
    lines = [line for line in args.files.read_text().splitlines() if line.strip()]
    log.info(f"extracting {len(lines)} pages")
    extract(lines)


if __name__ == "__main__":
    main()
