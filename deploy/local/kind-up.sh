#!/usr/bin/env bash
# Local Kubernetes for Yatra Pulse: kind cluster + Knative Serving + Kourier, images loaded, app deployed.
# Uses the deploy/local/kind profile: the same app manifests as any cluster, with images loaded into kind and
# Pulsar from ops/docker-compose.yml (a laptop rarely has memory for Knative and Pulsar in one cluster).
# On a real cluster use `kubectl apply -k deploy/k8s` (includes in-cluster Pulsar) instead.
set -euo pipefail
cd "$(dirname "$0")/../.."
CLUSTER=${CLUSTER:-yatra-pulse}
KNATIVE=${KNATIVE:-knative-v1.23.0}

kind get clusters | grep -qx "$CLUSTER" || kind create cluster --name "$CLUSTER" --wait 120s
kubectl config use-context "kind-$CLUSTER" >/dev/null

echo "Installing Knative Serving $KNATIVE with Kourier"
kubectl apply -f "https://github.com/knative/serving/releases/download/$KNATIVE/serving-crds.yaml" >/dev/null
kubectl apply -f "https://github.com/knative/serving/releases/download/$KNATIVE/serving-core.yaml" >/dev/null
kubectl apply -f "https://github.com/knative-extensions/net-kourier/releases/download/$KNATIVE/kourier.yaml" >/dev/null
kubectl patch configmap/config-network -n knative-serving --type merge -p '{"data":{"ingress-class":"kourier.ingress.networking.knative.dev"}}'
kubectl patch configmap/config-domain -n knative-serving --type merge -p '{"data":{"yatra.local":""}}'
kubectl wait --for=condition=Available deployment --all -n knative-serving --timeout=600s
kubectl wait --for=condition=Available deployment --all -n kourier-system --timeout=300s

echo "Building and loading images"
for c in web mcp metrics; do
  docker build -q -f "deploy/docker/$c.Dockerfile" -t "yatra-pulse/$c:1.0.0" -t "dev.local/yatra-pulse/$c:1.0.0" . >/dev/null
  kind load docker-image "dev.local/yatra-pulse/$c:1.0.0" --name "$CLUSTER"
done

echo "Pulsar: Docker Compose instance joined to the kind network as yatra-pulsar"
docker compose -f ops/docker-compose.yml up -d
until [ "$(docker inspect -f '{{.State.Health.Status}}' yatra-pulsar)" = healthy ]; do sleep 5; done
docker network connect kind yatra-pulsar 2>/dev/null || true

echo "Deploying (deploy/local/kind profile)"
kubectl apply -k deploy/local/kind
kubectl wait ksvc --all -n yatra-pulse --for=condition=Ready --timeout=600s
kubectl get ksvc -n yatra-pulse
