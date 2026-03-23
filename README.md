# DevOps Training Projects

This repository is my public DevOps and platform engineering portfolio. It contains hands-on AWS projects built to demonstrate containerized automation, scheduled cloud workloads, CI/CD delivery, observability, artifact management, and production-style troubleshooting.

Rather than presenting isolated scripts, this portfolio shows how I build and operate complete systems: code, containers, deployment workflows, runtime behavior, stored artifacts, and operational debugging.

## Live Dashboard

GitHub Pages Dashboard:
https://jeffriley101.github.io/devops-training/

The dashboard presents current project artifacts, including preview images, live metadata, and monitoring output from the portfolio projects in this repository.

---

## Career Pivot Focus

I am using this portfolio to support my transition into roles such as:

- DevOps Engineer
- Platform Engineer
- Automation Engineer
- Cloud / Infrastructure Engineer
- Site Reliability Engineer

The projects here are intentionally aligned with the work I want to do professionally:

- containerized automation
- scheduled cloud workloads
- CI/CD delivery workflows
- AWS infrastructure
- monitoring and observability
- artifact-oriented system design
- operational troubleshooting across code and infrastructure

---

## What This Repository Demonstrates

Across the projects in this repository, the main capabilities include:

- Python-based automation packaged for container execution
- Docker image build workflows for repeatable deployment
- Amazon ECR image publishing
- Amazon ECS Fargate task execution
- Amazon EventBridge scheduled job orchestration
- GitHub Actions CI/CD pipelines
- AWS OIDC-based CI authentication
- Amazon S3 artifact storage and retention design
- Amazon CloudWatch logging and runtime visibility
- Terraform-managed infrastructure
- environment-aware runtime configuration
- historical artifact generation for trend analysis
- operational debugging across application, scheduler, task definition, and storage layers

---

## Portfolio Projects

### Env Inspector
containerized-tools/env-inspector/

A production-style containerized automation platform that collects runtime environment data, runs on AWS Fargate, and stores structured artifacts in Amazon S3 with deployment traceability metadata.

What it shows
- scheduled container automation on AWS
- CI/CD-driven deployment workflow
- ECS task definition revision management
- artifact persistence and traceability
- runtime debugging and operator-focused output design

Preview image:
dashboard/images/env-inspector-preview.png

---

### Market Snapshot Bot
containerized-tools/market-snapshot-bot/

A containerized AWS market-monitoring workload built to demonstrate scheduled cloud execution, artifact pipelines, real-data workflow integration, and production-style troubleshooting.

This project began with a platform baseline using mock workflows, then evolved into a real-data system while preserving mock mode for safe development and rollback.

What it shows
- reusable scheduled workload architecture
- external data workflow integration
- structured JSON, CSV, and PNG artifact generation
- environment-aware runtime design
- careful extension of a working platform without destabilizing the baseline
- debugging across logs, task definitions, scheduler targets, and artifact outputs

Preview image:
dashboard/images/market-snapshot-preview.png

---

### Internet Health Monitor
containerized-tools/internet-health-monitor/

A containerized AWS-based monitoring platform that performs scheduled HTTP health checks, measures latency, classifies service state, and publishes historical observability artifacts for operator review.

What it shows
- reliability-focused automation design
- service health monitoring and classification
- historical latency trend generation
- operator-readable reporting
- cloud-scheduled monitoring workflows
- observability-oriented artifact pipelines

Preview image:
dashboard/images/internet-health-preview.png

---

## Troubleshooting Highlights

### Debugging Highlight - QQQ Volume Anomalies

During the transition from mock data to real Yahoo Finance minute-volume data, the QQQ volume workflow began showing isolated zero-minute samples and distorted spikes in the generated chart.

I approached the issue as a production-style data pipeline investigation rather than assuming the chart code was wrong. I traced when the real-data path entered the project, then validated the upstream feed independently outside the application workflow. That testing showed the anomaly was present in the Yahoo minute data itself, not just introduced by chart rendering.

Rather than forcing a cosmetic fix, I treated this as a data-quality and observability problem:

- isolated the symptom in generated artifacts
- traced the change that introduced the real-data path
- reproduced the issue directly against the upstream dependency
- avoided hard-coding a misleading normalization rule before gathering more evidence
- evaluated whether slightly wider aggregation windows would produce more stable operator-facing charts

This is the kind of troubleshooting work I want these projects to demonstrate: not just building automated workflows, but diagnosing unexpected behavior in production-style systems.

For expanded notes and raw validation output, see `/containerized-tools/market-snapshot-bot/doc`.

---

## What I Learned Building These Projects

These projects helped move me from learning tools individually to operating them as connected systems.

1. A working platform is already meaningful engineering value
I learned that proving container builds, scheduled execution, logging, storage, and artifact flow is itself a substantial outcome.

2. Infrastructure and application behavior are tightly connected
Many issues were not purely code bugs. They involved task definitions, environment variables, scheduler targets, storage paths, deployment wiring, and runtime assumptions.

3. Logs and artifacts are the truth
I learned to verify behavior through CloudWatch logs, generated files, charts, stored outputs, and runtime metadata rather than assuming the system behaved correctly.

4. Safe evolution matters
Across these projects, I focused on preserving a working baseline, then extending it carefully instead of destabilizing what already worked.

5. Operational troubleshooting is a real engineering skill
A major part of the work involved diagnosing why infrastructure, scheduling, deployment, or runtime behavior did not match expectations.

6. Communication matters too
README quality, architecture explanation, artifact examples, and project clarity are part of professional engineering value, not extras.

---

## AWS / DevOps Technologies Used

- Python
- Docker
- GitHub Actions
- Terraform
- Amazon ECS
- AWS Fargate
- Amazon EventBridge Scheduler
- Amazon ECR
- Amazon S3
- Amazon CloudWatch Logs
- IAM
- Git / GitHub

---

## Repository Structure

.
├── containerized-tools/
│   ├── env-inspector/
│   ├── internet-health-monitor/
│   └── market-snapshot-bot/
├── dashboard/
└── README.md

Each project directory contains its own implementation details, architecture notes, usage instructions, and project-specific documentation.

---

## Why This Repository Matters

This repository is meant to show more than familiarity with DevOps concepts.

It demonstrates that I can take a workload from source control to build, deployment, execution, observability, artifact storage, and operational troubleshooting in AWS.

For recruiters and hiring managers, that is the point of this portfolio: practical evidence of hands-on DevOps, platform, and automation engineering work.
