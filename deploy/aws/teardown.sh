#!/usr/bin/env bash
# Remove everything deploy/aws/deploy.sh created, in an order that leaves nothing billable behind:
#   1. Kubernetes resources that create AWS resources outside Terraform (Pulsar's EBS volume)
#   2. terraform destroy (CloudFront, ALB, EKS, node group, NAT gateway + EIP, VPC, ECR, IAM)
#   3. a sweep for leftovers, written to deploy/aws/results/teardown-<timestamp>.txt
# Requires --yes. Never touches the local kind cluster.
set -uo pipefail
cd "$(dirname "$0")/../.."
TF=deploy/aws/terraform
CTX=yatra-pulse-aws
[ "${1:-}" = "--yes" ] || { echo "Deletes the Yatra Pulse AWS deployment. Re-run with --yes."; exit 2; }
REGION=$(terraform -chdir=$TF output -raw region 2>/dev/null || echo "${AWS_REGION:-us-east-1}")
NAME=yatra-pulse
R=deploy/aws/results; mkdir -p "$R"; OUT="$R/teardown-$(date +%Y%m%d-%H%M%S).txt"
log() { printf '\n▸ %s\n' "$*"; }
{
echo "Yatra Pulse AWS teardown · $(date -u +%FT%TZ) · $REGION"

if kubectl config get-contexts -o name 2>/dev/null | grep -qx "$CTX" && kubectl --context "$CTX" get ns yatra-pulse >/dev/null 2>&1; then
  log "Delete the app namespace (releases Pulsar's EBS volume)"
  kubectl --context "$CTX" delete namespace yatra-pulse --timeout=600s
  for i in $(seq 1 60); do
    n=$(kubectl --context "$CTX" get pv -o name 2>/dev/null | wc -l | tr -d ' ')
    [ "$n" = 0 ] && break; sleep 5
  done
  echo "  persistent volumes left: ${n:-0}"
fi

log "terraform destroy"
terraform -chdir=$TF init -input=false >/dev/null
terraform -chdir=$TF destroy -input=false -auto-approve
tf=$?

log "Leftover sweep"
left=0
chk() { local what=$1 val=$2; if [ -n "$val" ] && [ "$val" != "None" ]; then echo "  LEFT  $what: $val"; left=1; else echo "  gone  $what"; fi; }
chk "EKS cluster"        "$(aws eks list-clusters --region $REGION --query "clusters[?@=='$NAME']" --output text)"
chk "Load balancers"     "$(aws elbv2 describe-load-balancers --region $REGION --query "LoadBalancers[?starts_with(LoadBalancerName,'$NAME')].LoadBalancerName" --output text)"
chk "Classic ELBs (k8s)" "$(aws elb describe-load-balancers --region $REGION --query "LoadBalancerDescriptions[].LoadBalancerName" --output text | tr '\t' '\n' | while read -r lb; do [ -n "$lb" ] && aws elb describe-tags --region $REGION --load-balancer-names "$lb" --query "TagDescriptions[?Tags[?Key=='kubernetes.io/cluster/$NAME']].LoadBalancerName" --output text; done)"
chk "CloudFront"         "$(aws cloudfront list-distributions --query "DistributionList.Items[?Comment=='Yatra Pulse'].Id" --output text)"
chk "NAT gateways"       "$(aws ec2 describe-nat-gateways --region $REGION --filter Name=tag:Project,Values=$NAME Name=state,Values=pending,available,deleting --query 'NatGateways[].NatGatewayId' --output text)"
chk "Elastic IPs"        "$(aws ec2 describe-addresses --region $REGION --filters Name=tag:Project,Values=$NAME --query 'Addresses[].AllocationId' --output text)"
chk "EBS volumes (k8s)"  "$(aws ec2 describe-volumes --region $REGION --filters Name=tag-key,Values=kubernetes.io/cluster/$NAME --query 'Volumes[].VolumeId' --output text)"
chk "EC2 instances"      "$(aws ec2 describe-instances --region $REGION --filters Name=tag:eks:cluster-name,Values=$NAME Name=instance-state-name,Values=pending,running,stopping,stopped --query 'Reservations[].Instances[].InstanceId' --output text)"
chk "VPC"                "$(aws ec2 describe-vpcs --region $REGION --filters Name=tag:Project,Values=$NAME --query 'Vpcs[].VpcId' --output text)"
chk "ECR repositories"   "$(aws ecr describe-repositories --region $REGION --query "repositories[?starts_with(repositoryName,'$NAME/')].repositoryName" --output text)"

kubectl config get-contexts -o name 2>/dev/null | grep -qx "$CTX" && kubectl config delete-context "$CTX" >/dev/null && echo "  removed kubeconfig context $CTX"
if [ $tf -eq 0 ] && [ $left -eq 0 ]; then echo "TEARDOWN COMPLETE: no Yatra Pulse resources left in $REGION"; else echo "TEARDOWN INCOMPLETE: see LEFT lines above"; fi
} 2>&1 | tee "$OUT"
echo "Saved $OUT"
grep -q "TEARDOWN COMPLETE" "$OUT"
