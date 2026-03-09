#List CLI Instances
IDS=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=cli-instance-*" \
  --query "Reservations[].Instances[].InstanceId" \
  --output text)

if [[ -z "$IDS" ]]; then
  echo "No matching instances found."
  exit 0
fi

#Confirm I want to terminate them
echo "The following instances will be terminated:"
echo "$IDS"
echo

read -p "Are you sure you want to continue? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
  echo "Aborted."
  exit 0
fi



#Terminate them
aws ec2 terminate-instances --instance-ids $IDS

#Wait until fully terminated
aws ec2 wait instance-terminated --instance-ids $IDS

#List again to confirm
#IDS=$(aws ec2 describe-instances \
#  --filters "Name=tag:Name,Values=cli-instance-*" \
#  --query "Reservations[].Instances[].InstanceId" \
#  --output text)

IDS=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=cli-instance-*" \
  --query "Reservations[].Instances[].InstanceId" \
  --output text)

