# --- IAM role for SSM ---
data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ssm_role" {
  name               = "devops-training-ssm-role"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.ssm_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm_profile" {
  name = "devops-training-ssm-profile"
  role = aws_iam_role.ssm_role.name
}

# Use the default VPC in the configured region
data "aws_vpc" "default" {
  default = true
}

resource "aws_security_group" "cli_sg" {
  name                   = "cli-instance-sg"
  description            = "Converged SG for CLI instance"
  vpc_id                 = data.aws_vpc.default.id
  revoke_rules_on_delete = true

  # Egress allow-all
  egress {
    description = "Allow all egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "cli-instance-sg"
    Project     = "devops-training"
    ManagedBy   = "terraform"
    Environment = "training"
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
    Name        = "cli-instance-terraform"
    Project     = "devops-training"
    ManagedBy   = "terraform"
    Environment = "training"
  }
}

