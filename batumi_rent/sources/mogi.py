"""Mogi.vn — a Vietnamese property portal, as a second source for Vietnam.

Batdongsan, the better-known site, sits behind a bot challenge that gates even
its robots.txt, so it cannot be read without defeating that. Mogi publishes a
permissive robots.txt (`Allow: /`, with its own /api/ disallowed) and sitemaps,
and serves listing pages as plain server-rendered HTML — so the public listing
pages are read here, never the API that robots.txt puts off limits.

One page of fifteen listings at a time, with a pause between pages.
"""

from __future__ import annotations

import html
import re
import ssl
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterator

BASE = "https://mogi.vn"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
PAUSE = 1.5
PER_PAGE = 15

try:
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except ImportError:                                  # pragma: no cover
    _SSL = ssl.create_default_context()

REGIONS = {"danang": ("da-nang", "Da Nang"), "hanoi": ("ha-noi", "Hanoi"),
           "hcmc": ("ho-chi-minh", "Ho Chi Minh"), "nhatrang": ("khanh-hoa", "Nha Trang")}

_ID = re.compile(r'href="(?P<url>[^"]+?-id(?P<id>\d+))"')
_TITLE = re.compile(r'<h2 class="prop-title">(.*?)</h2>', re.S)
_ADDR = re.compile(r'<div class="prop-addr">(.*?)</div>', re.S)
_ATTR = re.compile(r'<ul class="prop-attr">(.*?)</ul>', re.S)
_PRICE = re.compile(r'<div class="price">(.*?)</div>', re.S)
_CREATED = re.compile(r'<div class="prop-created">(\d{2})/(\d{2})/(\d{4})</div>')
_PHOTOS = re.compile(r'<div class="total"><i[^>]*></i><span>(\d+)</span></div>')
_AREA = re.compile(r"([\d.,]+)\s*m<sup>2</sup>")
_BEDS = re.compile(r"(\d+)\s*PN")

# "5 triệu 800 nghìn", "45 triệu", "1,2 tỷ", "Thỏa thuận"
_UNITS = ((r"tỷ", 1e9), (r"triệu", 1e6), (r"nghìn|ngàn", 1e3))

KINDS = ((("can-ho", "chung-cu"), "apartment"),
         (("phong-tro", "nha-tro"), "room"),
         (("nha-rieng", "nha-mat-tien", "nha-pho", "biet-thu", "nha-nguyen-can"), "house"))
# The combined rental page also carries offices, shopfronts, warehouses and
# land. Those are a different market, and were excluded from Chotot too.
COMMERCIAL = ("van-phong", "mat-bang", "kho-xuong", "nha-xuong", "dat-nen",
              "thue-dat", "cua-hang", "kiot", "shophouse")


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def parse_price(raw: str) -> float | None:
    """Vietnamese price wording to a number of dong."""
    text = raw.lower().replace(".", "").replace(",", ".")
    total = 0.0
    for pattern, mult in _UNITS:
        for m in re.finditer(rf"([\d.]+)\s*(?:{pattern})", text):
            try:
                total += float(m.group(1)) * mult
            except ValueError:
                continue
    return total or None


def _kind(url: str) -> str:
    if any(n in url for n in COMMERCIAL):
        return "commercial"
    for needles, name in KINDS:
        if any(n in url for n in needles):
            return name
    return "other"


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=30, context=_SSL) as resp:
        return resp.read().decode("utf-8", "replace")


RESIDENTIAL = {"apartment", "house", "room"}


