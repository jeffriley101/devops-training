#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'


# ---- Header comment ----
# launch_instance.sh
#
# Goal:
#   Ensure a single training EC2 instance exists in the selected region (default us-east-1),
#   with a security group that allows SSH from my current public IP and HTTP from anywhere.
#
# Behavior:
#   - Uses strict mode + structured logging + fail-fast checks
#   - Reuses an existing cli-instance-* if present (starts it if stopped)
#   - Waits for instance to be running + status checks OK
#   - Optionally SSHs in (disabled with --no-ssh)
#
# Usage:
#   ./launch_instance.sh --help
#   ./launch_instance.sh --no-ssh
#   ./launch_instance.sh --region us-east-1 --type t3.micro --no-ssh


log()   { echo "[INFO]  $(date +'%Y-%m-%d %H:%M:%S') - $*"; }
error() { echo "[ERROR] $(date +'%Y-%m-%d %H:%M:%S') - $*" >&2; }
trap 'error "Script failed at line $LINENO"; exit 1' ERR


# ---- Defaults/vars ---
DEFAULT_REGION="us-east-1"
DEFAULT_INSTANCE_TYPE="t3.micro"
DEFAULT_SG_NAME="devops-training-sg"
DEFAULT_KEY_NAME="devops-training-key"
DEFAULT_PEM_PATH="$HOME/.ssh/devops-training-key.pem"
DEFAULT_TAG_PREFIX="cli-instance"

REGION="$DEFAULT_REGION"
KEY_NAME="$DEFAULT_KEY_NAME"
PEM_PATH="$DEFAULT_PEM_PATH"
SG_NAME="$DEFAULT_SG_NAME"
INSTANCE_TYPE="$DEFAULT_INSTANCE_TYPE"
TAG_PREFIX="$DEFAULT_TAG_PREFIX"
DO_SSH=1
PLAN=0


# ---- Parsing block ----
usage() {
  cat <<EOF
Usage: $0 [options]

Options:
  --region REGION         AWS region (default: ${DEFAULT_REGION})
  --type INSTANCE_TYPE    EC2 instance type (default: ${DEFAULT_INSTANCE_TYPE})
  --sg-name NAME          Security group name (default: ${DEFAULT_SG_NAME})
  --key-name NAME         EC2 key pair name (default: ${DEFAULT_KEY_NAME})
  --pem-path PATH         Path to .pem (default: ${DEFAULT_PEM_PATH})
  --name-prefix PREFIX    Tag Name prefix (default: ${DEFAULT_TAG_PREFIX})
  --no-ssh                Don't auto-SSH; just print connection info
  -h, --help              Show help
EOF
}

#For options that take values
require_arg() {
  if [[ -z "${2:-}" ]]; then
    error "Option '$1' requires an argument."
    exit 1
  fi
}

#Helper
plan() { log "[PLAN] $*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)      require_arg "$1" "${2:-}"; REGION="$2"; shift 2 ;;
    --type)        require_arg "$1" "${2:-}"; INSTANCE_TYPE="$2"; shift 2 ;;
    --sg-name)     require_arg "$1" "${2:-}"; SG_NAME="$2"; shift 2 ;;
    --key-name)    require_arg "$1" "${2:-}"; KEY_NAME="$2"; shift 2 ;;
    --pem-path)    require_arg "$1" "${2:-}"; PEM_PATH="$2"; shift 2 ;;
    --name-prefix) require_arg "$1" "${2:-}"; TAG_PREFIX="$2"; shift 2 ;;
    --no-ssh)      DO_SSH=0; shift ;;
    --plan) PLAN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             error "Unknown option: $1"; usage; exit 1 ;;
  esac
done

TAG_NAME="${TAG_PREFIX}-$(date +%Y%m%d-%H%M%S)"

if [[ "$PLAN" -eq 1 ]]; then
  DO_SSH=0
fi

log "Config:"
log "  region=$REGION"
log "  instance_type=$INSTANCE_TYPE"
log "  sg=$SG_NAME"
log "  key=$KEY_NAME"
log "  plan_mode=$PLAN"
log "  ssh_enabled=$DO_SSH"




# ---- Checks ----
if [[ -z "${REGION}" ]]; then
#  error "AWS region not set. Run: aws configure (we standardize on us-east-1)."
  error "AWS region not set. Use --region (default is us-east-1)."
  exit 1
fi

if [[ ! -f "${PEM_PATH}" ]]; then
  error "PEM file not found at ${PEM_PATH}"
  exit 1
fi

chmod 600 "${PEM_PATH}" 2>/dev/null || true

# ---- Get latest Ubuntu 22.04 AMI (Jammy) ----
AMI_ID="$(aws ec2 describe-images --region "$REGION" \
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
SG_ID="$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=${SG_NAME}" \
  --query "SecurityGroups[0].GroupId" \
  --output text 2>/dev/null || true)"

