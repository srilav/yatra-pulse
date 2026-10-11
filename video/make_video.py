"""Make a narrated 1080p walkthrough of every Yatra Pulse feature, for YouTube.

    .venv/bin/python video/make_video.py

Drives index.html in headless Chrome, captures frames per scene, narrates each scene with the
macOS `say` voice (Rishi, en_IN), burns in captions, and writes:
    video/out/yatra-pulse.mp4      the video (1920x1080, H.264 + AAC)
    video/out/thumbnail.png         1280x720 YouTube thumbnail
    video/out/youtube.txt           title, description with chapters, and tags

Scenes that need claude.ai (saving to your account, Plan with Claude's answer, sending a
traveller event) are shown up to that point and narrated as such; nothing is faked.
"""

import shutil
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont
from selenium import webdriver

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "video" / "out"
WORK = OUT / "work"
PAGE = (ROOT / "index.html").as_uri()
ARCH = (ROOT / "resources" / "architecture.html").as_uri()
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 8
VOICE, RATE = "Rishi", 172
W, H = 1920, 1080
SCALE = 1.5  # render a 1280x720 page at 1.5x so text is readable on phones
PHONE = (390, 844, 2)  # css width, css height, device scale

# Page helpers injected once. Camera moves are done by the script; these only act on the page.
HELPERS = """
window.V = {
  add(k) { const b = document.querySelector(`[data-add="${k}"]`); if (b && b.getAttribute('aria-pressed') !== 'true') b.click(); },
  click(sel) { const e = document.querySelector(sel); if (e) e.click(); },
  set(id, v) { const e = document.getElementById(id); e.value = v; e.dispatchEvent(new Event('input', {bubbles: true})); e.dispatchEvent(new Event('change', {bubbles: true})); },
  clear() { setPlan([]); document.getElementById('toast').hidden = true; },
  book(k) { const a = document.querySelector(`[data-book="${k}"]`); a.addEventListener('click', e => e.preventDefault(), {once: true}); a.click(); },
  y(sel, off) { const e = document.querySelector(sel); return e ? Math.max(0, e.getBoundingClientRect().top + scrollY - off) : 0; },
};
const st = document.createElement('style');
st.textContent = '#toast{bottom:96px!important}';
document.head.appendChild(st);
document.getElementById('aMsg').textContent = '';
"""

