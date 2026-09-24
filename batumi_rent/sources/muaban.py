"""Muaban.net — a Vietnamese classifieds marketplace, and a live one.

Chosen over the property portals on freshness. Mogi, bds123 and the rest serve
mostly year-old stock past the first page or two; muaban's Da Nang rental pages
are still entirely same-day several pages deep, which is what makes a board
worth reading.

robots.txt is `Allow: /` with a published sitemap and only /dashboard/ off
limits, so the public listing pages are fair game. Each page embeds its
listings as JSON in Next.js's __NEXT_DATA__, so there is no HTML scraping: the
structured record is read directly.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

BASE = "https://muaban.net"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAUSE = 1.4

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                  # pragma: no cover
    _SSL = ssl.create_default_context()

# slug, city label, and the substring a listing's location must contain when
# the slug covers a whole province rather than the city itself.
REGIONS: dict[str, tuple[str, str, str | None]] = {
    "danang":   ("da-nang", "Da Nang", None),
    "hanoi":    ("ha-noi", "Hanoi", None),
    "hcmc":     ("ho-chi-minh", "Ho Chi Minh", None),
    "nhatrang": ("khanh-hoa", "Nha Trang", "Nha Trang"),
    "hoian":    ("quang-nam", "Hoi An", "Hội An"),
    "phuquoc":  ("kien-giang", "Phu Quoc", "Phú Quốc"),
    # The province is named after the city here, so the match has to carry the
    # "TP." — plain "Vũng Tàu" appears in every Bà Rịa and Phú Mỹ address too.
    "vungtau":  ("ba-ria-vung-tau", "Vung Tau", "TP. Vũng Tàu"),
}

_NEXT = re.compile(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_AREA = re.compile(r"([\d.,]+)\s*m²")
_BEDS = re.compile(r"(\d+)\s*PN")

# Land, commercial space and offices are a different market; excluded here just
# as they are for Chotot.
EXCLUDE = ("đất", "mặt bằng", "văn phòng", "kho", "xưởng", "shophouse", "kiot")
APARTMENT = ("chung cư", "căn hộ")
ROOM = ("phòng trọ", "nhà trọ")

AREA_MIN, AREA_MAX = 8, 500
VND_MIN, VND_MAX = 500_000, 500_000_000


def _kind(category: str) -> str:
    c = (category or "").lower()
    if any(x in c for x in EXCLUDE):
        return "commercial"
    if any(x in c for x in ROOM):
        return "room"
    if any(x in c for x in APARTMENT):
        return "apartment"
    if "nhà" in c or "biệt thự" in c or "villa" in c:
        return "house"
    return "other"


RESIDENTIAL = {"apartment", "house", "room"}


def _get(url: str, attempts: int = 3) -> str:
    """One page, with a couple of retries — the odd read times out."""
    last: Exception | None = None
    for n in range(attempts):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=45, context=_SSL) as resp:
                return resp.read().decode("utf-8", "replace")
        except Exception as exc:                      # noqa: BLE001
            last = exc
            time.sleep(2 * (n + 1))
    raise last if last else RuntimeError("unreachable")


def iter_listings(region: str, limit: int | None = None, max_pages: int = 130,
                  residential_only: bool = True,
                  max_age_days: int | None = 120) -> Iterator[dict[str, Any]]:
    """Newest first. Stops once a whole page predates `max_age_days`.

    Without that guard a deep crawl quietly fills the database with years-old
    stock — which is exactly what happened with the portal tried before this
    one, where page 40 was still returning 2023 listings.
    """
    slug, city, area_match = REGIONS[region]
    seen: set[int] = set()
    got = 0
    for page in range(1, max_pages + 1):
        url = f"{BASE}/bat-dong-san/cho-thue-nha-dat-{slug}" + (f"?page={page}" if page > 1 else "")
        blob = _NEXT.search(_get(url))
        if not blob:
            break
        try:
            items = json.loads(blob.group(1))["props"]["pageProps"]["classified"]["items"]
        except (KeyError, ValueError):
            break
        if not items:
            break
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
                  if max_age_days else None)
        page_newest: datetime | None = None
        fresh = 0
        for it in items:
            ident = it.get("id")
            if not ident or ident in seen:
                continue
            seen.add(ident)
            fresh += 1
            if it.get("is_expired"):
                continue
            try:
                posted = datetime.fromisoformat(it["publish_at"]).astimezone(timezone.utc)
                if page_newest is None or posted > page_newest:
                    page_newest = posted
            except (KeyError, TypeError, ValueError):
                page_newest = page_newest or datetime.now(timezone.utc)
            if area_match and area_match not in (it.get("location") or ""):
                continue
            kind = _kind(it.get("category_name", ""))
            if residential_only and kind not in RESIDENTIAL:
                continue
            attrs = " ".join(a.get("value", "") for a in (it.get("attributes") or []))
            yield {
                "id": int(ident),
                "url": BASE + (it.get("url") or ""),
                "kind": kind,
                "category": it.get("category_name"),
                "title": it.get("title") or "",
                "summary": (it.get("summary") or "")[:800],
                "location": it.get("location") or "",
                "price": it.get("price"),
                "price_display": it.get("price_display"),
                "attrs": attrs,
                "publish_at": it.get("publish_at"),
                "photos": it.get("total_images") or 0,
                "city": city,
                "_src": "muaban",
            }
            got += 1
            if limit and got >= limit:
                return
        if fresh == 0:
            break
        if cutoff and page_newest and page_newest < cutoff:
            print(f"  stopping at page {page}: listings are older than "
                  f"{max_age_days} days")
            return
        time.sleep(PAUSE)


def message_row(item: dict[str, Any], source: str) -> dict[str, Any]:
    raw_when = item.get("publish_at")
    try:
        when = datetime.fromisoformat(raw_when).astimezone(timezone.utc)
    except (TypeError, ValueError):
        when = datetime.now(timezone.utc)
    body = "\n".join(filter(None, [item.get("title"), item.get("location"),
                                   item.get("price_display"), item.get("attrs"),
                                   item.get("summary")]))
    return {
        "chat": source, "msg_id": item["id"],
        "date_utc": when.isoformat(timespec="seconds"),
        "edit_date": None, "sender_id": None, "sender_username": None,
        "sender_name": None, "text": body, "grouped_id": None, "reply_to": None,
        "n_photos": item.get("photos") or 0,
        "link": item["url"], "raw": None, "fetched_at": None,
    }


def listing_fields(raw: dict[str, Any], vnd_per_usd: float) -> dict[str, Any]:
    out: dict[str, Any] = {"deal_type": "rent_offer", "city": raw.get("city"),
                           "kind": raw.get("kind")}

    price = raw.get("price")
    if isinstance(price, (int, float)) and VND_MIN <= price <= VND_MAX:
        out["price"] = float(price)
        out["currency"] = "VND"
        out["price_usd"] = round(price / vnd_per_usd, 2)

    attrs = raw.get("attrs") or ""
    if m := _AREA.search(attrs):
        try:
            area = float(m.group(1).replace(".", "").replace(",", "."))
            if AREA_MIN <= area <= AREA_MAX:
                out["area_sqm"] = area
        except ValueError:
            pass
    if m := _BEDS.search(attrs):
        beds = int(m.group(1))
        if 0 < beds <= 10:
            out["bedrooms"], out["rooms"] = beds, beds + 1
            out["layout"] = f"{beds} bedroom"
    elif raw.get("kind") == "room":
        out["bedrooms"], out["rooms"], out["layout"] = 0, 1, "studio"

    # "Phường Tân Chính, Quận Thanh Khê, Đà Nẵng" — ward, district, province.
    # In the single-city regions the middle part is the city ("Phường Thắng
    # Tam, TP. Vũng Tàu, Bà Rịa - Vũng Tàu"), the same value on every listing,
    # so the ward is what locates a flat there. Same rule as Chotot's.
    parts = [p.strip() for p in (raw.get("location") or "").split(",") if p.strip()]
    if len(parts) >= 2:
        middle = parts[-2]
        is_city = middle.startswith(("TP.", "Tp.", "Thành phố", "Thành Phố",
                                     "Thị xã", "Thị Xã"))
        out["district"] = parts[0] if is_city else middle
        if parts[0] != out["district"]:
            out["address"] = parts[0]
    elif parts:
        out["district"] = parts[0]

    if out.get("price_usd") and out.get("area_sqm"):
        out["usd_per_sqm"] = round(out["price_usd"] / out["area_sqm"], 2)
    return out
