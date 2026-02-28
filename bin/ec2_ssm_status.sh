#!/usr/bin/env bash
set -euo pipefail

REGION="${1:-us-east-1}"

{
  # Header
  echo "Id Platform IP Status SG Ping LastPing"

  # Get instance IDs
  INSTANCE_IDS=$(aws ec2 describe-instances \
    --region "$REGION" \
    --query 'Reservations[].Instances[].InstanceId' \
    --output text)

  for ID in $INSTANCE_IDS; do
    EC2_LINE=$(aws ec2 describe-instances \
      --region "$REGION" \
      --instance-ids "$ID" \
      --query 'Reservations[0].Instances[0].{
        Id:InstanceId,
        IP:PrivateIpAddress,
        Status:State.Name,
        SG:SecurityGroups[0].GroupId
      }' \
      --output text)

    SSM_LINE=$(aws ssm describe-instance-information \
      --region "$REGION" \
      --query "InstanceInformationList[?InstanceId=='$ID'].{Ping:PingStatus,LastPing:LastPingDateTime}" \
      --output text)

    if [[ -z "${SSM_LINE:-}" ]]; then
      SSM_LINE="N/A N/A"
    fi

    echo "$EC2_LINE $SSM_LINE"
  done
} | column -t
