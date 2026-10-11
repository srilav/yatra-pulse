#!/usr/bin/env bash
# Record the key scenarios against the AWS deployment (through CloudFront) and keep the evidence in
# deploy/aws/results/: Selenium video + JSON, API smoke test, and a cluster snapshot.
set -uo pipefail
cd "$(dirname "$0")/../.."
CF=$(terraform -chdir=deploy/aws/terraform output -raw cloudfront_url)
R=deploy/aws/results
mkdir -p "$R"
echo "▸ Browser scenarios (Selenium) against $CF/"
.venv/bin/python tests/run_dashboard.py --url "$CF/" --hold 3 --pause 1 --record "$R/key-scenarios.mp4" --report "$R/selenium.json"
sel=$?
echo; echo "▸ API scenarios (MCP over HTTPS, telemetry through Pulsar)"
CF="$CF" ./deploy/aws/smoke-test.sh
api=$?
echo; echo "▸ Cluster snapshot"
{
  echo "# $(date -u +%FT%TZ) · context yatra-pulse-aws"
  kubectl --context yatra-pulse-aws get nodes -o wide
  echo; kubectl --context yatra-pulse-aws get ksvc,pods,pvc -n yatra-pulse
  echo; aws elbv2 describe-target-health --region "$(terraform -chdir=deploy/aws/terraform output -raw region)" \
        --target-group-arn "$(aws elbv2 describe-target-groups --names yatra-pulse-gw --query 'TargetGroups[0].TargetGroupArn' --output text)" \
        --query 'TargetHealthDescriptions[].[Target.Id,TargetHealth.State]' --output text
} | tee "$R/cluster-snapshot.txt"
echo; echo "Evidence in $R/"; ls -1 "$R"
[ $sel -eq 0 ] && [ $api -eq 0 ]
