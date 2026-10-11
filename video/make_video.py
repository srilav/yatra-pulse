"""Make a narrated 1080p walkthrough video of Musafir Mela for YouTube.

    .venv/bin/python video/make_video.py

Drives index.html in headless Chrome, captures frames per scene, narrates each scene with the
macOS `say` voice (Rishi, en_IN), burns in captions, and writes:
    video/out/musafir-mela.mp4      the video (1920x1080, H.264 + AAC)
    video/out/thumbnail.png         1280x720 YouTube thumbnail
    video/out/youtube.txt           title, description with chapters, and tags
"""

import shutil
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
from PIL import Image
from selenium import webdriver
from selenium.webdriver.common.by import By

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "video" / "out"
WORK = OUT / "work"
PAGE = (ROOT / "index.html").as_uri()
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
FPS = 8
VOICE, RATE = "Rishi", 168
W, H = 1920, 1080
SCALE = 1.5  # render a 1280x720 page at 1.5x so text is readable on phones

# Each scene: narration, caption, and what the camera does.
SCENES = [
    dict(id="title", card="title", say="Musafir Mela. Your live travel board for Mysuru Dasara, twenty twenty-six.",
         cap="Musafir Mela · live travel board for Mysuru Dasara 2026"),
    dict(id="hero", target="top", say="Dasara runs from the eleventh to the twenty-first of October. The board shows which festival day it is, and counts down to the Jamboo Savari elephant procession.",
         cap="11–21 Oct 2026 · day counter and Jamboo Savari countdown"),
    dict(id="now", target="nowH", say="Now and next shows what is live, and what starts soon, using the time in India.",
         cap="Now & next · live status in IST"),
    dict(id="plan", target="planH", action="plan", say="Build your own itinerary. Add any event or day trip with one tap, or let Claude draft a day-by-day plan, using only real events from the board.",
         cap="My itinerary · + Add to plan · Plan with Claude"),
    dict(id="timeline", target="tlH", pan="timeline", action="concerts",
         say="The eleven-day timeline covers the opening at Chamundi Hill, Yuva Dasara concerts with Shankar Ehsaan Loy and Sid Sriram, the Surya Kiran air show, and the Jamboo Savari. Every event links to its source.",
         cap="11-day timeline · every event links to its source"),
    dict(id="picks", target="picks", action="music", say="Choose a travel style for personal picks, and find D J and party nights in Mysuru and Bengaluru.",
         cap="Picks for you · DJ & party nights"),
    dict(id="gaya", target="gayaPanel", say="Planning for Pitru Paksha? The Gaya panel covers the rites, and nearby Bodh Gaya, Rajgir and Nalanda.",
         cap="Gaya · Pitru Paksha · Bodh Gaya, Rajgir, Nalanda"),
    dict(id="community", target="cmH", say="Travellers can add fun events with an official link. The host checks each one before it appears, so the list stays genuine.",
         cap="Fun events from travellers · checked by the host"),
    dict(id="trips", target="trH", say="Go deeper with day trips to Srirangapatna, Somanathapura, Melukote, Shravanabelagola, and more.",
         cap="Day trips from Mysuru"),
    dict(id="end", card="end", say="The board and its code are on GitHub, at srilav slash musafir mela. Happy travels, musafir!",
         cap="github.com/srilav/musafir-mela"),
]

CAPTION_JS = """
let c = document.getElementById('vidcap');
if (!c) {
  c = document.createElement('div'); c.id = 'vidcap';
  c.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9999;max-width:80%;' +
    'background:rgba(20,15,31,.88);color:#fff;font:600 21px/1.3 Mukta,system-ui,sans-serif;padding:8px 18px;' +
    'border-radius:12px;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.25)';
  document.body.appendChild(c);
}
c.textContent = arguments[0]; c.hidden = !arguments[0];
"""

