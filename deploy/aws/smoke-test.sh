#!/usr/bin/env bash
# End-to-end checks against the public AWS deployment: CloudFront (if enabled) → ALB → gateway → Knative.
# Writes the result to deploy/aws/results/smoke-<timestamp>.txt.
set -uo pipefail
cd "$(dirname "$0")/../.."
CF=${CF:-$(terraform -chdir=deploy/aws/terraform output -raw public_url)}
HAS_CF=$(terraform -chdir=deploy/aws/terraform output -raw cloudfront_distribution_id 2>/dev/null)
OUT=deploy/aws/results/smoke-$(date +%Y%m%d-%H%M%S).txt
mkdir -p deploy/aws/results
pass=0; fail=0
check() { if eval "$2" >/dev/null 2>&1; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }
mcp() { curl -s --max-time 60 -X POST "$CF/mcp" -H "content-type: application/json" -H "accept: application/json, text/event-stream" -d "$1"; }
tools() { curl -s --max-time 30 "$CF/ops/stats" | python3 -c "import json,sys; print(json.load(sys.stdin)['totals']['tool_calls'])"; }
{
echo "Yatra Pulse AWS smoke test · $(date -u +%FT%TZ) · $CF"
check "Board served at $CF" 'curl -sf "$CF/" | grep "<title>Yatra Pulse</title>"'
if [ -n "$HAS_CF" ]; then
  check "HTTP redirects to HTTPS" '[ "$(curl -s -o /dev/null -w "%{http_code}" "${CF/https:/http:}/")" = 301 ]'
fi
check "Architecture page served" 'curl -sf "$CF/architecture.html" | grep "Yatra Pulse Architecture"'
[ -n "$HAS_CF" ] && check "ALB is not reachable directly (CloudFront only)" '! curl -s --max-time 8 -o /dev/null "http://$(terraform -chdir=deploy/aws/terraform output -raw alb_dns_name)/"'
check "Ops health through the edge" 'curl -sf "$CF/ops/healthz" | grep "\"ok\":true"'
BEFORE=$(tools)
check "MCP search_board over HTTP(S)" 'mcp "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"search_board\",\"arguments\":{\"query\":\"Sid Sriram\",\"k\":1}}}" | grep "ev:15:Sid Sriram live"'
check "MCP check_itinerary finds the 17 Oct clash" 'mcp "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"check_itinerary\",\"arguments\":{\"stops\":[{\"key\":\"ev:17:Jasmine Sandlas closing night\",\"day\":17},{\"key\":\"ev:17:Drone show\",\"day\":17}]}}}" | grep Clash'
check "MCP check_itinerary: bus too slow on 21 Oct" 'mcp "{\"jsonrpc\":\"2.0\",\"id\":3,\"method\":\"tools/call\",\"params\":{\"name\":\"check_itinerary\",\"arguments\":{\"mode\":\"bus\",\"stops\":[{\"key\":\"ev:21:Jamboo Savari\",\"day\":21},{\"key\":\"ev:21:Torchlight parade\",\"day\":21}]}}}" | grep "Not enough time"'
for i in $(seq 1 20); do AFTER=$(tools); [ "${AFTER:-0}" -ge $((BEFORE+3)) ] && break; sleep 2; done
check "Telemetry MCP → Pulsar (EBS) → metrics ($BEFORE → $AFTER tool calls)" '[ "${AFTER:-0}" -ge $((BEFORE+3)) ]'
check "Prometheus metrics through the edge" 'curl -sf "$CF/ops/metrics" | grep yatra_tool_calls_total'
echo "$pass passed, $fail failed"
} | tee "$OUT"
echo "Saved $OUT"
[ "$fail" -eq 0 ]
