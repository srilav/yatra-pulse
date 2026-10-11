#!/usr/bin/env bash
# Stop or start the AWS worker nodes by hand (the same call the idle schedule makes).
#   ./deploy/aws/nodes.sh stop      scale to 0: app goes offline, EC2 billing stops
#   ./deploy/aws/nodes.sh start     scale back up and wait until the app answers
#   ./deploy/aws/nodes.sh status
# Run `start` before `terraform apply`, which would otherwise restore min_size underneath stopped nodes.
set -euo pipefail
cd "$(dirname "$0")/../.."
TF=deploy/aws/terraform
out() { terraform -chdir=$TF output -raw "$1"; }
REGION=$(out region); CLUSTER=$(out cluster_name); NG=$(out node_group_name)
N=$(terraform -chdir=$TF console <<<'var.node_count' 2>/dev/null || echo 2)
size() { aws eks update-nodegroup-config --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
           --scaling-config "minSize=$1,maxSize=$((N+1)),desiredSize=$2" --query 'update.id' --output text; }
status() { aws eks describe-nodegroup --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
           --query 'nodegroup.{status:status,min:scalingConfig.minSize,desired:scalingConfig.desiredSize,max:scalingConfig.maxSize}' --output table; }
case "${1:-status}" in
  stop)  echo "Scaling $NG to 0 (update $(size 0 0))"; aws eks wait nodegroup-active --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG"; status ;;
  start) echo "Scaling $NG to $N (update $(size "$N" "$N"))"; aws eks wait nodegroup-active --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG"
         kubectl --context yatra-pulse-aws wait --for=condition=Ready nodes --all --timeout=600s
         kubectl --context yatra-pulse-aws rollout status statefulset/pulsar -n yatra-pulse --timeout=600s
         CF=$(out public_url); for i in $(seq 1 60); do curl -sf -o /dev/null "$CF/ops/healthz" && break; sleep 10; done
         status; echo "App answering at $CF/" ;;
  status) status; aws scheduler list-schedules --region "$REGION" --name-prefix yatra-pulse-nodes --query 'Schedules[].[Name,State]' --output text ;;
  *) echo "Use stop, start or status"; exit 2 ;;
esac
