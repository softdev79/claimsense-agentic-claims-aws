# ClaimSense - AWS deployment on ECS Fargate | Author: Vishal Gupta (AWS capstone edition), based on ClaimSense by Shivank Agrawal
#
# What this creates (all deleted by deploy/destroy.sh):
#   ECR repository      -> stores the Docker image
#   ECS cluster+service -> runs ONE Fargate task with two containers: api (8000) and ui (8501)
#   IAM execution role  -> lets ECS pull the image, write logs and read the OpenAI key from SSM
#   Security group      -> opens 8000 and 8501 to var.allowed_cidr
#   CloudWatch log group-> container logs, kept 7 days
#
# Why ECS Fargate and not EKS? One small service does not need a Kubernetes control plane
# (EKS costs ~$73/month before any workload). Fargate = no servers to patch, pay per second.

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "me" {}

# Use the account's default VPC and its public subnets (keeps the demo simple)
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

locals {
  openai_param_arn = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.me.account_id}:parameter${var.openai_param_name}"
  image            = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
  log_options = {
    "awslogs-group"  = aws_cloudwatch_log_group.app.name
    "awslogs-region" = var.aws_region
  }
}

# ---------------------------------------------------------------- image registry
resource "aws_ecr_repository" "app" {
  name         = var.app_name
  force_delete = true # lets `terraform destroy` remove the repo even if images exist

  image_scanning_configuration {
    scan_on_push = true
  }
}

# ---------------------------------------------------------------- logs
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.app_name}"
  retention_in_days = 7
}

# ---------------------------------------------------------------- IAM
data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${var.app_name}-execution-role"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# The key never appears in Terraform state or in the task definition: ECS injects it at start-up.
resource "aws_iam_role_policy" "read_openai_key" {
  name = "${var.app_name}-read-openai-key"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameters"]
      Resource = local.openai_param_arn
    }]
  })
}

# ---------------------------------------------------------------- network access
resource "aws_security_group" "app" {
  name        = "${var.app_name}-sg"
  description = "ClaimSense portal and API"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Streamlit portal"
    from_port   = 8501
    to_port     = 8501
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  ingress {
    description = "FastAPI (Swagger docs at /docs)"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [var.allowed_cidr]
  }

  egress {
    description = "Outbound to OpenAI, ECR, CloudWatch"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ---------------------------------------------------------------- ECS
resource "aws_ecs_cluster" "main" {
  name = "${var.app_name}-cluster"
}

resource "aws_ecs_task_definition" "app" {
  family                   = var.app_name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"        # both containers share localhost inside the task
  cpu                      = var.task_cpu    # default 1024 = 1 vCPU
  memory                   = var.task_memory # default 2048 = 2 GB
  execution_role_arn       = aws_iam_role.execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name         = "api"
      image        = local.image
      essential    = true
      portMappings = [{ containerPort = 8000, protocol = "tcp" }]
      secrets      = [{ name = "OPENAI_API_KEY", valueFrom = local.openai_param_arn }]
      environment = [
        { name = "OPENAI_BASE_URL", value = var.llm_base_url },
        { name = "OPENAI_PROJECT_ID", value = var.llm_project_id },
        { name = "OPENAI_MODEL", value = var.llm_model },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options   = merge(local.log_options, { "awslogs-stream-prefix" = "api" })
      }
    },
    {
      name         = "ui"
      image        = local.image
      essential    = true
      command      = ["streamlit", "run", "ui/portal.py", "--server.port", "8501", "--server.address", "0.0.0.0", "--server.headless", "true"]
      environment  = [{ name = "API_URL", value = "http://localhost:8000" }]
      portMappings = [{ containerPort = 8501, protocol = "tcp" }]
      dependsOn    = [{ containerName = "api", condition = "START" }]
      logConfiguration = {
        logDriver = "awslogs"
        options   = merge(local.log_options, { "awslogs-stream-prefix" = "ui" })
      }
    }
  ])
}

resource "aws_ecs_service" "app" {
  name                 = var.app_name
  cluster              = aws_ecs_cluster.main.id
  task_definition      = aws_ecs_task_definition.app.arn
  desired_count        = var.desired_count # 0 = paused, pay nothing for Fargate
  launch_type          = "FARGATE"
  force_new_deployment = true # pick up a newly pushed :latest image on every apply

  network_configuration {
    subnets          = data.aws_subnets.default.ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # no load balancer in this demo; the task gets a public IP
  }
}

# ---------------------------------------------------------------- cost guard-rail (capstone addition)
# Emails you when spend crosses 50/80/100% of the budget, or is forecast to cross 100%.
# The first two budgets in an account are free. A budget only ALERTS - it never stops resources,
# so still pause (deploy/pause.ps1 -Action stop) or destroy when you are done.
resource "aws_budgets_budget" "monthly" {
  count        = var.budget_email == "" ? 0 : 1
  name         = "${var.app_name}-monthly-budget"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = [50, 80, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
