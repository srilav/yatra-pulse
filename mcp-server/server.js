#!/usr/bin/env node
// Yatra Pulse MCP server: lets Claude (Claude Code, Claude Desktop or any MCP client) answer
// Mysuru Dasara questions from the board's own data. Retrieval (RAG) happens here with
// search_board; Claude writes the answer from the passages and cites their sources.
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import { loadBoard } from "./board.js";

const B = loadBoard();
const KINDS = ["ev", "nt", "dj", "trip", "kb"];
const json = (data) => ({ content: [{ type: "text", text: JSON.stringify(data, null, 2) }] });
const dayLabel = (d) => B.fmtD.format(B.T(d, "12:00"));

const server = new McpServer({ name: "yatra-pulse", version: "1.0.0" });

server.registerTool("search_board", {
  title: "Search the Dasara board",
  description: "Retrieval over every Mysuru Dasara 2026 event, nightly show, DJ night, day trip and booking fact on the Yatra Pulse board, ranked by BM25. Returns passages with their key, day and source URL. Use it before answering any question about events, timings, venues, tickets or day trips, and cite the url of each passage you use.",
  inputSchema: {
    query: z.string().describe("What to look for, e.g. 'Sid Sriram concert' or 'Gold Card price'"),
    day: z.number().int().min(11).max(21).optional().describe("Only passages for this October day (undated passages always match)"),
    kinds: z.array(z.enum(KINDS)).optional().describe("ev = dated event, nt = every night, dj = DJ night, trip = day trip, kb = booking and travel facts"),
    k: z.number().int().min(1).max(20).optional().describe("How many passages, default 6"),
  },
  annotations: { readOnlyHint: true },
}, async ({ query, day, kinds, k }) =>
  json(B.searchBoard(query, { day, kinds, k: k ?? 6 }).filter((p) => p.score > 0).map(({ key, kind, day, title, text, url, score }) => ({ key, kind, day, title, text, url, score }))));

server.registerTool("get_day", {
  title: "Schedule for one day",
  description: "All events on one day of Mysuru Dasara 2026 (11–21 October) plus the shows that run every night, with times in IST, venues, ticket links and sources.",
  inputSchema: { day: z.number().int().min(11).max(21).describe("Day of October, 11 to 21") },
  annotations: { readOnlyHint: true },
}, async ({ day }) => json({
  day: dayLabel(day),
  festival_day: day - 10,
  events: B.EVENTS.filter((e) => e.d === day).map((e) => ({
    key: `ev:${e.d}:${e.title}`, title: e.title, time: e.tba && e.s === "00:00" ? "not announced" : `${e.s}–${e.e}`,
    gates_close: e.arriveBy, place: e.place, note: e.note, book: e.book, source: e.url,
  })),
  every_night: B.NIGHTLY.map((n) => ({ key: "nt:" + n.title, title: n.title, time: `${n.s}–${n.e}`, place: n.place, source: n.url })),
}));

