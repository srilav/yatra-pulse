#!/usr/bin/env node
// Yatra Pulse evaluator.
// Scores the parts of the board that make decisions for travellers, against labelled datasets:
//   1. Board search (RAG): hit@1, recall@3, MRR@5 over eval/datasets/retrieval.json
//   2. Trip check: accuracy over eval/datasets/tripcheck.json
//   3. Plan evaluator: grounding, invented stops, clashes, wrong-day stops for AI-drafted plans
//      (eval/datasets/plans.json), and whether its verdicts match the labels
//   4. Search latency: p95 over 2,000 queries
// Fails (exit 1) when any score is below eval/thresholds.json. Publishes the run to the Pulsar
// topic yatra-eval (skipped with a note if Pulsar isn't reachable, unless EVAL_REQUIRE_PULSAR=1).
//
//   node eval/run.js [--json] [--no-pulsar]
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { execSync } from "node:child_process";
import { loadBoard } from "../mcp-server/board.js";
import { Producer, TOPICS } from "../ops/pulsar.js";

const here = (p) => new URL(p, import.meta.url);
const load = (p) => JSON.parse(readFileSync(here(p), "utf8"));
const args = new Set(process.argv.slice(2));
const B = loadBoard();
const thresholds = load("./thresholds.json");

// ---------- 1. retrieval ----------
function evalRetrieval() {
  const { cases } = load("./datasets/retrieval.json");
  let hit1 = 0, rec3 = 0, mrr = 0;
  const misses = [];
  for (const c of cases) {
    const ranked = B.searchBoard(c.q, { k: 5 }).filter((h) => h.score > 0).map((h) => h.key);
    const rank = ranked.findIndex((k) => c.expect.includes(k));
    if (rank === 0) hit1++;
    if (rank >= 0 && rank < 3) rec3++;
    if (rank >= 0) mrr += 1 / (rank + 1);
    if (rank !== 0) misses.push({ q: c.q, expected: c.expect[0], got: ranked.slice(0, 3), rank: rank < 0 ? null : rank + 1 });
  }
  const n = cases.length;
  return { metrics: { retrieval_hit_at_1: hit1 / n, retrieval_recall_at_3: rec3 / n, retrieval_mrr_at_5: mrr / n }, cases: n, misses };
}

// ---------- 2. trip check ----------
function runTripCheck(stops, mode) {
  const entries = stops.map((s) => {
    const base = B.catalogItem(s.key.split("@")[0]);
    return base && { ...base, key: base.key.startsWith("ev:") ? base.key : `${base.key}@${s.day}`, day: s.day, time: s.time ?? base.time };
  }).filter(Boolean);
  const days = [...new Set(entries.map((e) => e.day))];
  return days.flatMap((d) => { const r = B.evaluateDay(entries.filter((e) => e.day === d), mode); return [...r.legs, ...r.notes]; });
}
function evalTripCheck() {
  const { cases } = load("./datasets/tripcheck.json");
  const failures = [];
  for (const c of cases) {
    const out = runTripCheck(c.stops, c.mode || "car");
    const problems = [];
    for (const e of c.expect || []) if (!out.some((o) => o.status === e.status && new RegExp(e.match).test(o.text))) problems.push(`expected ${e.status} /${e.match}/`);
    for (const f of c.forbid || []) { const hit = out.find((o) => o.status === f); if (hit) problems.push(`unexpected ${f}: ${hit.text}`); }
    if (problems.length) failures.push({ name: c.name, problems, got: out.map((o) => `${o.status}: ${o.text}`) });
  }
  return { metrics: { tripcheck_accuracy: (cases.length - failures.length) / cases.length }, cases: cases.length, failures };
}

// ---------- 3. plan evaluator ----------
// The same checks Plan with Claude applies before offering a draft, as a reusable scorer.
export function scorePlan(plan, mode = "car") {
  let keyed = 0, grounded = 0, invented = 0, wrongDay = 0;
  const entries = [];
  for (const d of plan.days || []) for (const s of d.stops || []) {
    if (!s.key) continue;
    keyed++;
    const base = B.catalogItem(String(s.key).split("@")[0]);
    if (!base) { invented++; continue; }
    grounded++;
    if (base.key.startsWith("ev:") && base.day !== d.day) wrongDay++;
    entries.push({ ...base, key: base.key.startsWith("ev:") ? base.key : `${base.key}@${d.day}`, day: base.key.startsWith("ev:") ? base.day : d.day, time: s.time ?? base.time });
  }
  const days = [...new Set(entries.map((e) => e.day))];
  const problems = days.flatMap((d) => { const r = B.evaluateDay(entries.filter((e) => e.day === d), mode); return [...r.legs, ...r.notes].filter((x) => x.status === "bad"); });
  return { grounded: keyed ? grounded / keyed : 1, invented, problems: problems.length, wrong_day: wrongDay, problem_text: problems.map((p) => p.text) };
}
function evalPlans() {
  const { cases } = load("./datasets/plans.json");
  const failures = [];
  for (const c of cases) {
    const got = scorePlan(c.plan);
    const wrong = Object.entries(c.expect).filter(([k, v]) => Math.abs((got[k] ?? 0) - v) > 1e-9);
    if (wrong.length) failures.push({ name: c.name, expected: c.expect, got });
  }
  return { metrics: { plan_eval_accuracy: (cases.length - failures.length) / cases.length }, cases: cases.length, failures };
}

