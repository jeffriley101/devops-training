#!/usr/bin/env bash
set -euo pipefail

CLUSTER="env-inspector-cluster"
TASK_DEF="env-inspector"

SUBNETS=$(aws ec2 describe-subnets \
  --filters Name=vpc-id,Values=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text) \
  --query "Subnets[*].SubnetId" \
  --output text | tr '\t' ',')

SG=$(aws ec2 describe-security-groups \
  --filters Name=group-name,Values=env-inspector-task-sg \
  --query "SecurityGroups[0].GroupId" \
  --output text)

aws ecs run-task \
  --cluster "$CLUSTER" \
  --launch-type FARGATE \
  --task-definition "$TASK_DEF" \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNETS],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --region us-east-1