# target: CSS selector the camera settles on (off = px above it). pan: selector to drift toward later.
# acts: (fraction of scene, JS) run once when the scene reaches that point.
SCENES = [
    dict(id="title", card="title", chapter="Intro",
         say="Yatra Pulse. A live travel board for Mysuru Dasara, twenty twenty-six. Here is everything it can do.",
         cap="Yatra Pulse · every feature in one tour"),
    dict(id="hero", target="body", chapter="Festival day and countdown",
         say="The header shows which day of the eleven-day festival it is, and counts down to the Jamboo Savari elephant procession on the twenty-first.",
         cap="Festival day counter · Jamboo Savari countdown"),
    dict(id="now", target="#nowH", chapter="Now and next",
         say="Now and next shows what is live right now, and what starts soon, using the time in India. It refreshes every minute.",
         cap="Now & next · live status in IST, refreshed every minute"),
    dict(id="timeline", target="#tlH", chapter="Timeline and filters",
         acts=[(0.25, "V.click('[data-f=\"music\"]')"), (0.45, "V.click('[data-f=\"dj\"]')"),
               (0.65, "V.click('[data-f=\"ritual\"]')"), (0.85, "V.click('[data-f=\"show\"]')"), (0.98, "V.click('[data-f=\"all\"]')")],
         say="The eleven-day timeline has every ritual, concert and show. Filter by concerts, D J and party, ritual, or shows. Every event links to its source.",
         cap="11-day timeline · filter: Concerts · DJ & party · Ritual · Shows"),
    dict(id="schedule", card="schedule", chapter="Schedule at a glance",
         say="Here is the schedule at a glance. Yuva Dasara concerts from the thirteenth to the seventeenth. Drone shows on the seventeenth and eighteenth. The air show from the eighteenth to the twentieth. And on the twenty-first, the Jamboo Savari and the torchlight parade.",
         cap="Schedule at a glance · 11–21 Oct"),
    dict(id="picks", target="#picks", off=150, pan="#dj", chapter="Picks for you and DJ nights",
         acts=[(0.2, "V.click('[data-v=\"music\"]')"), (0.35, "V.click('[data-v=\"heritage\"]')"), (0.48, "V.click('[data-v=\"family\"]')")],
         say="Choose a travel style: spiritual, music, heritage or family, for personal picks. Below, find D J and party nights in Mysuru and Bengaluru.",
         cap="Picks for you · Spiritual · Music & DJ · Heritage · Family"),
    dict(id="plan", target="#planH", chapter="Build your itinerary",
         acts=[(0.0, "V.clear(); V.set('pDay','12')"), (0.2, "V.add('ev:13:Yuva Dasara opening night')"),
               (0.4, "V.add('ev:15:Sid Sriram live')"), (0.6, "V.add('trip:Srirangapatna')")],
         say="Build your own itinerary. Tap add to plan on any event or day trip, and it lands on the right day. Copy the plan to share on WhatsApp. On claude dot ai it is saved to your account.",
         cap="My itinerary · + Add to plan · Copy plan"),
    dict(id="clash", target="#planH", chapter="Trip check: clashes",
         acts=[(0.0, "V.clear()"), (0.15, "V.add('ev:17:Jasmine Sandlas closing night')"), (0.4, "V.add('ev:17:Drone show')")],
         say="Trip check stops you booking two places at the same time. On the seventeenth, the drone show at Bannimantap and Jasmine Sandlas at Uttanahalli overlap, so it flags a clash the moment you add it.",
         cap="Trip check · clash: two venues at the same time"),
    dict(id="travel", target="#planH", chapter="Trip check: travel time by vehicle",
         acts=[(0.0, "V.clear(); V.set('pMode','car')"), (0.1, "V.add('ev:21:Jamboo Savari')"), (0.25, "V.add('ev:21:Torchlight parade')"),
               (0.55, "V.set('pMode','auto')"), (0.8, "V.set('pMode','bus')")],
         say="It also checks if you can get from one stop to the next. Choose car, two-wheeler, auto-rickshaw or bus. It allows for Dasara traffic, parking and entry, warns about gate closing times, and links a Google Maps route for each leg.",
         cap="Trip check · travel time by car, two-wheeler, auto or bus"),
    dict(id="far", target="#planH", chapter="Trip check: too far for the vehicle",
         acts=[(0.0, "V.clear(); V.set('pMode','auto'); V.set('pDay','19')"), (0.15, "V.add('trip:Shravanabelagola')"), (0.7, "V.set('pMode','car')")],
         say="Pick a long day trip by auto-rickshaw, and it tells you to take a car, taxi or bus instead.",
         cap="Trip check · warns when a trip is too far for the vehicle"),
    dict(id="ai", target="#aiPanel", off=120, chapter="Plan with Claude",
         acts=[(0.15, "V.set('aDay','13')"), (0.3, "V.set('aLen','3')"), (0.45, "V.set('aStyle','music')"), (0.6, "V.set('aPace','packed')"),
               (0.75, "V.set('aNote','travelling with parents, vegetarian food')")],
         say="Or let Claude draft the whole plan. Pick your arrival day, length of stay, style and pace. The board retrieves the events and facts that match, Claude plans from only those, and trip check reviews the draft before you add it. This runs on claude dot ai.",
         cap="Plan with Claude · retrieves matching events · trip-checks the draft · runs on claude.ai"),
    dict(id="book", target="#bookH", chapter="How to book",
         say="How to book. Check your itinerary first. Then go to the official site, mysore dasara dot gov dot in. TicketGenie is the only authorised seller. Collect your physical ticket at the Spandhana counter, at the Deputy Commissioner's office, with photo I D. Prices include fees and tax.",
         cap="How to book · mysoredasara.gov.in · collect at Spandhana Counter with ID"),
    dict(id="bookcheck", target="[data-book=\"ev:17:Drone show\"]", off=330, chapter="Book tickets safety check",
         acts=[(0.0, "V.clear(); V.add('ev:17:Jasmine Sandlas closing night'); document.getElementById('toast').hidden = true"),
               (0.3, "V.book('ev:17:Drone show')")],
         say="Tap book tickets, and trip check runs first. Here it warns that the drone show clashes with a concert already in your plan.",
         cap="Book tickets · checks your itinerary before opening the official site"),
    dict(id="community", target="#cmH", pan="#fPlace", chapter="Traveller events",
         acts=[(0.2, "V.set('fTitle','Example listing'); V.set('fUrl','https://bit.ly/abc')"), (0.4, "V.click('#fSubmit')")],
         say="Travellers can add fun events with an official link and a short write-up. Short links are rejected, and the host checks every event before it appears, so the list stays genuine.",
         cap="Traveller events · official links only · host checks each one"),
    dict(id="trips", target="#trH", chapter="Day trips",
         acts=[(0.5, "V.add('trip:Melukote')")],
         say="Go deeper with day trips to Srirangapatna, Somanathapura, Melukote, Shravanabelagola and more, each with a link and one tap to add.",
         cap="Day trips from Mysuru"),
    dict(id="gaya", target=".gaya", off=200, chapter="Gaya Pitru Paksha",
         say="Planning for Pitru Paksha? The Gaya panel covers the rites, and nearby Bodh Gaya, Rajgir and Nalanda.",
         cap="Gaya · Pitru Paksha · Bodh Gaya, Rajgir, Nalanda"),
    dict(id="dark", target="body", dark=True, pan="#planH", chapter="Dark mode",
         say="It follows your device's theme, with a full dark mode for night-time planning.",
         cap="Dark mode follows your device"),
    dict(id="phone", phone=True, chapter="On your phone",
         say="And it works on your phone, so the plan travels with you.",
         cap="Works on your phone"),
    dict(id="architecture", url="ARCH", target="#ai", off=10, pan="#telemetry", chapter="Architecture and agent goal",
         say="The architecture page explains the whole system: functionality, tech stack, components, and the A I agent's goal. Plan a trip a traveller can actually follow, with every stop real, nothing clashing, and every source cited.",
         cap="Architecture · agent goal: every stop real, no clashes, every source cited"),
    dict(id="evaluator", card="terminal", chapter="Evaluator quality gates",
         term=[("node eval/run.js", "node eval/run.js")],
         say="An evaluator keeps the A I honest. It scores search on thirty-one labelled questions, trip check on ten scenarios, and A I plans for invented or clashing stops, and fails the build if quality drops.",
         cap="Evaluator · search, trip check and plan grounding · gates in CI"),
    dict(id="telemetry", url="http://localhost:9464/", target="body", chapter="Telemetry on Apache Pulsar",
         pre=["cd mcp-server && TELEMETRY=pulsar node test.js", "node eval/run.js"],
         say="Every tool call becomes an event on Apache Pulsar. A metrics consumer turns them into live metrics and service level objectives, raises alerts, and counts the clashes trip check has caught.",
         cap="Telemetry → Apache Pulsar → metrics, SLOs and alerts"),
    dict(id="knative", card="terminal", chapter="Knative on Kubernetes",
         term=[("kubectl get ksvc -n yatra-pulse", "kubectl --context kind-yatra-pulse get ksvc -n yatra-pulse"),
               ("./deploy/local/smoke-test.sh", "./deploy/local/smoke-test.sh")],
         say="And it deploys on Kubernetes with Knative, on-prem or on any cloud. The web board and M C P server scale to zero when idle and wake on demand, while the metrics consumer stays on. Nine of nine smoke checks pass.",
         cap="Knative on Kubernetes · on-prem or any cloud · scale to zero"),
    dict(id="end", card="end", chapter="Links",
         say="The board, its code, and an M C P server that lets Claude search the board and check your plan, are on GitHub at srilav slash yatra pulse. Happy travels!",
         cap="github.com/srilav/yatra-pulse"),
]

