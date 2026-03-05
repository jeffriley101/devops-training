#!/usr/bin/env bash
set -euo pipefail

BUCKET="$(terraform -chdir=environments/ecs-scheduled-task output -raw artifacts_bucket_name 2>/dev/null || true)"

if [[ -z "${BUCKET}" ]]; then
  echo "[ERROR] Could not read artifacts_bucket_name from Terraform outputs."
  echo "Run from repo root with Terraform state available."
  exit 1
fi

echo "[INFO] Latest artifact:"
aws s3 ls "s3://${BUCKET}/env-inspector/training/latest.json"

echo
echo "[INFO] Last 5 minutes of logs:"
aws logs tail /ecs/env-inspector --since 5m
