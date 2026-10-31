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
