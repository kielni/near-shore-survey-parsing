-include local.env
export


.PHONY: lint prep extract map validate answers score

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

# Run one service on the PDFs listed in FILES and save raw responses to
# data/results/<service>/<file>.raw.json; the latest run of a file replaces
# the earlier one. E.g. make extract SERVICE=aws_textract
# Services: aws_textract, google_document_ai, handwriting_ocr, llm_api
# File lists: data/fronds_sample.txt (the 5 sample sheets used while
# developing; the default) and data/fronds_remaining.txt (the other scored
# sheets).
FILES ?= data/fronds_sample.txt
extract:
	uv run python -m comparison.$(SERVICE).extract --files $(FILES)

# Map one service's raw responses to the sheet schema, writing <file>.json
# beside each raw.json, e.g. make map SERVICE=aws_textract
map:
	uv run python -m comparison.$(SERVICE).map

# Check one service's mapped sheets against the validation rules, writing
# the flags into each <file>.json, e.g. make validate SERVICE=aws_textract
validate:
	uv run python -m comparison.validate --service $(SERVICE)

# Parse the verified answers into data/answers/: schema documents, unmatched
# groups, QA issues and per-sheet features.
answers:
	uv run python -m comparison.answers

# Score one service against the answers, writing scores.json and errors.csv
# beside its results, e.g. make score SERVICE=aws_textract SPLIT=scored.
# Leave SPLIT empty to score every answered sheet.
score:
	uv run python -m comparison.score --service $(SERVICE) \
		$(if $(SPLIT),--split $(SPLIT))
