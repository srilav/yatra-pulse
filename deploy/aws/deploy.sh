#!/usr/bin/env bash
# Deploy Yatra Pulse to AWS: Terraform (VPC, NAT, EKS, ECR, ALB, CloudFront) → Knative → images → app.
# Creates billable resources. Requires --yes. Tear down with deploy/aws/teardown.sh.
#   ./deploy/aws/deploy.sh --yes          full deploy
#   ./deploy/aws/deploy.sh --plan         terraform plan only (nothing created)
# ENABLE_CLOUDFRONT=false skips CloudFront (accounts not yet verified for it); the ALB is then the public entry.
set -euo pipefail
cd "$(dirname "$0")/../.."
TF=deploy/aws/terraform
CTX=yatra-pulse-aws
KNATIVE=${KNATIVE:-knative-v1.23.0}
export TF_VAR_enable_cloudfront=${ENABLE_CLOUDFRONT:-true}
TAG=${TAG:-1.0.0}
log() { printf '\n▸ %s\n' "$*"; }
k() { kubectl --context "$CTX" "$@"; }

case "${1:-}" in
  --plan) terraform -chdir=$TF init -input=false >/dev/null && terraform -chdir=$TF plan -input=false; exit 0 ;;
  --yes) ;;
  *) echo "This creates billable AWS resources (EKS, NAT gateway, ALB, EC2, CloudFront). Re-run with --yes, or --plan to preview."; exit 2 ;;
esac

log "Terraform: VPC, NAT gateway, EKS, ECR, ALB, CloudFront"
terraform -chdir=$TF init -input=false >/dev/null
terraform -chdir=$TF apply -input=false -auto-approve
out() { terraform -chdir=$TF output -raw "$1"; }
REGION=$(out region); CLUSTER=$(out cluster_name); REG=$(out ecr_registry); CF=$(out public_url)

log "kubeconfig context $CTX (kept separate from the local kind context)"
aws eks update-kubeconfig --region "$REGION" --name "$CLUSTER" --alias "$CTX" >/dev/null
k get nodes -o wide | sed 's/^/  /'

log "Knative Serving $KNATIVE with Kourier (ClusterIP: the ALB and edge gateway are the only entry)"
k apply -f "https://github.com/knative/serving/releases/download/$KNATIVE/serving-crds.yaml" >/dev/null
k apply -f "https://github.com/knative/serving/releases/download/$KNATIVE/serving-core.yaml" >/dev/null
curl -sL "https://github.com/knative-extensions/net-kourier/releases/download/$KNATIVE/kourier.yaml" \
  | sed 's/type: LoadBalancer/type: ClusterIP/' | k apply -f - >/dev/null
k patch configmap/config-network -n knative-serving --type merge -p '{"data":{"ingress-class":"kourier.ingress.networking.knative.dev"}}' >/dev/null
# Nodes pull from ECR with their instance role; Knative's controller has no ECR credentials, so skip
# its tag-to-digest lookup for this registry (it otherwise times out and revisions never start).
k patch configmap/config-deployment -n knative-serving --type merge -p "{\"data\":{\"registries-skipping-tag-resolving\":\"kind.local,ko.local,dev.local,$REG\"}}" >/dev/null
k wait --for=condition=Available deployment --all -n knative-serving --timeout=600s >/dev/null
k wait --for=condition=Available deployment --all -n kourier-system --timeout=300s >/dev/null

log "Build and push images to ECR ($REG)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REG" >/dev/null
for c in web mcp metrics; do
  docker build -q --platform linux/amd64 -f "deploy/docker/$c.Dockerfile" -t "$REG/yatra-pulse/$c:$TAG" . >/dev/null
  docker push -q "$REG/yatra-pulse/$c:$TAG" >/dev/null
  echo "  pushed $REG/yatra-pulse/$c:$TAG"
done

log "Deploy app, Pulsar and edge gateway (deploy/aws/k8s)"
k kustomize deploy/aws/k8s | sed "s#ECR_REGISTRY#$REG#g; s#:1.0.0#:$TAG#g" | k apply -f - >/dev/null
k rollout status statefulset/pulsar -n yatra-pulse --timeout=900s
k wait ksvc --all -n yatra-pulse --for=condition=Ready --timeout=600s
k rollout restart deployment/yatra-gateway -n yatra-pulse >/dev/null   # resolve the ksvc names now that they exist
k rollout status deployment/yatra-gateway -n yatra-pulse --timeout=300s

log "Waiting for ALB targets${TF_VAR_enable_cloudfront:+ and CloudFront}"
TG=$(aws elbv2 describe-target-groups --region "$REGION" --names yatra-pulse-gw --query 'TargetGroups[0].TargetGroupArn' --output text)
for i in $(seq 1 60); do
  healthy=$(aws elbv2 describe-target-health --region "$REGION" --target-group-arn "$TG" --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text)
  [ "$healthy" -ge 1 ] && break; sleep 10
done
echo "  healthy ALB targets: $healthy"
[ -n "$(out cloudfront_distribution_id)" ] && aws cloudfront wait distribution-deployed --id "$(out cloudfront_distribution_id)"
for i in $(seq 1 30); do curl -sf -o /dev/null "$CF/" && break; sleep 10; done

log "Deployed"
k get ksvc -n yatra-pulse
echo
echo "  Board:     $CF/"
echo "  MCP:       $CF/mcp"
echo "  Ops:       $CF/ops/"
echo "  Next:      ./deploy/aws/smoke-test.sh && ./deploy/aws/record-tests.sh"
echo "  Remove:    ./deploy/aws/teardown.sh --yes"
