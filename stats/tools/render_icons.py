"""Re-render the achievement badge PNGs (used as Discord thumbnails) after editing icons.py.

    pip install playwright && playwright install chromium
    python tools/render_icons.py            # from the stats/ folder

Writes cs2stats/web/ach/<id>.png (128x128, transparent). The website uses the SVGs directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright  # noqa: E402

from cs2stats import community, icons  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cs2stats", "web", "ach")

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    exe = os.environ.get("CHROME")
    with sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        pg = b.new_page(viewport={"width": 128, "height": 128})
        for aid in community.ACH:
            pg.set_content(f'<html><body style="margin:0;background:transparent">{icons.svg(aid, 128)}</body></html>')
            pg.locator("svg").screenshot(path=os.path.join(OUT, f"{aid}.png"), omit_background=True)
        b.close()
    print(f"wrote {len(community.ACH)} badges to {OUT}")
