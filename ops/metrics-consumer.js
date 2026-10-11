#!/usr/bin/env node
// Yatra Pulse metrics consumer.
// Subscribes to the Pulsar topics yatra-telemetry and yatra-eval, turns events into metrics, checks
// service level objectives (SLOs) over a rolling window, and raises alerts on yatra-alerts.
//
//   node ops/metrics-consumer.js            # http://localhost:9464
//   GET /metrics    Prometheus text format (scrape this)
//   GET /healthz    200 when subscribed to Pulsar, 503 otherwise
//   GET /stats      JSON summary used by the dashboard
//   GET /           live dashboard
import http from "node:http";
import { readFileSync } from "node:fs";
import { consume, Producer, TOPICS, PULSAR_WS_URL } from "./pulsar.js";

const PORT = Number(process.env.METRICS_PORT || process.env.PORT || 9464);
const WINDOW_MS = Number(process.env.SLO_WINDOW_MS || 15 * 60 * 1000);
const SUBSCRIPTION = process.env.METRICS_SUBSCRIPTION || "yatra-metrics";
// Failover: one active consumer per subscription, others on standby. Keeps counts whole when two
// replicas overlap (a Knative rollout, a restart) instead of Shared splitting messages between them.
const SUBSCRIPTION_TYPE = process.env.METRICS_SUBSCRIPTION_TYPE || "Failover";
const BUCKETS = [5, 10, 25, 50, 100, 250, 500, 1000, 2500];

// SLOs: the promise we make about the MCP server, checked over the rolling window.
export const SLOS = [
  { id: "availability", label: "Tool calls succeed", target: "≥ 99%", min: 20, value: (w) => w.calls ? 1 - w.errors / w.calls : 1, ok: (v) => v >= 0.99, fmt: pct },
  { id: "latency_p95", label: "p95 tool latency", target: "≤ 250 ms", min: 20, value: (w) => quantile(w.latencies, 0.95), ok: (v) => v <= 250, fmt: (v) => `${v.toFixed(1)} ms` },
  { id: "search_recall", label: "Searches that find something", target: "≥ 90%", min: 10, value: (w) => w.searches ? 1 - w.zeroResults / w.searches : 1, ok: (v) => v >= 0.9, fmt: pct },
  { id: "eval_passing", label: "Latest evaluator run passes", target: "pass", min: 0, value: () => (latestEval ? (latestEval.passed ? 1 : 0) : 1), ok: (v) => v === 1, fmt: (v) => (v ? "pass" : "fail") },
];

const log = (level, msg, fields = {}) => console.log(JSON.stringify({ ts: new Date().toISOString(), level, service: "yatra-metrics", msg, ...fields }));

// ---- state ----
const counters = new Map();          // name|labels -> value
const histos = new Map();            // tool -> { buckets[], sum, count }
const recent = [];                   // tool_call events in the window
let latestEval = null;
const sloState = new Map();          // id -> "ok" | "breach"
const startedAt = Date.now();

const key = (name, labels = {}) => name + JSON.stringify(Object.entries(labels).sort());
function inc(name, labels = {}, by = 1) { const k = key(name, labels); const c = counters.get(k) || { name, labels, value: 0 }; c.value += by; counters.set(k, c); }
function observe(tool, ms) {
  const h = histos.get(tool) || { buckets: BUCKETS.map(() => 0), sum: 0, count: 0 };
  BUCKETS.forEach((b, i) => { if (ms <= b) h.buckets[i]++; });
  h.sum += ms; h.count++;
  histos.set(tool, h);
}

function handleTelemetry(ev) {
  if (!ev || ev.v !== 1 || !ev.kind) throw new Error("not a telemetry event");
  inc("yatra_events_total", { kind: ev.kind, service: ev.service || "unknown" });
  if (ev.kind === "server_start") inc("yatra_server_starts_total", { service: ev.service });
  if (ev.kind !== "tool_call") return;
  const tool = String(ev.tool || "unknown");
  inc("yatra_tool_calls_total", { tool, outcome: ev.ok ? "ok" : "error" });
  observe(tool, Number(ev.latency_ms) || 0);
  if (tool === "search_board") {
    inc("yatra_search_total");
    if (ev.zero_result) inc("yatra_search_zero_results_total");
  }
  if (tool === "check_itinerary") {
    inc("yatra_itinerary_checks_total");
    inc("yatra_itinerary_problems_total", {}, Number(ev.problems) || 0);
    inc("yatra_itinerary_clashes_total", {}, Number(ev.clashes) || 0);
    if (ev.problems > 0) inc("yatra_itinerary_checks_with_problems_total");
  }
  recent.push({ t: Date.parse(ev.ts) || Date.now(), tool, ok: !!ev.ok, ms: Number(ev.latency_ms) || 0, zero: !!ev.zero_result });
}

