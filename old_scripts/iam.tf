# --- IAM role for SSM ---
#data "aws_iam_policy_document" "ec2_assume_role" {
#  statement {
#    effect = "Allow"
#    actions = ["sts:AssumeRole"]

#    principals {
#      type        = "Service"
#      identifiers = ["ec2.amazonaws.com"]
#    }
#  }
#}

#resource "aws_iam_role" "ssm_role" {
#  name               = "devops-training-ssm-role"
#  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
#}

#resource "aws_iam_role_policy_attachment" "ssm_core" {
#  role       = aws_iam_role.ssm_role.name
#  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
#}

#resource "aws_iam_instance_profile" "ssm_profile" {
#  name = "devops-training-ssm-profile"
#  role = aws_iam_role.ssm_role.name
#}


