# ClaimSense AWS capstone - shared helpers for the Windows PowerShell deploy scripts.
# Author: Vishal Gupta (AWS capstone edition)
# Dot-sourced by deploy.ps1, pause.ps1 and destroy.ps1. Do not run this file directly.

$ErrorActionPreference = "Stop"
$AppName      = "claimsense"
$ProjectRoot  = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TfDir        = Join-Path $PSScriptRoot "terraform"
$OpenAIParam  = "/$AppName/openai_api_key"

function Write-Step([string]$Text) { Write-Host "`n==> $Text" -ForegroundColor Cyan }

# Windows PowerShell 5.1 does not stop when a native .exe fails, so check the exit code ourselves.
function Assert-Ok([string]$What) {
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE). Read the error above." }
}

# The AWS CLI understands `aws login` profiles, but Terraform may not. So, just for each
# Terraform call, hand it the short-term keys of the profile as environment variables,
# then remove them again so later AWS CLI calls keep using the auto-refreshing profile.
function Invoke-Terraform {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$TfArgs)
    $lines = aws configure export-credentials --profile $AwsProfile --format powershell
    Assert-Ok "Reading credentials of profile '$AwsProfile' (run: aws login --profile $AwsProfile)"
    $lines | ForEach-Object { Invoke-Expression $_ }
    try {
        & terraform "-chdir=$TfDir" @TfArgs
        Assert-Ok "terraform $($TfArgs[0])"
    } finally {
        Remove-Item Env:AWS_ACCESS_KEY_ID, Env:AWS_SECRET_ACCESS_KEY, Env:AWS_SESSION_TOKEN, Env:AWS_CREDENTIAL_EXPIRATION -ErrorAction SilentlyContinue
    }
}

function Assert-AwsLogin {
    Write-Step "Checking you are signed in to AWS (profile '$AwsProfile', region $Region)"
    $script:AccountId = aws sts get-caller-identity --profile $AwsProfile --query Account --output text
    Assert-Ok "AWS sign-in check. Run: aws login --profile $AwsProfile"
    Write-Host "Signed in to AWS account $script:AccountId"
}

# Prints the public URL of the running task. The IP changes every time the task restarts.
function Show-AppUrl {
    Write-Step "Waiting for the service to become stable (2-5 minutes)"
    aws ecs wait services-stable --profile $AwsProfile --region $Region --cluster "$AppName-cluster" --services $AppName
    Assert-Ok "Waiting for ECS service (check logs: aws logs tail /ecs/$AppName --profile $AwsProfile --region $Region)"
    $task = aws ecs list-tasks --profile $AwsProfile --region $Region --cluster "$AppName-cluster" --service-name $AppName --query "taskArns[0]" --output text
    $eni  = aws ecs describe-tasks --profile $AwsProfile --region $Region --cluster "$AppName-cluster" --tasks $task --query "tasks[0].attachments[0].details[?name=='networkInterfaceId'].value" --output text
    $ip   = aws ec2 describe-network-interfaces --profile $AwsProfile --region $Region --network-interface-ids $eni --query "NetworkInterfaces[0].Association.PublicIp" --output text
    Write-Host ""
    Write-Host "ClaimSense is live:" -ForegroundColor Green
    Write-Host "  Portal   : http://${ip}:8501"
    Write-Host "  API docs : http://${ip}:8000/docs"
    Write-Host "  Logs     : aws logs tail /ecs/$AppName --follow --profile $AwsProfile --region $Region"
    Write-Host ""
    Write-Host "Pause when not demoing : powershell -ExecutionPolicy Bypass -File deploy\pause.ps1 -Action stop" -ForegroundColor Yellow
    Write-Host "Delete everything      : powershell -ExecutionPolicy Bypass -File deploy\destroy.ps1" -ForegroundColor Yellow
}
