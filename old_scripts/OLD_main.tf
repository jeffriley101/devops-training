terraform {
  backend "s3" {
    bucket         = "devops-training-tfstate-31229db4"
    key            = "training/single-instance/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "devops-training-terraform-locks"
    encrypt        = true
  }
}

terraform {
  required_version = ">= 1.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

# Use the default VPC in the configured region
data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "cli_sg" {
  name        = "cli-instance-sg"
  description = "Converged SG for CLI instance"
  vpc_id      = data.aws_vpc.default.id

  # SSH from your public IP only
  ingress {
    description = "SSH from my IP"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.my_ip]
  }

  # HTTP from anywhere
  ingress {
    description = "HTTP from anywhere"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Egress allow-all
  egress {
    description = "Allow all egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "cli-instance-sg"
  }
}

# --- Find default networking (so we can place the instance) ---

data "aws_subnets" "default_vpc_subnets" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# --- Pick an AMI automatically (Ubuntu 22.04 LTS, Canonical) ---

data "aws_ami" "ubuntu_2204" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# --- The instance itself ---

resource "aws_instance" "cli_instance" {
  ami                    = data.aws_ami.ubuntu_2204.id
  instance_type          = var.instance_type
  subnet_id              = data.aws_subnets.default_vpc_subnets.ids[0]
  key_name               = var.key_name
  vpc_security_group_ids = [aws_security_group.cli_sg.id]

  iam_instance_profile = aws_iam_instance_profile.ssm_profile.name

  user_data_replace_on_change = true

  user_data = <<-EOF
   #!/bin/bash
    set -e

    apt-get update -y
    apt-get install -y nginx

    systemctl enable nginx
    systemctl start nginx

    echo "Hi geph! nginx bootstrapped v2 at $(date -Is) on $(hostname)" > /var/www/html/index.html

    # --- SSM agent (Ubuntu often uses snap) ---
    snap install amazon-ssm-agent --classic || true
    systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || true

  EOF

  tags = {
    Name = "cli-instance-terraform"
  }
}
