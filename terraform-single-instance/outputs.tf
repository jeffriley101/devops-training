output "instance_id" {
  value = aws_instance.cli_instance.id
}

output "public_ip" {
  value = aws_instance.cli_instance.public_ip
}

output "ssh_command" {
  value = "ssh -i <your_key.pem> ubuntu@${aws_instance.cli_instance.public_ip}"
}
