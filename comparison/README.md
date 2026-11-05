# Compare automated parsing services for a handwritten kelp frond data collection sheet

## Introduction

Divers record kelp frond pull-down surveys by hand on printed data sheets. Two people transcribe each sheet into a Google Sheet, then an R script compares the two entries and flags differences for review.

This project tests whether automated parsing services could replace one of those entries, by comparing each service's reading with the human entry. It rotates the scanned sheets, sends them to each service, and saves each service's response in its native format. It then maps the responses into a shared schema, validates them, and scores them against the human entry. Finally, it generates a report that compares the services and shows the features that are hardest to parse.

## Dataset

- **Scanned sheets:** 27 PDFs of frond pull-down sheets, some with several pages (one paper sheet each). Of these, 22 have a human entry; the comparison uses only these 22.
- **Human entry:** the lab's first data entry; the comparison measures agreement with it.
- **Sample sheets:** 5 of the 22 (`data/fronds_sample.txt`), used while building each service's mapping; `data/fronds_remaining.txt` lists the other 17.

## Methodology

To verify the workflow, I started with the same set of 5 PDFs for each service. After confirming that the code worked as I expected, I parsed the 17 remaining sheets with GPT-6 Astra, AWS Textract, and Google Document AI. I did not parse the remaining sheets with Handwriting OCR or GPT-6 Astra. Handwriting OCR offers a free trial limited to 5 pages. GPT-6 Astra scored a few points higher than GPT-6 Sol, but not by enough to justify its price.

A preprocessing step (`make prep`) rotates the scanned sheets so that they are landscape and right side up. Every service starts from these preprocessed PDFs.

Then each service runs its own version of two steps (`make <step> SERVICE=<service>`):

- **extract:** send each sheet to the service and save its response unmodified (`data/results/<service>/<file>.raw.json`). Each service takes the input format it accepts: Textract and Document AI read the preprocessed PDF directly; Handwriting OCR and the LLMs read one PNG per page. Every service returns JSON in its own schema. Handwriting OCR generates an extractor template from an uploaded sample form. The LLMs share one prompt, based on the lab's data entry directions.
- **map:** turn the response into one shared format (a Pydantic model), applying the sheet's conventions in one place (`grid.py`: `E` means 11, `*` marks a broken stipe, and so on), so the same rules judge every service.

The validate step (`make validate`) checks each service's mapped sheets against fixed rules and adds a flag wherever a value looks wrong. It catches unreadable or low-confidence cells, lengths or depths out of range, frond lengths not filled left to right, more fronds than stipes, tag counts that don't add up, a duplicate rep, and a header site or date that disagrees with the filename. The flags mark cells for review and never change a value.

The score step (`make score`) compares each service's mapped sheets with the human entry downloaded from the lab's Google Sheet. It pairs the service's rows with the human entry's plants by rep, then by agreeing values, and compares them cell by cell after normalizing, skipping cells blank in both. It writes `scores.json` (cell and row accuracy, missed plants and extra rows, and how well the validation flags find wrong cells) and `errors.csv`, every cell that disagrees, beside the service's results.

Finally, the report step (`make report`) reads the scores and mapped results and renders them as HTML with a Jinja2 template. The report compares accuracy by service and by field, walks through one sheet row by row, and shows an example of each common problem.

Claude (Claude Code) wrote most of the code, working from the project owner's plan and requests; the owner chose the approach and reviewed each change.

### Services

