#output "instance_id" {
#  value = aws_instance.cli_instance.id
#}

#output "public_ip" {
#  value = aws_instance.cli_instance.public_ip
#}

#output "ssh_command" {
#  value = "ssh -i <your_key.pem> ubuntu@${aws_instance.cli_instance.public_ip}"
#}


output "instance_id" {
  value = module.cli.instance_id
}

output "public_ip" {
  value = module.cli.public_ip
}

output "security_group_id" {
  value = module.cli.security_group_id
}