server.registerTool("check_itinerary", {
  title: "Trip check an itinerary",
  description: "Checks a plan for stops at different venues that overlap in time and for gaps too short to travel by the chosen vehicle, using the board's venue map, Dasara traffic speeds and parking/entry time. Stops are keys from search_board or get_day (ev:, nt:, trip:). Returns each leg as ok, warn or bad with a reason and a Google Maps route.",
  inputSchema: {
    stops: z.array(z.object({
      key: z.string().describe("e.g. 'ev:17:Drone show', 'nt:Palace illumination', 'trip:Melukote'"),
      day: z.number().int().min(11).max(21),
      time: z.string().regex(/^\d\d:\d\d$/).optional().describe("Start time HH:MM for day trips or nightly shows"),
    })).min(1),
    mode: z.enum(["car", "bike", "auto", "bus"]).optional().describe("car = car or taxi, bike = two-wheeler, auto = auto-rickshaw, bus"),
  },
  annotations: { readOnlyHint: true },
}, async ({ stops, mode }) => {
  const m = mode ?? "car";
  const unknown = [];
  const entries = stops.map((s) => {
    const base = B.catalogItem(s.key.split("@")[0]);
    if (!base) { unknown.push(s.key); return null; }
    return { ...base, key: base.key.startsWith("ev:") ? base.key : `${base.key}@${s.day}`, day: s.day, time: s.time ?? base.time };
  }).filter(Boolean);
  const days = [...new Set(entries.map((e) => e.day))].sort((a, b) => a - b);
  const result = days.map((d) => {
    const r = B.evaluateDay(entries.filter((e) => e.day === d), m);
    return { day: dayLabel(d), stops: r.stops.map((s) => s.title), legs: r.legs, notes: r.notes };
  });
  const all = result.flatMap((r) => [...r.legs, ...r.notes]);
  return json({
    mode: B.MODES[m].label,
    summary: { problems: all.filter((x) => x.status === "bad").length, to_watch: all.filter((x) => x.status === "warn").length },
    days: result,
    unknown_keys: unknown,
    note: "Distances are straight-line × 1.4 with Dasara traffic speeds; check live traffic with the route links.",
  });
});

server.registerTool("booking_guide", {
  title: "How to book Dasara tickets",
  description: "Official booking steps, ticket prices, gate times, getting there and the helpline, each with its source.",
  annotations: { readOnlyHint: true },
}, async () => json(B.KNOWLEDGE.filter((k) => ["booking", "prices", "gates", "travel"].includes(k.id))));

server.registerResource("schedule", "yatra://schedule", {
  title: "Mysuru Dasara 2026 schedule",
  description: "Every event, 11–21 October 2026, as Markdown",
  mimeType: "text/markdown",
}, async (uri) => ({
  contents: [{
    uri: uri.href, mimeType: "text/markdown",
    text: "# Mysuru Dasara 2026\n\n" + B.DAYS.map((d) => `## ${dayLabel(d)} (day ${d - 10})\n` +
      (B.EVENTS.filter((e) => e.d === d).map((e) => `- **${e.title}** · ${e.tba && e.s === "00:00" ? "time not announced" : `${e.s}–${e.e}`} · ${e.place} · [source](${e.url})`).join("\n") || "- Palace lights, cultural stage and exhibition")).join("\n\n") +
      "\n\n## Every night\n" + B.NIGHTLY.map((n) => `- **${n.title}** · ${n.s}–${n.e} · ${n.place}`).join("\n"),
  }],
}));

server.registerResource("facts", "yatra://facts", {
  title: "Booking, travel and Gaya facts",
  description: "The board's knowledge passages with sources",
  mimeType: "application/json",
}, async (uri) => ({ contents: [{ uri: uri.href, mimeType: "application/json", text: JSON.stringify(B.KNOWLEDGE, null, 2) }] }));

server.registerPrompt("plan_trip", {
  title: "Plan a Dasara trip",
  description: "Plan a day-by-day Mysuru Dasara itinerary grounded in the board, then trip-check it.",
  argsSchema: {
    arrival_day: z.string().describe("Day of October you arrive, 11–21"),
    days: z.string().describe("How many days you stay"),
    style: z.string().describe("spiritual, music, heritage or family"),
    notes: z.string().optional().describe("Anything else, e.g. travelling with parents"),
  },
}, ({ arrival_day, days, style, notes }) => ({
  messages: [{
    role: "user",
    content: {
      type: "text",
      text: `Plan my Mysuru Dasara 2026 trip: arriving ${arrival_day} October for ${days} days, style ${style}.${notes ? ` Notes: ${notes}.` : ""}
1. Use search_board (with the day filter for each day) and get_day to find real events and day trips. Only use what the tools return.
2. Draft a day-by-day plan using the passage keys.
3. Run check_itinerary on the draft and fix any clash or travel problem it reports.
4. Give the final plan with times, venues, how to book (booking_guide), and cite the source url for each stop.`,
    },
  }],
}));

await server.connect(new StdioServerTransport());
