#!/usr/bin/env bash
# ClaimSense - one-command deploy to AWS ECS Fargate | Author: Vishal Gupta (AWS capstone edition), based on ClaimSense by Shivank Agrawal
#
# Needs: AWS CLI v2 (logged in: `aws sts get-caller-identity` works), Terraform >= 1.5, Docker running.
# Usage (from the project root):
#   export OPENAI_API_KEY=sk-...
#   bash deploy/deploy.sh
set -euo pipefail

: "${OPENAI_API_KEY:?Please export OPENAI_API_KEY first}"
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: stop it turning /claimsense/... into C:/Program Files/Git/claimsense/...
REGION="${AWS_REGION:-ap-south-1}"
APP="claimsense"
cd "$(dirname "$0")/terraform"

echo "1/5 Saving the OpenAI key in SSM Parameter Store (encrypted, never in Terraform state)"
aws ssm put-parameter --region "$REGION" --name "/$APP/openai_api_key" --type SecureString \
  --value "$OPENAI_API_KEY" --overwrite > /dev/null

echo "2/5 Creating the ECR repository"
terraform init -input=false > /dev/null
terraform apply -auto-approve -input=false -var="aws_region=$REGION" -target=aws_ecr_repository.app
REPO="$(terraform output -raw ecr_repository_url)"

echo "3/5 Building and pushing the Docker image (linux/amd64, also works on Apple Silicon)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:latest" ../..
docker push "$REPO:latest"

echo "4/5 Creating ECS cluster, service, IAM role, security group, logs"
terraform apply -auto-approve -input=false -var="aws_region=$REGION"   -var="llm_base_url=${OPENAI_BASE_URL:?Please export OPENAI_BASE_URL}" -var="llm_project_id=${OPENAI_PROJECT_ID:-}"   -var="llm_model=${OPENAI_MODEL:-qwen.qwen3-vl-235b-a22b-instruct}"

echo "5/5 Waiting for the service to become stable (2-4 minutes)"
aws ecs wait services-stable --region "$REGION" --cluster "$APP-cluster" --services "$APP"
TASK="$(aws ecs list-tasks --region "$REGION" --cluster "$APP-cluster" --service-name "$APP" \
  --query 'taskArns[0]' --output text)"
ENI="$(aws ecs describe-tasks --region "$REGION" --cluster "$APP-cluster" --tasks "$TASK" \
  --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value" --output text)"
IP="$(aws ec2 describe-network-interfaces --region "$REGION" --network-interface-ids "$ENI" \
  --query 'NetworkInterfaces[0].Association.PublicIp' --output text)"

echo ""
echo "ClaimSense is live:"
echo "  Portal   : http://$IP:8501"
echo "  API docs : http://$IP:8000/docs"
echo "  Logs     : aws logs tail /ecs/$APP --follow --region $REGION"
echo "When finished, delete everything with: bash deploy/destroy.sh"
