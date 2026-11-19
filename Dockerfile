# The daily pipeline as an AWS Lambda container image.
# Build from the project root (see the steps in the chat), not from this folder's parent.
FROM public.ecr.aws/lambda/python:3.14

# Same library versions as the laptop (requirements-lambda.txt is made from `pip freeze`),
# so the saved churn model loads exactly as it was trained.
COPY requirements-lambda.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r requirements-lambda.txt

COPY src/dp ${LAMBDA_TASK_ROOT}/dp
COPY sql ${LAMBDA_TASK_ROOT}/sql
ENV SQL_DIR=${LAMBDA_TASK_ROOT}/sql

CMD ["dp.handler.lambda_handler"]
