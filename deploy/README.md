# Deploying Yatra Pulse on Kubernetes with Knative

| Where | Folder | Start | Remove |
|---|---|---|---|
| Laptop (kind) | [`local/`](local/) | `./deploy/local/kind-up.sh` | `./deploy/local/teardown.sh` |
| AWS (EKS + CloudFront + ALB + NAT + ECR) | [`aws/`](aws/README.md) | `./deploy/aws/deploy.sh --yes` | `./deploy/aws/teardown.sh --yes` |
| Any other cluster with Knative | [`k8s/`](k8s/) | `kubectl apply -k deploy/k8s` | `kubectl delete -k deploy/k8s` |

Shared by all: images in `docker/`, app manifests in `k8s/`. Each environment keeps its own scripts and test evidence (`local/results/`, `aws/results/`).

Three Knative services plus Apache Pulsar. The same manifests run on a laptop (kind), on-prem (k3s, RKE2, OpenShift, kubeadm) or any cloud Kubernetes (GKE, EKS, AKS).

```
            Kourier / Istio / Contour (Knative ingress)
                │ Host: <service>.yatra-pulse.<your domain>
   ┌────────────┼──────────────────────┬───────────────────────┐
   ▼            ▼                      ▼                       │
yatra-web    yatra-mcp              yatra-metrics               │
0–5 pods     0–10 pods              exactly 1                   │
nginx        MCP over HTTP ──────►  Pulsar consumer ──► /metrics, SLOs, alerts
             │ telemetry            ▲
             ▼                      │
          Apache Pulsar (StatefulSet, or your Pulsar cluster)
          yatra-telemetry · yatra-eval · yatra-alerts · yatra-telemetry-dlq
```

| Path | What |
|---|---|
| `docker/web.Dockerfile` | Board, design guide, architecture and design-discussion pages on nginx-unprivileged (port 8080) |
| `docker/mcp.Dockerfile` | MCP server over Streamable HTTP (`mcp-server/http.js`), telemetry to Pulsar |
| `docker/metrics.Dockerfile` | Metrics consumer: Prometheus `/metrics`, `/healthz`, `/stats`, dashboard |
| `k8s/` | Full install: `app/` (namespace, ConfigMap, Knative Services) + `pulsar/` (StatefulSet) |
| `k8s/monitoring/` | Optional ServiceMonitor and PrometheusRule (Prometheus Operator) |
| `local/kind/` | Laptop profile: `dev.local` images loaded into kind; Pulsar from `ops/docker-compose.yml` |
| `local/kind-up.sh` | Local cluster with Knative + Kourier, images loaded, app deployed (kind profile) |
| `local/smoke-test.sh` | End-to-end check of a running deployment |

## Quick start (local, kind)

```bash
./deploy/local/kind-up.sh       # ~5–8 minutes the first time
./deploy/local/smoke-test.sh    # 9 checks
```

