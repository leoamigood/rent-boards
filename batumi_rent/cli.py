"""Command line interface."""

from __future__ import annotations

import argparse
import sys

from . import analyze, config, db
from .analyze import Filters


def _filter_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("filters")
    g.add_argument("--deal", default="rent_offer",
                   choices=["rent_offer", "rent_seek", "sale", "other", "any"],
                   help="default: rent_offer (someone offering a place)")
    g.add_argument("--term", choices=["long", "daily", "unknown", "any"], default=None)
    g.add_argument("--min-price", type=float, metavar="USD")
    g.add_argument("--max-price", type=float, metavar="USD")
    g.add_argument("--rooms", type=int, nargs="+", metavar="N",
                   help="total rooms, e.g. --rooms 2 3")
    g.add_argument("--min-area", type=float, metavar="M2")
    g.add_argument("--max-area", type=float, metavar="M2")
    g.add_argument("--district", help="substring, e.g. --district Химш")
    g.add_argument("--complex", dest="complex_name", help="substring of the building name")
    g.add_argument("--furnished", action=argparse.BooleanOptionalAction, default=None)
    g.add_argument("--pets", action=argparse.BooleanOptionalAction, default=None)
    g.add_argument("--sea-view", action="store_true")
    g.add_argument("--parking", action="store_true")
    g.add_argument("--no-agent", action="store_true", help="exclude posts that look like agents")
    g.add_argument("--with-photos", action="store_true")
    g.add_argument("--days", type=int, help="only posts from the last N days")
    g.add_argument("--text", help="free-text substring of the original post")
    g.add_argument("--has-price", action="store_true")
    g.add_argument("--has-area", action="store_true")
    g.add_argument("--no-dedupe", dest="dedupe", action="store_false", default=True,
                   help="keep repeated re-posts of the same ad")


def _filters(a: argparse.Namespace) -> Filters:
    return Filters(
        deal=a.deal, term=a.term, min_price=a.min_price, max_price=a.max_price,
        rooms=a.rooms or [], min_area=a.min_area, max_area=a.max_area,
        district=a.district, complex_name=a.complex_name, furnished=a.furnished,
        pets=a.pets, sea_view=a.sea_view, parking=a.parking, no_agent=a.no_agent,
        with_photos=a.with_photos, days=a.days, text=a.text,
        has_price=a.has_price, has_area=a.has_area, dedupe=a.dedupe,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="batumi-rent",
        description="Collect apartment rental posts from a Telegram chat and analyse them.")
    p.add_argument("--chat", help="override TG_CHAT (username, t.me link or id)")
    p.add_argument("--db", help="override DB_PATH")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="one-time Telegram sign-in (asks for the code)")
    sub.add_parser("join", help="join the chat with the logged-in account")

    f = sub.add_parser("fetch", help="download messages (new ones by default)")
    f.add_argument("--limit", type=int, help="stop after N messages")
    f.add_argument("--older", action="store_true",
                   help="continue backwards into older history instead of fetching new")
    f.add_argument("--days", type=int, help="stop once posts are older than N days")

    sub.add_parser("watch", help="stay connected and store new posts as they arrive")
    sub.add_parser("reparse", help="re-run the parser over stored messages (no network)")

    ls = sub.add_parser("list", help="list matching listings")
    _filter_args(ls)
    ls.add_argument("--sort", default="date", choices=sorted(analyze.SORTS))
    ls.add_argument("--limit", type=int, default=30)
    ls.add_argument("--full", action="store_true", help="print the original post text")
    ls.add_argument("--format", choices=["table", "csv", "json"], default="table")
    ls.add_argument("--out", help="write to a file instead of stdout")

    best = sub.add_parser("best", help="cheapest per m² among listings with price and area")
    _filter_args(best)
    best.add_argument("--limit", type=int, default=15)
    best.add_argument("--full", action="store_true")

    st = sub.add_parser("stats", help="market summary, sliced by district / layout / complex")
    _filter_args(st)

    ex = sub.add_parser("export", help="dump matching listings to CSV or JSON")
    _filter_args(ex)
    ex.add_argument("--format", choices=["csv", "json"], default="csv")
    ex.add_argument("--out", default="data/listings.csv")
    ex.add_argument("--limit", type=int, default=1_000_000)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config.load(chat_override=args.chat, db_override=args.db)

    if args.command in {"login", "join", "fetch", "watch", "reparse"}:
        try:
            from . import collect
        except ImportError:
            print("Telethon is not installed. Run: pip install -r requirements.txt",
                  file=sys.stderr)
            return 1

    match args.command:
        case "login":
            async def _login() -> None:
                client = collect.make_client(cfg)
                await client.start(phone=cfg.phone)
                me = await client.get_me()
                print(f"Signed in as {me.first_name} (@{me.username}). "
                      f"Session stored at {cfg.session_path}")
                await client.disconnect()
            collect.run(_login())

        case "join":
            async def _join() -> None:
                client = collect.make_client(cfg)
                async with client:
                    await collect.ensure_joined(client, cfg.chat)
            collect.run(_join())

        case "fetch":
            collect.run(collect.fetch(cfg, limit=args.limit, older=args.older,
                                      since_days=args.days))

        case "watch":
            collect.run(collect.watch(cfg))

        case "reparse":
            collect.reparse(cfg)

        case "list":
            conn = db.connect(cfg.db_path)
            rows = analyze.query(conn, _filters(args), sort=args.sort, limit=args.limit)
            match args.format:
                case "csv":
                    analyze.to_csv(rows, args.out)
                case "json":
                    analyze.to_json(rows, args.out)
                case _:
                    analyze.print_detail(rows) if args.full else analyze.print_table(rows)

        case "best":
            conn = db.connect(cfg.db_path)
            filters = _filters(args)
            filters.has_price = filters.has_area = True
            rows = analyze.query(conn, filters, sort="value", limit=args.limit)
            analyze.print_detail(rows) if args.full else analyze.print_table(rows)

        case "stats":
            conn = db.connect(cfg.db_path)
            analyze.summary(conn, _filters(args))

        case "export":
            conn = db.connect(cfg.db_path)
            rows = analyze.query(conn, _filters(args), sort="date", limit=args.limit)
            (analyze.to_csv if args.format == "csv" else analyze.to_json)(rows, args.out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
