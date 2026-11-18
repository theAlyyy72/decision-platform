output "lake_bucket" {
  description = "Name of the data lake bucket."
  value       = aws_s3_bucket.lake.bucket
}

output "lake_bucket_arn" {
  value = aws_s3_bucket.lake.arn
}

output "kms_key_arn" {
  value = aws_kms_key.lake.arn
}

output "kms_alias" {
  value = aws_kms_alias.lake.name
}
