// Loads the board's shared code (data, trip check, board search) straight out of ../index.html,
// so the MCP server and the web page always use the same events, rules and retrieval.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const PAGE = fileURLToPath(new URL("../index.html", import.meta.url));

export function loadBoard(path = PAGE) {
  const html = readFileSync(path, "utf8");
  const start = html.indexOf("/* SHARED-START");
  const end = html.indexOf("/* SHARED-END */");
  if (start < 0 || end < 0) throw new Error(`Shared code markers not found in ${path}`);
  const code = html.slice(start, end) +
    "\n;({ EVENTS, NIGHTLY, DJ, TRIPS, DAYS, VENUES, MODES, KNOWLEDGE, STYLE_WORDS, evaluateDay, searchBoard, boardPassages, catalogItem, fromMin, fmtD, fmtT, T });";
  // The shared code never touches the DOM at load time; planDay() falls back to the first day without one.
  const context = vm.createContext({ document: { getElementById: () => null } });
  return vm.runInContext(code, context, { filename: "index.html#shared" });
}
