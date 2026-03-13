variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "env_name" {
  type    = string
  default = "dev"
}

variable "image_uri" {
  type        = string
  description = "Full ECR image URI including tag"
}

variable "schedule_expression" {
  type        = string
  description = "EventBridge schedule expression"
  default     = "rate(15 minutes)"
}
