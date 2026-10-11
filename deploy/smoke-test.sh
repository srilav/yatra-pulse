#!/usr/bin/env bash
# End-to-end check of a Knative deployment: web page, MCP tool call over HTTP, telemetry arriving in
# Pulsar and counted by the metrics consumer. Uses a port-forward to Kourier, so it works on any cluster.
set -euo pipefail
NS=yatra-pulse
DOMAIN=${DOMAIN:-yatra.local}
PORT=${PORT:-18080}

kubectl port-forward -n kourier-system svc/kourier "$PORT:80" >/dev/null 2>&1 &
PF=$!
trap 'kill $PF 2>/dev/null' EXIT
sleep 3
req() { curl -s --max-time 60 -H "Host: $1.$NS.$DOMAIN" "http://localhost:$PORT$2" "${@:3}"; }
pass=0; fail=0
check() { if eval "$2"; then echo "PASS  $1"; pass=$((pass+1)); else echo "FAIL  $1"; fail=$((fail+1)); fi; }

check "Knative services are Ready" '[ "$(kubectl get ksvc -n $NS -o jsonpath="{range .items[*]}{.status.conditions[?(@.type==\"Ready\")].status}{end}")" = "TrueTrueTrue" ]'
check "web serves the board" 'req yatra-web /index.html | grep "<title>Yatra Pulse</title>" >/dev/null'
check "web serves the architecture page" 'req yatra-web /architecture.html | grep "Yatra Pulse Architecture" >/dev/null'
# After a scale-from-zero the pod answers before its telemetry connection opens (by design, readiness
# doesn't depend on Pulsar), so give it a few seconds.
for i in $(seq 1 15); do req yatra-mcp /readyz | grep "\"pulsar\":\"connected\"" >/dev/null && break; sleep 1; done
check "MCP readiness reports Pulsar connected" 'req yatra-mcp /readyz | grep "\"pulsar\":\"connected\"" >/dev/null'
BEFORE=$(req yatra-metrics /stats | python3 -c "import json,sys; print(json.load(sys.stdin)['totals']['tool_calls'])")
check "MCP search_board over HTTP" 'req yatra-mcp /mcp -X POST -H "content-type: application/json" -H "accept: application/json, text/event-stream" -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"search_board\",\"arguments\":{\"query\":\"Sid Sriram\",\"k\":1}}}" | grep "ev:15:Sid Sriram live" >/dev/null'
check "MCP check_itinerary finds the 17 Oct clash" 'req yatra-mcp /mcp -X POST -H "content-type: application/json" -H "accept: application/json, text/event-stream" -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"check_itinerary\",\"arguments\":{\"stops\":[{\"key\":\"ev:17:Jasmine Sandlas closing night\",\"day\":17},{\"key\":\"ev:17:Drone show\",\"day\":17}]}}}" | grep "Clash" >/dev/null'
for i in $(seq 1 15); do
  AFTER=$(req yatra-metrics /stats | python3 -c "import json,sys; print(json.load(sys.stdin)['totals']['tool_calls'])")
  [ "$AFTER" -ge $((BEFORE+2)) ] && break
  sleep 1
done
check "telemetry flowed MCP → Pulsar → metrics ($BEFORE → $AFTER tool calls)" '[ "$AFTER" -ge $((BEFORE+2)) ]'
check "metrics consumer is healthy" 'req yatra-metrics /healthz | grep "\"ok\":true" >/dev/null'
check "Prometheus metrics exported" 'req yatra-metrics /metrics | grep "yatra_tool_calls_total" >/dev/null'
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
