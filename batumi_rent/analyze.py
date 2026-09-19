"""Query and summarise what has been collected."""

from __future__ import annotations

import csv
import json
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

BASE = """
SELECT l.*, m.text, m.sender_username, m.sender_name, m.n_photos, m.link
FROM   listings l JOIN messages m USING (chat, msg_id)
"""


@dataclass
class Filters:
    deal: str | None = "rent_offer"
    term: str | None = None
    min_price: float | None = None
    max_price: float | None = None
    rooms: list[int] = field(default_factory=list)
    min_area: float | None = None
    max_area: float | None = None
    district: str | None = None
    city: str | None = None
    complex_name: str | None = None
    furnished: bool | None = None
    pets: bool | None = None
    sea_view: bool = False
    parking: bool = False
    no_agent: bool = False
    with_photos: bool = False
    days: int | None = None
    text: str | None = None
    has_price: bool = False
    has_area: bool = False
    dedupe: bool = True
    split_by_city: bool = False


def build_where(f: Filters) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    def add(sql: str, *values: Any) -> None:
        clauses.append(sql)
        params.extend(values)

    if f.deal and f.deal != "any":
        add("l.deal_type = ?", f.deal)
    if f.term and f.term != "any":
        add("l.term = ?", f.term)
    if f.min_price is not None:
        add("l.price_usd >= ?", f.min_price)
    if f.max_price is not None:
        add("l.price_usd <= ?", f.max_price)
    if f.rooms:
        add(f"l.rooms IN ({','.join('?' * len(f.rooms))})", *f.rooms)
    if f.min_area is not None:
        add("l.area_sqm >= ?", f.min_area)
    if f.max_area is not None:
        add("l.area_sqm <= ?", f.max_area)
    if f.city:
        add("l.city = ?", f.city)
    if f.district:
        add("l.district LIKE ?", f"%{f.district}%")
    if f.complex_name:
        add("l.complex_name LIKE ?", f"%{f.complex_name}%")
    if f.furnished is not None:
        add("l.furnished = ?", int(f.furnished))
    if f.pets is not None:
        add("l.pets = ?", int(f.pets))
    if f.sea_view:
        add("l.sea_view = 1")
    if f.parking:
        add("l.parking = 1")
    if f.no_agent:
        add("(l.is_agent = 0 OR l.is_agent IS NULL)")
    if f.with_photos:
        add("m.n_photos > 0")
    if f.days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=f.days)).isoformat(timespec="seconds")
        add("l.date_utc >= ?", cutoff)
    if f.text:
        add("m.text LIKE ?", f"%{f.text}%")
    if f.has_price:
        add("l.price_usd IS NOT NULL")
    if f.has_area:
        add("l.area_sqm IS NOT NULL")

    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


# One flat routinely appears 7-8 times from different accounts — agencies
# reposting the same stock. A text hash misses that (the wording is retyped),
# and so does anything keyed on the sender. Identify a listing by what it is:
# price, size, rooms and floor. Address is deliberately left out, since the same
# ad shows up as both "Химшиашвили" and "Химшиашвили 1". Falls back to the text
# fingerprint when too little was extracted to identify the flat.
#
# The attributes alone are not unique over a long archive: in a chat covering a
# whole country for a year, two different flats easily share a price and a
# bedroom count. Time settles it, but a fixed calendar bucket does not — a
# boundary landing mid-week splits genuine reposts in two. So matching rows are
# grouped only while the gap between consecutive posts stays under GAP_DAYS
# (see query): reposts of one flat arrive within days, the same figures months
# later are a different flat.
# City is deliberately optional. In a one-city chat it splits one flat in two
# whenever a repost happens to name the city and the original does not; in a
# country-wide chat it keeps two same-priced flats in different cities apart.
# Turn it on only where the chat actually spans cities (--split-cities).
def fingerprint(split_by_city: bool = False) -> str:
    city = " || '/' || COALESCE(l.city, '')" if split_by_city else ""
    return f"""
CASE WHEN l.price_usd IS NOT NULL AND (l.area_sqm IS NOT NULL OR l.rooms IS NOT NULL)
     THEN 'f:' || l.price_usd || '/' || COALESCE(l.area_sqm, -1) || '/'
                || COALESCE(l.rooms, -1) || '/' || COALESCE(l.floor, -1){city}
     ELSE 'd:' || l.dup_key END
"""


