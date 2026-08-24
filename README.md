# Technical Project Portfolio

This repository is my public portfolio for systems, DevOps, application engineering,
automation, and applied software development.

The projects here span full-stack web applications, Linux automation, PostgreSQL-backed
systems, Android audio engineering, cloud infrastructure, CI/CD, monitoring, data
pipelines, and production-style troubleshooting.

Rather than presenting isolated scripts, this portfolio shows how I build and operate
complete systems: application code, persistence, deployment workflows, runtime behavior,
testing, observability, documentation, and lifecycle cleanup.

---

## Live Portfolio

GitHub Pages:

```text
https://jeffriley101.github.io/devops-training/
```

The dashboard highlights my current application projects first, followed by preserved
evidence from completed AWS and automation projects.

---

## Featured Projects

### Woodshed Woodchuck

`woodshed-woodchuck/`

A full-stack music-practice application designed to make practice more engaging for
student musicians through persistent accounts, practice tracking, challenges, contests,
rewards, verification workflows, and interactive music tools.

The application has grown from a browser-based prototype into a database-backed
multi-user system with persistent state, seasonal competition, rewards, trusted-adult
practice verification, and responsive interfaces for desktop and mobile use.

**What it shows**

- FastAPI application architecture
- PostgreSQL-backed persistent application state
- SQLAlchemy data models and Alembic migrations
- authenticated user workflows
- cross-device persistence
- practice verification workflows
- seasonal challenges, contests, rewards, and progression systems
- responsive JavaScript / HTML / CSS application design
- automated testing across core user and gameplay workflows
- production deployment and migration management on Render

**Technologies**

Python · FastAPI · PostgreSQL · SQLAlchemy · Alembic · JavaScript · HTML/CSS ·
Git · GitHub · Render

---

### KHJW / Hoojshwah Radio

`hoojshwah-radio/`

A simulated-live internet radio platform for original music, combining a browser-based
station with a native Android client.

The web application provides the station interface and programming experience, while
the Android client uses native Media3 playback so audio can continue reliably during
screen-off and background use.

**What it shows**

- FastAPI web application development
- browser-based streaming interface
- native Android background audio
- Media3 / ExoPlayer playback architecture
- MediaSessionService and MediaController integration
- lock-screen, notification, Bluetooth, and headset playback controls
- web UI and native playback integration
- mobile troubleshooting across browser and native application behavior
- Render deployment
- Gradle / Android build workflow

**Technologies**

Python · FastAPI · JavaScript · HTML/CSS · Android · Media3 · ExoPlayer ·
MediaSessionService · Gradle · Git · GitHub · Render

---

### JILT + JILT GAME

`jilt/`
`jilt-game/`

A Python and PostgreSQL analytics pipeline paired with a FastAPI prediction game.

JILT processes historical intraday market data, normalizes timestamps, stores structured
records in PostgreSQL, derives daily results with SQL, and generates reporting artifacts.

JILT GAME consumes those results and turns them into an interactive browser-based
prediction game with persisted guesses and results.

**What it shows**

- Python and PostgreSQL integration
- relational schema design
- SQL query development and derived summaries
- time-series data ingestion
- time-zone-aware normalization
- JSON and chart artifact generation
- FastAPI / Jinja web application development
- PostgreSQL-backed game-state persistence
- loosely coupled analytics-to-application integration
- local-first development followed by hosted public testing

**Technologies**

Python · PostgreSQL · SQL · FastAPI · Jinja2 · HTML/CSS · Render

---

## Cloud Lifecycle & Cost Governance

The AWS projects in this repository completed their intended demonstration lifecycle.

After preserving source code, screenshots, documentation, generated artifacts, and
representative runtime evidence, I retired unnecessary scheduled infrastructure and
removed obsolete AWS-dependent CI/CD configuration.

I also converted the portfolio itself to static GitHub Pages deployment and removed
reproducible local Terraform provider caches and retired-project Python environments.

This work is part of the portfolio story: building infrastructure is only one part of
engineering. Systems also need to be documented, cost-controlled, maintained, and
retired responsibly when they are no longer needed.

---

## Completed AWS / DevOps Projects

### Internet Health Monitor

`containerized-tools/internet-health-monitor/`

A containerized monitoring platform that performed scheduled HTTP health checks,
measured latency, classified service state, and published historical observability
artifacts for operator review.

**What it shows**

- Docker-based monitoring workloads
- AWS ECS / Fargate execution
- EventBridge scheduling
- CloudWatch logging
- S3 artifact publishing
- latency trend generation
- operator-readable reporting
- Terraform-managed infrastructure
- lifecycle and cost-governance cleanup

**Preview**

`dashboard/images/internet-health-preview.png`

---

### Market Snapshot Bot

