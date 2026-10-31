"""Run Google Document AI Form Parser on preprocessed survey PDFs and save each result.

Each PDF is read from data/preprocessed/ and
sent as raw bytes with online processing, which takes up to 15 pages. The
response Document is saved unmodified to
data/results/google_document_ai/<file>.raw.json; the latest run of a file
replaces the earlier one.

Credentials and the Google Cloud project come from google-service-account.json;
the processor from DOCAI_LOCATION and DOCAI_PROCESSOR_ID in local.env.
"""

import argparse
import logging
import os
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud import documentai
from google.oauth2 import service_account

from comparison.config import configure_logging

log = logging.getLogger("google_document_ai")

RESULTS_DIR = Path("data/results/google_document_ai")


def make_client(
    location: str, credentials: service_account.Credentials
) -> documentai.DocumentProcessorServiceClient:
    """Return a Document AI client for a region."""
    options = ClientOptions(api_endpoint=f"{location}-documentai.googleapis.com")
    return documentai.DocumentProcessorServiceClient(
        credentials=credentials, client_options=options
    )


def extract(files: list[str]) -> None:
    """Process each file with Form Parser and save the raw Document JSON."""
    credentials = service_account.Credentials.from_service_account_file(
        "google-service-account.json"
    )
    location = os.environ["DOCAI_LOCATION"]
    client = make_client(location, credentials)
    # The processor is in the service account's own project.
    name = client.processor_path(
        credentials.project_id, location, os.environ["DOCAI_PROCESSOR_ID"]
    )
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    for key in files:
        content = (Path("data/preprocessed") / key).read_bytes()
        request = documentai.ProcessRequest(
            name=name,
            raw_document=documentai.RawDocument(
                content=content, mime_type="application/pdf"
            ),
        )
        document = client.process_document(request=request).document
        output = RESULTS_DIR / f"{Path(key).stem}.raw.json"
        output.write_text(documentai.Document.to_json(document))
        log.info(f"{key}: {len(document.pages)} pages -> {output}")


def main() -> None:
    """Parse arguments and run Form Parser on the listed files."""
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
