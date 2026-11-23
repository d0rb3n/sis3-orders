# SIS3 — Orders application

Author: Darkhan Makhmutov

## Architecture

Client -> ALB -> ECS Fargate -> SNS -> SQS -> Lambda -> DynamoDB
                                     |
                                     +-> DLQ after failed processing attempts

The container accepts orders and publishes OrderPlaced events.
Lambda validates each order and stores it in DynamoDB.
A conditional PutItem prevents duplicate writes for the same orderId.

## Requirements

- AWS CLI with credentials and permissions to deploy the templates
- Docker running with Linux AMD64 build support
- Python 3 with venv and pip
- Git
- AWS Region with at least two available Availability Zones

No access keys are stored in this repository.
The application receives AWS permissions through IAM roles.

## Deploy

Run from a clean Git checkout:

```bash
bash scripts/deploy.sh dev
bash scripts/deploy.sh defence
bash scripts/deploy.sh defence sis3-defence-copy
The default region is eu-north-1. Override it with AWS_REGION.

The script runs unit tests, creates artifact storage and ECR,
builds and pushes the container image, packages Lambda, and deploys
messaging, networking and the application.

The image tag is the full Git commit SHA. Existing tags are immutable.
The application URL is printed after deployment.
Infrastructure
- ECR: immutable image tags and cleanup of untagged images after 7 days.
- ECS: two Fargate tasks, each with 0.25 vCPU and 512 MiB memory.
- ALB: HTTP port 80, forwarding to container port 8080.
- Health check: GET /health.
- ECS tasks: private subnets, no public IP addresses.
- One NAT Gateway provides outbound connectivity.
- Task security group accepts port 8080 only from the ALB security group.
- Execution role: ECR image pull and CloudWatch log permissions.
- Task role: publication to the application SNS topic.
- SQS: visibility timeout 180 seconds, maxReceiveCount 3.
- DLQ: message retention 14 days.
- Lambda: partial batch failure reporting.
- DynamoDB: orderId partition key, on-demand billing and PITR.
- CloudWatch log retention: 7 days.
Verify
Replace APP_URL with the URL printed by deployment.
curl -i "$APP_URL/health"

curl -i -X POST "$APP_URL/orders" \
  -H "Content-Type: application/json" \
  -d '{"orderId":"demo-001","product":"Notebook","quantity":2}'
Expected responses:
- GET /health: HTTP 200.
- POST /orders: HTTP 202 with status ACCEPTED.
- DynamoDB: the order eventually appears with status PROCESSED.
Repeat the same orderId to check duplicate handling.
Lambda logs should show duplicate_skipped.

To test the DLQ, publish an OrderPlaced event with quantity 0
directly to the environment's SNS topic.
The HTTP API rejects invalid orders before publication.
Allow time for retries, then inspect the DLQ.
Tests
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
.venv/bin/python -m unittest discover -s tests -v
Unit tests use a mocked DynamoDB client.
AWS integration tests are performed against the deployed environment.
Limitations
This is a teaching application using HTTP without authentication.
Use only demonstration data.

One NAT Gateway is a cost and availability tradeoff:
both private subnets depend on its Availability Zone for outbound access.

Order IDs are deduplication keys. Reusing an ID does not update an order.
Cleanup
Record the physical table and artifact bucket names before deleting stacks.

For each environment:
1. Delete the app stack and wait for completion.
2. Delete the network stack and wait for completion.
3. Delete the messaging stack.
4. Delete the retained DynamoDB table after saving evidence.
5. Delete the ECR stack; its images are deleted with the repository.
6. Empty the artifact bucket, then delete the artifacts stack.
7. Verify that NAT Gateways, ALBs and ECS tasks have been removed.
DynamoDB tables are retained by the template.
Artifact buckets must be emptied before stack deletion.
