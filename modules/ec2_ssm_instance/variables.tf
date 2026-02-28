variable "region" {
  type        = string
  description = "AWS region"
}

variable "instance_type" {
  type        = string
  description = "EC2 instance type"
}

variable "my_ip" {
  type        = string
  description = "Your public IP in CIDR notation (e.g., 1.2.3.4/32)"
  default     = null
}

variable "environment" {
  description = "Environment name (dev, stage, etc.)"
  type        = string
}

variable "instance_profile_name" {
  description = "Existing IAM instance profile name to attach (owned by shared-iam stack)"
  type        = string
}

#variable "create_iam" {
#  description = "Whether to create IAM role/profile (set true for one env only)"
#  type        = bool
#  default     = true
#}

#variable "existing_instance_profile_name" {
#  description = "If create_iam=false, the name of an existing instance profile to attach"
#  type        = string
#  default     = null
#}

