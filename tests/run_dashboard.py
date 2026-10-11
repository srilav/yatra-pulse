"""Open Yatra Pulse in Chrome with Selenium, walk through the main features, and keep the window open.

Usage:
    .venv/bin/python tests/run_dashboard.py            # holds the window for 10 s at the end
    .venv/bin/python tests/run_dashboard.py --hold 30  # hold longer
    .venv/bin/python tests/run_dashboard.py --pause 3  # linger 3 s on each step
    .venv/bin/python tests/run_dashboard.py --url https://xxxx.cloudfront.net/ \
        --record deploy/aws/results/key-scenarios.mp4 --report deploy/aws/results/selenium.json

--url tests a deployed board instead of the local file. --record saves a captioned video of every
step (pass or fail) and --report a JSON summary. By default it runs index.html straight from disk. Traveller submissions and "Plan with Claude" need the
claude.ai viewer, so here they show their signed-out messages; everything else works.
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "index.html").as_uri()
SHOTS = ROOT / "screenshots"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=10, help="seconds to keep the browser open at the end")
    ap.add_argument("--pause", type=float, default=2, help="seconds to linger on each step")
    ap.add_argument("--url", default=PAGE, help="board to test (default: local index.html)")
    ap.add_argument("--record", help="save a captioned MP4 of the run here")
    ap.add_argument("--report", help="save a JSON summary here")
    args = ap.parse_args()
    frames, results = [], []

    SHOTS.mkdir(exist_ok=True)
    opts = webdriver.ChromeOptions()
    opts.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(options=opts)  # Selenium Manager finds the matching chromedriver
    wait = WebDriverWait(driver, 10)
    failures = []

    def snap(caption, ok=True):
        if args.record:
            frames.append((driver.get_screenshot_as_png(), caption, ok))

    def step(name, fn):
        started = time.perf_counter()
        try:
            fn()
            print(f"PASS  {name}")
            results.append({"name": name, "ok": True, "seconds": round(time.perf_counter() - started, 2)})
            snap(f"PASS  {name}")
        except Exception as exc:  # keep going so one failure doesn't hide the rest
            failures.append(name)
            print(f"FAIL  {name}: {exc}")
            results.append({"name": name, "ok": False, "error": str(exc)[:300], "seconds": round(time.perf_counter() - started, 2)})
            snap(f"FAIL  {name}", ok=False)
        time.sleep(args.pause)

    def scroll_to(el_id):
        el = driver.find_element(By.ID, el_id)
        driver.execute_script("arguments[0].scrollIntoView({block: 'start', behavior: 'smooth'})", el)
        time.sleep(0.6)
        return el

    try:
        driver.get(args.url)

        def loads():
            wait.until(EC.title_is("Yatra Pulse"))
            wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "#timeline .day"))
            assert len(driver.find_elements(By.CSS_SELECTOR, "#timeline .day")) == 11
            driver.save_screenshot(str(SHOTS / "01-home.png"))

        def now_and_next():
            cards = driver.find_elements(By.CSS_SELECTOR, "#nowGrid .ncard, #nowGrid .empty")
            assert cards, "Now & next is empty"

        def source_links():
            links = driver.find_elements(By.CSS_SELECTOR, "#timeline a.srclink")
            assert links and all(a.get_attribute("href").startswith("https://") for a in links)

        def filter_concerts():
            scroll_to("tlH")
            driver.find_element(By.CSS_SELECTOR, '[data-f="music"]').click()
            cats = {e.get_attribute("data-cat") for e in driver.find_elements(By.CSS_SELECTOR, "#timeline .ev")}
            assert cats == {"music"}, cats
            driver.save_screenshot(str(SHOTS / "02-concerts.png"))
            driver.find_element(By.CSS_SELECTOR, '[data-f="all"]').click()

        def add_to_plan():
            btn = driver.find_element(By.CSS_SELECTOR, '#timeline [data-add^="ev:21:Jamboo"]')
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", btn)
            btn.click()
            scroll_to("planH")
            wait.until(lambda d: "Jamboo Savari" in d.find_element(By.ID, "planList").text)
            driver.save_screenshot(str(SHOTS / "03-itinerary.png"))

        def day_trip_to_plan():
            btn = driver.find_element(By.CSS_SELECTOR, '#trips [data-add="trip:Srirangapatna"]')
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'})", btn)
            btn.click()
            assert "Srirangapatna" in driver.find_element(By.ID, "planList").text

        def remove_from_plan():
            scroll_to("planH")
            driver.find_element(By.CSS_SELECTOR, '[data-rm="trip:Srirangapatna"]').click()
            assert "Srirangapatna" not in driver.find_element(By.ID, "planList").text

        def picks_switch():
            driver.find_element(By.CSS_SELECTOR, '[data-v="music"]').click()
            assert "Yuva Dasara" in driver.find_element(By.ID, "picks").text or driver.find_elements(By.CSS_SELECTOR, "#picks li")

        def traveller_form_signed_out():
            scroll_to("cmH")
            driver.find_element(By.ID, "fSubmit").click()
            msg = driver.find_element(By.ID, "fMsg").text
            assert "Give the event a name" in msg, msg  # checks come before sign-in
            driver.find_element(By.ID, "fTitle").send_keys("Test event")
            driver.find_element(By.ID, "fUrl").send_keys("https://bit.ly/abc")
            driver.find_element(By.ID, "fSubmit").click()
            assert "Short links" in driver.find_element(By.ID, "fMsg").text
            driver.find_element(By.ID, "fUrl").clear()
            driver.find_element(By.ID, "fUrl").send_keys("https://example.org/event")
            driver.find_element(By.ID, "fPlace").send_keys("Test venue, Mysuru")
            driver.find_element(By.ID, "fWrite").send_keys("A test write-up that is long enough to pass the forty character check.")
            driver.find_element(By.ID, "fTrue").click()
            driver.find_element(By.ID, "fSubmit").click()
            msg = driver.find_element(By.ID, "fMsg").text
            assert "Sign in" in msg, msg
            driver.save_screenshot(str(SHOTS / "04-traveller-events.png"))

        def add(key):
            btn = driver.find_element(By.CSS_SELECTOR, f'[data-add="{key}"]')
            if btn.get_attribute("aria-pressed") != "true":
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'}); arguments[0].click()", btn)

        def legs(status):
            return [e.text for e in driver.find_elements(By.CSS_SELECTOR, f'#planList .leg[data-status="{status}"]')]

        def clash_different_venues():
            # 17 Oct: Jasmine Sandlas at Uttanahalli and the drone show at Bannimantap overlap in the evening
            add("ev:17:Jasmine Sandlas closing night")
            add("ev:17:Drone show")
            scroll_to("planH")
            bad = legs("bad")
            assert any("Clash" in t for t in bad), bad
            assert "problem" in driver.find_element(By.ID, "pCheck").text.lower()
            driver.save_screenshot(str(SHOTS / "05-trip-check-clash.png"))

        def toast_on_add():
            toast = driver.find_element(By.ID, "toast")
            assert toast.is_displayed() and "Clash" in toast.text, toast.text

        def same_venue_is_fine():
            # 21 Oct: Jamboo Savari at the palace, then the torchlight parade at Bannimantap
            add("ev:21:Jamboo Savari")
            add("nt:Palace illumination")
            texts = " ".join(legs("ok") + legs("warn") + legs("bad"))
            assert "Jamboo Savari seats: be inside by" in texts, texts

        def travel_time_shown():
            add("ev:21:Torchlight parade")
            texts = legs("ok") + legs("warn") + legs("bad")
            assert any("km" in t and "min by car" in t for t in texts), texts

        def auto_too_far():
            driver.execute_script("document.getElementById('pDay').value='19'")
            add("trip:Shravanabelagola")
            Select(driver.find_element(By.ID, "pMode")).select_by_value("auto")
            bad = legs("bad")
            assert any("too far for an auto-rickshaw" in t for t in bad), bad
            Select(driver.find_element(By.ID, "pMode")).select_by_value("car")
            assert not any("too far" in t for t in legs("bad"))

        def book_link_warns():
            link = driver.find_element(By.CSS_SELECTOR, '[data-book="ev:17:Drone show"]')
            driver.execute_script("arguments[0].addEventListener('click', e => e.preventDefault(), {once: true}); arguments[0].scrollIntoView({block: 'center'}); arguments[0].click()", link)
            toast = driver.find_element(By.ID, "toast").text
            assert toast.startswith("Before you book") and "Clash" in toast, toast
            driver.save_screenshot(str(SHOTS / "06-book-warning.png"))

        def board_search():
            hits = driver.execute_script("return searchBoard('Sid Sriram concert', {k: 3}).map(h => h.key)")
            assert hits[0] == "ev:15:Sid Sriram live", hits
            facts = driver.execute_script("return searchBoard('Gold Card price', {kinds: ['kb'], k: 1})[0].text")
            assert "6,961" in facts, facts

        step("Page loads with all 11 Dasara days", loads)
        step("Board search (RAG) ranks the right passages", board_search)
        step("Now & next shows events", now_and_next)
        step("Timeline events link to https sources", source_links)
        step("Concerts filter shows only concerts", filter_concerts)
        step("Add Jamboo Savari to my itinerary", add_to_plan)
        step("Add a day trip to my itinerary", day_trip_to_plan)
        step("Remove a stop from my itinerary", remove_from_plan)
        step("Picks change with travel style", picks_switch)
        step("Traveller form checks entries, then asks to sign in", traveller_form_signed_out)
        step("Trip check flags a clash at different venues", clash_different_venues)
        step("Adding a clashing event shows a warning", toast_on_add)
        step("Jamboo Savari gate time is flagged", same_venue_is_fine)
        step("Travel distance and time are shown between stops", travel_time_shown)
        step("Far day trip is flagged for an auto-rickshaw", auto_too_far)
        step("Book tickets warns about a clash first", book_link_warns)

        total = len(results)
        print(f"\n{total - len(failures)}/{total} passed against {args.url}. Screenshots in {SHOTS}")
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps({"url": args.url, "ran_at": datetime.now(timezone.utc).isoformat(),
                                                     "passed": total - len(failures), "total": total, "steps": results}, indent=2))
            print(f"Report: {args.report}")
        print(f"Keeping the browser open for {args.hold:g} s…")
        driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'})")
        time.sleep(args.hold)
    finally:
        driver.quit()
        if args.record and frames:
            write_video(frames, Path(args.record), args.url)
            print(f"Recording: {args.record}")
    return 1 if failures else 0


def write_video(frames, path, url, seconds_per_step=2.5):
    """Captioned MP4: each step's screenshot with a PASS/FAIL bar, plus a title card."""
    import io
    import imageio_ffmpeg
    import subprocess
    import tempfile
    from PIL import Image, ImageDraw, ImageFont

    def font(size):
        for f in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
            if Path(f).exists():
                return ImageFont.truetype(f, size)
        return ImageFont.load_default()

    W, H = 1280, 800
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        title = Image.new("RGB", (W, H), (20, 15, 31))
        d = ImageDraw.Draw(title)
        d.text((60, 300), "Yatra Pulse · key scenarios", font=font(54), fill=(242, 181, 68))
        d.text((60, 390), url, font=font(26), fill=(241, 236, 248))
        d.text((60, 440), datetime.now(timezone.utc).strftime("Recorded %d %b %Y %H:%M UTC · Selenium"), font=font(22), fill=(169, 159, 190))
        title.save(f"{tmp}/00000.png")
        for i, (png, caption, ok) in enumerate(frames, 1):
            img = Image.open(io.BytesIO(png)).convert("RGB").resize((W, H - 60))
            canvas = Image.new("RGB", (W, H), (20, 15, 31))
            canvas.paste(img, (0, 0))
            d = ImageDraw.Draw(canvas)
            d.rectangle([0, H - 60, W, H], fill=(29, 127, 84) if ok else (179, 38, 62))
            d.text((24, H - 46), f"{i}/{len(frames)}  {caption}", font=font(26), fill=(255, 255, 255))
            canvas.save(f"{tmp}/{i:05d}.png")
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(1 / seconds_per_step),
                        "-i", f"{tmp}/%05d.png", "-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-crf", "22", str(path)], check=True)


if __name__ == "__main__":
    sys.exit(main())
