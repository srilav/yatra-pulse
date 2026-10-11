#!/usr/bin/env node
// Yatra Pulse MCP server over Streamable HTTP, for containers, Kubernetes and Knative.
// Stateless: every request gets a fresh server and transport, so any replica can answer any
// request and Knative can scale from zero to many and back.
//
//   PORT=8080 node http.js
//   POST /mcp        MCP (JSON-RPC over Streamable HTTP)
//   GET  /healthz    liveness: the process is up
//   GET  /readyz     readiness: the board loaded (and whether Pulsar telemetry is connected)
import http from "node:http";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { loadBoard } from "./board.js";
import { createMcpServer } from "./app.js";
import { createTelemetry } from "../ops/telemetry.js";

const PORT = Number(process.env.PORT || 8080);
const MAX_BODY = 256 * 1024;
const B = loadBoard();
const tel = createTelemetry({ service: "yatra-pulse-mcp" });
const startedAt = Date.now();

const sendJson = (res, code, obj) => { res.writeHead(code, { "content-type": "application/json" }); res.end(JSON.stringify(obj)); };

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => { size += c.length; if (size > MAX_BODY) { reject(Object.assign(new Error("body too large"), { status: 413 })); req.destroy(); } else chunks.push(c); });
    req.on("end", () => { try { resolve(chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : undefined); } catch { reject(Object.assign(new Error("invalid JSON"), { status: 400 })); } });
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  const path = (req.url || "/").split("?")[0];
  if (path === "/healthz") return sendJson(res, 200, { ok: true, uptime_s: Math.round((Date.now() - startedAt) / 1000) });
  if (path === "/readyz") return sendJson(res, 200, { ok: true, events: B.EVENTS.length, telemetry: tel.mode, pulsar: tel.pulsarConnected ? "connected" : "not connected" });
  if (path !== "/mcp") return sendJson(res, 404, { error: "not found", try: ["/mcp", "/healthz", "/readyz"] });
  if (req.method !== "POST") return sendJson(res, 405, { jsonrpc: "2.0", error: { code: -32000, message: "Use POST (stateless server)" }, id: null });

  try {
    const body = await readBody(req);
    const mcp = createMcpServer(B, tel);
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: true });
    res.on("close", () => { transport.close(); mcp.close(); });
    await mcp.connect(transport);
    await transport.handleRequest(req, res, body);
  } catch (e) {
    tel.log("error", "mcp request failed", { error: e.message });
    if (!res.headersSent) sendJson(res, e.status || 500, { jsonrpc: "2.0", error: { code: -32603, message: e.message }, id: null });
  }
});

server.listen(PORT, () => {
  tel.emit("server_start", { transport: "http", events: B.EVENTS.length, telemetry_mode: tel.mode });
  tel.log("info", "MCP HTTP server listening", { port: PORT, telemetry: tel.mode });
});

// Knative sends SIGTERM before scaling a pod down: stop taking requests, drain telemetry, exit.
let stopping = false;
async function shutdown(reason) {
  if (stopping) return;
  stopping = true;
  tel.emit("server_stop", { transport: "http", reason, uptime_s: Math.round((Date.now() - startedAt) / 1000), telemetry: tel.counts });
  server.close();
  await tel.close();
  process.exit(0);
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
