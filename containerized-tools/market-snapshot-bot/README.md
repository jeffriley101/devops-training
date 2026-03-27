# Market Snapshot Bot

Market Snapshot Bot is a containerized AWS market-monitoring project built to demonstrate Python automation, scheduled cloud execution, artifact pipelines, and production-style troubleshooting.

Built as part of my DevOps and cloud engineering transition, this project shows that I can take a Python workload from local development into a repeatable AWS scheduled system with container packaging, task execution, artifact generation, and runtime validation.

It began as a platform and infrastructure exercise using mock data to prove the deployment path end to end. It then evolved into a real-data workflow project while deliberately preserving mock mode for safe development, testing, and rollback.

---

## Portfolio Summary

This project demonstrates hands-on work with:

- Python
- Docker
- AWS ECS Fargate
- Amazon EventBridge Scheduler
- Amazon ECR
- Amazon CloudWatch Logs
- Amazon S3
- GitHub Actions
- Terraform-aware deployment workflow thinking

The project is not just a script that prints market data. It is a scheduled cloud workload that runs in AWS, generates structured artifacts, preserves history, and requires real troubleshooting across code, configuration, task definitions, and scheduler behavior.

---

## What This Project Does

Market Snapshot Bot supports multiple workflows behind a single application entrypoint.

For each run, the application can:

1. load runtime configuration from environment variables
2. execute the selected workflow mode
3. collect price or volume data
4. generate structured JSON and CSV artifacts
5. preserve historical records for later review
6. build PNG chart artifacts
7. write outputs locally and/or publish them to S3
8. emit logs that support runtime troubleshooting in CloudWatch

This gives the project both current-state output and historical trend visibility.

---

## Why This Project Matters

The real value of this project is not just that it produces charts and CSV files.

The value is that it demonstrates the full lifecycle of a scheduled cloud workload:

- local development and testing
- containerization with Docker
- ECS task execution on Fargate
- EventBridge schedule orchestration
- artifact generation and retention
- environment-aware behavior
- troubleshooting when deployed runtime behavior does not match expectations
- extending a stable baseline without discarding it

That is much closer to real DevOps and cloud operations work than a one-off local script.

---

## Project Evolution

### Phase 1: Platform / Infrastructure Baseline

The first phase proved the operational foundation using mock data.

That baseline included:

- Docker image build workflow
- Amazon ECR image publishing
- ECS task definition registration and revision handling
- manual ECS/Fargate execution
- EventBridge Scheduler target wiring
- CloudWatch log verification
- scheduled JSON, CSV, and chart artifact generation

Even before real market data was introduced, this phase already demonstrated practical DevOps value because the platform itself was functioning end to end.

### Phase 2: Real-Data Extension

The second phase extended the platform into real-data workflows while preserving mock mode for safety and testability.

That phase included:

- keeping `DATA_SOURCE=mock` for controlled development and demos
- adding `DATA_SOURCE=real` for external market data integration
- moving price and volume workflows beyond purely mock-driven behavior
- validating runtime behavior through artifacts and logs instead of assumptions
- troubleshooting issues involving environment variables, task definitions, scheduler targets, and run-context behavior

This phase mattered because it showed controlled change rather than reckless rewriting.

---

## Workflow Modes

The application supports multiple workflow modes behind a single entrypoint.

### Price Workflow

```bash
python3 -m app.main --mode price
```

The price workflow:

- generates a price snapshot
- preserves historical price records
- appends a historical CSV dataset
- produces price history chart artifacts
- supports day-over-day trend visibility

Current real-data behavior:

- `XAG/USD` maps to `SI=F`
- `WTI/USD` maps to `CL=F`
- real mode uses Yahoo Finance data
- futures proxies are currently used for silver and crude oil pricing
- price history persistence allows charting and historical review across runs

### Volume Workflow

```bash
python3 -m app.main --mode volume
```

The volume workflow:

- monitors intraday market activity during a controlled time window
- collects volume samples during the defined market window
- generates JSON, CSV, and chart artifacts
- supports history accumulation for later comparison and troubleshooting

Current real-data behavior:

- tracks `QQQ` as the volume symbol
- uses Yahoo Finance intraday data
- filters to the intended ET monitoring window of `10:08–10:40 ET`

Development override for manual testing:

```bash
python3 -m app.main --mode volume --allow-outside-window
```

---

## Repository Structure