if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
  log "Security group ${SG_NAME} not found. Creating..."
  VPC_ID="$(aws ec2 describe-vpcs --region "$REGION" --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)"

if [[ "$PLAN" -eq 1 ]]; then
  plan "Would create security group: name=$SG_NAME (default VPC)"
  plan "Would authorize ingress: 22/tcp from MY_IP/32"
  plan "Would authorize ingress: 80/tcp from 0.0.0.0/0"
  plan "Would proceed to launch/reuse instance next"
  exit 0
else
  SG_ID="$(aws ec2 create-security-group --region "$REGION" \
    --group-name "${SG_NAME}" \
    --description "Security group for DevOps training instances" \
    --vpc-id "${VPC_ID}" \
    --query "GroupId" --output text)"

  MY_IP="$(curl -4 -s ifconfig.me || curl -4 -s https://api.ipify.org)"

  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "${SG_ID}" --protocol tcp --port 22 --cidr "${MY_IP}/32"

  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "${SG_ID}" --protocol tcp --port 80 --cidr 0.0.0.0/0
fi

  # Lock SSH to your current IPv4
  MY_IP="$(curl -4 -s ifconfig.me || curl -4 -s https://api.ipify.org)"
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "${SG_ID}" \
    --protocol tcp \
    --port 22 \
    --cidr "${MY_IP}/32"

  # Allow HTTP from anywhere (training)
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "${SG_ID}" \
    --protocol tcp \
    --port 80 \
    --cidr 0.0.0.0/0

  log "Created SG: ${SG_ID}"
else
  log "Using SG: ${SG_ID}"
fi

# ---- Idempotency-lite: reuse existing instance if present ----
EXISTING_INSTANCE_ID="$(aws ec2 describe-instances --region "$REGION" \
  --filters \
    "Name=tag:Name,Values=${TAG_PREFIX}-*" \
    "Name=instance-state-name,Values=running,pending,stopped" \
  --query 'Reservations[].Instances[0].InstanceId' \
  --output text || true)"


if [[ -n "${EXISTING_INSTANCE_ID}" && "${EXISTING_INSTANCE_ID}" != "None" ]]; then
  log "Found existing instance: ${EXISTING_INSTANCE_ID}"

  if [[ "$PLAN" -eq 1 ]]; then
    plan "Would reuse existing instance: ${EXISTING_INSTANCE_ID}"
    plan "Would ensure running (start if stopped)"
    plan "Would wait: instance-running"
    plan "Would wait: instance-status-ok"
    plan "Would fetch public IP"
    plan "Would not SSH (plan mode)"
    exit 0
  fi

  STATE="$(aws ec2 describe-instances --region "$REGION" \
    --instance-ids "${EXISTING_INSTANCE_ID}" \
    --query 'Reservations[0].Instances[0].State.Name' \
    --output text)"

  if [[ "${STATE}" == "stopped" ]]; then
    log "Instance is stopped. Starting..."
    aws ec2 start-instances --region "$REGION" --instance-ids "${EXISTING_INSTANCE_ID}" >/dev/null
    aws ec2 wait instance-running --region "$REGION" --instance-ids "${EXISTING_INSTANCE_ID}"
  else
    aws ec2 wait instance-running --region "$REGION" --instance-ids "${EXISTING_INSTANCE_ID}"
  fi

  INSTANCE_ID="${EXISTING_INSTANCE_ID}"
else


# ---- Launch instance ----
  log "Launching instance: ${TAG_NAME}"

  if [[ "$PLAN" -eq 1 ]]; then
    plan "Would run-instances: ami=$AMI_ID type=$INSTANCE_TYPE key=$KEY_NAME sg=$SG_ID tag=$TAG_NAME"
    plan "Would wait: instance-running"
    plan "Would wait: instance-status-ok"
    plan "Would fetch public IP"
    exit 0
  fi
  
  INSTANCE_ID="$(aws ec2 run-instances --region "$REGION" \
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

aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"

# Ensure the instance ID is visible to downstream calls
aws ec2 describe-instances --region "$REGION" --instance-ids "$INSTANCE_ID" --output json >/dev/null

log "Waiting for instance status checks to pass..."
aws ec2 wait instance-status-ok --region "$REGION" --instance-ids "$INSTANCE_ID"

fi

PUBLIC_IP="$(aws ec2 --region "$REGION" describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text)"

: "${PUBLIC_IP:?Failed to retrieve public IP}"

log "Public IP: ${PUBLIC_IP}"

if [[ "$DO_SSH" -eq 1 ]]; then
  log "Connecting via SSH..."
  ssh -i "$PEM_PATH" \
    -o IdentitiesOnly=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    "ubuntu@$PUBLIC_IP"
else
  log "--no-ssh set; not connecting."
  log "SSH: ssh -i \"$PEM_PATH\" ubuntu@$PUBLIC_IP"
fi