FINGERPRINT = fingerprint()

GAP_DAYS = 14

SORTS = {
    "price": "l.price_usd ASC NULLS LAST",
    "price-desc": "l.price_usd DESC",
    "value": "l.usd_per_sqm ASC NULLS LAST",
    "area": "l.area_sqm DESC NULLS LAST",
    "date": "l.date_utc DESC",
    "oldest": "l.date_utc ASC",
}


def query(conn: sqlite3.Connection, f: Filters, sort: str = "date",
          limit: int = 30) -> list[sqlite3.Row]:
    where, params = build_where(f)
    order = SORTS.get(sort, SORTS["date"])
    if f.dedupe:
        # Rows sharing a fingerprint are one flat only while consecutive posts
        # stay within GAP_DAYS of each other; a longer silence starts a new
        # group. Keeps the newest post of each group.
        sql = (f"WITH base AS ("
               f"  SELECT l.*, m.text, m.sender_username, m.sender_name, m.n_photos,"
               f"         m.link, {fingerprint(f.split_by_city)} AS ak, julianday(l.date_utc) AS jd"
               f"  FROM listings l JOIN messages m USING (chat, msg_id){where}),"
               f" flagged AS ("
               f"  SELECT *, CASE WHEN LAG(jd) OVER w IS NULL"
               f"                   OR jd - LAG(jd) OVER w > {GAP_DAYS}"
               f"                 THEN 1 ELSE 0 END AS newgrp"
               f"  FROM base WINDOW w AS (PARTITION BY ak ORDER BY jd)),"
               f" grouped AS ("
               f"  SELECT *, SUM(newgrp) OVER (PARTITION BY ak ORDER BY jd"
               f"           ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS gid"
               f"  FROM flagged)"
               f" SELECT * FROM ("
               f"  SELECT *, ROW_NUMBER() OVER (PARTITION BY ak, gid"
               f"           ORDER BY date_utc DESC) AS rn FROM grouped)"
               f" WHERE rn = 1 ORDER BY {order.replace('l.', '')} LIMIT ?")
    else:
        sql = f"{BASE}{where} ORDER BY {order} LIMIT ?"
    return conn.execute(sql, [*params, limit]).fetchall()


# ------------------------------------------------------------------ rendering

def _money(value: Any) -> str:
    return f"${value:,.0f}" if value is not None else "—"


def _fmt(row: sqlite3.Row) -> dict[str, str]:
    price = _money(row["price_usd"])
    if row["price_max_usd"]:
        price += f"–{row['price_max_usd']:,.0f}"
    flags = "".join([
        "🌊" if row["sea_view"] else "",
        "🛋" if row["furnished"] == 1 else ("∅" if row["furnished"] == 0 else ""),
        "🐾" if row["pets"] == 1 else "",
        "🅿️" if row["parking"] else "",
        "📷" if row["n_photos"] else "",
    ])
    return {
        "date": row["date_utc"][:10],
        "price": price,
        "term": {"long": "long", "daily": "daily"}.get(row["term"], "?"),
        "rooms": row["layout"] or (f"{row['rooms']}к" if row["rooms"] else "—"),
        "m2": f"{row['area_sqm']:.0f}" if row["area_sqm"] else "—",
        "$/m2": f"{row['usd_per_sqm']:.1f}" if row["usd_per_sqm"] else "—",
        "floor": (f"{row['floor']}/{row['floors_total']}" if row["floors_total"]
                  else (str(row["floor"]) if row["floor"] else "—")),
        "where": (row["complex_name"] or row["district"] or row["city"] or "—")[:18],
        "flags": flags,
        "link": row["link"] or "",
    }


COLUMNS = ["date", "price", "term", "rooms", "m2", "$/m2", "floor", "where", "flags", "link"]


def print_table(rows: Sequence[sqlite3.Row]) -> None:
    if not rows:
        print("No listings match those filters.")
        return
    data = [_fmt(r) for r in rows]
    widths = {c: max(len(c), *(len(d[c]) for d in data)) for c in COLUMNS}
    line = "  ".join(c.ljust(widths[c]) for c in COLUMNS)
    print(line)
    print("-" * len(line))
    for d in data:
        print("  ".join(d[c].ljust(widths[c]) for c in COLUMNS))
    print(f"\n{len(rows)} listing(s).")


