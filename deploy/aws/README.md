# Yatra Pulse on AWS: Knative on EKS behind CloudFront

Everything for AWS lives here, separate from the laptop setup in `deploy/local/`. Both use the same images (`deploy/docker/`) and the same app manifests (`deploy/k8s/`).

```
Traveller ──HTTPS──► CloudFront ──HTTP──► ALB (only CloudFront's IP ranges allowed)
                      │ /        cached          │
                      │ /mcp*    not cached, POST│
                      │ /ops/*   not cached      ▼
                      │                 EKS nodes (private subnets) : NodePort 30080
                      │                       edge gateway (nginx, 2 pods)
                      │                 ┌──────────┼───────────────┐
                      │                 ▼          ▼               ▼
                      │            yatra-web   yatra-mcp      yatra-metrics      ← Knative Services
                      │             0–5 pods   0–10 pods      exactly 1           (Kourier, ClusterIP)
                      │                          │ telemetry      ▲
                      │                          ▼                │
                      │                  Apache Pulsar StatefulSet on gp3 EBS
                      │
   Private subnets reach ECR, GitHub and Docker Hub through one NAT gateway.
```

| AWS service | Used for | Defined in |
|---|---|---|
| VPC, 2 AZs, public + private subnets | Network | `terraform/network.tf` |
| NAT gateway (one, with EIP) | Outbound for private nodes (image pulls, Knative manifests) | `terraform/network.tf` |
| EKS 1.35 + managed node group (2 × t3.large) | Kubernetes for Knative | `terraform/eks.tf` |
| EBS CSI driver (Pod Identity) + gp3 | Pulsar's persistent volume | `terraform/eks.tf`, `k8s/storageclass.yaml` |
| ECR (web, mcp, metrics) | Container images; scan on push; keep last 5 | `terraform/ecr.tf` |
| ALB + target group on NodePort 30080 | Load balancing to the edge gateway | `terraform/edge.tf` |
| CloudFront | HTTPS, edge caching for the board, no caching for `/mcp` and `/ops/` | `terraform/edge.tf` |

Knative Serving 1.23 with Kourier is installed by `deploy.sh`; Kourier is ClusterIP so the ALB is the only public entry point and no Kubernetes-created load balancer can be left behind.

## Run it

```bash
./deploy/aws/deploy.sh --plan       # preview (creates nothing)
./deploy/aws/deploy.sh --yes        # ~25 min: Terraform, Knative, build + push to ECR, app
./deploy/aws/smoke-test.sh          # 10 API checks through CloudFront
./deploy/aws/record-tests.sh        # Selenium key scenarios (video + JSON) + smoke + cluster snapshot
./deploy/aws/teardown.sh --yes      # delete everything, then sweep for leftovers
```

Requirements: AWS credentials with admin rights, Terraform ≥ 1.9, kubectl, Docker, the repo's `.venv` (Selenium).

## Design choices

- **Knative on EKS, not ECS Fargate.** Same manifests as on-prem and laptop; scale-to-zero and revisions come from Knative.
- **Terraform owns the ALB.** Instead of a Kubernetes ingress controller creating load balancers, the ALB and target group are Terraform resources and the node group's Auto Scaling group is attached to them. CloudFront can point at the ALB in the same apply, and `terraform destroy` removes it.
- **CloudFront-only ALB.** The ALB security group allows only CloudFront's origin-facing prefix list; the smoke test checks the ALB can't be reached directly.
- **Edge gateway.** CloudFront and the ALB see one host name, but Knative routes by host. A small nginx deployment maps paths (`/`, `/mcp`, `/ops/`) to each service's cluster-local address.
- **No customer KMS key.** A deleted key waits 7+ days pending deletion; skipping it lets teardown leave a clean account (EKS still encrypts at rest).
- **Teardown order.** Delete the namespace first so the EBS CSI driver deletes Pulsar's volume (not tracked by Terraform), then `terraform destroy`, then sweep for EKS, load balancers, CloudFront, NAT, EIPs, EBS, instances, VPC and ECR.
- **Separate kube context** `yatra-pulse-aws`; scripts always pass `--context`, so they can't touch the local `kind-yatra-pulse` cluster.