The kind profile (`deploy/local/kind`) uses the same app manifests but loads images as `dev.local/...` (a registry Knative doesn't try to resolve) and connects to the Pulsar from `ops/docker-compose.yml`, joined to the kind network as `yatra-pulsar`. Knative alone needs most of a 2 GB Docker VM, so running Pulsar in the same cluster on a laptop usually fails to schedule (we hit exactly that). On a real cluster, use `kubectl apply -k deploy/k8s`, which includes the Pulsar StatefulSet.

Open the services through Kourier:

```bash
kubectl port-forward -n kourier-system svc/kourier 18080:80
curl -H "Host: yatra-web.yatra-pulse.yatra.local"     http://localhost:18080/
curl -H "Host: yatra-metrics.yatra-pulse.yatra.local" http://localhost:18080/stats
```

Bring everything down (consumer, cluster, Pulsar): `./deploy/local/teardown.sh` (`--dry-run` to preview, `--purge` to also delete Pulsar data, images and local output).

## Any other cluster (on-prem or cloud)

1. **Install Knative Serving** with a networking layer: the [Knative Operator](https://knative.dev/docs/install/operator/knative-with-operators/), OpenShift Serverless, or the YAML in `local/kind-up.sh`.
2. **Push images** to a registry your cluster can pull from:
   ```bash
   REG=registry.example.com/yatra
   for c in web mcp metrics; do
     docker build -f deploy/docker/$c.Dockerfile -t $REG/$c:1.0.0 . && docker push $REG/$c:1.0.0
   done
   cd deploy/k8s && for c in web mcp metrics; do kustomize edit set image yatra-pulse/$c=$REG/$c:1.0.0; done
   ```
3. **Pulsar**: keep the single-node StatefulSet (`k8s/pulsar`) for small installs, or point `PULSAR_WS_URL` in `k8s/app/config.yaml` at your Pulsar cluster's WebSocket endpoint (Apache Pulsar Helm chart, or a managed service) and apply `k8s/app` only.
4. **Domain**: set your domain in Knative's `config-domain` and point DNS at the ingress load balancer. On clouds without DNS, use Knative's sslip.io default domain job.
5. `kubectl apply -k deploy/k8s` (or `deploy/k8s/app` with your own Pulsar) and, with the Prometheus Operator, `kubectl apply -f deploy/k8s/monitoring/`.

| Platform | Notes |
|---|---|
| kind / minikube / k3d | `local/kind-up.sh`; images via `kind load` |
| k3s / RKE2 / kubeadm (on-prem) | Knative Operator; local-path or Longhorn storage for Pulsar |
| OpenShift | OpenShift Serverless provides Knative; images run as non-root already |
| GKE | Knative Serving or Cloud Run for Anthos; standard storage class |
| EKS | Knative + Kourier or Istio; gp3 storage class for Pulsar |
| AKS | Knative + Kourier or Istio; managed-csi storage class for Pulsar |

## Design choices

- **Scale to zero where it's safe.** The web board and MCP server are stateless, so Knative scales them to zero when idle and up on demand (MCP: target 50 concurrent requests per pod, 2-minute scale-down delay to avoid cold starts mid-conversation).
- **Exactly one metrics consumer.** It keeps the rolling SLO window in memory and must stay subscribed, so `min-scale = max-scale = 1`.
- **Stateless MCP over HTTP.** Every request gets a fresh MCP server and transport, so any pod can serve any request.
- **Graceful shutdown.** On SIGTERM the MCP server records `server_stop`, drains telemetry and exits; the consumer closes its subscriptions.
- **Probes.** MCP readiness is `/readyz` (board loaded, Pulsar status), liveness `/healthz`; metrics readiness waits for the Pulsar subscription.
- **Non-root images** (`node` and `nginx` users), small resource requests, memory limits on everything.
- **Config in one ConfigMap** (`yatra-config`): Pulsar endpoint, telemetry mode, SLO window, query recording (off).

## Verified

Run on 11 October 2026 on macOS (Docker VM 1.9 GB, 6 CPUs): kind v0.33.0, Knative Serving and Kourier `knative-v1.23.0`, Apache Pulsar 4.2.4 via the kind profile. Output of `deploy/local/smoke-test.sh`:

```
PASS  Knative services are Ready
PASS  web serves the board
PASS  web serves the architecture page
PASS  MCP readiness reports Pulsar connected
PASS  MCP search_board over HTTP
PASS  MCP check_itinerary finds the 17 Oct clash
PASS  telemetry flowed MCP → Pulsar → metrics (4 → 6 tool calls)
PASS  metrics consumer is healthy
PASS  Prometheus metrics exported
9 passed, 0 failed
```

Scale to zero, observed after 4 idle minutes:

| Service | Pods after the smoke test | Pods after 4 min idle |
|---|---|---|
| yatra-web | 1 | 0 |
| yatra-mcp | 1 | 0 |
| yatra-metrics | 1 | 1 (always on, by design) |

First MCP request after scaling to zero (cold start, pod scheduled and started): 2.2 s. The next request on the warm pod: 224 ms (both measured through a kubectl port-forward to Kourier, which adds overhead).
