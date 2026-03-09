# DevOps Training Projects

This repository contains hands-on DevOps and platform engineering projects built to demonstrate real cloud infrastructure, containerized automation, CI/CD workflows, artifact management, and operational debugging.

The focus is on production-style systems rather than isolated code samples. Each project is designed to show how code moves from source control to build, deployment, execution, observability, and stored artifacts in a real AWS-based environment.



## What This Repository Demonstrates

Across the projects in this repository, the main capabilities include:

- containerized automation with Python
- AWS ECS/Fargate scheduled workloads
- EventBridge-based job scheduling
- GitHub Actions CI/CD pipelines
- Amazon ECR image publishing
- Amazon S3 artifact storage design
- CloudWatch logging and operational visibility
- Terraform-based infrastructure management
- runtime traceability and deployment debugging



## Projects

### Env Inspector
`containerized-tools/env-inspector/`

A containerized automation platform that collects runtime environment data, runs on AWS Fargate, and publishes structured artifacts to Amazon S3.

### Market Snapshot Bot
`containerized-tools/market-snapshot-bot/`

A scheduled market data automation platform built on a reusable containerized AWS foundation, focused on collecting external data and producing normalized artifacts.

Additional projects may be added over time as the repository expands.



## Repository Structure

```text
.
├── archive/
├── backups/
├── bin/
├── containerized-tools/
│   ├── env-inspector/
│   └── market-snapshot-bot/
└── README.md
```
