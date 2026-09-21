"""renthome.pro — a Vietnam rental marketplace.

Their robots.txt disallows /api/, and the listings are only reachable there:
the site is a JavaScript app and its listing pages render nothing without it.
This collector is used at the owner's explicit direction for personal use, so
it behaves like a considerate guest — one page at a time, a pause between
pages, no concurrency, and no attempt to defeat any protection, because there
is none to defeat: the endpoint is public and unauthenticated.

In exchange it is the richest source here. Price, area, bedrooms, bathrooms,
coordinates, the date a flat is free from, deposit, minimum stay, pets and
distance to the sea all arrive as fields.
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

BASE = "https://renthome.pro"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAGE = 50
PAUSE = 1.6

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                  # pragma: no cover
    _SSL = ssl.create_default_context()

# cityId -> the label used across the boards
REGIONS = {"danang": (1, "Da Nang"), "nhatrang": (2, "Nha Trang")}

KINDS = {"APARTMENT": "apartment", "HOUSE": "house", "VILLA": "house",
         "STUDIO": "apartment", "ROOM": "room", "TOWNHOUSE": "house"}

AREA_MIN, AREA_MAX = 8, 500
VND_MIN, VND_MAX = 500_000, 500_000_000

_TAGS = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    return " ".join(_TAGS.sub(" ", html or "").replace("&nbsp;", " ").split())


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


def iter_listings(region: str, limit: int | None = None,
                  max_age_days: int | None = 120) -> Iterator[dict[str, Any]]:
    city_id, city = REGIONS[region]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)
              if max_age_days else None)
    got = 0
    page = 0
    while True:
        payload = _get(f"{BASE}/api/apartments?cityId={city_id}&page={page}&size={PAGE}")
        items = payload.get("content") or payload.get("items") or []
        if not items:
            break
        newest: datetime | None = None
        for it in items:
            # Nightly lets are a different market and would distort every
            # median on the board, so only monthly prices are taken.
            if (it.get("pricePeriod") or "MONTH") != "MONTH":
                continue
            when = it.get("publishedAt") or it.get("createdAt")
            try:
                posted = datetime.fromisoformat(when.replace("Z", "+00:00"))
            except (AttributeError, ValueError):
                posted = datetime.now(timezone.utc)
            if newest is None or posted > newest:
                newest = posted
            it["_city"] = city
            it["_posted"] = posted.astimezone(timezone.utc).isoformat(timespec="seconds")
            it["_src"] = "renthome"
            yield it
            got += 1
            if limit and got >= limit:
                return
        if payload.get("last"):
            break
        if cutoff and newest and newest < cutoff:
            print(f"  stopping at page {page}: posts are older than {max_age_days} days")
            return
        page += 1
        time.sleep(PAUSE)


def message_row(ad: dict[str, Any], source: str) -> dict[str, Any]:
    body = "\n".join(filter(None, [ad.get("title"), _text(ad.get("description")),
                                   ad.get("addressText")]))
    return {
        "chat": source, "msg_id": int(ad["id"]), "date_utc": ad["_posted"],
        "edit_date": None, "sender_id": ad.get("realtorId"),
        "sender_username": ad.get("contactTelegram"),
        "sender_name": ad.get("organizationName"),
        "text": body, "grouped_id": None, "reply_to": None,
        "n_photos": len(ad.get("images") or []),
        "link": f"{BASE}/apartment/{ad['id']}",
        "raw": json.dumps(_keep(ad), ensure_ascii=False),
        "fetched_at": None,
    }


def _keep(ad: dict[str, Any]) -> dict[str, Any]:
    fields = ("id", "priceVnd", "priceAmount", "currency", "areaSqm", "bedrooms",
              "bathrooms", "floor", "totalFloors", "district", "addressText",
              "latitude", "longitude", "propertyType", "availableFrom",
              "petsAllowed", "furnishing", "seaDistanceM", "minStayMonths",
              "depositVnd", "billsIncluded", "contactPhone", "contactTelegram",
              "_city", "_src")
    return {k: ad.get(k) for k in fields if ad.get(k) is not None}


def listing_fields(raw: dict[str, Any], vnd_per_usd: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "deal_type": "rent_offer", "term": "long",
        "city": raw.get("_city"),
        "kind": KINDS.get(raw.get("propertyType") or "", "other"),
    }

    price = raw.get("priceVnd") or raw.get("priceAmount")
    if price and VND_MIN <= price <= VND_MAX:
        out["price"] = float(price)
        out["currency"] = "VND"
        out["price_usd"] = round(price / vnd_per_usd, 2)

    area = raw.get("areaSqm")
    if area and AREA_MIN <= area <= AREA_MAX:
        out["area_sqm"] = float(area)

    beds = raw.get("bedrooms")
    if beds is not None and 0 <= beds <= 10:
        out["bedrooms"] = int(beds)
        out["rooms"] = int(beds) + 1
        out["layout"] = "studio" if beds == 0 else f"{int(beds)} bedroom"

    for src_key, dest in (("floor", "floor"), ("totalFloors", "floors_total")):
        v = raw.get(src_key)
        if v is not None and 0 < v <= 80:
            out[dest] = int(v)

    if raw.get("district"):
        out["district"] = raw["district"]
    if raw.get("addressText"):
        out["address"] = raw["addressText"][:120]
    if raw.get("availableFrom"):
        out["available_from"] = str(raw["availableFrom"])
    if raw.get("petsAllowed") is not None:
        out["pets"] = 1 if raw["petsAllowed"] else 0
    if raw.get("furnishing"):
        out["furnished"] = 0 if str(raw["furnishing"]).upper() == "NONE" else 1
    if raw.get("seaDistanceM") is not None and raw["seaDistanceM"] <= 800:
        out["sea_view"] = 1

    lat, lon = raw.get("latitude"), raw.get("longitude")
    if lat and lon and 8.0 <= lat <= 24.0 and 102.0 <= lon <= 110.0:
        out["lat"], out["lon"] = float(lat), float(lon)

    out["phone"] = raw.get("contactPhone")
    if raw.get("contactTelegram"):
        out["contact"] = "@" + str(raw["contactTelegram"]).lstrip("@")

    if out.get("price_usd") and out.get("area_sqm"):
        out["usd_per_sqm"] = round(out["price_usd"] / out["area_sqm"], 2)
    return out