## Cost (us-east-1, approximate)

| Item | Per hour |
|---|---|
| EKS control plane | $0.10 |
| 2 × t3.large | $0.17 |
| NAT gateway | $0.045 + data |
| ALB | $0.0225 + LCU |
| Public IPv4 (NAT EIP, ALB) | ~$0.015 |
| EBS (2 × 30 GiB root + 5 GiB gp3) | ~$0.01 |
| CloudFront, ECR | cents |
| **Total** | **≈ $0.36/hour** |

## Verified run

Run on 11 October 2026 in us-east-1 (account verified for everything except CloudFront, see below).

| Check | Result |
|---|---|
| Terraform | VPC, NAT gateway, EKS 1.35 (2 × t3.large), EBS CSI, ECR, ALB, idle-shutdown schedule created |
| Images | web, mcp, metrics built and pushed to ECR |
| Knative Serving 1.23 + Kourier | Installed; yatra-web, yatra-mcp, yatra-metrics Ready |
| Pulsar on gp3 EBS | Running; metrics consumer subscribed |
| Browser scenarios (Selenium, recorded) | **16/16 passed**: `results/key-scenarios.mp4`, `results/selenium.json` |
| API scenarios | **8/8 passed**: `smoke-20261011-232842.txt` |
| Teardown | See `results/teardown-*.txt` |

```
Yatra Pulse AWS smoke test · 2026-10-11T17:58:42Z · http://yatra-pulse-alb-1441277353.us-east-1.elb.amazonaws.com
PASS  Board served at http://yatra-pulse-alb-1441277353.us-east-1.elb.amazonaws.com
PASS  Architecture page served
PASS  Ops health through the edge
PASS  MCP search_board over HTTP(S)
PASS  MCP check_itinerary finds the 17 Oct clash
PASS  MCP check_itinerary: bus too slow on 21 Oct
PASS  Telemetry MCP → Pulsar (EBS) → metrics (3 → 6 tool calls)
PASS  Prometheus metrics through the edge
8 passed, 0 failed
```

**Watch the recording:** https://srilav.github.io/yatra-pulse/deploy/aws/results/key-scenarios.mp4 (captioned, one frame per scenario, all PASS).

### What we hit on AWS and fixed

| Problem | Cause | Fix |
|---|---|---|
| CloudFront `403 AccessDenied: Your account must be verified` | New AWS accounts need AWS Support verification before creating CloudFront distributions | `enable_cloudfront` variable (default on). Run with `ENABLE_CLOUDFRONT=false` until AWS verifies the account; the ALB is then the public entry on HTTP |
| Knative revisions `RevisionMissing` (ECR digest lookup timed out) | Knative's controller resolves tags to digests itself and has no ECR credentials | `registries-skipping-tag-resolving` includes the ECR registry; nodes pull with their instance role |
| Pulsar `AccessDeniedException: /pulsar/data/metadata` | Fresh EBS volume owned by root; Pulsar runs as uid 10000 | `securityContext.fsGroup: 10000` in the shared Pulsar manifest |
| ALB closed to the internet after a targeted apply | `terraform apply -target=…cloudfront` also re-applied its dependency, the ALB security group, with CloudFront on | Re-applied with CloudFront off; don't `-target` CloudFront until the account is verified |
| Browser tests 0/16: page script not running | `index.html` had no `<meta charset>`; nginx sends `text/html` without charset, so Chrome mis-decoded UTF-8 | `<meta charset="utf-8">` added to the page (claude.ai and GitHub Pages had hidden this) |
| Metrics consumer started before Pulsar was healthy and stayed disconnected | Under investigation: the consumer logged no reconnect attempts | Restarted the pod; the readiness probe kept it out of service meanwhile |

