#!/usr/bin/env node
// Game day: publish synthetic telemetry to prove alerts fire and resolve.
//   node ops/simulate.js errors     # 40 calls, 25% failing  → availability SLO breach
//   node ops/simulate.js slow       # 40 calls at ~600 ms    → latency SLO breach
//   node ops/simulate.js empty      # 20 searches, 50% empty → search SLO breach
//   node ops/simulate.js healthy    # 400 fast, successful calls → dilutes the window so alerts resolve
//   node ops/simulate.js poison     # one non-JSON message    → goes to the dead-letter topic
// Events are marked service="synthetic" so they're easy to tell apart.
import { randomUUID } from "node:crypto";
import { Producer, TOPICS } from "./pulsar.js";

const scenario = process.argv[2] || "healthy";
const producer = new Producer(TOPICS.telemetry);
const call = (over) => ({ v: 1, id: randomUUID(), ts: new Date().toISOString(), kind: "tool_call", service: "synthetic", tool: "search_board", ok: true, latency_ms: 2 + Math.random() * 6, results: 5, zero_result: false, ...over });

const plans = {
  errors: () => Array.from({ length: 40 }, (_, i) => call({ tool: "check_itinerary", ok: i % 4 !== 0, error: i % 4 === 0 ? { type: "Error", message: "synthetic failure" } : undefined })),
  slow: () => Array.from({ length: 40 }, () => call({ latency_ms: 550 + Math.random() * 100 })),
  empty: () => Array.from({ length: 20 }, (_, i) => call({ results: i % 2 ? 0 : 5, zero_result: i % 2 === 1 })),
  healthy: () => Array.from({ length: 400 }, () => call({})),
};

if (scenario === "poison") {
  await producer.sendAndWait({}, {}, 5000).catch(() => {});
  producer.ws.send(JSON.stringify({ payload: Buffer.from("this is not json").toString("base64"), context: "poison" }));
  await new Promise((r) => setTimeout(r, 500));
  console.log("Sent one non-JSON message; the consumer should move it to", TOPICS.dlq);
} else if (plans[scenario]) {
  const events = plans[scenario]();
  for (const e of events) await producer.sendAndWait(e, { kind: e.kind, service: "synthetic" });
  console.log(`Published ${events.length} synthetic events (${scenario}) to ${TOPICS.telemetry}`);
} else {
  console.error(`Unknown scenario "${scenario}". Use errors, slow, empty, healthy or poison.`);
  process.exitCode = 1;
}
await producer.close();
