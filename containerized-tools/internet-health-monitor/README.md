# Internet Health Monitor — Scheduled Reliability & Service Monitoring Platform

Internet Health Monitor is a containerized reliability engineering platform that performs scheduled service health checks, generates structured monitoring artifacts, and produces historical latency trend visualizations.

The system demonstrates production-style monitoring automation using AWS container orchestration and infrastructure-as-code.

---

## 🎯 Purpose

This project showcases:

- Service availability monitoring
- Latency measurement and classification
- Scheduled container workloads
- Historical reliability artifact generation
- Operator-facing reporting
- Platform reuse across monitoring domains

It is designed as a portfolio-grade example of DevOps, Platform Engineering, and Site Reliability Engineering practices.

---

## 🧠 What This Platform Does

On a schedule, the system:

1. Executes HTTP health checks against multiple external services
2. Measures latency and validates expected status codes
3. Classifies service state as:
   - **Healthy**
   - **Degraded**
   - **Unhealthy**
4. Generates operator-readable and machine-readable artifacts
5. Stores artifacts in Amazon S3
6. Builds historical latency trend charts from prior runs

---

## 🌐 Monitored Targets

Example monitored services:

- Google
- TimeAndDate
- Ground News
- Weather.gov

Targets are configurable via YAML.

---

## 🏗 Architecture

**Execution Platform**
- AWS ECS Fargate scheduled tasks
- Amazon EventBridge Scheduler
- AWS CloudWatch Logs

**Storage & Artifacts**
- Amazon S3 (latest + historical artifacts)
- Structured JSON monitoring results
- Human-readable text reports
- Historical latency trend charts (PNG)

**Infrastructure as Code**
- Terraform modular infrastructure
- IAM least-privilege roles
- Multi-environment deployment support

**Container & CI/CD**
- Docker containerized monitoring workload
- GitHub Actions build pipelines
- Amazon ECR image registry
- Immutable image tagging

---

## 📦 Artifact Outputs

Each monitoring run produces:

### Latest Artifacts

internet-health-monitor/dev/latest-results.json
internet-health-monitor/dev/latest-report.txt
internet-health-monitor/dev/charts/latest-latency-trend.png


### Historical Artifacts

internet-health-monitor/dev/YYYY/MM/DD/results-<RUN_ID>.json
internet-health-monitor/dev/YYYY/MM/DD/report-<RUN_ID>.txt
internet-health-monitor/dev/charts/YYYY/MM/DD/latency-trend-<RUN_ID>.png


---

## 📊 Monitoring Outputs

### Health Report
- Target count
- Healthy / degraded / unhealthy totals
- Latency measurements
- Failure classification
- Execution metadata

### JSON Results
- Structured monitoring schema
- Per-target performance data
- Machine-readable status records

### Latency Trend Charts
- Historical latency over time
- One line per monitored service
- Built from prior run artifacts

---

## ⚙️ Operator Commands

### View Latest Health Report
```bash
aws s3 cp s3://<bucket>/internet-health-monitor/dev/latest-report.txt -
View Latest JSON Results
aws s3 cp s3://<bucket>/internet-health-monitor/dev/latest-results.json - | jq
```

Open Latest Latency Chart
aws s3 cp s3://<bucket>/internet-health-monitor/dev/charts/latest-latency-trend.png /tmp/chart.png
xdg-open /tmp/chart.png




🚀 Skills Demonstrated
Reliability Engineering

Service health monitoring

Latency tracking

Failure classification

Historical trend analysis

DevOps & Platform Engineering

Containerized workloads

Scheduled cloud execution

Infrastructure automation

Artifact pipelines

Observability workflows

Cloud Architecture

ECS/Fargate orchestration

Event-driven scheduling

IAM role design

S3 artifact lifecycle management

CloudWatch operational logging

🔮 Future Enhancements

Degraded state SLO thresholds

Failure anomaly detection

Scheduled chart-generation task

Multi-chart reporting dashboards

Alerting integrations