- [AWS Textract](https://aws.amazon.com/textract/): Amazon's document OCR, which finds tables and form fields and returns text with positions and confidence.
- [Google Document AI (Form Parser)](https://cloud.google.com/document-ai): Google's ready-made processor for tables and form fields.
- [Handwriting OCR](https://www.handwritingocr.com/): a handwriting transcription service, with a custom extractor set up in its dashboard to return the sheet's fields.
- [GPT-6 Sol and GPT-6 Astra (OpenAI)](https://platform.openai.com/docs/models): language models that read each page image and return every field as written; Astra is the larger model, at about 7 times the cost.

## Results

| Service | PDFs | Pages | Cells | Matching | Rows matching | Mismatches per sheet | Missed plants | Per page |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GPT-6 Astra (OpenAI) | 5 | 8 | 1,107 | 93.9% | 15 of 39 | 14 | 1 | $0.340 |
| Handwriting OCR | 5 | 5 | 1,041 | 92.9% | 11 of 37 | 15 | 0 | $0.150 |
| GPT-6 Sol (OpenAI) | 22 | 36 | 4,321 | 89.7% | 31 of 173 | 20 | 2 | $0.050 |
| AWS Textract | 22 | 36 | 4,330 | 74.0% | 1 of 182 | 51 | 4 | $0.065 |
| Google Document AI (Form Parser) | 22 | 36 | 1,853 | 13.1% | 0 of 152 | 73 | 135 | $0.030 |

**PDF**: one survey's scan (one date and site); **pages** counts its paper sheets, as a survey can fill several.\
**Matching**: the share of compared cells that agree with the human entry.\
**Mismatches per sheet**: the cells someone would check for each sheet; a proxy for the reconciliation workload.\
**Missed plants**: plants in the human entry with no row read (counted only where the service read every page).

## Discussion

The LLMs and Handwriting OCR were much more accurate than the table-based OCR services. They read the handwriting fairly well, including `*` marks and the hand-added `Blk` column. Handwriting OCR offers a free trial limited to 5 pages; after that, its [Pay as You Go plan](https://www.handwritingocr.com/#pricing) costs $15 for 100 pages. GPT-6 Astra scored a few points higher than GPT-6 Sol, but on only 5 sheets, and not by enough to justify its price ($0.34 vs. $0.05 per page). Tuning and testing the LLM prompt could address the hardest inputs.

AWS Textract struggled with times, depths and tag counts. Its bounding boxes proved very useful for cropping the examples in the report.

Google Document AI (Form Parser) isn't feasible: it merges columns and rows, and pairs labels with the wrong values. A custom processor might do much better, but Google charges $0.05 an hour (about $36 a month) to host one, which doesn't make sense at small volumes.

Common challenging issues across services included `*` for a broken stipe written anywhere, `E` for 11, dropped decimal points, the hand-added tag column, the circled depth unit, the site written in the OBS/BUD box, and tick marks beside reps.

## Conclusions

Both GPT-6 Sol and Handwriting OCR are promising ways to reduce tedious manual entry. The Handwriting OCR team focuses on parsing handwritten documents and says it is constantly improving its models and workflow. The LLMs are also improving, and the lab can customize them more directly for this purpose.

This comparison is a deliberately untuned baseline. Preprocessing only rotates the scans and scales pages to one size. The mapping step fixes only what the conventions require (such as `E` and dropped decimal points over 31 m), and the validation rules flag implausible values without changing them. More context or guardrails could improve accuracy: for example, choosing the site from a list of known sites, giving the LLM worked examples, or limiting values to what fits the site.

## Run it

The Python environment comes from the repo root (`pyproject.toml`, managed with uv). Everything else lives in this folder: run the `make` targets from here, with paths relative to it. Copy `local.env.example` to `local.env` and fill in the keys for the services you run. Document AI and the Google Sheet also need a Google Service Account ()`google-service-account.json`); the Google Sheet needs to be shared with the service account email.

```sh
make prep                      # data/preprocessed/Fronds/*.pdf, data/preprocessed/contact_sheet_Fronds.pdf
make answers                   # data/answers/fronds/*.json, issues.tsv, unmatched.tsv, features.tsv

# For each service: aws_textract, google_document_ai, handwriting_ocr, llm_api
# extract skips files that already have results; make clean-results SERVICE=<service> to remove results
make extract SERVICE=aws_textract FILES=data/fronds_sample.txt     # data/results/aws_textract/<file>.raw.json
make extract SERVICE=aws_textract FILES=data/fronds_remaining.txt
make map SERVICE=aws_textract       # data/results/aws_textract/<file>.json
make validate SERVICE=aws_textract  # adds flags to each <file>.json
make score SERVICE=aws_textract     # data/results/aws_textract/scores.json, errors.csv

make report                    # report/report.html, report/img/
make sheet                     # one tab per service in the Google Sheet GOOGLE_SHEET_ID
```

`llm_api` runs every model in `MODELS` (`llm_api/config.py`), each in its own folder, `data/results/llm_api/<model>/`. 

Handwriting OCR takes `FILES=data/fronds_handwriting_ocr.txt`

## References

- [Report generated by report.py](report/report.html)
- [AWS Textract](https://docs.aws.amazon.com/textract/)
- [Google Document AI](https://cloud.google.com/document-ai/docs)
- [Handwriting OCR](https://www.handwritingocr.com/api/docs)
- [OpenAI](https://platform.openai.com/docs)
- [Getting the Most out of GPT-5.4 for Vision and Document Understanding](https://developers.openai.com/cookbook/examples/multimodal/document_and_multimodal_understanding_tips)
- [From Handwriting to Structured Data: Benchmarking AI Digitisation of Handwritten Forms](https://arxiv.org/html/2604.16504v1)
