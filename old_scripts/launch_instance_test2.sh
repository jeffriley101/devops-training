#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

log()   { echo "[INFO]  $(date +'%Y-%m-%d %H:%M:%S') - $*"; }
error() { echo "[ERROR] $(date +'%Y-%m-%d %H:%M:%S') - $*" >&2; }
trap 'error "Script failed at line $LINENO"; exit 1' ERR


DEFAULT_REGION="us-east-1"
DEFAULT_INSTANCE_TYPE="t3.micro"
DEFAULT_SG_NAME="devops-training-sg"
DEFAULT_KEY_NAME="devops-training-key"
DEFAULT_PEM_PATH="$HOME/.ssh/devops-training-key.pem"
DEFAULT_TAG_PREFIX="cli-instance"



# ---- Config (edit if needed) ----
REGION="$(aws configure get region || true)"
KEY_NAME="devops-training-key"
PEM_PATH="$HOME/.ssh/devops-training-key.pem"
SG_NAME="devops-training-sg"
INSTANCE_TYPE="t3.micro"
TAG_PREFIX="cli-instance"
TAG_NAME="${TAG_PREFIX}-$(date +%Y%m%d-%H%M%S)"

# ---- Checks ----
if [[ -z "${REGION}" ]]; then
  error "AWS region not set. Run: aws configure (we standardize on us-east-1)."
  exit 1
fi

if [[ ! -f "${PEM_PATH}" ]]; then
  error "PEM file not found at ${PEM_PATH}"
  exit 1
fi

chmod 600 "${PEM_PATH}" 2>/dev/null || true

# ---- Get latest Ubuntu 22.04 AMI (Jammy) ----
AMI_ID="$(aws ec2 describe-images \
  --owners 099720109477 \
  --filters \
    "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
    "Name=state,Values=available" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" \
  --output text)"

: "${AMI_ID:?AMI_ID lookup failed}"

log "Region: ${REGION}"
log "AMI:    ${AMI_ID}"
log "Key:    ${KEY_NAME}"
log "PEM:    ${PEM_PATH}"

# ---- Get SG ID (create if missing) ----
SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${SG_NAME}" \
  --query "SecurityGroups[0].GroupId" \
  --output text 2>/dev/null || true)"

if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
  log "Security group ${SG_NAME} not found. Creating..."
  VPC_ID="$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)"
  SG_ID="$(aws ec2 create-security-group \
    --group-name "${SG_NAME}" \
    --description "Security group for DevOps training instances" \
    --vpc-id "${VPC_ID}" \
    --query "GroupId" --output text)"

  # Lock SSH to your current IPv4
  MY_IP="$(curl -4 -s ifconfig.me || curl -4 -s https://api.ipify.org)"
  aws ec2 authorize-security-group-ingress --group-id "${SG_ID}" --protocol tcp --port 22 --cidr "${MY_IP}/32"

  # Allow HTTP from anywhere (training)
  aws ec2 authorize-security-group-ingress --group-id "${SG_ID}" --protocol tcp --port 80 --cidr 0.0.0.0/0

  log "Created SG: ${SG_ID}"
else
  log "Using SG: ${SG_ID}"
fi

# ---- Idempotency-lite: reuse existing instance if present ----
EXISTING_INSTANCE_ID="$(aws ec2 describe-instances \
  --filters \
    "Name=tag:Name,Values=${TAG_PREFIX}-*" \
    "Name=instance-state-name,Values=running,pending,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' \
  --output text || true)"

if [[ -n "${EXISTING_INSTANCE_ID}" && "${EXISTING_INSTANCE_ID}" != "None" ]]; then
  log "Found existing instance: ${EXISTING_INSTANCE_ID}"

  STATE="$(aws ec2 describe-instances \
    --instance-ids "${EXISTING_INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text)"

  if [[ "${STATE}" == "stopped" ]]; then
    log "Instance is stopped. Starting..."
    aws ec2 start-instances --instance-ids "${EXISTING_INSTANCE_ID}" >/dev/null
    aws ec2 wait instance-running --instance-ids "${EXISTING_INSTANCE_ID}"
  else
    aws ec2 wait instance-running --instance-ids "${EXISTING_INSTANCE_ID}"
  fi

  INSTANCE_ID="${EXISTING_INSTANCE_ID}"
else
  # ---- Launch instance ----
  log "Launching instance: ${TAG_NAME}"
  INSTANCE_ID="$(aws ec2 run-instances \
    --image-id "${AMI_ID}" \
    --count 1 \
    --instance-type "${INSTANCE_TYPE}" \
    --key-name "${KEY_NAME}" \
    --security-group-ids "${SG_ID}" \
    --associate-public-ip-address \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${TAG_NAME}}]" \
    --query "Instances[0].InstanceId" \
    --output text)"

  : "${INSTANCE_ID:?Failed to retrieve instance ID}"

  log "InstanceId: ${INSTANCE_ID}"
  log "Waiting for running..."
#old but works:  aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

aws ec2 wait instance-running --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"

# Ensure the instance ID is visible to downstream calls
aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" --output json >/dev/null

log "Waiting for instance status checks to pass..."
aws ec2 wait instance-status-ok --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"




 # log "Waiting for instance status checks to pass..."
 # aws ec2 wait instance-status-ok --instance-ids "$INSTANCE_ID"
fi

PUBLIC_IP="$(aws ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text)"

: "${PUBLIC_IP:?Failed to retrieve public IP}"

log "Public IP: ${PUBLIC_IP}"
log "Connecting via SSH..."

ssh -i "$PEM_PATH" \
  -o IdentitiesOnly=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  "ubuntu@$PUBLIC_IP"
