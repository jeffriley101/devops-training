# Market Snapshot Bot

Market Snapshot Bot is a portfolio-style containerized market monitoring platform built to demonstrate Python automation, AWS scheduled container execution, artifact generation, and production-style operational troubleshooting.

The project began as a platform and infrastructure exercise to prove container packaging, ECS/Fargate task execution, EventBridge scheduling, logging, and artifact pipelines. It then evolved into a real-data workflow project while preserving mock mode for safe development and testing.

---

## What This Project Demonstrates

* Python automation packaged in Docker
* Amazon ECR image publishing
* Amazon ECS Fargate task execution
* EventBridge Scheduler recurring job orchestration
* multi-mode application design through a single entrypoint
* structured JSON, CSV, and chart artifact generation
* production-style troubleshooting across ECS, IAM, scheduler, and runtime behavior
* evolution from mock workflows to real-data integration

---

## Project Phases

### Project 2.1 — Platform / Infrastructure Phase

This phase established the cloud execution platform using mock data and proved:

* Docker build and push workflow
* ECS task definition registration
* manual ECS/Fargate execution
* EventBridge Scheduler wiring
* CloudWatch log verification
* scheduled JSON, CSV, and chart artifact generation

### Project 2.2 — Real Data Phase

This phase extended the platform into real-data workflows while preserving mock mode for testing.

Current direction:

* `DATA_SOURCE=mock` for safe testing and development
* `DATA_SOURCE=real` for external market data integration

---

## Workflow Modes

The application supports multiple workflow modes behind a single entrypoint.

### Price Workflow

```bash
python3 -m app.main --mode price
```

This workflow:

* generates a daily price snapshot
* appends historical CSV data
* produces a price history chart

Current real-data implementation:

* `XAG/USD` maps to `SI=F`
* `WTI/USD` maps to `CL=F`
* real mode uses Yahoo Finance
* futures proxies are currently used for silver and crude oil pricing

### Volume Workflow

```bash
python3 -m app.main --mode volume
```

This workflow:

* monitors intraday market activity during a controlled ET window
* collects minute-by-minute volume samples
* generates JSON, CSV, and intraday chart artifacts

Current real-data implementation:

* tracks `QQQ` as the volume symbol
* uses Yahoo Finance intraday data
* filters data to the intended ET monitoring window of `10:08–10:40 ET`

Development override for manual testing:

```bash
python3 -m app.main --mode volume --allow-outside-window
```

## Run the project locally with:

```bash
./bin/run_local_demo.sh
```

This provides a simple local demonstration path outside AWS scheduling.

---

## Sample Charts

### Sample Volume Monitoring Chart

![Sample Volume Chart](images/sample-volume-chart.png)

---

## Core AWS Architecture

The workload runs on:

* **Amazon ECR** for container image storage
* **Amazon ECS Fargate** for task execution
* **Amazon EventBridge Scheduler** for recurring runs
* **Amazon CloudWatch Logs** for runtime logging

---

## Example Artifacts

The project generates structured outputs for both workflows, including:

* JSON snapshots
* historical CSV datasets
* PNG chart artifacts

Typical artifact paths include:

```text
dev/price/daily/YYYY/MM/DD/price_snapshot.json
dev/price/history/price_history.csv
dev/price/charts/price_history.png

dev/volume/daily/YYYY/MM/DD/volume_samples.json
dev/volume/daily/YYYY/MM/DD/volume_samples.csv
dev/volume/charts/YYYY/MM/DD/volume_window.png
```

---

## Local Configuration

Key environment variables include:

* `APP_ENV`
* `DATA_SOURCE`
* `SYMBOLS`
* `VOLUME_SYMBOL`
* `S3_BUCKET`
* `S3_PREFIX`
* `LOG_LEVEL`

Example real-data local configuration:

```bash
export APP_ENV="dev"
export DATA_SOURCE="real"
export SYMBOLS="XAG/USD,WTI/USD"
export VOLUME_SYMBOL="QQQ"
export S3_BUCKET="your-bucket-name"
export S3_PREFIX="market-snapshot-bot/dev"
export LOG_LEVEL="INFO"
```

---

## Why This Project Matters

This project demonstrates more than application code. It shows the surrounding operational discipline required to build, package, deploy, schedule, verify, and troubleshoot a production-style containerized workload on AWS.

It also reflects an important engineering progression: starting with a stable platform baseline, then extending it into real-data behavior without breaking the original system.

