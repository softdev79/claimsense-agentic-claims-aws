#!/usr/bin/env bash
# ClaimSense - delete EVERY AWS resource created by deploy.sh | Author: Vishal Gupta (AWS capstone edition), based on ClaimSense by Shivank Agrawal
# Usage (from the project root): bash deploy/destroy.sh
set -euo pipefail

export MSYS_NO_PATHCONV=1  # Git Bash on Windows: stop it turning /claimsense/... into C:/Program Files/Git/claimsense/...
REGION="${AWS_REGION:-ap-south-1}"
APP="claimsense"
cd "$(dirname "$0")/terraform"

echo "1/2 Destroying ECS service, cluster, ECR repo (with images), IAM role, security group, log group"
terraform init -input=false > /dev/null
terraform destroy -auto-approve -input=false -var="aws_region=$REGION"

echo "2/2 Deleting the OpenAI key from SSM Parameter Store"
aws ssm delete-parameter --region "$REGION" --name "/$APP/openai_api_key" 2> /dev/null || true

echo "Done. Verify nothing is left:"
echo "  aws ecs list-clusters --region $REGION"
echo "  aws ecr describe-repositories --region $REGION"