def iter_listings(region: str, limit: int | None = None, max_pages: int = 120,
                  residential_only: bool = True) -> Iterator[dict[str, Any]]:
    slug, city = REGIONS[region]
    seen: set[str] = set()
    got = 0
    for page in range(1, max_pages + 1):
        url = f"{BASE}/{slug}/thue-nha-dat" + (f"?cp={page}" if page > 1 else "")
        body = _get(url)
        chunks = body.split('<a class="link-overlay"')[1:]
        if not chunks:
            break
        fresh = 0
        for chunk in chunks:
            chunk = chunk[:2500]
            ident = _ID.search(chunk)
            if not ident or ident.group("id") in seen:
                continue
            seen.add(ident.group("id"))
            fresh += 1
            kind = _kind(ident.group("url"))
            if residential_only and kind not in RESIDENTIAL:
                continue
            attrs = _ATTR.search(chunk)
            attr_text = attrs.group(1) if attrs else ""
            created = _CREATED.search(chunk)
            title = _TITLE.search(chunk)
            addr = _ADDR.search(chunk)
            price = _PRICE.search(chunk)
            photos = _PHOTOS.search(chunk)
            yield {
                "id": int(ident.group("id")),
                "url": html.unescape(ident.group("url")),
                "kind": kind,
                "title": _text(title.group(1)) if title else "",
                "addr": _text(addr.group(1)) if addr else "",
                "price_raw": _text(price.group(1)) if price else "",
                "area": _AREA.search(attr_text).group(1) if _AREA.search(attr_text) else None,
                "beds": _BEDS.search(attr_text).group(1) if _BEDS.search(attr_text) else None,
                "date": f"{created.group(3)}-{created.group(2)}-{created.group(1)}"
                        if created else None,
                "photos": int(photos.group(1)) if photos else 0,
                "city": city,
                "_src": "mogi",
            }
            got += 1
            if limit and got >= limit:
                return
        if fresh == 0:                      # the site started repeating itself
            break
        time.sleep(PAUSE)


def message_row(item: dict[str, Any], source: str) -> dict[str, Any]:
    when = (datetime.fromisoformat(item["date"]).replace(tzinfo=timezone.utc)
            if item.get("date") else datetime.now(timezone.utc))
    body = "\n".join(filter(None, [item.get("title"), item.get("addr"),
                                   item.get("price_raw")]))
    return {
        "chat": source,
        "msg_id": item["id"],
        "date_utc": when.isoformat(timespec="seconds"),
        "edit_date": None, "sender_id": None, "sender_username": None,
        "sender_name": None, "text": body, "grouped_id": None, "reply_to": None,
        "n_photos": item.get("photos") or 0,
        "link": item["url"],
        "raw": None,                        # filled by the caller
        "fetched_at": None,
    }


AREA_MIN, AREA_MAX = 8, 500
VND_MIN, VND_MAX = 500_000, 500_000_000


def listing_fields(raw: dict[str, Any], vnd_per_usd: float) -> dict[str, Any]:
    out: dict[str, Any] = {"deal_type": "rent_offer", "city": raw.get("city"),
                           "kind": raw.get("kind")}

    price = parse_price(raw.get("price_raw") or "")
    if price and VND_MIN <= price <= VND_MAX:
        out["price"] = float(price)
        out["currency"] = "VND"
        out["price_usd"] = round(price / vnd_per_usd, 2)

    if raw.get("area"):
        try:
            area = float(str(raw["area"]).replace(",", "."))
            if AREA_MIN <= area <= AREA_MAX:
                out["area_sqm"] = area
        except ValueError:
            pass

    if raw.get("beds"):
        beds = int(raw["beds"])
        if 0 < beds <= 10:
            out["bedrooms"], out["rooms"] = beds, beds + 1
            out["layout"] = f"{beds} bedroom"
    elif raw.get("kind") == "room":
        out["bedrooms"], out["rooms"], out["layout"] = 0, 1, "studio"

    # "Quận Sơn Trà, Đà Nẵng" -> district, city
    addr = (raw.get("addr") or "").split(",")
    if addr and addr[0].strip():
        out["district"] = addr[0].strip()

    if out.get("price_usd") and out.get("area_sqm"):
        out["usd_per_sqm"] = round(out["price_usd"] / out["area_sqm"], 2)
    return out
