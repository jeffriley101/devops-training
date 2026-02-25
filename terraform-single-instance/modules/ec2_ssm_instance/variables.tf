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