function handleEval(ev) {
  if (!ev || ev.kind !== "eval_run") throw new Error("not an eval event");
  latestEval = ev;
  inc("yatra_eval_runs_total", { result: ev.passed ? "pass" : "fail" });
}

// ---- windowed SLO evaluation ----
function windowStats() {
  const cutoff = Date.now() - WINDOW_MS;
  while (recent.length && recent[0].t < cutoff) recent.shift();
  const searches = recent.filter((r) => r.tool === "search_board");
  return {
    calls: recent.length,
    errors: recent.filter((r) => !r.ok).length,
    latencies: recent.map((r) => r.ms),
    searches: searches.length,
    zeroResults: searches.filter((r) => r.zero).length,
  };
}

let alertProducer;
function checkSlos() {
  const w = windowStats();
  return SLOS.map((s) => {
    const enough = s.id === "eval_passing" ? !!latestEval : (s.id === "search_recall" ? w.searches : w.calls) >= s.min;
    const v = s.value(w);
    const status = !enough ? "no_data" : s.ok(v) ? "ok" : "breach";
    const prev = sloState.get(s.id) || "ok";
    if (status === "breach" && prev !== "breach") raise(s, v, "firing");
    if (status === "ok" && prev === "breach") raise(s, v, "resolved");
    if (status !== "no_data") sloState.set(s.id, status);
    return { id: s.id, label: s.label, target: s.target, value: enough ? s.fmt(v) : "not enough data", raw: v, status };
  });
}
function raise(slo, value, state) {
  const alert = { v: 1, kind: "alert", ts: new Date().toISOString(), slo: slo.id, label: slo.label, target: slo.target, value: slo.fmt(value), state, window_min: WINDOW_MS / 60000 };
  log(state === "firing" ? "error" : "info", `SLO ${state}: ${slo.label}`, alert);
  inc("yatra_alerts_total", { slo: slo.id, state });
  alertProducer?.send(alert, { slo: slo.id, state });
}

// ---- outputs ----
function prometheus() {
  const lines = [];
  const typed = new Set();
  const help = {
    yatra_events_total: "Telemetry events received, by kind and service",
    yatra_tool_calls_total: "MCP tool calls, by tool and outcome",
    yatra_search_total: "search_board calls",
    yatra_search_zero_results_total: "search_board calls that returned nothing",
    yatra_itinerary_checks_total: "check_itinerary calls",
    yatra_itinerary_checks_with_problems_total: "check_itinerary calls that found at least one problem",
    yatra_itinerary_problems_total: "Problems found by trip check (clashes, not enough time, too far)",
    yatra_itinerary_clashes_total: "Clashes found by trip check",
    yatra_eval_runs_total: "Evaluator runs, by result",
    yatra_alerts_total: "SLO alert transitions",
    yatra_server_starts_total: "MCP server starts",
  };
  const lbl = (labels) => { const e = Object.entries(labels); return e.length ? `{${e.map(([k, v]) => `${k}="${String(v).replace(/"/g, '\\"')}"`).join(",")}}` : ""; };
  for (const c of counters.values()) {
    if (!typed.has(c.name)) { lines.push(`# HELP ${c.name} ${help[c.name] || c.name}`, `# TYPE ${c.name} counter`); typed.add(c.name); }
    lines.push(`${c.name}${lbl(c.labels)} ${c.value}`);
  }
  lines.push("# HELP yatra_tool_latency_ms MCP tool latency in milliseconds", "# TYPE yatra_tool_latency_ms histogram");
  for (const [tool, h] of histos) {
    BUCKETS.forEach((b, i) => lines.push(`yatra_tool_latency_ms_bucket{tool="${tool}",le="${b}"} ${h.buckets[i]}`));
    lines.push(`yatra_tool_latency_ms_bucket{tool="${tool}",le="+Inf"} ${h.count}`, `yatra_tool_latency_ms_sum{tool="${tool}"} ${h.sum.toFixed(2)}`, `yatra_tool_latency_ms_count{tool="${tool}"} ${h.count}`);
  }
  if (latestEval) {
    lines.push("# HELP yatra_eval_metric Latest evaluator score by metric", "# TYPE yatra_eval_metric gauge");
    for (const [m, v] of Object.entries(latestEval.metrics || {})) lines.push(`yatra_eval_metric{metric="${m}"} ${v}`);
    lines.push("# TYPE yatra_eval_passed gauge", `yatra_eval_passed ${latestEval.passed ? 1 : 0}`);
  }
  lines.push("# TYPE yatra_slo_ok gauge");
  for (const s of checkSlos()) lines.push(`yatra_slo_ok{slo="${s.id}"} ${s.status === "breach" ? 0 : 1}`);
  lines.push("# TYPE yatra_pulsar_connected gauge", `yatra_pulsar_connected ${telemetrySub.state.connected ? 1 : 0}`);
  lines.push("# TYPE yatra_consumer_messages_total counter", `yatra_consumer_messages_total{result="acked"} ${telemetrySub.state.acked}`, `yatra_consumer_messages_total{result="poison"} ${telemetrySub.state.poison}`, `yatra_consumer_messages_total{result="nacked"} ${telemetrySub.state.nacked}`);
  return lines.join("\n") + "\n";
}

