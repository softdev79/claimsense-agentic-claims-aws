<#
ClaimSense AWS capstone - pause or resume the app to save money.
Author: Vishal Gupta

    powershell -ExecutionPolicy Bypass -File deploy\pause.ps1 -Action stop    # 0 tasks: no Fargate / public-IP charges
    powershell -ExecutionPolicy Bypass -File deploy\pause.ps1 -Action start   # 1 task: prints the new URL

While paused you still pay a few cents a month for the image in ECR. Claims you submitted
are lost on restart (the demo keeps data inside the container), the 20 sample claims come back.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("stop", "start")][string]$Action,
    [string]$Region = "ap-south-1",
    [string]$AwsProfile = "claimsense"
)
. (Join-Path $PSScriptRoot "_common.ps1")
Assert-AwsLogin

$count = if ($Action -eq "stop") { 0 } else { 1 }
Write-Step "Setting the ECS service to $count running task(s)"
aws ecs update-service --profile $AwsProfile --region $Region --cluster "$AppName-cluster" --service $AppName --desired-count $count --query "service.desiredCount" --output text
Assert-Ok "Updating the ECS service"

if ($Action -eq "start") {
    Show-AppUrl
} else {
    Write-Host "Paused. Start again with: powershell -ExecutionPolicy Bypass -File deploy\pause.ps1 -Action start" -ForegroundColor Green
}
