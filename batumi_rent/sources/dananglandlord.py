"""dananglandlord.com — a Da Nang letting agency, read through WordPress.

The site publishes each flat as a `property` post, and WordPress exposes those
over its own REST API. That is public, paginated and far lighter than parsing
the rendered pages, and robots.txt permits it (`Allow: /`, with only wp-admin
and wp-content disallowed).

Custom fields are not exposed, so price, size and bedrooms come out of the post
text — which is written in plain English and parses cleanly: price is quoted in
both dong and dollars, area in m², bedrooms in the title.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

BASE = "https://dananglandlord.com/wp-json/wp/v2/property"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PER_PAGE = 50
PAUSE = 1.2
CITY = "Da Nang"

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                  # pragma: no cover
    _SSL = ssl.create_default_context()

_TAGS = re.compile(r"<[^>]+>")
_ENTITY = re.compile(r"&#(\d+);|&([a-z]+);")
_ENTITIES = {"amp": "&", "nbsp": " ", "quot": '"', "lt": "<", "gt": ">",
             "hellip": "…", "rsquo": "’", "lsquo": "‘", "ldquo": "“", "rdquo": "”"}


def _text(html: str) -> str:
    def entity(m: re.Match[str]) -> str:
        if m.group(1):
            try:
                return chr(int(m.group(1)))
            except ValueError:
                return ""
        return _ENTITIES.get(m.group(2) or "", "")
    return " ".join(_ENTITY.sub(entity, _TAGS.sub(" ", html or "")).split())


def _get(url: str, attempts: int = 3) -> Any:
    last: Exception | None = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=45, context=_SSL) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(2 * (n + 1))
    raise last if last else RuntimeError("unreachable")


def iter_listings(limit: int | None = None, max_pages: int = 60,
                  max_age_days: int | None = 120) -> Iterator[dict[str, Any]]:
    """Newest first; stops once a whole page predates `max_age_days`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
              if max_age_days else None)
    got = 0
    for page in range(1, max_pages + 1):
        items = _get(f"{BASE}?per_page={PER_PAGE}&page={page}"
                     f"&orderby=date&order=desc&_fields=id,date_gmt,link,title,content")
        if not items:
            break
        newest: datetime | None = None
        for it in items:
            try:
                posted = datetime.fromisoformat(it["date_gmt"]).replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                posted = datetime.now(timezone.utc)
            if newest is None or posted > newest:
                newest = posted
            title = _text((it.get("title") or {}).get("rendered", ""))
            body = _text((it.get("content") or {}).get("rendered", ""))
            if not title and not body:
                continue
            yield {
                "id": int(it["id"]),
                "url": it.get("link") or "",
                "title": title,
                "body": body[:1500],
                "posted": posted.isoformat(timespec="seconds"),
                "city": CITY,
                "_src": "dananglandlord",
            }
            got += 1
            if limit and got >= limit:
                return
        if cutoff and newest and newest < cutoff:
            print(f"  stopping at page {page}: posts are older than {max_age_days} days")
            return
        time.sleep(PAUSE)


def message_row(item: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "chat": source, "msg_id": item["id"], "date_utc": item["posted"],
        "edit_date": None, "sender_id": None, "sender_username": None,
        "sender_name": "Da Nang Landlord",
        "text": item["title"] + "\n" + item["body"],
        "grouped_id": None, "reply_to": None, "n_photos": 0,
        "link": item["url"],
        "raw": json.dumps({"_src": "dananglandlord", "city": item["city"]},
                          ensure_ascii=False),
        "fetched_at": None,
    }
