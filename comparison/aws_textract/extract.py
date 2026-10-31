"""Run AWS Textract on preprocessed survey PDFs in S3 and save each raw response.

Every file goes through the asynchronous StartDocumentAnalysis /
GetDocumentAnalysis API, which takes single- and multi-page PDFs alike, so
there is one code path. Responses are saved unmodified (apart from joining
result pages) to data/results/aws_textract/<file>.raw.json; the latest run
of a file replaces the earlier one.
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3

from comparison.config import configure_logging

if TYPE_CHECKING:
    from mypy_boto3_textract import TextractClient

log = logging.getLogger("aws_textract")


def start_job(textract: TextractClient, bucket: str, key: str) -> str:
    """Start table and form analysis of one PDF and return the job ID."""
    response = textract.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES", "FORMS"],
    )
    return response["JobId"]


def wait_for_job(textract: TextractClient, job_id: str) -> dict[str, Any]:
    """Poll a job until it finishes and return its full response.

    GetDocumentAnalysis returns at most 1,000 blocks per call, so the blocks
    of every result page are joined into one response, in the same shape as
    AnalyzeDocument, which Textractor can load directly.
    """
    while True:
        response = textract.get_document_analysis(JobId=job_id)
        log.info(f"{job_id}\t{response.get('JobStatus')}")
        if response["JobStatus"] != "IN_PROGRESS":
            break
        time.sleep(5)
    # A failed job has no Blocks, although the stubs mark the key required.
    blocks = list(response.get("Blocks", []))
    next_token = response.get("NextToken")
    while next_token:
        page = textract.get_document_analysis(JobId=job_id, NextToken=next_token)
        blocks.extend(page["Blocks"])
        next_token = page.get("NextToken")
    # A plain dict, since the stubs' TypedDict doesn't allow dropping
    # ResponseMetadata.
    result = {
        key: value
        for key, value in response.items()
        if key not in ("NextToken", "ResponseMetadata")
    }
    result["Blocks"] = blocks
    return result


def extract(files: list[str]) -> None:
    """Analyze each file with Textract and save the raw responses locally."""
    bucket = os.environ["S3_BUCKET"]
    region = os.environ["AWS_REGION"]
    textract = boto3.client("textract", region_name=region)
    # Start every job before waiting on any, so Textract works on them in
    # parallel.
    jobs = {name: start_job(textract, bucket, name) for name in files}
    log.info(f"started {len(jobs)} Textract jobs")
    for name, job_id in jobs.items():
        response = wait_for_job(textract, job_id)
        output = Path("data/results/aws_textract", f"{Path(name).stem}.raw.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(response))
        pages = response.get("DocumentMetadata", {}).get("Pages")
        status = response["JobStatus"]
        message = response.get("StatusMessage", "")
        log.info(f"{name}: {status} {message}, {pages} pages -> {output}")


def main() -> None:
    """Parse arguments and run extraction."""
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files",
        type=Path,
        required=True,
        help="text file listing one PDF S3 key per line, e.g. Fronds/x.pdf",
    )
    args = parser.parse_args()
    files = args.files.read_text().split()
    log.info(f"extracting {len(files)} files")
    extract(files)


if __name__ == "__main__":
    main()