CARD_JS = """
let k = document.getElementById('vidcard');
if (!arguments[0]) { if (k) k.remove(); return; }
if (!k) { k = document.createElement('div'); k.id = 'vidcard'; document.body.appendChild(k); }
k.style.cssText = 'position:fixed;inset:0;z-index:9998;display:grid;place-content:center;gap:12px;text-align:center;' +
  'background:radial-gradient(ellipse at 50% 30%,#2a1f45 0%,#140F1F 70%);color:#F1ECF8;font-family:Mukta,system-ui,sans-serif';
const bulbs = '<div style="height:11px;width:600px;margin:0 auto;background-image:radial-gradient(circle,#FFC857 0 3.5px,transparent 4px);background-size:20px 11px"></div>';
k.innerHTML = arguments[0] === 'title'
  ? bulbs + '<div style="font:400 100px/1 \\'Yatra One\\',serif">Musafir <span style="color:#F2B544">Mela</span></div>' +
    '<div style="font-size:30px;color:#C9BEDD">Mysuru Dasara 2026 · 11–21 October</div>' +
    '<div style="font-size:20px;color:#A99FBE">Live events · Itinerary · AI trip planner · Day trips</div>' + bulbs
  : bulbs + '<div style="font:400 74px/1 \\'Yatra One\\',serif">Happy travels, <span style="color:#F2B544">musafir</span></div>' +
    '<div style="font:500 30px/1.4 \\'JetBrains Mono\\',monospace;color:#F2B544">github.com/srilav/musafir-mela</div>' +
    '<div style="font-size:20px;color:#A99FBE">Times and passes: confirm with official Mysuru Dasara channels</div>' + bulbs;
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
    return t * t * (3 - 2 * t)


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
    d.execute_script("document.getElementById('aMsg').textContent=''")  # offline note isn't relevant on video
    page_y = lambda el_id: d.execute_script(
        "const e=document.getElementById(arguments[0]); return e ? Math.max(0, e.getBoundingClientRect().top + scrollY - 24) : 0", el_id)
    max_y = lambda: d.execute_script("return document.documentElement.scrollHeight - innerHeight")

    chapters, t0, segments = [], 0.0, []
    for i, sc in enumerate(SCENES):
        wav = WORK / f"s{i:02d}.aiff"
        run(["say", "-v", VOICE, "-r", str(RATE), "-o", str(wav), sc["say"]])
        dur = audio_seconds(wav) + 0.8
        n = int(dur * FPS)

        d.execute_script(CARD_JS, sc.get("card"))
        d.execute_script(CAPTION_JS, sc["cap"])
        if sc.get("card"):
            d.execute_script("scrollTo(0,0)")
        start = d.execute_script("return scrollY")
        if sc.get("target") == "top":
            end = 0
        elif sc.get("target") == "picks":
            end = page_y("picks") - 150
        elif sc.get("target") == "gayaPanel":
            end = d.execute_script("const g=document.querySelector('.gaya'); return g.getBoundingClientRect().top + scrollY - 200")
        elif sc.get("target"):
            end = page_y(sc["target"])
        else:
            end = start
        end = max(0, min(end, max_y()))
        pan_to = None
        if sc.get("pan") == "timeline":
            pan_to = min(page_y("timeline") + 1800, max_y())

        frames = WORK / f"f{i:02d}"
        frames.mkdir()
        acted = False
        for f in range(n):
            t = f / max(1, n - 1)
            if pan_to is None:
                y = start + (end - start) * ease(min(1, t / 0.18))
            elif t < 0.15:
                y = start + (end - start) * ease(t / 0.15)
            else:
                y = end + (pan_to - end) * ease((t - 0.15) / 0.85)
            d.execute_script("scrollTo(0, arguments[0])", y)
            if not acted and t > 0.3 and sc.get("action"):
                acted = True
                act = sc["action"]
                if act == "plan":
                    for key in ["ev:13:Yuva Dasara opening night", "ev:15:Sid Sriram live", "trip:Srirangapatna", "ev:21:Jamboo Savari"]:
                        b = d.find_elements(By.CSS_SELECTOR, f'[data-add="{key}"]')
                        if b and b[0].get_attribute("aria-pressed") != "true":
                            d.execute_script("arguments[0].click()", b[0])
                    d.execute_script("document.getElementById('aMsg').textContent=''")
                elif act == "concerts":
                    d.execute_script("document.querySelector('[data-f=\"music\"]').click()")
                elif act == "music":
                    d.execute_script("document.querySelector('[data-v=\"music\"]').click()")
            d.save_screenshot(str(frames / f"{f:05d}.png"))
        if sc.get("action") == "concerts":
            d.execute_script("document.querySelector('[data-f=\"all\"]').click()")

        seg = WORK / f"seg{i:02d}.mp4"
        run([FFMPEG, "-y", "-framerate", str(FPS), "-i", str(frames / "%05d.png"), "-i", str(wav),
             "-af", "apad", "-t", f"{n / FPS:.3f}", "-vf", f"scale={W}:{H},fps=30,format=yuv420p",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", str(seg)])
        segments.append(seg)
        chapters.append((t0, sc["cap"].split(" · ")[0]))
        t0 += n / FPS
        if sc["id"] == "title":
            Image.open(frames / f"{n - 1:05d}.png").convert("RGB").resize((1280, 720), Image.LANCZOS).save(OUT / "thumbnail.png")
        print(f"scene {i + 1}/{len(SCENES)} {sc['id']}: {n / FPS:.1f}s")
    d.quit()

    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segments))
    final = OUT / "musafir-mela.mp4"
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(final)])

    stamp = lambda s: f"{int(s // 60)}:{int(s % 60):02d}"
    chapters[0] = (0.0, "Intro")
    (OUT / "youtube.txt").write_text(f"""TITLE
Mysuru Dasara 2026 Travel Board | Live Events, Itinerary & AI Trip Planner | Musafir Mela

DESCRIPTION
Musafir Mela is a live travel board for Mysuru Dasara 2026 (11–21 October): what's on now, the 11-day schedule with source links, Yuva Dasara concerts, DJ and party nights, a day-by-day itinerary, an AI trip planner that only uses real events, traveller-added events checked by the host, day trips from Mysuru, and Gaya Pitru Paksha info.

Code: https://github.com/srilav/musafir-mela

Chapters
{chr(10).join(f"{stamp(t)} {name}" for t, name in chapters)}

Event times change. Confirm timings and passes with official Mysuru Dasara channels before you travel.

TAGS
Mysuru Dasara 2026, Mysore Dasara, Jamboo Savari, Yuva Dasara, Mysore travel, Karnataka tourism, Dasara itinerary, Srirangapatna, Gaya Pitru Paksha, AI trip planner, travel itinerary
""")
    shutil.rmtree(WORK)
    print(f"\nDone: {final} ({t0:.0f}s)")


if __name__ == "__main__":
    main()
