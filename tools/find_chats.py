"""Search Telegram for chats that carry rental posts for a given city.

Ranks candidates by what they actually contain rather than by name: how many
recent messages parse as rental ads, and how many mention the city at all.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from telethon import functions, errors            # noqa: E402
from telethon.tl.types import Channel, Chat       # noqa: E402

from batumi_rent import collect, config, parser    # noqa: E402

CITIES = {
    "danang": {
        "queries": ["Дананг", "Дананг аренда", "Дананг жилье", "Da Nang rent",
                    "Danang rent", "Da Nang apartment", "Дананг чат",
                    "thuê nhà Đà Nẵng", "Danang expats", "Дананг Вьетнам"],
        "words": ("дананг", "да нанг", "da nang", "danang", "đà nẵng", "днг"),
    },
    "hoian": {
        "queries": ["Хойан", "Хой Ан", "Hoi An", "Hoi An rent", "Hoi An apartment",
                    "Hoi An housing", "Хойан аренда", "thuê nhà Hội An",
                    "Hoi An expats", "Хойан жилье"],
        "words": ("хойан", "хой ан", "hoi an", "hội an", "hoian", "хой-ан"),
    },
    "phuquoc": {
        "queries": ["Фукуок", "Фу Куок", "Phu Quoc", "Phu Quoc rent",
                    "Phu Quoc apartment", "Фукуок аренда", "thuê nhà Phú Quốc",
                    "Phu Quoc housing", "Фукуок жилье", "Phu Quoc expats"],
        "words": ("фукуок", "фу куок", "phu quoc", "phú quốc", "phuquoc", "фу-куок"),
    },
    "hcmc": {
        "queries": ["Хошимин", "Сайгон", "Хошимин аренда", "Сайгон аренда",
                    "Ho Chi Minh rent", "Saigon rent", "Saigon apartment",
                    "thuê nhà Sài Gòn", "Saigon expats", "Хошимин жилье",
                    "HCMC apartment", "Сайгон чат"],
        "words": ("хошимин", "сайгон", "ho chi minh", "saigon", "hcmc",
                  "sài gòn", "sai gon", "хчм"),
    },
    "nhatrang": {
        "queries": ["Нячанг", "Нячанг аренда", "Nha Trang rent",
                    "Nha Trang apartment", "Нячанг жилье", "thuê nhà Nha Trang"],
        "words": ("нячанг", "nha trang", "nhatrang", "нha trang"),
    },
}
SAMPLE = 120


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sample", type=int, default=SAMPLE,
                    help="messages to read per candidate")
    ap.add_argument("--limit", type=int, default=30, help="candidates to inspect")
    ap.add_argument("--city", default="danang", choices=sorted(CITIES),
                    help="which city's searches to run")
    args = ap.parse_args()
    queries = CITIES[args.city]["queries"]
    city_words = CITIES[args.city]["words"]

    cfg = config.load()
    client = collect.make_client(cfg)
    await client.connect()
    if not await client.is_user_authorized():
        print("Not signed in — run: python -m batumi_rent login")
        return 1

    seen: dict[str, object] = {}
    for q in queries:
        try:
            res = await client(functions.contacts.SearchRequest(q=q, limit=30))
        except errors.FloodWaitError as exc:
            print(f"rate limited, waiting {exc.seconds}s")
            await asyncio.sleep(exc.seconds)
            continue
        for chat in res.chats:
            if isinstance(chat, (Channel, Chat)) and getattr(chat, "username", None):
                seen.setdefault(chat.username, chat)
        await asyncio.sleep(1.0)

    print(f"{len(seen)} public chats matched the searches; reading the newest "
          f"{args.sample} messages of each\n")
    rows = []
    for username, chat in list(seen.items())[: args.limit]:
        try:
            msgs = await client.get_messages(chat, limit=args.sample)
        except (errors.RPCError, ValueError) as exc:
            rows.append((username, getattr(chat, "title", "?"), None, None, None, None,
                         type(exc).__name__))
            continue
        texts = [m.message for m in msgs if getattr(m, "message", None)]
        if not texts:
            continue
        listings = sum(1 for t in texts if parser.is_listing(t))
        city = sum(1 for t in texts if any(w in t.lower() for w in city_words))
        both = sum(1 for t in texts
                   if parser.is_listing(t) and any(w in t.lower() for w in city_words))
        dates = [m.date for m in msgs if getattr(m, "date", None)]
        span_days = max((max(dates) - min(dates)).days, 1) if len(dates) > 1 else 1
        per_day = len(msgs) / span_days
        rows.append((username, (getattr(chat, "title", "") or "")[:34], len(texts),
                     listings, city, both, f"{per_day:.0f}/day"))
        await asyncio.sleep(0.8)

    rows.sort(key=lambda r: (r[5] or 0), reverse=True)
    print(f"{'chat':<26}{'title':<36}{'msgs':>5}{'ads':>5}{'city':>6}{'both':>6}  rate")
    for username, title, n, listings, city, both, rate in rows:
        if n is None:
            print(f"{'@'+username:<26}{title:<36}   unreadable ({rate})")
            continue
        print(f"{'@'+username:<26}{title:<36}{n:>5}{listings:>5}{city:>6}{both:>6}  {rate}")
    await client.disconnect()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
