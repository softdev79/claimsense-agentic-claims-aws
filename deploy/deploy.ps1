<#
ClaimSense AWS capstone - one-command deploy from Windows (PyCharm terminal / PowerShell).
Author: Vishal Gupta

Needs: AWS CLI v2 signed in (aws login --profile claimsense), Terraform >= 1.5, Docker Desktop running.

Run from the project root:
    powershell -ExecutionPolicy Bypass -File deploy\deploy.ps1 -BudgetEmail you@example.com

Options:
    -Region       AWS region (default ap-south-1 = Mumbai)
    -AwsProfile   AWS CLI profile created by `aws login --profile ...` (default claimsense)
    -OpenToWorld  Let anyone on the internet open the app (default: only YOUR current IP)
    -BudgetEmail  Email for AWS Budget cost alerts (strongly recommended)
    -BudgetUsd    Monthly budget in USD (default 10)
#>
param(
    [string]$Region = "ap-south-1",
    [string]$AwsProfile = "claimsense",
    [switch]$OpenToWorld,
    [string]$BudgetEmail = "",
    [int]$BudgetUsd = 10
)
. (Join-Path $PSScriptRoot "_common.ps1")
Set-Location $ProjectRoot

# ---------------------------------------------------------------- 0. pre-flight checks
Assert-AwsLogin

Write-Step "Checking Docker Desktop is running"
docker info --format "{{.ServerVersion}}" | Out-Null
Assert-Ok "Docker check. Start Docker Desktop and wait until it says 'Engine running'"

$tfVersion = terraform -version   # capture first: Select-Object -First would kill terraform mid-output
Assert-Ok "Terraform check"
$tfVersion | Select-Object -First 1

# ---------------------------------------------------------------- 1. Bedrock key -> SSM
# Reads a value from the environment first, then from .env in the project root.
function Get-Setting([string]$Name) {
    $value = [Environment]::GetEnvironmentVariable($Name)
    if (-not $value -and (Test-Path ".env")) {
        $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -First 1
        if ($line) { $value = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'") }
    }
    return $value
}

Write-Step "1/5 Saving the Bedrock API key in SSM Parameter Store (encrypted, never in Terraform state)"
$baseUrl   = Get-Setting "OPENAI_BASE_URL"
$projectId = Get-Setting "OPENAI_PROJECT_ID"
$model     = Get-Setting "OPENAI_MODEL"
if (-not $baseUrl) { throw "OPENAI_BASE_URL is missing in .env (e.g. https://bedrock-mantle.us-east-1.api.aws/v1)" }
Write-Host "Model endpoint: $baseUrl"
# Budget email: -BudgetEmail, else BUDGET_EMAIL in .env. (Deploying without one would DELETE an existing budget.)
if (-not $BudgetEmail) { $BudgetEmail = Get-Setting "BUDGET_EMAIL" }
if (-not $BudgetEmail) { Write-Host "No budget email given: no AWS Budget alert will exist after this deploy." -ForegroundColor Yellow }
$key = Get-Setting "OPENAI_API_KEY"
if (-not $key -or $key -like "your-*" -or $key -like "sk-your-key*") {
    $secure = Read-Host "Paste your Bedrock API key (input hidden)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
aws ssm put-parameter --profile $AwsProfile --region $Region --name $OpenAIParam --type SecureString --value $key --overwrite | Out-Null
Assert-Ok "Saving the key in SSM"
$key = $null

# Who may open the app: your IP only (safer, the app spends your OpenAI credits) or everyone.
if ($OpenToWorld) {
    $cidr = "0.0.0.0/0"
    Write-Host "WARNING: the portal will be open to the whole internet." -ForegroundColor Yellow
} else {
    $cidr = (Invoke-RestMethod -Uri "https://checkip.amazonaws.com").Trim() + "/32"
    Write-Host "Only your current IP ($cidr) will be allowed in. Re-run deploy.ps1 if your IP changes."
}
$tfVars = @("-var=aws_region=$Region", "-var=allowed_cidr=$cidr", "-var=budget_email=$BudgetEmail",
            "-var=monthly_budget_usd=$BudgetUsd", "-var=llm_base_url=$baseUrl", "-var=llm_project_id=$projectId")
if ($model) { $tfVars += "-var=llm_model=$model" }

# ---------------------------------------------------------------- 2. ECR repository
Write-Step "2/5 Creating the ECR repository (where the Docker image is stored)"
Invoke-Terraform init -input=false
Invoke-Terraform apply -auto-approve -input=false @tfVars "-target=aws_ecr_repository.app"
$repo = (& terraform "-chdir=$TfDir" output -raw ecr_repository_url)
Assert-Ok "Reading ECR URL"
$registry = $repo.Split("/")[0]

# ---------------------------------------------------------------- 3. build + push image
Write-Step "3/5 Building and pushing the Docker image (first build takes 3-8 minutes)"
docker build --platform linux/amd64 -t "${repo}:latest" .
Assert-Ok "docker build"

# The ECR login token (from `aws login` session credentials) is too long for Windows Credential Manager
# ("The stub received bad data"). So, only for login + push: use a throw-away Docker config folder and hide
# the Windows credential helpers from PATH, so Docker keeps the token in that folder. The folder (and the
# token, valid 12 h) is deleted right after the push. Your normal Docker setup is not changed.
$dockerExe = (Get-Command docker).Source
$tempDockerConfig = Join-Path $env:TEMP ("claimsense-docker-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tempDockerConfig | Out-Null
Set-Content -Path (Join-Path $tempDockerConfig "config.json") -Value "{}" -Encoding ascii
$savedPath = $env:PATH
try {
    $env:DOCKER_CONFIG = $tempDockerConfig
    $env:PATH = ($env:PATH -split ';' | Where-Object {
        $_ -and -not (Test-Path (Join-Path $_ 'docker-credential-wincred.exe')) -and
        -not (Test-Path (Join-Path $_ 'docker-credential-desktop.exe')) }) -join ';'
    # Pipe through cmd.exe: Windows PowerShell 5.1 re-encodes text piped between programs, which corrupts
    # the password and makes ECR answer "400 Bad Request".
    cmd /c "aws ecr get-login-password --profile $AwsProfile --region $Region | `"$dockerExe`" login --username AWS --password-stdin $registry"
    Assert-Ok "Docker login to ECR"
    & $dockerExe push "${repo}:latest"
    Assert-Ok "docker push"
} finally {
    $env:PATH = $savedPath
    Remove-Item Env:DOCKER_CONFIG -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tempDockerConfig -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- 4. everything else
Write-Step "4/5 Creating ECS cluster, service, IAM role, security group, logs, budget"
Invoke-Terraform apply -auto-approve -input=false @tfVars

# ---------------------------------------------------------------- 5. URL
Write-Step "5/5 Starting the app"
Show-AppUrl
if ($BudgetEmail) { Write-Host "Budget alerts go to $BudgetEmail (check your inbox/spam)." }
