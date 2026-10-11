"""Build docs/design.html from docs/design.template.html, using index.html's own stylesheet.

    python3 docs/build_design.py

Run it after changing styles in index.html so the design guide stays in sync.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
app = (ROOT / "index.html").read_text()
style = re.search(r"<style>.*?</style>", app, re.S).group(0)
template = (ROOT / "docs" / "design.template.html").read_text()
marker = "<!-- APP_STYLE: the app's own <style> block is copied here by docs/build_design.py -->"
assert marker in template
(ROOT / "docs" / "design.html").write_text(template.replace(marker, "<!-- App stylesheet, copied from index.html -->\n" + style))
print("Wrote docs/design.html")