```text
.
├── app/
│   ├── artifacts/          # JSON/CSV history builders
│   ├── charts/             # price and volume chart generation
│   ├── clients/            # market data client logic
│   ├── models/             # schemas and data structures
│   ├── storage/            # S3 key/path helpers
│   ├── workflows/          # price and volume workflow implementations
│   ├── config.py           # runtime configuration
│   └── main.py             # single application entrypoint
├── bin/run_local_demo.sh   # simple local demo runner
├── dev/                    # local artifact output examples
├── images/                 # sample images for documentation
├── infra/
│   ├── ecs/                # ECS task definition assets
│   └── scheduler/          # EventBridge Scheduler target/policy files
├── samples/                # sample market data payloads
├── tests/                  # test scaffolding
├── Dockerfile
├── requirements.txt
└── README.md
```

---

## Run the Project Locally

```bash
./bin/run_local_demo.sh
```

This provides a simple local demonstration path outside AWS scheduling.

You can also run each workflow directly:

```bash
python3 -m app.main --mode price
python3 -m app.main --mode volume
python3 -m app.main --mode volume --allow-outside-window
```

---

## Core AWS Architecture

The project uses a practical AWS scheduled-workload pattern:

- **Amazon ECR** stores versioned container images
- **Amazon ECS Fargate** runs the workload as scheduled tasks
- **Amazon EventBridge Scheduler** triggers recurring executions
- **Amazon CloudWatch Logs** provides runtime visibility and troubleshooting data
- **Amazon S3** stores structured output artifacts when configured for cloud publishing

This architecture is intentionally simple enough to explain clearly while still reflecting real operational patterns.

---

## Artifacts and Output Design

The project generates structured outputs across both workflows, including:

- JSON snapshots
- daily CSV outputs
- historical CSV datasets
- PNG chart artifacts

Representative paths include:

```text
dev/price/daily/YYYY/MM/DD/price_snapshot.json
dev/price/history/price_history.csv
dev/price/charts/price_history_all_runs.png
dev/price/charts/price_history_official_daily.png

dev/volume/daily/YYYY/MM/DD/volume_samples.json
dev/volume/daily/YYYY/MM/DD/volume_samples.csv
dev/volume/charts/YYYY/MM/DD/volume_window.png
dev/volume/history/volume_history.csv
```

This matters because the system is not just executing code. It is producing outputs that can be inspected, validated, and compared over time.

---

## Sample Chart

### Sample Volume Monitoring Chart

![Sample Volume Chart](images/sample-volume-chart.png)

---

## Configuration

Key environment variables include:

- `APP_ENV`
- `DATA_SOURCE`
- `SYMBOLS`
- `VOLUME_SYMBOL`
- `S3_BUCKET`
- `S3_PREFIX`
- `LOG_LEVEL`

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

## What I Learned Building This Project

This project taught me lessons that are directly relevant to DevOps and cloud operations work.

### 1. A working platform is a real milestone

A project does not need live production data on day one to have professional value. Proving container build, ECS execution, schedule orchestration, logging, and artifact flow was already a meaningful engineering milestone.

### 2. Environment handling can make or break a deployment

Small differences between local runs, scheduled runs, and task definitions can create real failures. I learned to treat environment variables, runtime context, and scheduler targets as first-class parts of the system.

### 3. Logs and artifacts are the truth

Instead of assuming the code behaved correctly, I learned to verify outcomes through CloudWatch logs, generated files, charts, and S3 paths. That mindset matters in production work.

### 4. Safe evolution beats reckless rewriting

Rather than throwing away mock mode, I preserved it and layered in real-data behavior carefully. That is much closer to how real systems should be changed in professional environments.

### 5. Infrastructure and application behavior are tightly connected

Many issues were not just code bugs. They involved task definitions, scheduler wiring, cloud configuration, run windows, and output destinations. I learned to debug across the full stack instead of staying in one lane.

### 6. Persistence makes the workflow more meaningful

Adding price-history persistence strengthened the project by turning it from a simple snapshot generator into a more useful historical-monitoring workflow with ongoing trend visibility.

---

## Resume-Level Summary

Built a containerized AWS market-monitoring project using Python, Docker, ECS Fargate, EventBridge Scheduler, CloudWatch, S3, and artifact-based workflows to collect market data, preserve price history, generate charts, and troubleshoot scheduled runtime behavior across code and cloud configuration.

---

## Status

Current state: **working, evolving, and portfolio-ready**
