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
from .sources import chotot
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


def message_row(msg: Message, chat: str) -> dict[str, Any]:
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
        "link": f"https://t.me/{chat}/{msg.id}",
        "fetched_at": db.now_utc(),
    }


def listing_row(msg_row: dict[str, Any], cfg: Config) -> dict[str, Any] | None:
    text = msg_row.get("text") or ""

    # Sources that already know the price, size and bedroom count keep their
    # own values; the text parser still runs underneath to pick up what the
    # structured record does not carry (sea view, furnishing, term, pets).
    structured: dict[str, Any] = {}
    if raw := msg_row.get("raw"):
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            record = {}
        if record.get("_src") == "chotot":
            structured = chotot.listing_fields(record, cfg.vnd_per_usd)

    if not structured and not parser.is_listing(text):
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
                since_days: int | None = None) -> None:
    conn = db.connect(cfg.db_path)
    client = make_client(cfg)
    async with client:
        await ensure_joined(client, cfg.chat)
        entity = await resolve_chat(client, cfg.chat)

        kwargs: dict[str, Any] = {"limit": limit}
        known_max = db.max_msg_id(conn, cfg.chat)
        known_min = db.min_msg_id(conn, cfg.chat)
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
                buffer.append(message_row(msg, cfg.chat))
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
        db.set_state(conn, f"last_fetch:{cfg.chat}", db.now_utc())
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
    """Re-run the parser over stored messages — no network access needed."""
    conn = db.connect(cfg.db_path)
    rows = conn.execute("SELECT * FROM messages WHERE chat=?", (cfg.chat,)).fetchall()
    updated = 0
    batch: list[dict[str, Any]] = []
    for row in rows:
        listing = listing_row(dict(row), cfg)
        if listing:
            batch.append(listing)
        if len(batch) >= 500:
            updated += db.save_listings(conn, batch)
            batch.clear()
    updated += db.save_listings(conn, batch)
    # Drop rows that no longer parse as listings at all.
    conn.execute(
        "DELETE FROM listings WHERE chat=? AND parser_version < ?",
        (cfg.chat, parser.PARSER_VERSION))
    conn.commit()
    conn.close()
    print(f"Re-parsed {len(rows)} messages -> {updated} listings "
          f"(parser v{parser.PARSER_VERSION}).")


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
