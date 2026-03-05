# Env Inspector – Containerized Automation Platform

This project demonstrates a production-style DevOps automation pipeline built on AWS.

## Architecture

Developer
   │
   │ git push
   ▼
GitHub Actions (CI/CD)
   │
   │ build container
   ▼
Amazon ECR
   │
   │ new image tag
   ▼
Amazon ECS Task Definition
   │
   │ run task (manual or scheduled)
   ▼
ECS Fargate Runtime
   │
   ├─ env-inspector container
   │     collects environment metadata
   │
   └─ uploader container
         uploads artifact to S3
   ▼
Amazon S3 Artifact Store
   ├─ timestamped history
   └─ latest.json pointer

What This System Demonstrates

    Containerized automation tools

    CI/CD container build pipeline

    Immutable container artifacts

    ECS task orchestration

    Artifact storage in S3

    Infrastructure-as-Code workflow

    IAM role separation for runtime and CI/CD

Deployment Flow

git push
↓
GitHub Actions builds container
↓
Image pushed to Amazon ECR
↓
New ECS task definition revision registered
↓
Task executed (manual or scheduled)
↓
Automation output stored in S3


## Repository Structure

.github/workflows/      # CI/CD pipelines
containerized-tools/    # containerized automation tools
environments/           # infrastructure environments
modules/                # reusable Terraform modules

Example Artifact

Artifacts are written to:

s3://env-inspector-artifacts/.../training/YYYY/MM/DD/


Each run produces:

- timestamped JSON record
- `latest.json` pointer to most recent result

---

This project demonstrates a full DevOps automation lifecycle from source control to runtime execution.
