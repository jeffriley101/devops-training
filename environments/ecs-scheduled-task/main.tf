terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.5"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "image_uri" {
  type        = string
  description = "Full ECR image URI including tag"
  # Example: 646313139147.dkr.ecr.us-east-1.amazonaws.com/env-inspector:git-f56c56f
}

variable "schedule_expression" {
  type        = string
  description = "EventBridge schedule expression"
  # Example: rate(5 minutes)  OR  cron(0/10 * * * ? *)
  default = "rate(10 minutes)"
}


# --- S3 bucket ---
resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "env-inspector-artifacts-${random_id.suffix.hex}"
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# --- Use default VPC/subnets to keep this simple ---
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}


# --- CloudWatch logs ---
resource "aws_cloudwatch_log_group" "env_inspector" {
  name              = "/ecs/env-inspector"
  retention_in_days = 14
}

# --- ECS Cluster ---
resource "aws_ecs_cluster" "this" {
  name = "env-inspector-cluster"
}

# --- Security Group for the task ENI (egress-only is fine) ---
resource "aws_security_group" "task" {
  name        = "env-inspector-task-sg"
  description = "SG for env-inspector scheduled task"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}


# --- IAM: task execution role (pull image, write logs) ---
data "aws_iam_policy_document" "task_exec_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "env-inspector-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.task_exec_assume.json
}

resource "aws_iam_role_policy_attachment" "task_exec_attach" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Optional: a task role (empty for now; useful later if your app needs AWS API access)
resource "aws_iam_role" "task_role" {
  name               = "env-inspector-task-role"
  assume_role_policy = data.aws_iam_policy_document.task_exec_assume.json
}


# --- S3 permissions for task role ---
data "aws_iam_policy_document" "task_s3_write" {
  statement {
    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload"
    ]
    resources = [
      "${aws_s3_bucket.artifacts.arn}/env-inspector/*"
    ]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["env-inspector/*"]
    }
  }
}

resource "aws_iam_role_policy" "task_s3_write" {
  name   = "env-inspector-task-s3-write"
  role   = aws_iam_role.task_role.id
  policy = data.aws_iam_policy_document.task_s3_write.json
}

output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}


# --- ECS Task Definition ---
resource "aws_ecs_task_definition" "this" {
  family                   = "env-inspector"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task_role.arn

  volume {
    name = "shared"
  }

  container_definitions = jsonencode([
    {
      name      = "env-inspector"
      image     = var.image_uri
      essential = false

      command = ["--format", "json", "--out", "/shared/env-inspector.json"]

      mountPoints = [
        { sourceVolume = "shared", containerPath = "/shared", readOnly = false }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.env_inspector.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    },

    {
      name      = "uploader"
      image     = "public.ecr.aws/aws-cli/aws-cli:2.15.57"
      essential = true

      entryPoint = ["sh", "-c"]

      dependsOn = [
        { containerName = "env-inspector", condition = "COMPLETE" }
      ]

      mountPoints = [
        { sourceVolume = "shared", containerPath = "/shared", readOnly = false }
      ]

      command = [
        "set -euo pipefail; TS=$(date -u +%Y%m%dT%H%M%SZ); Y=$(date -u +%Y); M=$(date -u +%m); D=$(date -u +%d); KEY=env-inspector/${var.env_name}/$${Y}/$${M}/$${D}/env-inspector-$${TS}.json; aws s3 cp /shared/env-inspector.json s3://${aws_s3_bucket.artifacts.bucket}/$${KEY}; aws s3 cp /shared/env-inspector.json s3://${aws_s3_bucket.artifacts.bucket}/env-inspector/${var.env_name}/latest.json"
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.env_inspector.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}



# --- IAM: EventBridge -> ECS RunTask role ---
data "aws_iam_policy_document" "events_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "events_to_ecs" {
  name               = "env-inspector-events-to-ecs-role"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

data "aws_iam_policy_document" "events_to_ecs_policy" {
  statement {
    actions = [
      "ecs:RunTask"
    ]
    resources = [aws_ecs_task_definition.this.arn]
  }

  # EventBridge must be allowed to pass both roles to ECS
  statement {
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.task_execution.arn,
      aws_iam_role.task_role.arn
    ]
  }
}

resource "aws_iam_role_policy" "events_to_ecs_inline" {
  name   = "env-inspector-events-to-ecs-policy"
  role   = aws_iam_role.events_to_ecs.id
  policy = data.aws_iam_policy_document.events_to_ecs_policy.json
}

# --- Schedule rule + target ---
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "env-inspector-schedule"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "ecs" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "env-inspector-ecs-task"
  arn       = aws_ecs_cluster.this.arn
  role_arn  = aws_iam_role.events_to_ecs.arn

  ecs_target {
    task_definition_arn = aws_ecs_task_definition.this.arn
    launch_type         = "FARGATE"
    task_count          = 1

    network_configuration {
      subnets          = data.aws_subnets.default.ids
      security_groups  = [aws_security_group.task.id]
      assign_public_ip = true
    }
  }
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.env_inspector.name
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}