`containerized-tools/market-snapshot-bot/`

A containerized market-data automation project built around scheduled cloud execution,
historical artifacts, chart generation, and external-data workflows.

The project began with mock workflows and later added real market-data integration while
preserving a safe development path.

**What it shows**

- scheduled container workloads
- external-data integration
- JSON, CSV, and PNG artifact generation
- persisted history
- environment-aware runtime configuration
- ECS / Fargate execution
- EventBridge scheduling
- CloudWatch troubleshooting
- S3 artifact storage
- lifecycle cleanup after project completion

**Preview**

`dashboard/images/market-snapshot-preview.png`

---

### Env Inspector

`containerized-tools/env-inspector/`

A containerized automation project that captured runtime environment metadata and
deployment traceability information from AWS ECS tasks.

**What it shows**

- GitHub Actions CI/CD
- AWS OIDC authentication
- immutable ECR image publishing
- ECS task-definition revision management
- Fargate execution
- runtime metadata capture
- S3 artifact persistence
- CloudWatch logging
- deployment traceability using Git SHA and task metadata

**Preview**

`dashboard/images/env-inspector-preview.png`

---

## Troubleshooting Highlight

### QQQ Volume Anomalies

During the transition from mock data to real Yahoo Finance minute-volume data, the
Market Snapshot Bot began showing isolated zero-minute samples and distorted spikes.

I treated the issue as a data-pipeline investigation rather than assuming the charting
code was wrong.

The investigation included:

- isolating the symptom in generated artifacts
- tracing when the real-data path entered the project
- testing the upstream data independently from the application
- confirming that anomalous samples were present in the upstream feed
- avoiding a cosmetic normalization rule before gathering enough evidence
- evaluating wider aggregation windows for more stable operator-facing charts

This project became useful evidence of production-style troubleshooting: following the
data through the system, validating dependencies independently, and avoiding a fix that
would hide rather than explain the underlying behavior.

Expanded notes are preserved under:

`containerized-tools/market-snapshot-bot/doc`

---

## Engineering Capabilities Demonstrated

Across these projects:

- Linux-based development and troubleshooting
- Python application and automation development
- FastAPI web application architecture
- PostgreSQL and SQL
- SQLAlchemy and Alembic
- server-backed persistent application state
- JavaScript / HTML / CSS interfaces
- Android native media playback
- Docker containerization
- Git and GitHub workflows
- GitHub Actions CI/CD
- AWS ECS / Fargate
- Amazon ECR
- Amazon EventBridge
- Amazon S3
- Amazon CloudWatch
- IAM / OIDC-based CI authentication
- Terraform infrastructure
- external-data pipelines
- generated JSON, CSV, and chart artifacts
- responsive desktop/mobile application testing
- operational debugging across application and infrastructure layers
- lifecycle and cost-governance decisions

---

## What I Learned Building These Projects

### 1. Application behavior and infrastructure are connected

Many real failures are not purely code bugs. They involve configuration, persistence,
network behavior, runtime assumptions, deployment wiring, and external dependencies.

### 2. Logs, state, and artifacts provide evidence

I rely on logs, database state, generated artifacts, runtime metadata, and reproducible
tests rather than assuming a deployment behaved correctly.

### 3. Safe evolution matters

Several projects began as smaller working systems and were expanded incrementally while
preserving existing behavior.

### 4. Persistence changes application design

Moving from browser-only state into PostgreSQL-backed accounts, progression, contests,
and verification workflows required stronger thinking about data integrity, migrations,
ownership, and cross-device behavior.

### 5. Platform limitations sometimes require architectural changes

KHJW's mobile playback work is an example: browser behavior alone could not provide the
reliability I wanted, so the project gained a native Android playback layer.

### 6. Lifecycle management is engineering work

A project is not finished just because it runs. It also needs to be tested, documented,
cost-controlled, preserved appropriately, and eventually retired when its operational
purpose is complete.

---

## Repository Structure

```text
.
├── containerized-tools/
│   ├── env-inspector/
│   ├── internet-health-monitor/
│   └── market-snapshot-bot/
├── dashboard/
├── hoojshwah-radio/
├── jilt/
├── jilt-game/
├── woodshed-woodchuck/
└── README.md
```

Individual project directories contain implementation details, documentation, tests,
and project-specific notes.

---

## Career Focus

I am using this portfolio to support roles such as:

- Linux Systems Engineer
- Infrastructure Support Engineer
- Production Support Engineer
- Technical Operations Engineer
- Platform Engineer
- DevOps Engineer
- Cloud Operations Engineer
- Site Reliability Engineer
- Automation Engineer

The common thread across these projects is practical systems work: building applications,
automating operations, diagnosing failures, managing persistent state, deploying software,
and maintaining systems through their full lifecycle.
