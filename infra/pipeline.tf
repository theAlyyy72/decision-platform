# Everything the daily job needs before the Lambda function itself exists:
# a place for its container image (ECR), a log group, and an IAM role that can do
# only what the pipeline does (see the policy below).

locals {
  # What the job may READ from the lake bucket, and what it may WRITE. Note that it can write
  # nothing under source/ or models/, and it can delete nothing at all.
  read_prefixes  = ["source/*", "raw/*", "models/*", "_ingestion_log.jsonl"]
  write_prefixes = ["raw/*", "validated/*", "quarantine/*", "clean/*", "curated/*", "ml/*", "reports/*", "_ingestion_log.jsonl"]
}

resource "aws_ecr_repository" "pipeline" {
  name                 = "${var.project}-pipeline"
  image_tag_mutability = "IMMUTABLE" # a tag such as v1 always means the same image
  force_delete         = true        # lets `terraform destroy` remove the repository with its images

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Images are large (about 1 GB), so keep only the latest 3 to keep storage costs near zero.
resource "aws_ecr_lifecycle_policy" "pipeline" {
  repository = aws_ecr_repository.pipeline.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 3 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 3
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "pipeline" {
  name              = "/aws/lambda/${var.project}-pipeline"
  retention_in_days = 30
}

data "aws_iam_policy_document" "pipeline_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pipeline" {
  name               = "${var.project}-pipeline"
  assume_role_policy = data.aws_iam_policy_document.pipeline_trust.json
}

data "aws_iam_policy_document" "pipeline" {
  statement {
    sid       = "ListOnlyTheFoldersItReads"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.lake.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.read_prefixes
    }
  }

  statement {
    sid       = "ReadInputs"
    actions   = ["s3:GetObject"]
    resources = [for p in local.read_prefixes : "${aws_s3_bucket.lake.arn}/${p}"]
  }

  statement {
    sid       = "WriteOutputs"
    actions   = ["s3:PutObject", "s3:AbortMultipartUpload"]
    resources = [for p in local.write_prefixes : "${aws_s3_bucket.lake.arn}/${p}"]
  }

  statement {
    sid       = "UseTheLakeKeyForS3Only"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [aws_kms_key.lake.arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${var.region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "WriteItsOwnLogs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.pipeline.arn}:*"]
  }
}

resource "aws_iam_role_policy" "pipeline" {
  name   = "pipeline-least-privilege"
  role   = aws_iam_role.pipeline.id
  policy = data.aws_iam_policy_document.pipeline.json
}

output "ecr_repository_url" {
  value = aws_ecr_repository.pipeline.repository_url
}

output "pipeline_role_arn" {
  value = aws_iam_role.pipeline.arn
}
