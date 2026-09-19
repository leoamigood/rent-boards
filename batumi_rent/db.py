"""SQLite storage: raw messages are kept verbatim, parsed fields live alongside.

Keeping the two apart means the parser can be improved and re-run over history
(`reparse`) without re-downloading anything from Telegram.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS messages (
    chat            TEXT    NOT NULL,
    msg_id          INTEGER NOT NULL,
    date_utc        TEXT    NOT NULL,
    edit_date       TEXT,
    sender_id       INTEGER,
    sender_username TEXT,
    sender_name     TEXT,
    text            TEXT,
    grouped_id      INTEGER,
    reply_to        INTEGER,
    n_photos        INTEGER NOT NULL DEFAULT 0,
    link            TEXT,
    raw             TEXT,
    fetched_at      TEXT    NOT NULL,
    PRIMARY KEY (chat, msg_id)
);

CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_utc);

CREATE TABLE IF NOT EXISTS listings (
    chat            TEXT    NOT NULL,
    msg_id          INTEGER NOT NULL,
    date_utc        TEXT    NOT NULL,
    deal_type       TEXT,          -- rent_offer | rent_seek | sale | other
    term            TEXT,          -- long | daily | unknown
    price           REAL,
    currency        TEXT,          -- USD | GEL | EUR
    price_usd       REAL,
    price_max_usd   REAL,          -- upper bound when the post gives a range
    rooms           INTEGER,       -- total living rooms (studio = 1)
    bedrooms        INTEGER,
    layout          TEXT,          -- "2+1", "studio", "3 комн"...
    area_sqm        REAL,
    floor           INTEGER,
    floors_total    INTEGER,
    district        TEXT,
    complex_name    TEXT,
    address         TEXT,
    city            TEXT,
    kind            TEXT,
    lat             REAL,
    lon             REAL,
    furnished       INTEGER,       -- 1 yes / 0 no / NULL unknown
    pets            INTEGER,
    sea_view        INTEGER,
    parking         INTEGER,
    available_from  TEXT,
    is_agent        INTEGER,
    phone           TEXT,
    contact         TEXT,
    lang            TEXT,
    dup_key         TEXT,
    usd_per_sqm     REAL,
    parser_version  INTEGER NOT NULL,
    parsed_at       TEXT    NOT NULL,
    PRIMARY KEY (chat, msg_id),
    FOREIGN KEY (chat, msg_id) REFERENCES messages(chat, msg_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_listings_price   ON listings(price_usd);
CREATE INDEX IF NOT EXISTS idx_listings_date    ON listings(date_utc);
CREATE INDEX IF NOT EXISTS idx_listings_deal    ON listings(deal_type, term);
CREATE INDEX IF NOT EXISTS idx_listings_dist    ON listings(district);
CREATE INDEX IF NOT EXISTS idx_listings_dup     ON listings(dup_key);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Everything an analysis query usually wants, already joined.
DROP VIEW IF EXISTS v_offers;
CREATE VIEW v_offers AS
SELECT l.*, m.text, m.sender_username, m.sender_name, m.n_photos, m.link
FROM   listings l
JOIN   messages m USING (chat, msg_id)
WHERE  l.deal_type = 'rent_offer';
"""

MESSAGE_COLUMNS = (
    "chat", "msg_id", "date_utc", "edit_date", "sender_id", "sender_username",
    "sender_name", "text", "grouped_id", "reply_to", "n_photos", "link", "raw",
    "fetched_at",
)

LISTING_COLUMNS = (
    "chat", "msg_id", "date_utc", "deal_type", "term", "price", "currency",
    "price_usd", "price_max_usd", "rooms", "bedrooms", "layout", "area_sqm",
    "floor", "floors_total", "district", "complex_name", "address", "city", "kind", "lat", "lon", "furnished", "pets",
    "sea_view", "parking", "available_from", "is_agent", "phone", "contact",
    "lang", "dup_key", "usd_per_sqm", "parser_version", "parsed_at",
)


# Columns added after the first release, applied to databases already on disk.
MIGRATIONS: dict[str, dict[str, str]] = {
    "listings": {"address": "TEXT", "city": "TEXT", "kind": "TEXT",
                 "lat": "REAL", "lon": "REAL"},
    "messages": {"raw": "TEXT"},
}


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    for table, columns in MIGRATIONS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for column, ddl in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.executescript(SCHEMA)
    return conn


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _upsert(conn: sqlite3.Connection, table: str, columns: tuple[str, ...],
            rows: Iterable[dict[str, Any]]) -> int:
    placeholders = ",".join("?" * len(columns))
    updates = ",".join(f"{c}=excluded.{c}" for c in columns if c not in ("chat", "msg_id"))
    sql = (
        f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(chat, msg_id) DO UPDATE SET {updates}"
    )
    payload = [tuple(r.get(c) for c in columns) for r in rows]
    if not payload:
        return 0
    conn.executemany(sql, payload)
    return len(payload)


def save_messages(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    return _upsert(conn, "messages", MESSAGE_COLUMNS, rows)


def save_listings(conn: sqlite3.Connection, rows: Iterable[dict[str, Any]]) -> int:
    return _upsert(conn, "listings", LISTING_COLUMNS, rows)


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def max_msg_id(conn: sqlite3.Connection, chat: str) -> int:
    row = conn.execute("SELECT MAX(msg_id) AS m FROM messages WHERE chat=?", (chat,)).fetchone()
    return row["m"] or 0


def min_msg_id(conn: sqlite3.Connection, chat: str) -> int:
    row = conn.execute("SELECT MIN(msg_id) AS m FROM messages WHERE chat=?", (chat,)).fetchone()
    return row["m"] or 0
