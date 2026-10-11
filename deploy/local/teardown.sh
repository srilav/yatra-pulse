#!/usr/bin/env bash
# Bring down everything Yatra Pulse runs on this machine:
#   - the metrics consumer and any local MCP HTTP server (node processes)
#   - the kind cluster with Knative and the Yatra Pulse services
#   - Apache Pulsar from ops/docker-compose.yml
# Options:
#   --purge     also delete Pulsar's data volume, the built images and local telemetry/eval output
#   --dry-run   show what would be removed without removing it
# Safe to run more than once; anything already gone is skipped.
set -uo pipefail
cd "$(dirname "$0")/../.."

PURGE=0; DRY=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "Unknown option $a (use --purge, --dry-run)"; exit 2 ;;
  esac
done
CLUSTER=${CLUSTER:-yatra-pulse}
run() { if [ "$DRY" = 1 ]; then echo "  would run: $*"; return 1; else "$@"; fi; }
step() { printf '\n▸ %s\n' "$1"; }

step "Local processes"
for pat in "ops/metrics-consumer.js" "mcp-server/http.js"; do
  if pgrep -f "$pat" >/dev/null; then run pkill -f "$pat" && echo "  stopped $pat"; else echo "  $pat not running"; fi
done
pgrep -f "port-forward -n kourier-system" >/dev/null && run pkill -f "port-forward -n kourier-system" && echo "  stopped Kourier port-forward"

step "kind cluster ($CLUSTER)"
if command -v kind >/dev/null && kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  run kind delete cluster --name "$CLUSTER"
else
  echo "  no cluster named $CLUSTER"
fi

step "Apache Pulsar (Docker Compose)"
if docker ps -a --format '{{.Names}}' | grep -qx yatra-pulsar; then
  docker network disconnect kind yatra-pulsar >/dev/null 2>&1 || true
  if [ "$PURGE" = 1 ]; then run docker compose -f ops/docker-compose.yml down -v; else run docker compose -f ops/docker-compose.yml down; fi
else
  echo "  yatra-pulsar not running"
fi

if [ "$PURGE" = 1 ]; then
  step "Images and local output (--purge)"
  for img in $(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^(dev\.local/)?yatra-pulse/'); do run docker rmi "$img" >/dev/null && echo "  removed $img"; done
  for f in ops/telemetry.jsonl eval/results; do [ -e "$f" ] && run rm -rf "$f" && echo "  removed $f"; done
fi

step "Check"
left=0
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" && { echo "  kind cluster still present"; left=1; }
docker ps --format '{{.Names}}' | grep -qx yatra-pulsar && { echo "  yatra-pulsar still running"; left=1; }
pgrep -f "ops/metrics-consumer.js|mcp-server/http.js" >/dev/null && { echo "  node services still running"; left=1; }
if [ "$DRY" = 1 ]; then echo "  dry run: nothing removed"; exit 0; fi
[ "$left" = 0 ] && echo "  all Yatra Pulse services are down"
exit $left
