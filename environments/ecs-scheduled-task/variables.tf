variable "env_name" {
  type        = string
  description = "Environment name for S3 key partitioning (e.g., training, dev, stage)"
  default     = "training"
}
