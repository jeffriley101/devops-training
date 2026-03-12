# DevOps Automation & Platform Engineering Portfolio

This repository contains a set of production‑style DevOps projects built
while pivoting into **DevOps / Automation / Platform Engineering**.

The focus of this portfolio is not just application code, but
**operational systems**: containerized workloads, cloud execution
platforms, artifact pipelines, infrastructure‑as‑code, and
observability.

These projects demonstrate the ability to design, build, deploy, and
operate real workloads on AWS using modern DevOps practices.

------------------------------------------------------------------------

# Portfolio Projects

## 1. Env Inspector --- Containerized Automation Platform

A deterministic containerized automation CLI deployed to AWS and
executed on a schedule using ECS/Fargate.

The system demonstrates a full DevOps workflow from source control
through container build, deployment, scheduled execution, artifact
generation, and operational traceability.

### Key Capabilities

-   Python automation packaged in Docker
-   GitHub Actions CI/CD with AWS OIDC authentication
-   Immutable container publishing to Amazon ECR
-   ECS/Fargate task execution
-   EventBridge scheduled workloads
-   S3 artifact storage
-   CloudWatch log visibility
-   Runtime traceability metadata

Each execution produces artifacts containing metadata such as:

-   Git commit SHA
-   ECS task definition ARN
-   ECS task ARN
-   runtime environment data

This makes every run **auditable and traceable**.

**Primary Technologies**

Docker • AWS ECS • Fargate • EventBridge • ECR • S3 • CloudWatch •
GitHub Actions • Terraform

------------------------------------------------------------------------

## 2. Market Snapshot Bot --- Containerized Data Monitoring Platform

A containerized market monitoring system designed to demonstrate
**real‑world operational automation** using scheduled cloud workloads.

The project began as an infrastructure platform exercise and evolved
into a real‑data workflow while preserving mock mode for safe
development.

### Key Capabilities

-   Python automation packaged in Docker
-   Scheduled container execution using ECS/Fargate
-   EventBridge Scheduler job orchestration
-   Multi‑mode application design (price + volume workflows)
-   JSON, CSV, and PNG artifact generation
-   Historical dataset construction
-   Production‑style troubleshooting across AWS services

### Workflow Modes

**Price Monitoring** - Generates daily price snapshots - Maintains
historical CSV dataset - Produces price history charts

**Volume Monitoring** - Collects minute‑level intraday volume samples -
Produces structured monitoring artifacts - Generates monitoring charts
for market activity

The project demonstrates **how to evolve a stable platform from mock
workflows into real‑data automation** without breaking the underlying
infrastructure.

**Primary Technologies**

Python • Docker • AWS ECS • EventBridge • ECR • CloudWatch • S3

------------------------------------------------------------------------

## 3. Internet Health Monitor --- Reliability Monitoring Platform

A containerized monitoring system that performs scheduled Internet
health checks and generates historical observability artifacts.

Rather than being a simple "website checker," the project focuses on
**operational reliability workflows** similar to internal monitoring
tools used by real engineering teams.

### Key Capabilities

-   Scheduled HTTP health checks
-   Latency measurement and classification
-   Health state modeling (Healthy / Degraded / Unhealthy)
-   Structured artifact generation
-   Historical run tracking
-   Latency trend chart generation
-   Operator‑friendly reporting

Each monitoring run produces:

-   machine‑readable JSON results
-   human‑readable health reports
-   historical monitoring artifacts
-   latency trend charts

The system runs on AWS ECS/Fargate and stores artifacts in Amazon S3 for
historical analysis.

**Primary Technologies**

Python • Docker • AWS ECS • EventBridge • S3 • CloudWatch • Terraform

------------------------------------------------------------------------

# Core Skills Demonstrated

## DevOps Engineering

-   CI/CD pipeline design
-   container build and deployment workflows
-   immutable image publishing
-   scheduled automation workloads
-   artifact lifecycle management

## Cloud Engineering

-   AWS ECS / Fargate orchestration
-   EventBridge scheduled jobs
-   S3 artifact storage architecture
-   CloudWatch logging and troubleshooting
-   IAM least‑privilege design

## Infrastructure as Code

-   Terraform modular infrastructure
-   environment isolation
-   safe infrastructure lifecycle management

## Platform Engineering

-   containerized internal tooling
-   reusable workload execution patterns
-   artifact‑based observability
-   operator‑focused automation design

------------------------------------------------------------------------

# Engineering Philosophy

These projects focus on **building real systems rather than isolated
scripts**.

Each project demonstrates:

-   deterministic execution
-   traceable artifacts
-   operational visibility
-   reproducible infrastructure
-   clean deployment workflows

The goal is to show the ability to design **automation platforms and
internal engineering tools**, not just write code.

------------------------------------------------------------------------

# Current Direction

Primary areas of interest:

-   Automation platforms
-   Platform engineering
-   Developer tooling
-   Infrastructure‑aware CLI tools
-   Reliability engineering systems

------------------------------------------------------------------------

# Repository Structure

Typical layout:

    containerized-tools/
        env-inspector/
        market-snapshot-bot/
        internet-health-monitor/

    modules/
    environments/
    shared-infrastructure/

Each project includes its own documentation explaining architecture,
workflows, and operational behavior.

------------------------------------------------------------------------

# Author

Jeffrey Alan Riley

DevOps / Automation / Platform Engineering Portfolio
