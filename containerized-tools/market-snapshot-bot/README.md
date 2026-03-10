# Market Snapshot Bot

Market Snapshot Bot is a portfolio-style containerized market monitoring workload built to demonstrate Python automation, ECS/Fargate task execution, AWS EventBridge Scheduler orchestration, artifact generation, and production-style operational workflow.

## Project Purpose

This project demonstrates how to:

- package a Python workload in Docker
- publish the container image to Amazon ECR
- register and run the workload as an ECS Fargate task
- schedule recurring executions with EventBridge Scheduler
- separate workflow modes within one application entrypoint
- generate structured output artifacts for downstream analysis

## Workflow Modes

The application supports multiple modes from a single entrypoint.

### 1. Price Monitoring

Command:

```bash
python3 -m app.main --mode price
```

Purpose:

generate a daily price snapshot

append structured historical CSV data

produce a multi-day price trend chart

2. Volume Monitoring

Command:

python3 -m app.main --mode volume

Purpose:

run during a weekday intraday monitoring window

collect minute-by-minute sample data

generate JSON, CSV, and intraday chart artifacts

Local Developer Commands

Shell aliases used during development:

price
volume
volume-dev
price-chart
volume-chart
Containerization

The project is packaged as a Docker image and pushed to Amazon ECR.

Example image URI:

646313139147.dkr.ecr.us-east-1.amazonaws.com/market-snapshot-bot:latest
AWS Runtime Architecture

The workload runs on:

Amazon ECR for container image storage

Amazon ECS Fargate for task execution

Amazon EventBridge Scheduler for recurring task scheduling

Amazon CloudWatch Logs for runtime logging

ECS Resources

ECS Cluster: env-inspector-cluster

ECS Task Definition Family: market-snapshot-bot

Container Name: market-snapshot-bot

IAM Roles

Task definition roles in use:

Execution Role: arn:aws:iam::646313139147:role/env-inspector-task-execution-role

Task Role: arn:aws:iam::646313139147:role/env-inspector-task-role

Scheduler execution role:

arn:aws:iam::646313139147:role/market-monitor-scheduler-role

EventBridge Scheduler Setup

Two schedules are configured.

Daily Price Schedule

Name: market-monitor-price-daily

Time: daily at 4:00 PM America/New_York

Cron:

cron(0 16 * * ? *)
Weekday Volume Schedule

Name: market-monitor-volume-weekday

Time: weekdays at 10:08 AM America/New_York

Cron:

cron(8 10 ? * MON-FRI *)
Scheduler Target Files

Scheduler target definitions are stored in:

infra/scheduler/price-target.json
infra/scheduler/volume-target.json

These targets define:

ECS cluster ARN

task definition ARN

Fargate launch type

awsvpc subnet and security group settings

container command override for each workflow mode

Useful AWS Environment Variables

Common exported values used for setup and troubleshooting:

export AWS_REGION="us-east-1"
export AWS_DEFAULT_REGION="us-east-1"
export CLUSTER_ARN="arn:aws:ecs:us-east-1:646313139147:cluster/env-inspector-cluster"
export SCHEDULER_ROLE_ARN="arn:aws:iam::646313139147:role/market-monitor-scheduler-role"
export SUBNET_1="subnet-0c62c182b234ae364"
export SUBNET_2="subnet-0afa828b1fbad3f37"
export SECURITY_GROUP_ID="sg-0de0c1e877a79617d"
export CONTAINER_NAME="market-snapshot-bot"
Manual ECS Test Command

Example one-off run for the price workflow:

command aws ecs run-task \
  --region "$AWS_REGION" \
  --cluster "$CLUSTER_ARN" \
  --launch-type FARGATE \
  --task-definition "$TASK_DEFINITION_ARN" \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET_1,$SUBNET_2],securityGroups=[$SECURITY_GROUP_ID],assignPublicIp=ENABLED}" \
  --overrides '{"containerOverrides":[{"name":"market-snapshot-bot","command":["python3","-m","app.main","--mode","price"]}]}'
Scheduler Management Commands
Verify Schedules
command aws scheduler list-schedules \
  --region "$AWS_REGION" \
  --group-name default \
  --output table
Inspect One Schedule
command aws scheduler get-schedule \
  --region "$AWS_REGION" \
  --name market-monitor-price-daily \
  --group-name default
Disable Volume Schedule
command aws scheduler update-schedule \
  --region "$AWS_REGION" \
  --name market-monitor-volume-weekday \
  --group-name default \
  --schedule-expression 'cron(8 10 ? * MON-FRI *)' \
  --schedule-expression-timezone 'America/New_York' \
  --flexible-time-window '{"Mode":"OFF"}' \
  --state DISABLED \
  --target file://volume-target.json
Disable Price Schedule
command aws scheduler update-schedule \
  --region "$AWS_REGION" \
  --name market-monitor-price-daily \
  --group-name default \
  --schedule-expression 'cron(0 16 * * ? *)' \
  --schedule-expression-timezone 'America/New_York' \
  --flexible-time-window '{"Mode":"OFF"}' \
  --state DISABLED \
  --target file://price-target.json
Log Verification

Tail logs from the ECS task:

command aws logs tail /ecs/market-snapshot-bot \
  --region "$AWS_REGION" \
  --since 1h
Current Completion Status

Implementation is complete through:

local workflow support

Docker packaging

ECR image push

ECS task definition registration

manual ECS task execution

EventBridge Scheduler configuration

Final completion requires successful verification of the scheduled run in ECS logs and output artifacts.
