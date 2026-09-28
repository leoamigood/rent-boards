"""Fill the boards' blank CARTO_TOKEN slot from the environment, in place.

Run by .github/workflows/pages.yml against the checked-out docs/ tree just
before it is uploaded, so the token lives in the deployed page and nowhere in
the repository. Carto's raster basemaps serve anonymously too, so an unset
secret is a warning rather than an error — the map still draws.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SLOT = 'var CARTO_TOKEN="";'


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "docs")
    token = os.environ.get("CARTO_TOKEN", "").strip()
    if not token:
        print("CARTO_TOKEN is unset — basemap tiles stay anonymous")
        return 0
    if '"' in token or "\\" in token:
        print("CARTO_TOKEN contains a quote or backslash; refusing to inject")
        return 1

    filled = 0
    for page in sorted(root.rglob("index.html")):
        text = page.read_text(encoding="utf-8")
        if SLOT not in text:
            continue
        page.write_text(text.replace(SLOT, f'var CARTO_TOKEN="{token}";'),
                        encoding="utf-8")
        filled += 1
        print(f"  {page}: token injected")

    # A board that lost the slot would deploy silently token-less; say so.
    if not filled:
        print(f"no page under {root}/ carries {SLOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
