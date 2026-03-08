# Env Inspector – Containerized Automation Platform
`env-inspector` is a production-style DevOps automation project built on AWS with containerized tooling, CI/CD deployment, scheduled execution, artifact storage, and runtime traceability.

The platform builds a containerized automation tool, deploys it through GitHub Actions, runs it on AWS Fargate, and stores execution artifacts in Amazon S3.


## Overview
This project demonstrates how to build and operate a scheduled container automation platform using:

- Amazon ECS
- AWS Fargate
- Amazon EventBridge
- Amazon ECR
- Amazon S3
- Amazon CloudWatch Logs
- GitHub Actions
- Terraform

The system supports both scheduled execution and manual execution of the same containerized automation workflow.


## What This Project Does
The `env-inspector` container collects runtime environment data and produces a JSON artifact. A second `uploader` container copies that artifact to S3, where it is stored both as a timestamped record and as a rolling `latest.json`.

The platform also captures deployment and runtime traceability metadata, including:

- Git commit SHA
- ECS task definition ARN
- ECS task ARN
- run source

This makes each artifact auditable: you can tell what code produced it, what task definition ran it, and which ECS task executed it.


## Architecture
See `docs/architecture.txt` for the text version of the diagram.

![Env Inspector architecture](docs/architecture-diagram.svg)

### High-level flow
1. GitHub Actions authenticates to AWS using OIDC
2. CI builds the `env-inspector` container image
3. CI pushes the image to Amazon ECR using an immutable git-SHA tag
4. CI registers a new ECS task definition revision
5. CI updates the EventBridge target to use the new task definition
6. EventBridge triggers the ECS Fargate task on schedule
7. `env-inspector` collects environment data and writes JSON output to a shared volume
8. `uploader` copies the artifact to Amazon S3
9. CloudWatch Logs capture execution output for visibility and debugging


## Deployment Flow

```text
git push
  ↓
GitHub Actions builds container
  ↓
Image pushed to Amazon ECR
  ↓
New ECS task definition revision registered
  ↓
EventBridge target updated to new revision
  ↓
Scheduled Fargate task runs container
  ↓
Automation output stored in S3
```


## Example Output
Example human-readable output from a local run:

```text
Collecting environment data... done
[INFO] Collected environment data

ENV INSPECTOR
=========================================

STATUS: OK
WARNINGS: none

----------------------------------------

DETAILS
-----------------------------------------
timestamp_utc_iso            2026-03-08T05:16:07.226529+00:00
timestamp_utc_human          2026-03-08 05:16:07 UTC
hostname                     26fb681d74a8
ip_address                   172.17.0.2
platform                     Linux
platform_release             6.17.0-14-generic
python_version               3.12.13
environment_variables_count  8
cpu_count                    4
memory_total_mb              7648
disk_total_gb                55.99
disk_free_gb                 19.61
git_sha                      unknown
task_def_arn                 unknown
task_arn                     unknown
run_source                   manual
```


### Example ECS Artifact
Example structured JSON artifact from a successful ECS run:

```json
{
  "cpu_count": 2,
  "disk_free_gb": 17.77,
  "disk_total_gb": 29.36,
  "environment_variables_count": 15,
  "git_sha": "3606b13aa142d786860ea61cba9c0a2fb3ae75ae",
  "hostname": "ip-172-31-16-239.ec2.internal",
  "ip_address": "172.31.16.239",
  "memory_total_mb": 939,
  "platform": "Linux",
  "platform_release": "5.10.248-247.988.amzn2.x86_64",
  "python_version": "3.12.13",
  "run_source": "manual",
  "status": "OK",
  "task_arn": "arn:aws:ecs:us-east-1:646313139147:task/env-inspector-cluster/6ab36f008338427fb86628a25c03942f",
  "task_def_arn": "arn:aws:ecs:us-east-1:646313139147:task-definition/env-inspector:19",
  "timestamp_utc_human": "2026-03-08 05:39:45 UTC",
  "timestamp_utc_iso": "2026-03-08T05:39:45.346134+00:00",
  "warnings": []
}
```


