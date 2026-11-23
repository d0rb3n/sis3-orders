#!/usr/bin/env bash
set -euo pipefail

ENVIRONMENT="${1:?Usage: bash scripts/deploy.sh dev|defence|prod [stack-prefix]}"
PREFIX="${2:-sis3-${ENVIRONMENT}}"

case "$ENVIRONMENT" in
  dev|defence|prod) ;;
  *) echo "Environment must be dev, defence or prod"; exit 1 ;;
esac

if [[ ! "$PREFIX" =~ ^[a-zA-Z][a-zA-Z0-9-]{0,79}$ ]]; then
  echo "Invalid stack prefix"
  exit 1
fi

cd "$(dirname "$0")/.."

export AWS_DEFAULT_REGION="${AWS_REGION:-eu-north-1}"
export AWS_REGION="$AWS_DEFAULT_REGION"
export AWS_PAGER=""

for tool in aws docker git python3; do
  command -v "$tool" >/dev/null || {
    echo "Required tool is missing: $tool"
    exit 1
  }
done

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Commit your changes before deploying."
  exit 1
fi

IMAGE_TAG=$(git rev-parse HEAD)
mkdir -p .deploy

echo "Checking AWS access and Docker..."
aws sts get-caller-identity --query Account --output text
docker info >/dev/null

echo "Running consumer tests..."
python3 -m venv .venv
.venv/bin/pip install -r app/requirements.txt
.venv/bin/python -m unittest discover -s tests -v

deploy_stack() {
  aws cloudformation deploy \
    --region "$AWS_REGION" \
    --no-fail-on-empty-changeset \
    "$@"
}

output() {
  local value
  value=$(aws cloudformation describe-stacks \
    --stack-name "$1" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue | [0]" \
    --output text)

  if [[ -z "$value" || "$value" == "None" ]]; then
    echo "Missing output $2 in stack $1" >&2
    return 1
  fi
  printf '%s' "$value"
}

echo "Deploying artifact storage..."
deploy_stack \
  --template-file infra/artifacts.yaml \
  --stack-name "${PREFIX}-artifacts"

echo "Deploying ECR..."
deploy_stack \
  --template-file infra/ecr.yaml \
  --stack-name "${PREFIX}-ecr" \
  --parameter-overrides Environment="$ENVIRONMENT"

ARTIFACT_BUCKET=$(output "${PREFIX}-artifacts" BucketName)
REPO_NAME=$(output "${PREFIX}-ecr" RepositoryName)
REPO_URI=$(output "${PREFIX}-ecr" RepositoryUri)
REPO_ARN=$(output "${PREFIX}-ecr" RepositoryArn)

EXISTING_TAG=$(aws ecr list-images \
  --repository-name "$REPO_NAME" \
  --filter tagStatus=TAGGED \
  --query "imageIds[?imageTag=='${IMAGE_TAG}'].imageTag" \
  --output text)

if [[ -z "$EXISTING_TAG" ]]; then
  echo "Building and pushing image ${IMAGE_TAG}..."
  aws ecr get-login-password \
    | docker login \
      --username AWS \
      --password-stdin "${REPO_URI%%/*}"

  docker build \
    --platform linux/amd64 \
    -t "${REPO_URI}:${IMAGE_TAG}" \
    ./app

  docker push "${REPO_URI}:${IMAGE_TAG}"
else
  echo "Image for this commit already exists; using it."
fi

echo "Packaging Lambda..."
aws cloudformation package \
  --template-file infra/messaging.yaml \
  --s3-bucket "$ARTIFACT_BUCKET" \
  --output-template-file .deploy/messaging-packaged.yaml

echo "Deploying messaging..."
deploy_stack \
  --template-file .deploy/messaging-packaged.yaml \
  --stack-name "${PREFIX}-messaging" \
  --parameter-overrides Environment="$ENVIRONMENT" \
  --capabilities CAPABILITY_IAM

echo "Deploying network..."
deploy_stack \
  --template-file infra/network.yaml \
  --stack-name "${PREFIX}-network"

VPC_ID=$(output "${PREFIX}-network" VpcId)
PUBLIC_SUBNETS=$(output "${PREFIX}-network" PublicSubnetIds)
PRIVATE_SUBNETS=$(output "${PREFIX}-network" PrivateSubnetIds)
ALB_SG=$(output "${PREFIX}-network" AlbSecurityGroupId)
TASK_SG=$(output "${PREFIX}-network" TaskSecurityGroupId)
TOPIC_ARN=$(output "${PREFIX}-messaging" TopicArn)

echo "Deploying ECS and ALB..."
deploy_stack \
  --template-file infra/app.yaml \
  --stack-name "${PREFIX}-app" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    VpcId="$VPC_ID" \
    PublicSubnetIds="$PUBLIC_SUBNETS" \
    PrivateSubnetIds="$PRIVATE_SUBNETS" \
    AlbSecurityGroupId="$ALB_SG" \
    TaskSecurityGroupId="$TASK_SG" \
    RepositoryArn="$REPO_ARN" \
    RepositoryUri="$REPO_URI" \
    TopicArn="$TOPIC_ARN" \
    ImageTag="$IMAGE_TAG" \
    DesiredCount=2

APP_URL=$(output "${PREFIX}-app" ApplicationUrl)

printf '\nDeployment complete\n'
printf 'Environment: %s\n' "$ENVIRONMENT"
printf 'Stack prefix: %s\n' "$PREFIX"
printf 'Git commit / image tag: %s\n' "$IMAGE_TAG"
printf 'Application URL: %s\n' "$APP_URL"
