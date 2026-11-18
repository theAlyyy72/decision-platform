# Athena lets us run SQL on the curated Parquet files in S3. We only pay for data scanned
# (5 USD per TB, at least 10 MB per query), which is a tiny fraction of a cent for this lake.
# The tables themselves are created from the real Parquet schemas by `python3 -m dp.catalog`.

resource "aws_glue_catalog_database" "lake" {
  name        = replace(var.project, "-", "_")
  description = "Curated tables of the ${var.project} data lake"
}

resource "aws_athena_workgroup" "lake" {
  name          = var.project
  force_destroy = true

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    # A runaway query is stopped after scanning 1 GiB (about 0.005 USD).
    bytes_scanned_cutoff_per_query = 1073741824

    result_configuration {
      output_location = "s3://${aws_s3_bucket.lake.bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.lake.arn
      }
    }
  }
}

output "athena_database" {
  value = aws_glue_catalog_database.lake.name
}

output "athena_workgroup" {
  value = aws_athena_workgroup.lake.name
}
