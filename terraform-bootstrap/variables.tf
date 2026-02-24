variable "region" {
  type        = string
  description = "AWS region for Terraform backend resources"
  default     = "us-east-1"
}

variable "bucket_name_prefix" {
  type        = string
  description = "Prefix for the globally-unique S3 bucket name (suffix will be added automatically)"
  default     = "devops-training-tfstate"
}

variable "lock_table_name" {
  type        = string
  description = "DynamoDB table name for state locking"
  default     = "devops-training-terraform-locks"
}


