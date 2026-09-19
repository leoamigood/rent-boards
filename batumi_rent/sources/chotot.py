"""Chotot / Nhatot — Vietnam's largest classifieds site.

Unlike a chat, this source is already structured: the API gives price, size,
bedroom count, district, ward and street as fields, so those are used directly
and the free-text parser only fills the gaps (sea view, furnishing, term).

Access note: www.nhatot.com sits behind a bot challenge that also gates
robots.txt, but this JSON gateway — the one the site's own front end calls —
answers ordinary requests. Only that endpoint is used, one page at a time with
a pause between pages.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterator

GATEWAY = "https://gateway.chotot.com/v1/public/ad-listing"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAGE = 50
PAUSE = 1.2          # seconds between pages

# The python.org macOS build ships no CA bundle for urllib, so point at
# certifi's explicitly rather than turning verification off.
try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                  # pragma: no cover
    _SSL = ssl.create_default_context()

# region_v2 codes, discovered from the API's own responses.
REGIONS = {
    "danang": (3017, "Da Nang"),
    "hanoi": (12000, "Hanoi"),
    "hcmc": (13000, "Ho Chi Minh"),
    "nhatrang": (3020, "Nha Trang"),
}

# Residential rental categories. Offices (1030) are deliberately left out.
CATEGORIES = {
    1010: "apartment",
    1020: "house",
    1050: "room",
}

AREA_MIN, AREA_MAX = 8, 500


def _get(params: dict[str, Any]) -> dict[str, Any]:
    url = f"{GATEWAY}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as resp:
        return json.loads(resp.read().decode("utf-8"))


def iter_ads(region: str, limit: int | None = None,
             categories: tuple[int, ...] = tuple(CATEGORIES)) -> Iterator[dict[str, Any]]:
    """Yield rental ads for a region, newest first, one page at a time."""
    region_id, _ = REGIONS[region]
    seen = 0
    for cg in categories:
        offset = 0
        while True:
            payload = _get({"region_v2": region_id, "cg": cg, "st": "u",
                            "limit": PAGE, "o": offset})
            ads = payload.get("ads") or []
            if not ads:
                break
            for ad in ads:
                ad["_category_kind"] = CATEGORIES.get(cg, "other")
                yield ad
                seen += 1
                if limit and seen >= limit:
                    return
            offset += len(ads)
            if offset >= min(payload.get("total") or 0, 9_950):
                break                       # the API stops paging past ~10k
            time.sleep(PAUSE)


def message_row(ad: dict[str, Any], source: str) -> dict[str, Any]:
    ms = ad.get("list_time") or ad.get("orig_list_time")
    when = (datetime.fromtimestamp(ms / 1000, timezone.utc) if ms
            else datetime.now(timezone.utc))
    body = "\n".join(filter(None, [ad.get("subject"), ad.get("body")]))
    return {
        "chat": source,
        "msg_id": int(ad["list_id"]),
        "date_utc": when.isoformat(timespec="seconds"),
        "edit_date": None,
        "sender_id": ad.get("account_id"),
        "sender_username": None,
        "sender_name": ad.get("account_name"),
        "text": body,
        "grouped_id": None,
        "reply_to": None,
        "n_photos": ad.get("number_of_images") or 0,
        "link": f"https://www.nhatot.com/{ad['list_id']}.htm",
        "raw": json.dumps(_keep(ad), ensure_ascii=False),
        "fetched_at": None,          # filled by the caller
    }


def _keep(ad: dict[str, Any]) -> dict[str, Any]:
    """The fields the mapping below needs, so a reparse can redo it offline."""
    fields = ("list_id", "price", "is_price_not_valid", "size", "rooms",
              "area_name", "ward_name", "street_name", "pty_project_name",
              "region_name", "category", "category_name", "_category_kind",
              "latitude", "longitude", "type")
    out = {k: ad.get(k) for k in fields if ad.get(k) not in (None, "")}
    out["_src"] = "chotot"
    return out


def listing_fields(raw: dict[str, Any], vnd_per_usd: float) -> dict[str, Any]:
    """Structured fields straight from the ad, overriding the text parser.

    Only values that survive a sanity check are returned; posters mistype the
    size often enough that a 2-bedroom flat can claim 632 m².
    """
    out: dict[str, Any] = {"deal_type": "rent_offer", "city": "Da Nang",
                           "kind": raw.get("_category_kind")}
    if raw.get("region_name"):
        out["city"] = {"Đà Nẵng": "Da Nang", "Hà Nội": "Hanoi",
                       "Tp Hồ Chí Minh": "Ho Chi Minh",
                       "Khánh Hòa": "Nha Trang"}.get(raw["region_name"],
                                                     raw["region_name"])

    price = raw.get("price")
    if price and not raw.get("is_price_not_valid") and 300_000 <= price <= 500_000_000:
        out["price"] = float(price)
        out["currency"] = "VND"
        out["price_usd"] = round(price / vnd_per_usd, 2)

    size = raw.get("size")
    if size and AREA_MIN <= size <= AREA_MAX:
        out["area_sqm"] = float(size)

    rooms = raw.get("rooms")
    if rooms and 0 < rooms <= 10:
        out["bedrooms"] = int(rooms)
        out["rooms"] = int(rooms) + 1
        out["layout"] = f"{int(rooms)} bedroom"
    elif raw.get("_category_kind") == "room":
        out["bedrooms"] = 0
        out["rooms"] = 1
        out["layout"] = "studio"

    if raw.get("area_name"):
        out["district"] = raw["area_name"]
    address = " ".join(filter(None, [raw.get("street_name"), raw.get("ward_name")]))
    if address:
        out["address"] = address
    if raw.get("pty_project_name"):
        out["complex_name"] = raw["pty_project_name"]

    # A handful of ads are geocoded to another city entirely; keep only points
    # that fall inside Vietnam and let the map fit itself to the rest.
    lat, lon = raw.get("latitude"), raw.get("longitude")
    if lat and lon and 8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0:
        out["lat"], out["lon"] = float(lat), float(lon)

    if out.get("price_usd") and out.get("area_sqm"):
        out["usd_per_sqm"] = round(out["price_usd"] / out["area_sqm"], 2)
    return out