// ---------- 4. latency ----------
function evalLatency() {
  const { cases } = load("./datasets/retrieval.json");
  const times = [];
  for (let i = 0; i < 2000; i++) {
    const q = cases[i % cases.length].q;
    const t = performance.now();
    B.searchBoard(q, { k: 6 });
    times.push(performance.now() - t);
  }
  times.sort((a, b) => a - b);
  return { metrics: { search_p95_ms: times[Math.floor(0.95 * times.length)], search_p50_ms: times[Math.floor(0.5 * times.length)] } };
}

// ---------- run ----------
const sections = { retrieval: evalRetrieval(), tripcheck: evalTripCheck(), plans: evalPlans(), latency: evalLatency() };
const metrics = Object.assign({}, ...Object.values(sections).map((s) => s.metrics));
const failures = Object.entries(thresholds).flatMap(([m, t]) => {
  const v = metrics[m];
  const lowerIsBetter = m.endsWith("_ms");
  return (lowerIsBetter ? v > t : v < t) ? [`${m} = ${v.toFixed(3)} (needs ${lowerIsBetter ? "≤" : "≥"} ${t})`] : [];
});
let git = null;
try { git = execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] }).toString().trim(); } catch {}
const run = {
  v: 1, kind: "eval_run", ts: new Date().toISOString(), git, passed: failures.length === 0,
  metrics: Object.fromEntries(Object.entries(metrics).map(([k, v]) => [k, +v.toFixed(4)])),
  thresholds, failures,
  details: {
    retrieval: { cases: sections.retrieval.cases, misses: sections.retrieval.misses },
    tripcheck: { cases: sections.tripcheck.cases, failures: sections.tripcheck.failures },
    plans: { cases: sections.plans.cases, failures: sections.plans.failures },
  },
};

mkdirSync(here("./results/"), { recursive: true });
writeFileSync(here("./results/latest.json"), JSON.stringify(run, null, 2));

if (args.has("--json")) console.log(JSON.stringify(run, null, 2));
else {
  console.log(`\nYatra Pulse evaluation ${git ? `@ ${git}` : ""}\n`);
  for (const [m, v] of Object.entries(run.metrics)) {
    const t = thresholds[m];
    const ok = t === undefined ? "" : (m.endsWith("_ms") ? v <= t : v >= t) ? "PASS" : "FAIL";
    console.log(`  ${ok.padEnd(5)}${m.padEnd(26)}${String(v).padEnd(10)}${t !== undefined ? `target ${m.endsWith("_ms") ? "≤" : "≥"} ${t}` : ""}`);
  }
  for (const m of run.details.retrieval.misses) console.log(`  · search miss: "${m.q}" → expected ${m.expected}, ranked ${m.rank ?? "not in top 5"}; top: ${m.got.join(", ")}`);
  for (const f of [...run.details.tripcheck.failures, ...run.details.plans.failures]) console.log(`  · FAILED ${f.name}: ${JSON.stringify(f.problems || f.got)}`);
  console.log(`\n${run.passed ? "PASSED" : "FAILED"} · results in eval/results/latest.json`);
}

if (!args.has("--no-pulsar")) {
  const producer = new Producer(TOPICS.eval);
  try {
    const id = await producer.sendAndWait(run, { kind: "eval_run", passed: String(run.passed) }, 4000);
    console.log(`Published to Pulsar topic ${TOPICS.eval} (message ${id})`);
  } catch (e) {
    console.log(`Pulsar publish skipped: ${e.message}`);
    if (process.env.EVAL_REQUIRE_PULSAR === "1") process.exitCode = 1;
  } finally { await producer.close(500); }
}
if (!run.passed) process.exitCode = 1;