CAPTION_JS = """
let c = document.getElementById('vidcap');
if (!c) {
  c = document.createElement('div'); c.id = 'vidcap';
  c.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;max-width:86%;' +
    'background:rgba(20,15,31,.9);color:#fff;font:600 21px/1.3 Mukta,system-ui,sans-serif;padding:8px 18px;' +
    'border-radius:12px;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.25)';
  document.body.appendChild(c);
}
c.textContent = arguments[0]; c.hidden = !arguments[0];
"""

CARD_JS = r"""
let k = document.getElementById('vidcard');
if (!arguments[0]) { if (k) k.remove(); return; }
if (!k) { k = document.createElement('div'); k.id = 'vidcard'; document.body.appendChild(k); }
k.style.cssText = 'position:fixed;inset:0;z-index:9998;display:grid;place-content:center;gap:12px;text-align:center;' +
  'background:radial-gradient(ellipse at 50% 30%,#2a1f45 0%,#140F1F 70%);color:#F1ECF8;font-family:Mukta,system-ui,sans-serif';
const bulbs = '<div style="height:11px;width:600px;margin:0 auto;background-image:radial-gradient(circle,#FFC857 0 3.5px,transparent 4px);background-size:20px 11px"></div>';
const SCHED = [
  ['11 Sun','Opening puja, Chamundi Hill · palace lights begin'],
  ['12 Mon','Classical music & dance, palace stage'],
  ['13 Tue','Yuva Dasara: Prabhu Deva, Ajaneesh Loknath'],
  ['14 Wed','Yuva Dasara: Shankar-Ehsaan-Loy'],
  ['15 Thu','Yuva Dasara: Sid Sriram'],
  ['16 Fri','Yuva Dasara: Sandalwood Nights'],
  ['17 Sat','Yuva Dasara: Jasmine Sandlas · Drone show'],
  ['18 Sun','Surya Kiran air show · Drone show'],
  ['19 Mon','Durga Ashtami · air show'],
  ['20 Tue','Ayudha Puja · air show'],
  ['21 Wed','Jamboo Savari 4:10 pm · Torchlight parade'],
];
if (arguments[0] === 'terminal') {
  const lines = String(arguments[1]).split('\n');
  const shown = lines.slice(0, Math.max(1, Math.ceil(lines.length * Math.min(1, (arguments[2] ?? 1) / 0.6))));
  k.style.placeContent = 'stretch';
  k.style.padding = '28px 40px 90px';
  k.innerHTML = '<div style="background:#0E0A16;border:1px solid #3a2f55;border-radius:12px;padding:18px 22px;text-align:left;overflow:hidden;font:500 13.5px/1.55 \'JetBrains Mono\',monospace;color:#E8E2F4;white-space:pre-wrap">' +
    '<div style="display:flex;gap:7px;margin-bottom:12px"><i style="width:11px;height:11px;border-radius:50%;background:#F0607A"></i><i style="width:11px;height:11px;border-radius:50%;background:#F2B544"></i><i style="width:11px;height:11px;border-radius:50%;background:#4CC38A"></i></div>' +
    shown.map((l) => l.replace(/&/g,'&amp;').replace(/</g,'&lt;')
      .replace(/^(\$ .*)$/, '<span style="color:#F2B544">$1</span>')
      .replace(/\b(PASS(ED)?)\b/g, '<span style="color:#4CC38A">$1</span>')
      .replace(/\b(FAIL(ED)?)\b/g, '<span style="color:#F0607A">$1</span>')).join('\n') + '</div>';
  return;
}
if (arguments[0] === 'schedule') {
  k.innerHTML = '<div style="font:400 54px/1 \'Yatra One\',serif;margin-bottom:6px">Dasara 2026 <span style="color:#F2B544">schedule</span></div>' +
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px 40px;text-align:left;width:1180px">' +
    SCHED.map(([d, t], i) => '<div style="display:flex;gap:16px;align-items:baseline;padding:8px 0;border-top:1px solid #3a2f55' + (i === 10 ? ';grid-column:span 2;color:#FFC857;font-weight:700' : '') + '">' +
      '<span style="font:500 20px \'JetBrains Mono\',monospace;color:#F2B544;min-width:120px;white-space:nowrap">' + d + ' Oct</span><span style="font-size:21px;white-space:nowrap">' + t + '</span></div>').join('') +
    '</div><div style="font-size:17px;color:#A99FBE;margin:6px 0 70px">Palace illumination every night, 7–10 pm · Yuva Dasara 5:30–10:30 pm, Uttanahalli</div>';
  return;
}
k.innerHTML = arguments[0] === 'title'
  ? bulbs + '<div style="font:400 100px/1 \'Yatra One\',serif">Yatra <span style="color:#F2B544">Pulse</span></div>' +
    '<div style="font-size:30px;color:#C9BEDD">Mysuru Dasara 2026 · 11–21 October</div>' +
    '<div style="font-size:20px;color:#A99FBE">Live events · Schedule · Booking · Itinerary · Trip check · AI planner · Evaluator · Pulsar · Knative</div>' + bulbs
  : bulbs + '<div style="font:400 74px/1 \'Yatra One\',serif">Happy <span style="color:#F2B544">travels</span></div>' +
    '<div style="font:500 30px/1.4 \'JetBrains Mono\',monospace;color:#F2B544">github.com/srilav/yatra-pulse</div>' +
    '<div style="font-size:20px;color:#A99FBE">Official tickets: mysoredasara.gov.in · Helpline 1077</div>' + bulbs;
"""


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def audio_seconds(path: Path) -> float:
    out = subprocess.run(["afinfo", str(path)], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if "estimated duration" in line:
            return float(line.split(":")[1].split()[0])
    raise RuntimeError(f"no duration for {path}")


def ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def font(size):
    for f in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc"]:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def phone_frame(shot: Path, caption: str, dest: Path):
    """Place a phone screenshot on a dark stage with the caption underneath."""
    stage = Image.new("RGB", (W, H), (20, 15, 31))
    ph = Image.open(shot).convert("RGB")
    ph = ph.resize((int(ph.width * 860 / ph.height), 860), Image.LANCZOS)
    x, y = (W - ph.width) // 2, 60
    d = ImageDraw.Draw(stage)
    d.rounded_rectangle([x - 14, y - 14, x + ph.width + 14, y + ph.height + 14], radius=44, fill=(44, 34, 70))
    stage.paste(ph, (x, y))
    f = font(32)
    tw = d.textlength(caption, font=f)
    d.rounded_rectangle([(W - tw) / 2 - 26, 968, (W + tw) / 2 + 26, 1026], radius=14, fill=(0, 0, 0))
    d.text(((W - tw) / 2, 976), caption, font=f, fill=(255, 255, 255))
    stage.save(dest)


def main():
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True)

    opts = webdriver.ChromeOptions()
    opts.add_argument("--headless=new")
    opts.add_argument(f"--window-size={int(W / SCALE)},{int(H / SCALE)}")
    opts.add_argument("--hide-scrollbars")
    opts.add_argument(f"--force-device-scale-factor={SCALE}")
    d = webdriver.Chrome(options=opts)
    d.get(PAGE)
    time.sleep(3)  # fonts
    d.execute_script(HELPERS)
    max_y = lambda: d.execute_script("return document.documentElement.scrollHeight - innerHeight")

    chapters, t0, segments = [], 0.0, []
    for i, sc in enumerate(SCENES):
        wav = WORK / f"s{i:02d}.aiff"
        run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(wav), sc["say"]])
        dur = audio_seconds(wav) + 0.8
        n = int(dur * FPS)
        frames = WORK / f"f{i:02d}"
        frames.mkdir()

        for cmd in sc.get("pre", []):
            subprocess.run(cmd, shell=True, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if sc.get("url"):
            d.get(ARCH if sc["url"] == "ARCH" else sc["url"])
            time.sleep(3)
            d.execute_script("window.V = { y(sel, off) { const e = document.querySelector(sel); return e ? Math.max(0, e.getBoundingClientRect().top + scrollY - off) : 0; } };")
        term_text = None
        if sc.get("term"):
            parts = []
            for shown, cmd in sc["term"]:
                out = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True)
                parts.append(f"$ {shown}\n" + (out.stdout + out.stderr).strip())
            term_text = "\n\n".join(parts)

        dark = "dark" if sc.get("dark") or sc.get("phone") else "light"
        d.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": dark}]})
        if sc.get("phone"):
            d.execute_script("document.getElementById('vidcap')?.remove(); document.getElementById('vidcard')?.remove(); scrollTo(0,0)")
            d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": PHONE[0], "height": PHONE[1], "deviceScaleFactor": PHONE[2], "mobile": True})
            time.sleep(0.5)
            end = d.execute_script("return document.getElementById('planH').getBoundingClientRect().top + scrollY - 20")
            shot = WORK / "phone.png"
            for f in range(n):
                d.execute_script("scrollTo(0, arguments[0])", end * ease(f / max(1, n - 1) / 0.85))
                d.save_screenshot(str(shot))
                phone_frame(shot, sc["cap"], frames / f"{f:05d}.png")
            d.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
        else:
            d.execute_script(CARD_JS, sc.get("card"), term_text, 0)
            d.execute_script(CAPTION_JS, sc["cap"])
            acts = list(sc.get("acts", []))
            # actions at fraction 0 run before the camera picks its target, so it frames the result
            while acts and acts[0][0] == 0.0:
                d.execute_script(acts.pop(0)[1])
            start = d.execute_script("return scrollY")
            if sc.get("card") or sc.get("target") == "body":
                end = 0
            elif sc.get("target"):
                end = d.execute_script("return V.y(arguments[0], arguments[1])", sc["target"], sc.get("off", 24))
            else:
                end = start
            end = max(0, min(end, max_y()))
            pan_to = min(d.execute_script("return V.y(arguments[0], 40)", sc["pan"]), max_y()) if sc.get("pan") else None
            for f in range(n):
                t = f / max(1, n - 1)
                while acts and t >= acts[0][0]:
                    d.execute_script(acts.pop(0)[1])
                if pan_to is None or t < 0.5:
                    y = start + (end - start) * ease(t / 0.15)
                else:
                    y = end + (pan_to - end) * ease((t - 0.5) / 0.4)
                d.execute_script("scrollTo(0, arguments[0])", y)
                if term_text is not None:
                    d.execute_script(CARD_JS, "terminal", term_text, t)
                d.save_screenshot(str(frames / f"{f:05d}.png"))

        seg = WORK / f"seg{i:02d}.mp4"
        run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames / "%05d.png"), "-i", str(wav),
             "-af", "apad", "-t", f"{n / FPS:.3f}", "-vf", f"scale={W}:{H},fps=30,format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(seg)])
        segments.append(seg)
        chapters.append((t0, sc["chapter"]))
        t0 += n / FPS
        if sc["id"] == "title":
            Image.open(frames / f"{n - 1:05d}.png").convert("RGB").resize((1280, 720), Image.LANCZOS).save(OUT / "thumbnail.png")
        print(f"scene {i + 1}/{len(SCENES)} {sc['id']}: {n / FPS:.1f}s", flush=True)
    d.quit()

    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segments))
    final = OUT / "yatra-pulse.mp4"
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(final)])

    stamp = lambda s: f"{int(s // 60)}:{int(s % 60):02d}"
    (OUT / "youtube.txt").write_text(f"""TITLE
Mysuru Dasara 2026: Schedule, How to Book, AI Trip Planner & Clash Checker | Yatra Pulse

DESCRIPTION
Yatra Pulse is a live travel board for Mysuru Dasara 2026 (11–21 October). This tour shows every feature: what's on now, the full 11-day schedule with source links, filters, personal picks, DJ and party nights, building an itinerary, Trip check (flags two venues at the same time and whether you can get between stops by car, two-wheeler, auto or bus), Plan with Claude, how to book official tickets, traveller-added events checked by the host, day trips, Gaya Pitru Paksha info, dark mode and phone view. It then covers how it's built: the architecture and the AI agent's goal, the evaluator that keeps the AI honest, telemetry on Apache Pulsar with live metrics and SLOs, and deployment on Kubernetes with Knative.

Official tickets: https://mysoredasara.gov.in (TicketGenie is the only authorised seller)
Dasara helpline: 1077
Code: https://github.com/srilav/yatra-pulse

Chapters
{chr(10).join(f"{stamp(t)} {name}" for t, name in chapters)}

Event times change. Confirm timings and passes with official Mysuru Dasara channels before you travel. Travel times are estimates; check live traffic on the day.

TAGS
Mysuru Dasara 2026, Mysore Dasara, Jamboo Savari, Yuva Dasara, Mysore Dasara schedule, Dasara Gold Card, Dasara tickets booking, Dasara itinerary, Mysore travel, Karnataka tourism, Srirangapatna, Gaya Pitru Paksha, AI trip planner, travel itinerary, RAG, MCP, Apache Pulsar, Knative, Kubernetes
""")
    shutil.rmtree(WORK)
    print(f"\nDone: {final} ({t0:.0f}s)")


if __name__ == "__main__":
    main()
