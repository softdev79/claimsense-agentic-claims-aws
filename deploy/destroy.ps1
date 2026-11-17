<#
ClaimSense AWS capstone - delete EVERY AWS resource created by deploy.ps1.
Author: Vishal Gupta

    powershell -ExecutionPolicy Bypass -File deploy\destroy.ps1
#>
param(
    [string]$Region = "ap-south-1",
    [string]$AwsProfile = "claimsense"
)
. (Join-Path $PSScriptRoot "_common.ps1")
Assert-AwsLogin

Write-Step "1/2 Destroying ECS service, cluster, ECR repo (with images), IAM role, security group, logs, budget"
Invoke-Terraform init -input=false
Invoke-Terraform destroy -auto-approve -input=false "-var=aws_region=$Region"

Write-Step "2/2 Deleting the OpenAI key from SSM Parameter Store"
# (Windows PowerShell turns redirected stderr into a terminating error, so let the CLI print it instead.)
$ErrorActionPreference = "Continue"
aws ssm delete-parameter --profile $AwsProfile --region $Region --name $OpenAIParam
if ($LASTEXITCODE -ne 0) { Write-Host "(ParameterNotFound just means it was already deleted)" }

Write-Host "`nDone. Double-check nothing is left (both lists should be empty):" -ForegroundColor Green
aws ecs list-clusters --profile $AwsProfile --region $Region --output text
aws ecr describe-repositories --profile $AwsProfile --region $Region --query "repositories[].repositoryName" --output text
