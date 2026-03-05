# Env Inspector – Containerized Automation Platform

This project demonstrates a production-style DevOps automation pipeline built on AWS.



## Architecture (10 lines)

See `docs/architecture.txt` for the full diagram.

- GitHub Actions authenticates to AWS via OIDC
- CI builds the env-inspector container and pushes to ECR using immutable git-SHA tags
- CI registers a new ECS task definition revision with the new image
- CI updates the EventBridge schedule target to the new task definition revision
- EventBridge triggers the Fargate task on schedule (or manual run)
- env-inspector writes JSON output to a shared volume
- uploader copies output to S3 and updates a latest.json pointer
- CloudWatch Logs capture both containers’ output for audit/debugging



# --- ---


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
