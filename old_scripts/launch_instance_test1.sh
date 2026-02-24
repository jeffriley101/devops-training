#!/usr/bin/env bash
set -euo pipefail

# ---- Config (edit if needed) ----
REGION="$(aws configure get region)"
AMI_ID="$(aws ec2 describe-images \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" "Name=state,Values=available" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" \
  --output text)"

KEY_NAME="devops-training-key"
PEM_PATH="$HOME/.ssh/devops-training-key.pem"
SG_NAME="devops-training-sg"
INSTANCE_TYPE="t3.micro"
TAG_NAME="cli-instance-$(date +%Y%m%d-%H%M%S)"

# ---- Checks ----
if [[ -z "${REGION}" ]]; then
  echo "ERROR: AWS region not set. Run: aws configure"
  exit 1
fi

if [[ ! -f "${PEM_PATH}" ]]; then
  echo "ERROR: PEM file not found at ${PEM_PATH}"
  exit 1
fi

chmod 400 "${PEM_PATH}" || true

echo "Region: ${REGION}"
echo "AMI:    ${AMI_ID}"
echo "Key:    ${KEY_NAME}"
echo "PEM:    ${PEM_PATH}"

# ---- Get SG ID (create if missing) ----
SG_ID="$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=${SG_NAME}" \
  --query "SecurityGroups[0].GroupId" \
  --output text 2>/dev/null || true)"

if [[ -z "${SG_ID}" || "${SG_ID}" == "None" ]]; then
  echo "Security group ${SG_NAME} not found. Creating..."
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

  echo "Created SG: ${SG_ID}"
else
  echo "Using SG: ${SG_ID}"
fi

# ---- Launch instance ----
echo "Launching instance..."
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

echo "InstanceId: ${INSTANCE_ID}"
echo "Waiting for running..."
aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

PUBLIC_IP="$(aws ec2 describe-instances \
  --instance-ids "${INSTANCE_ID}" \
  --query "Reservations[0].Instances[0].PublicIpAddress" \
  --output text)"

echo "Public IP: ${PUBLIC_IP}"
echo "SSH: ssh -i ${PEM_PATH} ubuntu@${PUBLIC_IP}"
echo "Connecting..."
ssh -i "${PEM_PATH}" "ubuntu@${PUBLIC_IP}"
