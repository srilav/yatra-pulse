// Smoke test: starts the server over stdio like a real MCP client and checks each capability.
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";

const client = new Client({ name: "yatra-pulse-test", version: "1.0.0" });
await client.connect(new StdioClientTransport({ command: process.execPath, args: [fileURLToPath(new URL("./server.js", import.meta.url))], env: { ...process.env, TELEMETRY: process.env.TELEMETRY || "off" } }));
const call = async (name, args = {}) => JSON.parse((await client.callTool({ name, arguments: args })).content[0].text);
let passed = 0;
const check = async (label, fn) => { await fn(); passed++; console.log(`PASS  ${label}`); };

await check("lists 4 tools, 2 resources, 1 prompt", async () => {
  const tools = (await client.listTools()).tools.map((t) => t.name).sort();
  assert.deepEqual(tools, ["booking_guide", "check_itinerary", "get_day", "search_board"]);
  assert.equal((await client.listResources()).resources.length, 2);
  assert.equal((await client.listPrompts()).prompts[0].name, "plan_trip");
});
await check("search_board finds Sid Sriram with its source", async () => {
  const hits = await call("search_board", { query: "Sid Sriram concert" });
  assert.equal(hits[0].key, "ev:15:Sid Sriram live");
  assert.match(hits[0].url, /^https:\/\//);
});
await check("search_board answers a booking question from facts", async () => {
  const hits = await call("search_board", { query: "Gold Card price", kinds: ["kb"] });
  assert.match(hits[0].text, /6,961/);
});
await check("search_board finds a concert on a given day", async () => {
  const hits = await call("search_board", { query: "concert", day: 14, kinds: ["ev"] });
  assert.ok(hits.length > 0, "expected at least one concert on 14 Oct");
  assert.ok(hits.every((h) => h.day === 14));
});
await check("get_day returns Jamboo Savari with gate time", async () => {
  const day = await call("get_day", { day: 21 });
  const js = day.events.find((e) => e.title === "Jamboo Savari");
  assert.equal(js.gates_close, "12:30");
});
await check("check_itinerary flags the 17 Oct clash", async () => {
  const r = await call("check_itinerary", { stops: [{ key: "ev:17:Jasmine Sandlas closing night", day: 17 }, { key: "ev:17:Drone show", day: 17 }] });
  assert.equal(r.summary.problems, 1);
  assert.match(r.days[0].legs[0].text, /^Clash/);
});
await check("check_itinerary flags a far trip by auto", async () => {
  const r = await call("check_itinerary", { stops: [{ key: "trip:Shravanabelagola", day: 19 }], mode: "auto" });
  assert.ok(r.days[0].notes.some((n) => /too far/.test(n.text)));
});
await check("schedule resource lists all 11 days", async () => {
  const text = (await client.readResource({ uri: "yatra://schedule" })).contents[0].text;
  assert.equal((text.match(/^## .*\(day \d+\)$/gm) || []).length, 11);
});

await client.close();
console.log(`\n${passed}/8 passed`);
