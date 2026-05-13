# DevOps Training Projects

This repository is my public DevOps, cloud, platform engineering, and applied software portfolio. It contains hands-on projects built to demonstrate containerized automation, scheduled cloud workloads, CI/CD delivery, observability, artifact management, production-style troubleshooting, and user-facing Python web applications.

Rather than presenting isolated scripts, this portfolio shows how I build and operate complete systems: application code, containers, deployment workflows, runtime behavior, stored artifacts, operational debugging, documentation, and lifecycle cleanup.

---

## Live Dashboard

GitHub Pages Dashboard:

```text
https://jeffriley101.github.io/devops-training/
```

The dashboard presents project artifacts, preview images, metadata, and preserved proof from the projects in this repository. Some earlier AWS scheduled workloads are now paused as part of cost-governance cleanup after final screenshots and artifacts were preserved.

---

## Career Focus

I am using this portfolio to support my transition into roles such as:

- DevOps Engineer
- Platform Engineer
- Automation Engineer
- Cloud / Infrastructure Engineer
- Site Reliability Engineer
- Production Support / Technical Operations Engineer

The projects here are intentionally aligned with the work I want to do professionally:

- containerized automation
- scheduled cloud workloads
- CI/CD delivery workflows
- AWS infrastructure
- monitoring and observability
- artifact-oriented system design
- operational troubleshooting across code and infrastructure
- Python web applications connected to real data workflows
- practical lifecycle and cost-governance decisions

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
- FastAPI application development
- PostgreSQL-backed application design
- Render deployment for public testing
- cost-governance cleanup after successful cloud demos

---

## Portfolio Projects

### Env Inspector

`containerized-tools/env-inspector/`

A production-style containerized automation platform that collects runtime environment data, runs on AWS Fargate, and stores structured artifacts in Amazon S3 with deployment traceability metadata.

**What it shows**

- scheduled container automation on AWS
- CI/CD-driven deployment workflow
- ECS task definition revision management
- artifact persistence and traceability
- runtime debugging and operator-focused output design
- completed cloud demo lifecycle with scheduled execution paused when no longer needed

**Preview image**

`dashboard/images/env-inspector-preview.png`

---

### Market Snapshot Bot

`containerized-tools/market-snapshot-bot/`

A containerized AWS market-monitoring workload built to demonstrate scheduled cloud execution, artifact pipelines, real-data workflow integration, and production-style troubleshooting.

This project began with a platform baseline using mock workflows, then evolved into a real-data system while preserving mock mode for safe development and rollback. It includes persisted price-history tracking, chart artifact generation, and a completed lifecycle/cost-governance phase where scheduled cloud runs were paused after final proof was preserved.

**What it shows**

- reusable scheduled workload architecture
- external data workflow integration
- structured JSON, CSV, and PNG artifact generation
- environment-aware runtime design
- persisted price-history workflow design
- careful extension of a working platform without destabilizing the baseline
- debugging across logs, task definitions, scheduler targets, and artifact outputs
- lifecycle cleanup and cost governance after project completion

**Preview image**

`dashboard/images/market-snapshot-preview.png`

**Final proof**

`containerized-tools/market-snapshot-bot/final-screenshots/`

---

### Internet Health Monitor

`containerized-tools/internet-health-monitor/`

A containerized AWS monitoring platform that performs HTTP health checks, measures latency, classifies service state, and publishes historical observability artifacts for operator review.

The project successfully demonstrated scheduled monitoring, S3 artifact publishing, latency trend generation, and dashboard presentation. Scheduled cloud runs are currently paused as part of cost-governance cleanup while final screenshots and artifacts are preserved.

**What it shows**

- reliability-focused automation design
- service health monitoring and classification
- historical latency trend generation
- operator-readable reporting
- cloud-scheduled monitoring workflows
- observability-oriented artifact pipelines
- lifecycle cleanup and cost governance after project completion

**Preview image**

`dashboard/images/internet-health-preview.png`

**Final proof**

`containerized-tools/internet-health-monitor/final-screenshots/`

---

### JILT — Jeff’s Intraday Low Toolkit

`jilt/`

A local-first Python and PostgreSQL analytics project built to analyze historical intraday market data and determine which 5-minute bucket most often contains a symbol’s daily low.

This project was designed to strengthen practical SQL, relational schema design, market-data normalization, derived-summary workflows, operator-readable reporting, and chart artifact generation through a real system instead of isolated exercises.

**What it shows**

- Python and PostgreSQL integration
- practical SQL schema design and query development
- historical intraday market-data ingestion
- derived summary-table workflows
- time-zone-aware data normalization
- CLI-based local analytics workflow design
- operator-readable terminal reporting and saved chart artifacts

