terraform {
  backend "s3" {
    bucket         = "devops-training-tfstate-31229db4"
    key            = "dev/terraform.tfstate"
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

module "cli" {
  source = "../../modules/ec2_ssm_instance"

  environment           = "dev"
  region                = var.region
  instance_type         = var.instance_type
  my_ip                 = var.my_ip
  instance_profile_name = "devops-training-ssm-profile"

  #  create_iam = true

}



