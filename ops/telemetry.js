// Structured telemetry for Yatra Pulse services.
// Every event goes to the Pulsar topic yatra-telemetry; while Pulsar is unreachable, events are
// appended to a local JSONL file instead, so nothing is lost and the service never blocks or fails
// because of telemetry. Logs are JSON lines on stderr (stdout belongs to the MCP protocol).
//
// TELEMETRY=pulsar (default) | file | off
// TELEMETRY_FILE=path for the fallback file (default ops/telemetry.jsonl)
// TELEMETRY_INCLUDE_QUERIES=1 to record search query text (off by default: queries can be personal)
import { appendFileSync } from "node:fs";
import { createHash, randomUUID } from "node:crypto";
import { hostname } from "node:os";
import { fileURLToPath } from "node:url";
import { Producer, TOPICS } from "./pulsar.js";

const MODE = (process.env.TELEMETRY || "pulsar").toLowerCase();
const FILE = process.env.TELEMETRY_FILE || fileURLToPath(new URL("./telemetry.jsonl", import.meta.url));
export const INCLUDE_QUERIES = process.env.TELEMETRY_INCLUDE_QUERIES === "1";
export const hashText = (s) => createHash("sha256").update(String(s)).digest("hex").slice(0, 12);

export function createTelemetry({ service, version = "1.0.0" }) {
  const base = { service, version, host: hostname(), pid: process.pid };
  const counts = { emitted: 0, toPulsar: 0, toFile: 0 };

  const log = (level, msg, fields = {}) =>
    process.stderr.write(JSON.stringify({ ts: new Date().toISOString(), level, service, msg, ...fields }) + "\n");

  // Events emitted during the first few seconds wait in memory for the first connection, so a
  // short-lived process doesn't send its startup events to the fallback file.
  const startedAt = Date.now();
  const early = [];
  const toFile = (event) => {
    try { appendFileSync(FILE, JSON.stringify(event) + "\n"); counts.toFile++; } catch (e) { log("error", "telemetry file write failed", { error: e.message }); }
  };
  const producer = MODE === "pulsar"
    ? new Producer(TOPICS.telemetry, {
        onState: (s) => {
          log(s === "connected" ? "info" : "warn", `pulsar ${s}`, { topic: TOPICS.telemetry });
          if (s === "connected") while (early.length) { const ev = early.shift(); if (producer.send(ev, { kind: ev.kind, service })) counts.toPulsar++; else toFile(ev); }
        },
      })
    : null;

  function emit(kind, attrs = {}) {
    if (MODE === "off") return;
    const event = { v: 1, id: randomUUID(), ts: new Date().toISOString(), kind, ...base, ...attrs };
    counts.emitted++;
    if (producer?.send(event, { kind, service })) { counts.toPulsar++; return; }
    if (producer && Date.now() - startedAt < 5000 && early.length < 500) { early.push(event); return; }
    toFile(event);
  }

  /** Time fn(), emit one "tool_call" event with ok/error, latency and attrs from describe(result). */
  async function span(tool, fn, { input = {}, describe = () => ({}) } = {}) {
    const start = performance.now();
    const traceId = randomUUID();
    try {
      const result = await fn();
      emit("tool_call", { tool, trace_id: traceId, ok: true, latency_ms: +(performance.now() - start).toFixed(2), input, ...safe(describe, result) });
      return result;
    } catch (e) {
      const latency = +(performance.now() - start).toFixed(2);
      emit("tool_call", { tool, trace_id: traceId, ok: false, latency_ms: latency, input, error: { type: e?.name || "Error", message: String(e?.message || e).slice(0, 300) } });
      log("error", "tool failed", { tool, trace_id: traceId, error: String(e?.message || e) });
      throw e;
    }
  }

  async function close() {
    if (!producer) return;
    const until = Date.now() + 3000;  // give a still-connecting producer a moment to take the early events
    while (early.length && !producer.connected && Date.now() < until) await new Promise((r) => setTimeout(r, 50));
    early.splice(0).forEach(toFile);
    await producer.close();
  }

  return { emit, span, log, close, counts, get pulsarConnected() { return !!producer?.connected; }, mode: MODE };
}

function safe(fn, value) {
  try { return fn(value) || {}; } catch { return {}; }
}
