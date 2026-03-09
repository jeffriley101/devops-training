# Market Snapshot Bot

Scheduled financial market data automation platform built on a reusable AWS container automation foundation.



## ⚡ Quick Local Demo (30 seconds)

Run the full workflow locally:

```bash
./bin/run_local_demo.sh
```

This script will:

    Prepare local directories

    Set required environment variables

    Generate market snapshot artifacts

    Print live silver and oil pricing summary


No AWS setup required.



## Purpose

This project demonstrates platform engineering skills applied to financial data workflows:

- Scheduled container automation (ECS Fargate + EventBridge)
- External API integration for commodity market data
- Data normalization into stable internal schemas
- Versioned artifact publishing to S3
- Production-style platform reuse across domains



## Short example output

People love seeing results without running anything.

Add:

```markdown
### Example Output

Market Snapshot [success]
Snapshot ID: 20260309T023112Z
Collected At: 2026-03-09T02:31:12Z
Environment: dev
Source: twelve_data

    XAG/USD | Silver Spot / US Dollar | price=31.42 USD | status=success

    WTI/USD | WTI Crude Oil / US Dollar | price=67.85 USD | status=success
```



## Scope (v1)

Instruments:
- XAG/USD (Silver)
- WTI/USD (Crude Oil)

Cadence:
- Scheduled batch snapshot

Artifacts:
- Raw source payload
- Normalized snapshot (internal schema)
- Human-readable summary



## Architecture Overview

EventBridge Schedule  
→ ECS Fargate Task  
→ Containerized Snapshot Engine  
→ External Market Data API  
→ Artifact Generation  
→ S3 Versioned Storage  
→ CloudWatch Logs



## Normalized Data Contract

Each run produces a MarketSnapshot object containing:

- snapshot_id
- collected_at_utc
- environment
- source
- overall_status
- instruments[]

Each instrument includes:

- symbol
- display_name
- asset_class
- price
- currency
- as_of_utc
- source
- source_symbol
- status

Status values:
- success
- partial_failure
- failure



## Artifact Layout (S3)


