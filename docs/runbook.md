# Runbook

What to do when an alert arrives, and the routine jobs for running the platform on AWS.

Names used below: Lambda `decision-platform-pipeline`, schedule rule `decision-platform-daily`, region `ap-southeast-2`, AWS CLI profile `decision-platform`. Alerts arrive by email from the SNS topic `decision-platform-alerts`.

## Before any command

```bash
aws login --profile decision-platform                      # the session lasts a few hours
eval "$(aws configure export-credentials --profile decision-platform --format env)"   # Terraform and Docker need this
BUCKET=$(terraform -chdir=infra output -raw lake_bucket)   # run from the project root
```

An `ExpiredToken` or `session has expired` error always means: run the first two lines again.

## Which alert is it?

| Email subject | Meaning | Go to |
|---|---|---|
| `ALARM: decision-platform-pipeline-failed` | A run crashed or stopped itself (this includes "more than 5% of rows failed validation") | [1](#1-the-pipeline-failed) |
| `ALARM: decision-platform-no-successful-run-26h` | No successful run for 26 hours, so the data is stale | [2](#2-no-successful-run-in-26-hours) |
| `AWS Budgets: ... exceeded` | Spend passed 80% of 10 USD, or is forecast to pass 100% | [3](#3-budget-alert) |

A run that says `no_new_data` is a success. It means every source file was already ingested.

## 1. The pipeline failed

**Read the error first.**

```bash
aws logs tail /aws/lambda/decision-platform-pipeline --since 2h --profile decision-platform
```

Look for the last `errorMessage`. The usual ones:

| Message contains | Cause | What to do |
|---|---|---|
| `validation failed, too many rows quarantined` | A table has more than 5% bad rows | See 1a |
| `ingestion failed` | A source file has the wrong columns or cannot be read | Fix the source file, then see 1b |
| `Task timed out` or `signal: killed` | The run needs more time or memory than the function has (15 minutes, 3008 MB) | Check how much the data grew; raise memory in `infra/lambda.tf` if your quota allows |
| `AccessDenied` | The pipeline role may not touch that part of S3 | Add the prefix to `write_prefixes` or `read_prefixes` in `infra/pipeline.tf`, then `terraform apply` |
| `ModuleNotFoundError` or a model that will not load | The image does not match the code or library versions | Rebuild and push a new image (section 5) |

### 1a. Validation stopped the run

The report and the quarantined rows are saved before the error is raised, so nothing is lost.

```bash
aws s3 ls s3://$BUCKET/reports/ --profile decision-platform
aws s3 cp s3://$BUCKET/reports/<newest file> - --profile decision-platform | python3 -m json.tool | head -60
```

The report shows, per table, how many rows were quarantined and by which rule. To see the rows:

```bash
aws s3 ls s3://$BUCKET/quarantine/<table>/ --recursive --profile decision-platform
aws s3 cp s3://$BUCKET/quarantine/<table>/<partition>/<file>.csv - --profile decision-platform | head -20
```

The `reject_reason` column says why each row failed. Then decide:

- **The source is wrong** (most common): get a corrected file and continue at 1b.
- **The rule is wrong for new but legitimate data:** change the rule in `src/dp/validate.py`, add a test, build a new image (section 5), then run again as in 1b. Do not just raise the 5% limit to make the alert go away.

### 1b. Run again after fixing the source

A batch that failed validation is still recorded in the ingestion log, so running again with the same file reports `no_new_data`. Upload the corrected file (a new file has a new checksum, so it counts as new):

```bash
aws s3 cp data/source/<table>.csv s3://$BUCKET/source/<table>.csv --profile decision-platform
aws lambda invoke --function-name decision-platform-pipeline --profile decision-platform \
  --cli-read-timeout 900 --cli-binary-format raw-in-base64-out --payload '{}' /tmp/out.json && cat /tmp/out.json
```

If the files are unchanged but you fixed the code and want to process them again, remove the log first. The bucket is versioned, so the old log is kept behind a delete marker:

```bash
aws s3 rm s3://$BUCKET/_ingestion_log.jsonl --profile decision-platform
```

A good result looks like `"status": "ok"` or `"warn"` with `"scored_customers"` filled in. `warn` is normal: it means missing emails or payment-method spelling variants, which are kept and counted.

## 2. No successful run in 26 hours

Work through these in order:

```bash
# Is the daily rule still on? Expect "State": "ENABLED" and cron(30 23 * * ? *)
aws events describe-rule --name decision-platform-daily --profile decision-platform

# Does the function exist and is it active? Expect "Active"
aws lambda get-function --function-name decision-platform-pipeline --query 'Configuration.State' --profile decision-platform

# What happened in the last two days?
aws logs tail /aws/lambda/decision-platform-pipeline --since 2d --profile decision-platform
```

- Rule disabled: `aws events enable-rule --name decision-platform-daily --profile decision-platform`
- The runs are failing: you should also have a failed alarm, go to section 1.
- No runs at all and the rule is enabled: check the AWS account. The free plan ends on 7 May 2027 or when the credits run out, after which projects are paused. Check the Projects page in AWS Settings.

Run it once by hand to confirm it works (the command in 1b).

## 3. Budget alert

Open Billing, then Cost Explorer, group by service. What normally costs money here:

- KMS key: about 1 USD a month, always.
- ECR: image storage, only the latest 3 images are kept.
- CloudWatch Logs: kept for 30 days.
- Athena: stops any query at 1 GiB scanned.
- Lambda: one run of about a minute a day, a fraction of a cent.

To stop spending right now without deleting anything, pause the schedule:

```bash
aws events disable-rule --name decision-platform-daily --profile decision-platform
```

## 4. Routine jobs

**Run the pipeline by hand**

```bash
aws lambda invoke --function-name decision-platform-pipeline --profile decision-platform \
  --cli-read-timeout 900 --cli-binary-format raw-in-base64-out --payload '{}' /tmp/out.json && cat /tmp/out.json
```

`status` is `ok`, `warn` (ran, with warnings) or `no_new_data` (nothing to do). Anything else is an error, see section 1.

**Refresh the Athena tables** after the schema of a curated table changes:

```bash
python3 -m dp.catalog --bucket $BUCKET
python3 -m dp.catalog --bucket $BUCKET --query "SELECT period_start, orders, net_revenue FROM revenue_kpis WHERE grain = 'month' ORDER BY period_start DESC LIMIT 3"
```

**Rebuild the dashboard** from what is in S3:

```bash
aws s3 sync s3://$BUCKET/curated data/lake/curated --profile decision-platform
aws s3 sync s3://$BUCKET/reports data/lake/reports --profile decision-platform
python3 -m dp.dashboard && open dashboard/index.html
```

**Test that alerts reach you** (a fake alarm, nothing breaks):

```bash
aws cloudwatch set-alarm-state --alarm-name decision-platform-pipeline-failed --state-value ALARM --state-reason "test" --profile decision-platform
```

## 5. Ship new code

Image tags are immutable, so every change needs a new tag.

```bash
ECR=$(terraform -chdir=infra output -raw ecr_repository_url)
aws ecr get-login-password --region ap-southeast-2 --profile decision-platform | docker login --username AWS --password-stdin ${ECR%%/*}
docker build --platform linux/arm64 --provenance=false -t $ECR:v2 .
caffeinate -i docker push $ECR:v2        # run it again if you see "broken pipe"; finished layers are not sent twice
cd infra && terraform apply -var image_tag=v2
```

Then change the default of `image_tag` in `infra/lambda.tf` to `v2` and commit it. If pushes keep breaking, set `"max-concurrent-uploads": 1` in Docker Desktop, Settings, Docker Engine.

Check the library versions in `requirements-lambda.txt` still match your laptop (`pip freeze`), or the saved model may not load.

## 6. Shut everything down

Keep `infra/terraform.tfstate`: Terraform uses it to know what exists. If it is lost, the resources must be deleted by hand.

1. The bucket is versioned, so Terraform cannot delete it while it holds objects. In `infra/main.tf`, add `force_destroy = true` inside `resource "aws_s3_bucket" "lake"`, then:
   ```bash
   cd infra
   terraform apply
   terraform destroy
   ```
2. This removes the bucket and all data in it, the Lambda and its schedule, the alarms, the SNS topic, the ECR repository and images, the Athena workgroup and database, the IAM role, and the budget. The KMS key is not deleted at once: it waits 7 days.
3. Confirm the SNS email subscription is gone and `aws s3 ls --profile decision-platform` shows no lake bucket.
4. The AWS project itself stays until you delete it in AWS Settings.

To bring it back later: `terraform apply`, build and push the image (section 5), upload the data (`aws s3 sync data/lake s3://$BUCKET/`), run `python3 -m dp.catalog --bucket $BUCKET`.
