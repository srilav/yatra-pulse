# Yatra Pulse

A live festival travel board for **Mysuru Dasara 2026** (11–21 October), built for travellers who want the culture, not just the crowd.

Live version: https://claude.ai/artifact/1KQUyk25gdmTGtbnHSxgJX

## Video walkthrough

[![Watch the Yatra Pulse walkthrough on YouTube](resources/thumbnail.png)](https://www.youtube.com/watch?v=VNpOtIx-vuU)

▶️ **Watch on YouTube: https://www.youtube.com/watch?v=VNpOtIx-vuU**

A 3½-minute narrated tour of every feature, with chapters on YouTube: live board, schedule, filters, picks, itinerary, Trip check (clashes, travel time by vehicle), Plan with Claude, booking, traveller events, day trips, Gaya, dark mode and phone. The MP4 is also in [`resources/yatra-pulse.mp4`](resources/yatra-pulse.mp4). YouTube title, description, chapters and tags are in [`resources/youtube.txt`](resources/youtube.txt).

To regenerate it (macOS, uses the `say` voice "Rishi"):

```bash
.venv/bin/pip install imageio-ffmpeg pillow
.venv/bin/python video/make_video.py
```

## What it shows

- **Now & next**: what's live and coming up, computed from the current time in India (IST) and refreshed every minute.
- **11-day timeline**: rituals, Yuva Dasara concerts, shows and the Jamboo Savari, filterable by type. Every event links to its source.
- **Picks for you**: suggestions for Spiritual, Music & DJ, Heritage or Family travellers.
- **DJ & party nights**: in Mysuru and Bengaluru.
- **My itinerary**: tap "+ Add to plan" on any event or day trip to build a day-by-day plan. Copy it to share on WhatsApp or email. Saved to your claude.ai account (or this browser when opened from disk).
- **Trip check**: checks your itinerary for stops at different venues that overlap in time, and whether each gap is long enough to travel by car or taxi, two-wheeler, auto-rickshaw or bus. It allows for Dasara traffic and for parking and entry at crowded venues, links a Google Maps route for each leg, and warns you when you add a stop or tap "Book tickets".
- **Plan with Claude**: pick arrival day, length, style and pace. The board retrieves matching events and facts (RAG), Claude drafts from only those, and Trip check reviews the draft.
- **Fun events from travellers**: anyone with Contributor access can submit an event with an official link and a short write-up. Submissions stay hidden until the host approves them.
- **Day trips** from Mysuru, and the **Gaya Pitru Paksha** status.

## Ask Claude about Dasara (MCP + RAG)

`mcp-server/` is an MCP server that lets Claude Code or Claude Desktop search the board (RAG), read any day's schedule, get booking facts, and trip-check a plan. It reuses the page's own data and rules.

```bash
cd mcp-server && npm install && npm test   # 8 checks
```

In this folder, Claude Code picks it up from `.mcp.json`. Elsewhere: `claude mcp add yatra-pulse -- node /path/to/mcp-server/server.js`.

Full details, every component and use case: [SPEC.md](SPEC.md). How each feature is built, component by component: [resources/design-discussion.html](resources/design-discussion.html). Visual design: [docs/design.html](docs/design.html).

## Evaluation, metrics and observability (Apache Pulsar)

```bash
docker compose -f ops/docker-compose.yml up -d   # Apache Pulsar 4.2.4 standalone
node ops/metrics-consumer.js                     # metrics, SLOs, alerts → http://localhost:9464
node eval/run.js                                 # quality gates; publishes to Pulsar
cd mcp-server && TELEMETRY=pulsar npm test       # real tool traffic → yatra-telemetry
```

- **Evaluator** (`eval/`): labelled datasets score board search (hit@1 0.97, recall@3 1.0), trip check (10/10) and AI plan grounding (4/4), with gates in `eval/thresholds.json` and CI.
- **Telemetry**: every MCP tool call is an event on the Pulsar topic `yatra-telemetry` (falls back to a file if Pulsar is down).
- **Metrics consumer**: Prometheus `/metrics`, `/healthz`, a live dashboard, four SLOs, alerts on `yatra-alerts`, and a dead-letter topic.
- **Operations**: [ops/RUNBOOK.md](ops/RUNBOOK.md) (SLOs, alerts, game day), [ops/alerts.yml](ops/alerts.yml), [CI](.github/workflows/ci.yml).

## Deploy on Kubernetes with Knative (on-prem or any cloud)

Three Knative services (web board, MCP server over HTTP, metrics consumer) plus Apache Pulsar. Verified on kind with Knative Serving 1.23: 9/9 smoke checks, telemetry flowing MCP → Pulsar → metrics.

```bash
./deploy/kind-up.sh && ./deploy/smoke-test.sh   # laptop
kubectl apply -k deploy/k8s                     # any cluster with Knative (images in your registry)
```

Guide: [deploy/README.md](deploy/README.md). Architecture by category (functionality, tech stack, components, AI-enabled components, end-to-end testing, telemetry, deployment): [resources/architecture.html](resources/architecture.html).

## How it runs

`index.html` is a single self-contained page (HTML, CSS and plain JavaScript). It is published as a claude.ai Artifact and uses the Artifact runtime's `db`, `user` and `sample` capabilities for saved itineraries, traveller submissions and Plan with Claude. Opened directly in a browser, the schedule and itinerary work (saved in that browser), while submissions and Plan with Claude are disabled.

Access rules used when publishing:

```json
{
  "db": { "rules": [
    { "path": "", "read": "view", "write": "admin" },
    { "path": "tips", "read": "admin", "write": "owner" },
    { "path": "tips/{self}", "read": "interact", "write": "interact" },
    { "path": "data/users/{self}", "read": "interact", "write": "interact" }
  ]},
  "user": { "scopes": ["profile"] },
  "sample": {}
}
```

## Browser test (Selenium)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tests/run_dashboard.py --hold 15 --pause 2
```

Opens the board in Chrome, runs 15 checks (timeline, source links, filters, itinerary add/remove, picks, traveller form, and trip-check clashes, travel times, vehicle limits and booking warnings), saves screenshots to `screenshots/`, lingers `--pause` seconds on each step and keeps the window open `--hold` seconds at the end.

## Data

Event details were last checked on 11 October 2026 from public sources linked inside the page. Timings marked "Time TBA" had not been announced yet.
