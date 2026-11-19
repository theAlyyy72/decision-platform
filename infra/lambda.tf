# The daily pipeline as a Lambda function that runs the container image in ECR.
# To ship new code: build and push a new tag (v2, v3 ...), then set image_tag and apply.

variable "image_tag" {
  description = "Tag of the pipeline image in ECR. Tags are immutable, so every code change needs a new one."
  type        = string
  default     = "v1"
}

variable "as_of" {
  description = "Last day of data to use. Fixed because the synthetic data ends on this day."
  type        = string
  default     = "2026-10-31"
}

resource "aws_lambda_function" "pipeline" {
  function_name = "${var.project}-pipeline"
  role          = aws_iam_role.pipeline.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.pipeline.repository_url}:${var.image_tag}"
  architectures = ["arm64"] # matches the image built on an Apple silicon Mac, and costs less
  memory_size   = 3008
  timeout       = 900 # 15 minutes, the maximum. A normal run takes about a minute.

  ephemeral_storage {
    size = 2048 # MB of /tmp for the work folder
  }

  environment {
    variables = {
      LAKE_BUCKET = aws_s3_bucket.lake.bucket
      AS_OF       = var.as_of
      HOME        = "/tmp" # some libraries want a writable home folder
    }
  }

  depends_on = [aws_iam_role_policy.pipeline, aws_cloudwatch_log_group.pipeline]
}

output "pipeline_function" {
  value = aws_lambda_function.pipeline.function_name
}
