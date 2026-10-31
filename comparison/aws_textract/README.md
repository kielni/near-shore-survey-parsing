# AWS Textract

https://aws.amazon.com/textract/

## Preqrequistes

### Confirm Textract permission without processing a page

Point `analyze-document` at an S3 key that doesn't exist. Textract checks permissions before fetching the document, so no page is processed or billed.

```bash
aws textract analyze-document --feature-types TABLES \
  --document '{"S3Object":{"Bucket":"<BUCKET>","Name":"does-not-exist.png"}}'
```

- `AccessDeniedException`: missing `textract:AnalyzeDocument`.
- `InvalidS3ObjectException`: Textract permission is OK. The error is expected because the object doesn't exist.

### Confirm bucket access and region

Textract reads S3 with the caller's credentials, so the caller needs `s3:GetObject`, and the bucket must be in the same region as the Textract call.

```bash
aws s3api head-object --bucket <BUCKET> --key <real-file>   # caller can read the object
aws s3api get-bucket-location --bucket <BUCKET>             # compare with:
aws configure get region
```
