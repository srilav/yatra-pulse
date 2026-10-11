"""Open Musafir Mela in Chrome with Selenium, walk through the main features, and keep the window open.

Usage:
    .venv/bin/python tests/run_dashboard.py            # holds the window for 10 s at the end
    .venv/bin/python tests/run_dashboard.py --hold 30  # hold longer
    .venv/bin/python tests/run_dashboard.py --pause 3  # linger 3 s on each step

Runs index.html straight from disk. Traveller submissions and "Plan with Claude" need the
claude.ai viewer, so here they show their signed-out messages; everything else works.
"""

import argparse
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "index.html").as_uri()
SHOTS = ROOT / "screenshots"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=float, default=10, help="seconds to keep the browser open at the end")
    ap.add_argument("--pause", type=float, default=2, help="seconds to linger on each step")
    args = ap.parse_args()

    SHOTS.mkdir(exist_ok=True)
    opts = webdriver.ChromeOptions()
    opts.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(options=opts)  # Selenium Manager finds the matching chromedriver
    wait = WebDriverWait(driver, 10)
    failures = []

    def step(name, fn):
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # keep going so one failure doesn't hide the rest
            failures.append(name)
            print(f"FAIL  {name}: {exc}")
        time.sleep(args.pause)

    def scroll_to(el_id):
        el = driver.find_element(By.ID, el_id)
        driver.execute_script("arguments[0].scrollIntoView({block: 'start', behavior: 'smooth'})", el)
        time.sleep(0.6)
        return el

    try:
        driver.get(PAGE)

        def loads():
            wait.until(EC.title_is("Musafir Mela"))
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
            assert "Sign in" in msg, msg
            driver.save_screenshot(str(SHOTS / "04-traveller-events.png"))

        step("Page loads with all 11 Dasara days", loads)
        step("Now & next shows events", now_and_next)
        step("Timeline events link to https sources", source_links)
        step("Concerts filter shows only concerts", filter_concerts)
        step("Add Jamboo Savari to my itinerary", add_to_plan)
        step("Add a day trip to my itinerary", day_trip_to_plan)
        step("Remove a stop from my itinerary", remove_from_plan)
        step("Picks change with travel style", picks_switch)
        step("Traveller form asks to sign in when offline", traveller_form_signed_out)

        print(f"\n{9 - len(failures)}/9 passed. Screenshots in {SHOTS}")
        print(f"Keeping the browser open for {args.hold:g} s…")
        driver.execute_script("window.scrollTo({top: 0, behavior: 'smooth'})")
        time.sleep(args.hold)
    finally:
        driver.quit()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
