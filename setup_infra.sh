#!/usr/bin/env bash
# Create the shared S3 bucket. Safe to re-run.
set -euo pipefail

cd "$(dirname "$0")"
set -a
source local.env
set +a

if aws s3api head-bucket --bucket "$S3_BUCKET" 2>/dev/null; then
    echo "Bucket $S3_BUCKET already exists"
elif [ "$AWS_REGION" = "us-east-1" ]; then
    # us-east-1 rejects an explicit LocationConstraint.
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION"
else
    aws s3api create-bucket --bucket "$S3_BUCKET" --region "$AWS_REGION" \
        --create-bucket-configuration LocationConstraint="$AWS_REGION"
fi

# Manual Google Cloud setup, done once in the console, all in the service
# account's project (project_id in google-service-account.json):
#
# 1. Billing is enabled on the project, and the Document AI API is enabled
#    (APIs & Services > Library > Cloud Document AI API).
# 2. Service account key: the JSON key is saved outside the repo and linked
#    as google-service-account.json (gitignored).
# 3. IAM & Admin > IAM > Grant access: add the service account (client_email
#    in google-service-account.json) with the role Document AI API User
#    (roles/documentai.apiUser).
# 4. Open Document AI, not the API's metrics page:
#    https://console.cloud.google.com/ai/document-ai?project=<your project>
#    Processor gallery (may be called Explore processors) > Form Parser >
#    Create processor: name kelp-form-parser, region US.
# 5. Copy the processor ID from its details page (also the end of the
#    prediction endpoint, .../processors/<ID>:process) into local.env:
#        DOCAI_LOCATION=us
#        DOCAI_PROCESSOR_ID=<ID>
