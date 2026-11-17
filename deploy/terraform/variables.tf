# ClaimSense | Author: Vishal Gupta (AWS capstone edition), based on ClaimSense by Shivank Agrawal
variable "aws_region" {
  description = "AWS region to deploy into (ap-south-1 = Mumbai)"
  type        = string
  default     = "ap-south-1"
}

variable "app_name" {
  description = "Prefix for every resource name"
  type        = string
  default     = "claimsense"
}

variable "image_tag" {
  description = "Docker image tag pushed to ECR by deploy.sh"
  type        = string
  default     = "latest"
}

variable "allowed_cidr" {
  description = "Who can open the portal/API. Use your own IP like 49.36.10.20/32 for anything beyond a demo"
  type        = string
  default     = "0.0.0.0/0"
}

variable "openai_param_name" {
  description = "SSM Parameter Store name that holds the OpenAI key (created by deploy.sh, not by Terraform)"
  type        = string
  default     = "/claimsense/openai_api_key"
}

# ------------------------------------------------------------------ capstone additions (cost control)
variable "task_cpu" {
  description = "Fargate CPU units for the task (1024 = 1 vCPU). Both containers share it."
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Fargate memory in MiB for the task (must be a valid pair with task_cpu)"
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "How many copies of the task to run. 0 = paused (no Fargate charges), 1 = running"
  type        = number
  default     = 1
}

variable "budget_email" {
  description = "Email that receives AWS Budget alerts. Leave empty to skip creating the budget"
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD; alerts at 50%, 80% and 100% (actual) and 100% (forecast)"
  type        = number
  default     = 10
}

# ------------------------------------------------------------------ capstone additions (Amazon Bedrock)
variable "llm_base_url" {
  description = "Bedrock OpenAI-compatible endpoint, e.g. https://bedrock-mantle.us-east-1.api.aws/v1 (read from .env by deploy.ps1)"
  type        = string
  default     = ""
}

variable "llm_project_id" {
  description = "Bedrock project ID sent as OPENAI_PROJECT_ID (read from .env by deploy.ps1)"
  type        = string
  default     = ""
}

variable "llm_model" {
  description = "Bedrock model ID used for every agent step"
  type        = string
  default     = "qwen.qwen3-vl-235b-a22b-instruct"
}
