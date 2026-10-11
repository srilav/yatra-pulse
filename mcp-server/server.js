#!/usr/bin/env node
// Yatra Pulse MCP server over stdio: lets Claude (Claude Code, Claude Desktop or any MCP client)
// answer Mysuru Dasara questions from the board's own data. Retrieval (RAG) happens in
// search_board; Claude writes the answer from the passages and cites their sources.
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { loadBoard } from "./board.js";
import { createMcpServer } from "./app.js";
import { createTelemetry } from "../ops/telemetry.js";

const B = loadBoard();
const tel = createTelemetry({ service: "yatra-pulse-mcp" });
const server = createMcpServer(B, tel);

const startedAt = Date.now();
tel.emit("server_start", { events: B.EVENTS.length, passages: B.boardPassages().length, telemetry_mode: tel.mode });
tel.log("info", "server started", { events: B.EVENTS.length, telemetry: tel.mode });

// Graceful shutdown: record the stop and drain pending telemetry before exiting.
let stopping = false;
const shutdown = async (reason) => {
  if (stopping) return;
  stopping = true;
  tel.emit("server_stop", { reason, uptime_s: Math.round((Date.now() - startedAt) / 1000), telemetry: tel.counts });
  await tel.close();
  process.exit(0);
};
process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.stdin.on("close", () => shutdown("client_disconnected"));

await server.connect(new StdioServerTransport());
