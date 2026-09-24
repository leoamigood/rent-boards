"""Assemble docs/ for GitHub Pages.

The boards are written once and published twice. Inside a Claude artifact the
sandbox blocks third-party tiles, so the Da Nang map falls back to the drawn
SVG; here Leaflet and Carto's basemap are injected and it becomes a real map.
Contacts are stripped from every public copy.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

LEAFLET = (
    '<link rel="stylesheet" '
    'href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">\n'
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js">'
    "</script>"
)

# board directory -> (published path, db, chat, extra build flags)
BOARDS = [
    ("dashboard",    "batumi",  "data/rentals.db", "batumiarendachat", []),
    ("dashboard-vn", "vietnam", "data/vietnam.db", "Rent_Vietnam",
     ["--split-cities", "--with-wanted"]),
    # Several cities in one database, so the dedupe key must include the city:
    # without it a $500 two-bed in Da Nang merges with one in Saigon.
    ("dashboard-dn", "coast",   "data/danang.db",  "chotot:danang",
     ["--split-cities"]),
]

INDEX = """<title>Rent Boards</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&display=swap">
<style>
:root{color-scheme:light;--bg:#F0F2F5;--fg:#141A22;--mut:#4C5663;--line:#DCE1E8;--card:#FBFBFC}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0E1218;--fg:#E7EBF1;--mut:#A6B0BD;--line:#242C38;--card:#141821}}
body{background:var(--bg);color:var(--fg);font-family:"DM Sans",system-ui,sans-serif;
     margin:0;line-height:1.5}
.w{max-width:760px;margin:0 auto;padding-inline:20px;padding-block:56px}
h1{font-size:32px;margin:0 0 8px}
p{color:var(--mut);margin:0 0 28px;max-width:60ch}
a.card{display:block;background:var(--card);border:1px solid var(--line);
  border-radius:8px;padding:18px 20px;margin-bottom:12px;text-decoration:none;color:inherit}
a.card:hover{border-color:#1D6FB8}
b{display:block;font-size:17px;margin-bottom:3px}
span{color:var(--mut);font-size:13.5px}
</style>
<div class="w">
<h1>Rent boards</h1>
<p>Rental listings collected from Telegram chats and Vietnamese classifieds,
de-duplicated and ranked by value. Contact details are left out of these public
copies; each listing links back to its original post.</p>
<a class="card" href="coast/"><b>Vietnam cities</b><span>Da Nang, Saigon, Nha Trang, Hoi An and Phu Quoc — from Nhatot and muaban, on a map</span></a>
<a class="card" href="vietnam/"><b>Vietnam</b><span>Flats offered and wanted across a dozen cities, 14 months</span></a>
<a class="card" href="batumi/"><b>Batumi</b><span>A week of the Georgian rental chat</span></a>
</div>
"""


# Paths that have moved. GitHub Pages serves static files only, so the old
# location gets a stub that forwards rather than a 404 for anyone holding the
# earlier link.
REDIRECTS = {"danang": "coast"}

REDIRECT_PAGE = """<title>Moved</title>
<link rel="canonical" href="../{to}/">
<meta http-equiv="refresh" content="0; url=../{to}/">
<p style="font:15px/1.5 system-ui;margin:3rem">This board moved to
<a href="../{to}/">/{to}/</a>.</p>
"""


def _has_data(db: Path, chat: str) -> bool:
    """True when this database actually holds listings for the board's source."""
    if not db.exists():
        return False
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            return bool(conn.execute(
                "SELECT 1 FROM listings WHERE chat=? LIMIT 1", (chat,)).fetchone())
    except sqlite3.Error:
        return False


def main() -> int:
    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / ".nojekyll").write_text("")
    (DOCS / "index.html").write_text(INDEX, encoding="utf-8")

    for src, slug, db, chat, extra in BOARDS:
        out = DOCS / slug
        # The databases are gitignored, so a checkout usually holds only some of
        # them. Rebuild the boards whose data is here and leave the published
        # copy of the rest alone: emptying docs/ first meant one missing
        # database took every other board down with it.
        if not _has_data(ROOT / db, chat):
            print(f"  {slug}/  skipped — no listings for {chat} in {db}")
            continue
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        cmd = [sys.executable, str(ROOT / "tools" / "build_dashboard.py"),
               "--chat", chat, "--db", db, "--out", str(out.relative_to(ROOT)),
               "--strip-contacts", *extra]
        subprocess.run(cmd, cwd=ROOT, check=True)

        page = (ROOT / src / "index.html").read_text(encoding="utf-8")
        page = page.replace("<!--LEAFLET-->", LEAFLET)
        (out / "index.html").write_text(page, encoding="utf-8")
        print(f"  {slug}/  {'with Leaflet' if LEAFLET in page else 'no map'}")

    for old, new in REDIRECTS.items():
        stub = DOCS / old
        stub.mkdir(parents=True, exist_ok=True)
        (stub / "index.html").write_text(REDIRECT_PAGE.format(to=new), encoding="utf-8")
        print(f"  {old}/ -> {new}/  (redirect)")

    total = sum(f.stat().st_size for f in DOCS.rglob("*") if f.is_file())
    print(f"docs/ built — {total // 1024} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
