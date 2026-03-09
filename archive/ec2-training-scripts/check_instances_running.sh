#Check instances running

aws configure set region us-east-1

aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" \
  --output text
  
aws configure set region us-east-2

aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].InstanceId" \
  --output text

#Leave in east-1 region  
aws configure set region us-east-1

#Confirm east-1
aws configure get region
