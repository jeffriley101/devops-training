output "instance_id" {
  value = aws_instance.cli_instance.id
}

output "public_ip" {
  value = aws_instance.cli_instance.public_ip
}

output "security_group_id" {
  value = aws_security_group.cli_sg.id
}