**Project README**

`jilt/README.md`

---

### JILT-GAME

`jilt-game/`

A FastAPI web-game prototype that turns JILT intraday-low analytics into a playable daily bucket-guessing game.

JILT-GAME was deployed on Render for limited public testing and uses PostgreSQL, Jinja templates, generated result artifacts, and chart integration to connect a backend analytics workflow to a user-facing browser game.

**What it shows**

- FastAPI web application development
- PostgreSQL-backed game-state persistence
- Render deployment for public testing
- artifact-driven integration with an analytics pipeline
- Jinja template rendering and HTML/CSS interface design
- daily result ingestion and winner display
- turning backend analytics into an interactive product prototype

**Project README**

`jilt-game/README.md`

---

### Woodshed Woodchuck

`woodshed-woodchuck/`

A game for musicians.

This project is in development.

---

## Troubleshooting Highlight

### QQQ Volume Anomalies

During the transition from mock data to real Yahoo Finance minute-volume data, the QQQ volume workflow began showing isolated zero-minute samples and distorted spikes in the generated chart.

I approached the issue as a production-style data pipeline investigation rather than assuming the chart code was wrong. I traced when the real-data path entered the project, then validated the upstream feed independently outside the application workflow. That testing showed the anomaly was present in the Yahoo minute data itself, not just introduced by chart rendering.

Rather than forcing a cosmetic fix, I treated this as a data-quality and observability problem:

- isolated the symptom in generated artifacts
- traced the change that introduced the real-data path
- reproduced the issue directly against the upstream dependency
- avoided hard-coding a misleading normalization rule before gathering more evidence
- evaluated whether slightly wider aggregation windows would produce more stable operator-facing charts

This is the kind of troubleshooting work I want these projects to demonstrate: not just building automated workflows, but diagnosing unexpected behavior in production-style systems.

For expanded notes and raw validation output, see:

`containerized-tools/market-snapshot-bot/doc`

---

## Cost-Governance / Lifecycle Note

Several AWS scheduled workloads in this repository successfully demonstrated their intended architecture and behavior. After preserving final screenshots, generated artifacts, and README documentation, their schedules were paused to avoid unnecessary ongoing cloud usage.

This is part of the portfolio story: I did not only build cloud workloads; I also managed their lifecycle responsibly.

Currently paused scheduled workloads include:

- Env Inspector schedule
- Internet Health Monitor schedule
- Market Snapshot Bot price schedule
- Market Snapshot Bot volume schedule

---

## What I Learned Building These Projects

These projects helped move me from learning tools individually to operating them as connected systems.

### 1. A working platform is already meaningful engineering value

Proving container builds, scheduled execution, logging, storage, and artifact flow is itself a substantial outcome.

### 2. Infrastructure and application behavior are tightly connected

Many issues were not purely code bugs. They involved task definitions, environment variables, scheduler targets, storage paths, deployment wiring, and runtime assumptions.

### 3. Logs and artifacts are the truth

I learned to verify behavior through CloudWatch logs, generated files, charts, stored outputs, and runtime metadata rather than assuming the system behaved correctly.

### 4. Safe evolution matters

Across these projects, I focused on preserving a working baseline, then extending it carefully instead of destabilizing what already worked.

### 5. Operational troubleshooting is a real engineering skill

A major part of the work involved diagnosing why infrastructure, scheduling, deployment, or runtime behavior did not match expectations.

### 6. Lifecycle management matters

A cloud project is not finished just because it runs. It also needs to be documented, validated, cost-controlled, and either maintained or intentionally paused.

### 7. Communication matters too

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

## Application / Data Technologies Used

- FastAPI
- Jinja templates
- PostgreSQL
- SQL
- HTML / CSS
- JSON and CSV artifacts
- Render

---

## Repository Structure

```text
.
├── containerized-tools/
│   ├── env-inspector/
│   ├── internet-health-monitor/
│   └── market-snapshot-bot/
├── dashboard/
├── jilt/
├── jilt-game/
├── woodshed-woodchuck/
└── README.md
```

Each project directory contains its own implementation details, architecture notes, usage instructions, and project-specific documentation.

---

## Why This Repository Matters

This repository is meant to show more than familiarity with DevOps concepts.

It demonstrates that I can take a workload from source control through build, deployment, execution, observability, artifact storage, operational troubleshooting, documentation, and lifecycle cleanup.

It also shows that I can extend backend automation and analytics into user-facing applications when the project calls for it.

For recruiters and hiring managers, that is the point of this portfolio: practical evidence of hands-on DevOps, platform, automation, and applied software engineering work.
