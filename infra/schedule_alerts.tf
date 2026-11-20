# Runs the pipeline every day and emails you when something is wrong.
#   - 05:00 IST every day (23:30 UTC the day before), two hours before the 07:00 deadline
#   - alarm 1: the pipeline run failed (this includes "more than 5% of rows failed validation",
#     because the pipeline stops with an error then)
#   - alarm 2: no successful run in the last 26 hours (your freshness rule)
#   - a budget alert for the 10 USD a month limit
# "No new data today" counts as a successful run, so it never raises an alarm.

variable "alert_email" {
  description = "Where alarms and budget warnings are sent. Put it in infra/terraform.tfvars (git ignores that file)."
  type        = string
}

variable "schedule_cron" {
  description = "When the pipeline runs, in UTC. 23:30 UTC is 05:00 IST."
  type        = string
  default     = "cron(30 23 * * ? *)"
}

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}

# AWS sends a confirmation email first. Alerts only start after you click the link in it.
resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_event_rule" "daily" {
  name                = "${var.project}-daily"
  description         = "Runs the ${var.project} pipeline every day at 05:00 IST"
  schedule_expression = var.schedule_cron
}

resource "aws_cloudwatch_event_target" "daily" {
  rule = aws_cloudwatch_event_rule.daily.name
  arn  = aws_lambda_function.pipeline.arn
}

resource "aws_lambda_permission" "events" {
  statement_id  = "AllowDailyRule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.pipeline.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily.arn
}

# Alarm 1: any failed run. One email per failure; it returns to OK by itself a few minutes later.
resource "aws_cloudwatch_metric_alarm" "pipeline_failed" {
  alarm_name          = "${var.project}-pipeline-failed"
  alarm_description   = "The daily pipeline failed or stopped (for example too many rows failed validation). See the log group /aws/lambda/${var.project}-pipeline and the report in s3://${aws_s3_bucket.lake.bucket}/reports/."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.pipeline.function_name }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Alarm 2: successful runs = invocations minus errors. The alarm fires only if every one of the
# last 26 hours had no successful run (an hour with no data counts as "no successful run").
resource "aws_cloudwatch_metric_alarm" "data_stale" {
  alarm_name          = "${var.project}-no-successful-run-26h"
  alarm_description   = "No successful pipeline run in the last 26 hours, so the data is stale. Check that the daily rule is enabled and look at the pipeline logs."
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  evaluation_periods  = 26
  datapoints_to_alarm = 26
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]

  metric_query {
    id          = "success"
    expression  = "invocations - FILL(errors, 0)"
    label       = "Successful runs"
    return_data = true
  }

  metric_query {
    id = "invocations"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Invocations"
      dimensions  = { FunctionName = aws_lambda_function.pipeline.function_name }
      period      = 3600
      stat        = "Sum"
    }
  }

  metric_query {
    id = "errors"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = { FunctionName = aws_lambda_function.pipeline.function_name }
      period      = 3600
      stat        = "Sum"
    }
  }
}

# Budget alert: an email at 80% of 10 USD actually spent, and at 100% forecast for the month.
resource "aws_budgets_budget" "monthly" {
  name         = "${var.project}-monthly"
  budget_type  = "COST"
  limit_amount = "10"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

output "alerts_topic_arn" {
  value = aws_sns_topic.alerts.arn
}

output "daily_rule" {
  value = aws_cloudwatch_event_rule.daily.name
}
