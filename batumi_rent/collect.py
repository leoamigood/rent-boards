"""Telegram side: join the chat, download history, follow it live.

Uses the MTProto user API (Telethon) rather than a bot, because a bot can only
read messages in a group it has been added to as an admin — which is not an
option for a public chat you don't own.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import TelegramClient, errors, events, functions
from telethon.tl.types import Message

from . import db, parser
from .sources import chotot, dananglandlord, muaban, renthome
from .config import Config

BATCH = 200


def make_client(cfg: Config) -> TelegramClient:
    api_id, api_hash = cfg.require_api()
    return TelegramClient(str(cfg.session_path), api_id, api_hash)


async def resolve_chat(client: TelegramClient, chat: str):
    try:
        return await client.get_entity(int(chat) if chat.lstrip("-").isdigit() else chat)
    except (ValueError, errors.UsernameNotOccupiedError) as exc:
        raise SystemExit(f"Cannot resolve chat {chat!r}: {exc}") from exc


async def ensure_joined(client: TelegramClient, chat: str) -> Any:
    entity = await resolve_chat(client, chat)
    try:
        await client(functions.channels.JoinChannelRequest(entity))
        print(f"Joined @{chat}")
    except errors.UserAlreadyParticipantError:
        pass
    except errors.RPCError as exc:
        # Plenty of chats can be read without joining; only warn.
        print(f"Note: could not join ({exc.__class__.__name__}); reading anyway")
    return entity


def message_row(msg: Message, chat: str, link_chat: str | None = None) -> dict[str, Any]:
    sender = getattr(msg, "sender", None)
    name = None
    if sender is not None:
        name = " ".join(filter(None, [getattr(sender, "first_name", None),
                                      getattr(sender, "last_name", None)])) or \
               getattr(sender, "title", None)
    return {
        "chat": chat,
        "msg_id": msg.id,
        "date_utc": msg.date.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "edit_date": msg.edit_date.isoformat(timespec="seconds") if msg.edit_date else None,
        "sender_id": msg.sender_id,
        "sender_username": getattr(sender, "username", None),
        "sender_name": name,
        "text": msg.message or "",
        "grouped_id": msg.grouped_id,
        "reply_to": msg.reply_to.reply_to_msg_id if msg.reply_to else None,
        "n_photos": 1 if msg.photo else 0,
        "link": f"https://t.me/{link_chat or chat}/{msg.id}",
        "fetched_at": db.now_utc(),
    }


def listing_row(msg_row: dict[str, Any], cfg: Config) -> dict[str, Any] | None:
    text = msg_row.get("text") or ""

    # Sources that already know the price, size and bedroom count keep their
    # own values; the text parser still runs underneath to pick up what the
    # structured record does not carry (sea view, furnishing, term, pets).
    structured: dict[str, Any] = {}
    # Some sources hand over a whole listing record; others only annotate one
    # (the city a channel covers). Only the former is evidence that the message
    # is an ad at all — otherwise every empty photo in an album becomes one.
    is_record = False
    if raw := msg_row.get("raw"):
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            record = {}
        if record.get("_src") == "chotot":
            structured = chotot.listing_fields(record, cfg.vnd_per_usd)
            is_record = True
        elif record.get("_src") == "muaban":
            structured = muaban.listing_fields(record, cfg.vnd_per_usd)
            is_record = True
        elif record.get("_src") == "renthome":
            structured = renthome.listing_fields(record, cfg.vnd_per_usd)
            is_record = True
        elif record.get("_src") in ("telegram", "dananglandlord"):
            # A single-city channel: the city is a property of the source, not
            # something to hope each post spells out.
            structured = {k: v for k, v in record.items()
                          if k == "city" and v is not None}

    if not is_record and not parser.is_listing(text):
        return None

    fields = parser.parse(text, sender_id=msg_row.get("sender_id"),
                          gel_per_usd=cfg.gel_per_usd, eur_per_usd=cfg.eur_per_usd,
                          vnd_per_usd=cfg.vnd_per_usd)
    fields.update({k: v for k, v in structured.items() if v is not None})
    fields.update(chat=msg_row["chat"], msg_id=msg_row["msg_id"],
                  date_utc=msg_row["date_utc"], parsed_at=db.now_utc())
    return fields


def _flush(conn: sqlite3.Connection, messages: list[dict[str, Any]], cfg: Config) -> int:
    if not messages:
        return 0
    db.save_messages(conn, messages)
    listings = [r for r in (listing_row(m, cfg) for m in messages) if r]
    db.save_listings(conn, listings)
    conn.commit()
    return len(listings)


async def fetch(cfg: Config, *, limit: int | None = None, older: bool = False,
                since_days: int | None = None, source: str | None = None,
                city: str | None = None, join: bool = True) -> None:
    """Pull a Telegram chat.

    `source` stores the rows under a different key than the chat's username,
    so several channels can feed one logical source; links still point at the
    real channel. Public channels read fine without joining, so `join` is
    optional — it is not this tool's place to subscribe an account.
    """
    store_as = source or cfg.chat
    conn = db.connect(cfg.db_path)
    client = make_client(cfg)
    async with client:
        if join:
            await ensure_joined(client, cfg.chat)
        entity = await resolve_chat(client, cfg.chat)

        kwargs: dict[str, Any] = {"limit": limit}
        known_max = db.max_msg_id(conn, store_as)
        known_min = db.min_msg_id(conn, store_as)
        if older and known_min:
            kwargs["offset_id"] = known_min          # continue further back
            mode = f"history older than message {known_min}"
        elif known_max and not older:
            kwargs["min_id"] = known_max             # only what arrived since
            mode = f"new messages after {known_max}"
        else:
            mode = "full history (newest first)"
        if since_days:
            kwargs["offset_date"] = None
            cutoff = datetime.now(timezone.utc) - timedelta(days=since_days)
        else:
            cutoff = None

        print(f"Fetching {mode} from @{cfg.chat}...")
        buffer: list[dict[str, Any]] = []
        total = listings = 0
        try:
            async for msg in client.iter_messages(entity, **kwargs):
                if cutoff and msg.date.astimezone(timezone.utc) < cutoff:
                    break
                if not isinstance(msg, Message):
                    continue
                row = message_row(msg, store_as, link_chat=cfg.chat)
                if city:
                    row["raw"] = json.dumps({"_src": "telegram", "city": city},
                                            ensure_ascii=False)
                buffer.append(row)
                total += 1
                if len(buffer) >= BATCH:
                    listings += _flush(conn, buffer, cfg)
                    buffer.clear()
                    print(f"  {total} messages, {listings} listings...", end="\r", flush=True)
        except errors.FloodWaitError as exc:
            print(f"\nTelegram asked to wait {exc.seconds}s — stopping early, "
                  f"run the command again later.")
        finally:
            listings += _flush(conn, buffer, cfg)
        print(f"\nStored {total} messages, {listings} of them parsed as listings.")
        db.set_state(conn, f"last_fetch:{store_as}", db.now_utc())
        conn.commit()
    conn.close()


async def watch(cfg: Config) -> None:
    conn = db.connect(cfg.db_path)
    client = make_client(cfg)
    async with client:
        entity = await ensure_joined(client, cfg.chat)

        @client.on(events.NewMessage(chats=entity))
        async def handler(event) -> None:  # noqa: ANN001
            row = message_row(event.message, cfg.chat)
            if row["sender_username"] is None and event.message.sender_id:
                try:
                    sender = await event.get_sender()
                    row["sender_username"] = getattr(sender, "username", None)
                except errors.RPCError:
                    pass
            count = _flush(conn, [row], cfg)
            if count:
                fields = listing_row(row, cfg) or {}
                price = fields.get("price_usd")
                print(f"[{row['date_utc']}] listing: "
                      f"{fields.get('layout') or '?'} · "
                      f"{('$%.0f' % price) if price else 'no price'} · "
                      f"{fields.get('district') or '?'} · {row['link']}")

        print(f"Watching @{cfg.chat} for new posts. Ctrl-C to stop.")
        await client.run_until_disconnected()
    conn.close()


def reparse(cfg: Config, *, only_new_version: bool = True) -> None:
    """Re-run the parser over stored messages — no network access needed.

    The listings for the chat are rebuilt from scratch rather than upserted:
    a message that stops qualifying as an ad has to lose its row, and an
    upsert would leave the stale one behind forever.
    """
    conn = db.connect(cfg.db_path)
    rows = conn.execute("SELECT * FROM messages WHERE chat=?", (cfg.chat,)).fetchall()
    if not rows:
        print(f"No stored messages for {cfg.chat!r}.")
        conn.close()
        return

    before = conn.execute("SELECT COUNT(*) FROM listings WHERE chat=?",
                          (cfg.chat,)).fetchone()[0]
    conn.execute("DELETE FROM listings WHERE chat=?", (cfg.chat,))

    kept = 0
    batch: list[dict[str, Any]] = []
    for row in rows:
        listing = listing_row(dict(row), cfg)
        if listing:
            batch.append(listing)
        if len(batch) >= 500:
            kept += db.save_listings(conn, batch)
            batch.clear()
    kept += db.save_listings(conn, batch)
    conn.commit()
    conn.close()
    delta = kept - before
    print(f"Re-parsed {len(rows)} messages -> {kept} listings "
          f"({delta:+d} vs before, parser v{parser.PARSER_VERSION}).")


def run(coro) -> None:
    try:
        asyncio.run(coro)
    except KeyboardInterrupt:
        print("\nStopped.")


def fetch_chotot(cfg: Config, region: str, limit: int | None = None) -> None:
    """Pull rental ads from the Chotot gateway into the same tables."""
    source = f"chotot:{region}"
    conn = db.connect(cfg.db_path)
    known = {r[0] for r in conn.execute(
        "SELECT msg_id FROM messages WHERE chat=?", (source,))}

    print(f"Fetching rentals for {region} from Chotot"
          f"{f' (up to {limit})' if limit else ''}...")
    buffer: list[dict[str, Any]] = []
    total = listings = fresh = 0
    try:
        for ad in chotot.iter_ads(region, limit=limit):
            row = chotot.message_row(ad, source)
            row["fetched_at"] = db.now_utc()
            if row["msg_id"] not in known:
                fresh += 1
            buffer.append(row)
            total += 1
            if len(buffer) >= BATCH:
                listings += _flush(conn, buffer, cfg)
                buffer.clear()
                print(f"  {total} ads, {listings} listings...", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nstopping early")
    except Exception as exc:                       # noqa: BLE001 - report and keep what we have
        print(f"\nStopped after {total} ads: {exc.__class__.__name__}: {exc}")
    finally:
        listings += _flush(conn, buffer, cfg)

    print(f"\nStored {total} ads ({fresh} new), {listings} parsed as listings.")
    db.set_state(conn, f"last_fetch:{source}", db.now_utc())
    conn.commit()
    conn.close()


def fetch_muaban(cfg: Config, region: str, limit: int | None = None,
                 max_age_days: int | None = 120) -> None:
    """Pull rental listings from muaban.net's public listing pages."""
    source = f"muaban:{region}"
    conn = db.connect(cfg.db_path)
    known = {r[0] for r in conn.execute(
        "SELECT msg_id FROM messages WHERE chat=?", (source,))}

    print(f"Fetching rentals for {region} from muaban.net"
          f"{f' (up to {limit})' if limit else ''}...")
    buffer: list[dict[str, Any]] = []
    total = listings = fresh = 0
    try:
        for item in muaban.iter_listings(region, limit=limit,
                                         max_age_days=max_age_days):
            row = muaban.message_row(item, source)
            row["raw"] = json.dumps(item, ensure_ascii=False)
            row["fetched_at"] = db.now_utc()
            if row["msg_id"] not in known:
                fresh += 1
            buffer.append(row)
            total += 1
            if len(buffer) >= BATCH:
                listings += _flush(conn, buffer, cfg)
                buffer.clear()
                print(f"  {total} listings...", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nstopping early")
    except Exception as exc:                       # noqa: BLE001
        print(f"\nStopped after {total}: {exc.__class__.__name__}: {exc}")
    finally:
        listings += _flush(conn, buffer, cfg)

    print(f"\nStored {total} listings ({fresh} new), {listings} parsed.")
    db.set_state(conn, f"last_fetch:{source}", db.now_utc())
    conn.commit()
    conn.close()


def fetch_dananglandlord(cfg: Config, limit: int | None = None,
                         max_age_days: int | None = 120) -> None:
    """Pull listings from dananglandlord.com's WordPress REST API."""
    source = "dananglandlord:danang"
    conn = db.connect(cfg.db_path)
    known = {r[0] for r in conn.execute(
        "SELECT msg_id FROM messages WHERE chat=?", (source,))}

    print(f"Fetching Da Nang listings from dananglandlord.com"
          f"{f' (up to {limit})' if limit else ''}...")
    buffer: list[dict[str, Any]] = []
    total = listings = fresh = 0
    try:
        for item in dananglandlord.iter_listings(limit=limit,
                                                 max_age_days=max_age_days):
            row = dananglandlord.message_row(item, source)
            row["fetched_at"] = db.now_utc()
            if row["msg_id"] not in known:
                fresh += 1
            buffer.append(row)
            total += 1
            if len(buffer) >= BATCH:
                listings += _flush(conn, buffer, cfg)
                buffer.clear()
                print(f"  {total} posts...", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nstopping early")
    except Exception as exc:                       # noqa: BLE001
        print(f"\nStopped after {total}: {exc.__class__.__name__}: {exc}")
    finally:
        listings += _flush(conn, buffer, cfg)

    print(f"\nStored {total} posts ({fresh} new), {listings} parsed as listings.")
    db.set_state(conn, f"last_fetch:{source}", db.now_utc())
    conn.commit()
    conn.close()


def fetch_renthome(cfg: Config, region: str, limit: int | None = None,
                   max_age_days: int | None = 120) -> None:
    """Pull listings from renthome.pro."""
    source = f"renthome:{region}"
    conn = db.connect(cfg.db_path)
    known = {r[0] for r in conn.execute(
        "SELECT msg_id FROM messages WHERE chat=?", (source,))}

    print(f"Fetching {region} listings from renthome.pro"
          f"{f' (up to {limit})' if limit else ''}...")
    buffer: list[dict[str, Any]] = []
    total = listings = fresh = 0
    try:
        for ad in renthome.iter_listings(region, limit=limit,
                                         max_age_days=max_age_days):
            row = renthome.message_row(ad, source)
            row["fetched_at"] = db.now_utc()
            if row["msg_id"] not in known:
                fresh += 1
            buffer.append(row)
            total += 1
            if len(buffer) >= BATCH:
                listings += _flush(conn, buffer, cfg)
                buffer.clear()
                print(f"  {total} listings...", end="\r", flush=True)
    except KeyboardInterrupt:
        print("\nstopping early")
    except Exception as exc:                       # noqa: BLE001
        print(f"\nStopped after {total}: {exc.__class__.__name__}: {exc}")
    finally:
        listings += _flush(conn, buffer, cfg)

    print(f"\nStored {total} listings ({fresh} new), {listings} parsed.")
    db.set_state(conn, f"last_fetch:{source}", db.now_utc())
    conn.commit()
    conn.close()
