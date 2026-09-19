"""Write data.js for the dashboard from the collected listings.

Run after a fetch to refresh the local dashboard:
    .venv/bin/python tools/build_dashboard.py

The public copy on GitHub Pages leaves the posters' contact details out — they
put those in a group chat, not on the open web. The Telegram link still reaches
them:
    .venv/bin/python tools/build_dashboard.py --out docs --strip-contacts
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from batumi_rent import analyze, config, db  # noqa: E402

TEXT_LIMIT = 600

# Clearing the phone/handle fields is not enough: most ads repeat the number in
# the body of the post, so the text itself has to be redacted too.
_DIGIT_RUN = re.compile(r"\+?\d[\d\s\-()]{7,}\d")
_HANDLE = re.compile(r"@[A-Za-z][A-Za-z0-9_]{4,31}")


# Classifieds text arrives with the odd replacement/object character and
# stray control codes; they carry no meaning and break the publish step.
_JUNK_CHARS = {0xFFFD, 0xFFFC, 0x0B, 0x0C,
               *range(0x00, 0x09), *range(0x0E, 0x20)}
_JUNK_MAP = dict.fromkeys(_JUNK_CHARS)


def clean(text: str) -> str:
    return text.translate(_JUNK_MAP)


def scrub(text: str) -> str:
    def hide(m: re.Match[str]) -> str:
        return "[contact hidden]" if sum(c.isdigit() for c in m.group(0)) >= 9 else m.group(0)
    return _HANDLE.sub("[handle hidden]", _DIGIT_RUN.sub(hide, text))


def main() -> int:
    ap = argparse.ArgumentParser(description="Build data.js for the dashboard.")
    ap.add_argument("--out", default="dashboard",
                    help="directory to write data.js into (default: dashboard)")
    ap.add_argument("--strip-contacts", action="store_true",
                    help="omit phone numbers and @handles from the output")
    ap.add_argument("--chat", help="override TG_CHAT")
    ap.add_argument("--db", help="override DB_PATH")
    ap.add_argument("--split-cities", action="store_true",
                    help="dedupe per city (use for chats covering several)")
    ap.add_argument("--with-wanted", action="store_true",
                    help="also export the 'wanted' posts, where people say what "
                         "they are looking for and what they will pay")
    args = ap.parse_args()

    cfg = config.load(chat_override=args.chat, db_override=args.db)
    conn = db.connect(cfg.db_path)
    deals = ["rent_offer"] + (["rent_seek"] if args.with_wanted else [])
    rows = []
    for deal in deals:
        rows += analyze.query(conn, analyze.Filters(deal=deal,
                                                    split_by_city=args.split_cities),
                              sort="date", limit=1_000_000)

    out = []
    for r in rows:
        text = clean((r["text"] or "").strip())
        if args.strip_contacts:
            text = scrub(text)
        out.append({
            "id": r["msg_id"],
            "deal": r["deal_type"],
            "d": r["date_utc"][:10],
            "p": r["price_usd"],
            "raw": r["price"],
            "cur": r["currency"],
            "city": r["city"],
            "kind": r["kind"],
            "bd": r["bedrooms"],
            "a": r["area_sqm"],
            "r": r["rooms"],
            "lay": r["layout"],
            "fl": r["floor"],
            "ft": r["floors_total"],
            "loc": r["address"] or r["district"],
            "dist": r["district"],
            "cx": r["complex_name"],
            "ppm": r["usd_per_sqm"],
            "t": r["term"],
            "sv": 1 if r["sea_view"] else 0,
            "fu": r["furnished"],
            "pk": 1 if r["parking"] else 0,
            "pet": r["pets"],
            "ag": r["is_agent"],
            "ph": None if args.strip_contacts else r["phone"],
            "un": None if args.strip_contacts else (
                r["contact"] or (f"@{r['sender_username']}" if r["sender_username"] else None)),
            "np": r["n_photos"],
            "txt": text[:TEXT_LIMIT] + ("…" if len(text) > TEXT_LIMIT else ""),
            "url": r["link"],
        })

    span = conn.execute("SELECT MIN(date_utc) a, MAX(date_utc) b FROM messages").fetchone()
    meta = {
        "chat": cfg.chat,
        "deals": deals,
        "messages": conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"],
        "posts": conn.execute(
            "SELECT COUNT(*) n FROM listings WHERE deal_type='rent_offer'").fetchone()["n"],
        "from": span["a"][:10], "to": span["b"][:10],
        "built": db.now_utc()[:16].replace("T", " "),
        "contacts": not args.strip_contacts,
    }

    target = ROOT / args.out / "data.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "window.LISTINGS=" + json.dumps(out, ensure_ascii=False, separators=(",", ":")) +
        ";\nwindow.META=" + json.dumps(meta, ensure_ascii=False) + ";\n",
        encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} — {len(out)} flats, "
          f"{target.stat().st_size // 1024} KB"
          f"{' (contacts stripped)' if args.strip_contacts else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
