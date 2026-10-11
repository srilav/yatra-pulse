# Musafir Mela

A live festival travel board for **Mysuru Dasara 2026** (11–21 October), built for travellers who want the culture, not just the crowd.

Live version: https://claude.ai/artifact/1KQUyk25gdmTGtbnHSxgJX

## Video walkthrough

[![Musafir Mela video walkthrough](resources/thumbnail.png)](resources/musafir-mela.mp4)

A 2-minute narrated tour covering the live board, the full schedule, how to book official tickets, the itinerary and day trips: [`resources/musafir-mela.mp4`](resources/musafir-mela.mp4). YouTube title, description, chapters and tags are in [`resources/youtube.txt`](resources/youtube.txt).

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
- **Plan with Claude**: pick arrival day, length, style and pace, and Claude drafts an itinerary using only events and day trips listed on the board, so it can't invent stops.
- **Fun events from travellers**: anyone with Contributor access can submit an event with an official link and a short write-up. Submissions stay hidden until the host approves them.
- **Day trips** from Mysuru, and the **Gaya Pitru Paksha** status.

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