function stats() {
  const w = windowStats();
  const byTool = {};
  for (const c of counters.values()) if (c.name === "yatra_tool_calls_total") {
    const t = (byTool[c.labels.tool] ||= { ok: 0, error: 0 });
    t[c.labels.outcome] += c.value;
  }
  for (const [tool, h] of histos) Object.assign(byTool[tool] ||= { ok: 0, error: 0 }, { avg_ms: +(h.sum / h.count).toFixed(1), p95_bucket_ms: BUCKETS.find((b, i) => h.buckets[i] >= 0.95 * h.count) ?? ">2500" });
  const val = (name) => [...counters.values()].filter((c) => c.name === name).reduce((n, c) => n + c.value, 0);
  return {
    pulsar: { url: PULSAR_WS_URL, connected: telemetrySub.state.connected, received: telemetrySub.state.received, poison: telemetrySub.state.poison, last_message_age_s: telemetrySub.state.lastMessageAt ? Math.round((Date.now() - telemetrySub.state.lastMessageAt) / 1000) : null },
    uptime_s: Math.round((Date.now() - startedAt) / 1000),
    window_min: WINDOW_MS / 60000,
    window: { calls: w.calls, errors: w.errors, p50_ms: +quantile(w.latencies, 0.5).toFixed(1), p95_ms: +quantile(w.latencies, 0.95).toFixed(1), searches: w.searches, zero_results: w.zeroResults },
    totals: { tool_calls: val("yatra_tool_calls_total"), searches: val("yatra_search_total"), zero_results: val("yatra_search_zero_results_total"), itinerary_checks: val("yatra_itinerary_checks_total"), checks_with_problems: val("yatra_itinerary_checks_with_problems_total"), clashes: val("yatra_itinerary_clashes_total"), alerts: val("yatra_alerts_total") },
    tools: byTool,
    slos: checkSlos(),
    eval: latestEval && { ts: latestEval.ts, passed: latestEval.passed, metrics: latestEval.metrics, failures: latestEval.failures, git: latestEval.git },
  };
}

// ---- wiring ----
const dlq = new Producer(TOPICS.dlq);
alertProducer = new Producer(TOPICS.alerts);
const telemetrySub = consume(TOPICS.telemetry, SUBSCRIPTION, handleTelemetry, {
  subscriptionType: SUBSCRIPTION_TYPE,
  onState: (s) => log(s === "connected" ? "info" : "warn", `telemetry subscription ${s}`, { topic: TOPICS.telemetry }),
  onPoison: (raw, meta) => { log("warn", "unparseable telemetry sent to DLQ", { messageId: meta.messageId }); dlq.send({ raw: raw.slice(0, 2000), messageId: meta.messageId, reason: "not JSON" }); },
});
const evalSub = consume(TOPICS.eval, SUBSCRIPTION, handleEval, { subscriptionType: SUBSCRIPTION_TYPE, onState: (s) => log("info", `eval subscription ${s}`, { topic: TOPICS.eval }) });
setInterval(checkSlos, 30000).unref();

const DASHBOARD = new URL("./dashboard.html", import.meta.url);
const server = http.createServer((req, res) => {
  const send = (code, type, body) => { res.writeHead(code, { "content-type": type, "cache-control": "no-store" }); res.end(body); };
  if (req.url === "/metrics") return send(200, "text/plain; version=0.0.4", prometheus());
  if (req.url === "/stats") return send(200, "application/json", JSON.stringify(stats(), null, 2));
  if (req.url === "/healthz") {
    const ok = telemetrySub.state.connected;
    return send(ok ? 200 : 503, "application/json", JSON.stringify({ ok, pulsar: ok ? "connected" : "disconnected", uptime_s: Math.round((Date.now() - startedAt) / 1000) }));
  }
  if (req.url === "/" || req.url === "/dashboard") return send(200, "text/html; charset=utf-8", readFileSync(DASHBOARD));
  send(404, "text/plain", "not found\n");
});
server.listen(PORT, () => log("info", "metrics consumer listening", { port: PORT, pulsar: PULSAR_WS_URL, window_min: WINDOW_MS / 60000 }));

const shutdown = async () => {
  log("info", "shutting down");
  telemetrySub.close(); evalSub.close();
  await Promise.all([dlq.close(), alertProducer.close()]);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);

function quantile(values, q) {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))];
}
function pct(v) { return `${(v * 100).toFixed(1)}%`; }
