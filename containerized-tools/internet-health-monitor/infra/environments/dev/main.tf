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

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "artifacts" {
  bucket = "internet-health-monitor-artifacts-${random_id.suffix.hex}"
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

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_cloudwatch_log_group" "internet_health_monitor" {
  name              = "/ecs/internet-health-monitor"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "this" {
  name = "internet-health-monitor-cluster"
}

resource "aws_security_group" "task" {
  name        = "internet-health-monitor-task-sg"
  description = "SG for internet-health-monitor scheduled task"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

data "aws_iam_policy_document" "task_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "internet-health-monitor-task-execution-role"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

resource "aws_iam_role_policy_attachment" "task_exec_attach" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role" "task_role" {
  name               = "internet-health-monitor-task-role"
  assume_role_policy = data.aws_iam_policy_document.task_assume.json
}

data "aws_iam_policy_document" "task_s3_write" {
  statement {
    actions = [
      "s3:PutObject",
      "s3:AbortMultipartUpload"
    ]
    resources = [
      "${aws_s3_bucket.artifacts.arn}/internet-health-monitor/*"
    ]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.artifacts.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["internet-health-monitor/*"]
    }
  }
}

resource "aws_iam_role_policy" "task_s3_write" {
  name   = "internet-health-monitor-task-s3-write"
  role   = aws_iam_role.task_role.id
  policy = data.aws_iam_policy_document.task_s3_write.json
}

resource "aws_ecs_task_definition" "this" {
  family                   = "internet-health-monitor"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task_role.arn

  container_definitions = jsonencode([
    {
      name      = "internet-health-monitor"
      image     = var.image_uri
      essential = true

      environment = [
        { name = "ENVIRONMENT", value = var.env_name },
        { name = "GIT_SHA", value = "terraform-dev" },
        { name = "ARTIFACT_S3_BUCKET", value = aws_s3_bucket.artifacts.bucket },
        { name = "ARTIFACT_S3_PREFIX", value = "internet-health-monitor/${var.env_name}" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.internet_health_monitor.name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "ecs"
        }
      }
    }
  ])
}

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
  name               = "internet-health-monitor-events-to-ecs-role"
  assume_role_policy = data.aws_iam_policy_document.events_assume.json
}

data "aws_iam_policy_document" "events_to_ecs_policy" {
  statement {
    actions   = ["ecs:RunTask"]
    resources = [aws_ecs_task_definition.this.arn]
  }

  statement {
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.task_execution.arn,
      aws_iam_role.task_role.arn
    ]
  }
}

resource "aws_iam_role_policy" "events_to_ecs_inline" {
  name   = "internet-health-monitor-events-to-ecs-policy"
  role   = aws_iam_role.events_to_ecs.id
  policy = data.aws_iam_policy_document.events_to_ecs_policy.json
}

resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "internet-health-monitor-schedule"
  schedule_expression = var.schedule_expression
}

resource "aws_cloudwatch_event_target" "ecs" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "internet-health-monitor-ecs-task"
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
