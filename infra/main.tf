# The data lake: one encrypted, private S3 bucket. The zones are prefixes in it and
# mirror the local layout in data/lake/ (raw/, validated/, quarantine/, clean/, curated/, ml/, reports/).
# Later steps add one IAM role per job, each allowed to touch only the prefixes it needs.

data "aws_caller_identity" "current" {}

locals {
  # The account id makes the name globally unique. It is read at run time, not written in the code.
  bucket_name = "${var.project}-lake-${data.aws_caller_identity.current.account_id}-${var.region}"
}

# One customer-managed key for the whole lake. It costs about 1 USD a month.
resource "aws_kms_key" "lake" {
  description             = "${var.project} data lake encryption key"
  enable_key_rotation     = true
  deletion_window_in_days = 7
}

resource "aws_kms_alias" "lake" {
  name          = "alias/${var.project}-lake"
  target_key_id = aws_kms_key.lake.key_id
}

resource "aws_s3_bucket" "lake" {
  bucket = local.bucket_name
}

# Nothing in this bucket can ever be made public.
resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Every object is encrypted with the key above. The bucket key keeps KMS request costs low.
resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.lake.arn
    }
    bucket_key_enabled = true
  }
}

# Versioning lets us recover from a bad overwrite. Old versions are cleaned up below.
resource "aws_s3_bucket_versioning" "lake" {
  bucket = aws_s3_bucket.lake.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket     = aws_s3_bucket.lake.id
  depends_on = [aws_s3_bucket_versioning.lake]

  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  rule {
    id     = "expire-athena-results"
    status = "Enabled"

    filter {
      prefix = "athena-results/"
    }

    expiration {
      days = 7
    }
  }
}

# Refuse any request that does not use TLS.
data "aws_iam_policy_document" "lake" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.lake.arn,
      "${aws_s3_bucket.lake.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket     = aws_s3_bucket.lake.id
  policy     = data.aws_iam_policy_document.lake.json
  depends_on = [aws_s3_bucket_public_access_block.lake]
}
