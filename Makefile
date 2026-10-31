-include local.env
export


.PHONY: lint prep extract

lint:
	uv run ruff check --fix .
	uv run ruff format .
	uv run mypy .

# Rotate samples upright, build contact sheets, and upload preprocessed PDFs
# to S3.
prep:
	uv run python -m comparison.preprocess
	aws s3 sync data/preprocessed/Fronds s3://$(S3_BUCKET)/Fronds/ \
		--exclude "*" --include "*.pdf"

# Run one service on the PDFs listed in FILES and save raw responses under
# data/results/<service>/<run_id>/,
# e.g. make extract SERVICE=aws_textract
# Services: aws_textract, google_document_ai, handwriting_ocr, llm_api
FILES ?= data/fronds_test.txt
extract:
	uv run python -m comparison.$(SERVICE).extract --files $(FILES)