def print_detail(rows: Sequence[sqlite3.Row]) -> None:
    for row in rows:
        d = _fmt(row)
        print("=" * 78)
        print(f"{d['price']} · {d['rooms']} · {d['m2']} m² · {d['where']} · "
              f"{d['term']} · {d['date']}")
        contact = " ".join(filter(None, [row["phone"], row["contact"],
                                         f"@{row['sender_username']}" if row["sender_username"] else None]))
        print(f"{row['link']}   {contact}")
        print("-" * 78)
        print((row["text"] or "").strip()[:900])
        print()


def to_csv(rows: Iterable[sqlite3.Row], path: str | None) -> None:
    rows = list(rows)
    if not rows:
        print("Nothing to export.")
        return
    handle = open(path, "w", newline="", encoding="utf-8") if path else sys.stdout
    try:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in rows[0].keys()})
    finally:
        if path:
            handle.close()
            print(f"Wrote {len(rows)} rows to {path}")


def to_json(rows: Iterable[sqlite3.Row], path: str | None) -> None:
    payload = [dict(r) for r in rows]
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Wrote {len(payload)} rows to {path}")
    else:
        print(text)


# -------------------------------------------------------------------- summary

def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _group_summary(rows: Sequence[sqlite3.Row], column: str, label: str,
                   min_count: int = 3) -> None:
    groups: dict[Any, list[sqlite3.Row]] = {}
    for row in rows:
        groups.setdefault(row[column], []).append(row)

    out = []
    for name, items in groups.items():
        if name is None or len(items) < min_count:
            continue
        prices = [r["price_usd"] for r in items if r["price_usd"]]
        per_sqm = [r["usd_per_sqm"] for r in items if r["usd_per_sqm"]]
        if not prices:
            continue
        out.append((str(name), len(items), _median(prices),
                    min(prices), max(prices), _median(per_sqm)))
    if not out:
        return
    out.sort(key=lambda r: r[2] or 0)
    print(f"\n{label}")
    print(f"{'':<22}{'n':>5}{'median':>10}{'min':>9}{'max':>9}{'$/m² med':>10}")
    for name, n, med, lo, hi, psm in out:
        print(f"{name[:22]:<22}{n:>5}{_money(med):>10}{_money(lo):>9}"
              f"{_money(hi):>9}{(f'{psm:.1f}' if psm else '—'):>10}")


def summary(conn: sqlite3.Connection, f: Filters) -> None:
    totals = conn.execute(
        "SELECT COUNT(*) n, COUNT(DISTINCT dup_key) uniq FROM listings").fetchone()
    msgs = conn.execute("SELECT COUNT(*) n FROM messages").fetchone()["n"]
    span = conn.execute(
        "SELECT MIN(date_utc) a, MAX(date_utc) b FROM messages").fetchone()
    print(f"{msgs:,} messages collected "
          f"({(span['a'] or '?')[:10]} → {(span['b'] or '?')[:10]})")
    print(f"{totals['n']:,} parsed listings, {totals['uniq']:,} unique after dedupe")

    by_type = conn.execute(
        "SELECT deal_type, term, COUNT(*) n FROM listings GROUP BY 1,2 ORDER BY n DESC")
    print("\nposts by type")
    for row in by_type:
        print(f"  {row['deal_type']:<12}{row['term']:<9}{row['n']:>7,}")

    rows = query(conn, f, sort="date", limit=1_000_000)
    print(f"\ncurrent filter: {len(rows):,} listings"
          f"{' after dedupe' if f.dedupe else ''}")
    prices = [r["price_usd"] for r in rows if r["price_usd"] is not None]
    if prices:
        prices.sort()
        def pct(p: float) -> float:
            return prices[min(int(len(prices) * p), len(prices) - 1)]
        print(f"\nprice distribution for the current filter (USD, n={len(prices):,})")
        print(f"  p10 {_money(pct(0.10))}   p25 {_money(pct(0.25))}   "
              f"median {_money(pct(0.50))}   p75 {_money(pct(0.75))}   "
              f"p90 {_money(pct(0.90))}")

    _group_summary(rows, "city", "by city")
    _group_summary(rows, "layout", "by layout")
    _group_summary(rows, "district", "by district")
    _group_summary(rows, "complex_name", "by complex")
    _group_summary(rows, "address", "by street address", min_count=4)
