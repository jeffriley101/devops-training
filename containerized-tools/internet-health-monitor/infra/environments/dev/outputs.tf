output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.bucket
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.internet_health_monitor.name
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "schedule_rule_name" {
  value = aws_cloudwatch_event_rule.schedule.name
}
