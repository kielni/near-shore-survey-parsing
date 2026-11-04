-include local.env
export


.PHONY: lint prep extract map validate answers score clean-results report sheet

lint:
	uv run ruff check --fix .
	uv run ruff format .
	uv run mypy .

# Preprocess the scans (rotation), build contact sheets, and upload the
# preprocessed PDFs to S3.
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

# Score one service on the sheets marked scored in data/split.tsv, writing
# scores.json and errors.csv beside its results, e.g. make score SERVICE=aws_textract
score:
	uv run python -m comparison.score --service $(SERVICE)

# Delete one service's results, so extract runs its files again, e.g.
# make clean-results SERVICE=aws_textract, or SERVICE=llm_api/gpt-6-sol for
# one LLM model.
clean-results:
	@test -n "$(SERVICE)" || { echo "usage: make clean-results SERVICE=<service>"; exit 1; }
	@case "$(SERVICE)" in *..*) echo "SERVICE must not contain .."; exit 1;; esac
	rm -rf data/results/$(SERVICE)

# Gather the saved results into comparison/report/ (calls no service).
report:
	uv run python -m comparison.report

# Write every service's results to the demo Google Sheet (GOOGLE_SHEET_ID),
# one tab per service, in the human entry's layout.
sheet:
	uv run python -m comparison.sheet
