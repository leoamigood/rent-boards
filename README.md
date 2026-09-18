# batumi-rent

Collects apartment rental posts from a Telegram chat (default:
[`t.me/batumiarendachat`](https://t.me/batumiarendachat)), parses the free-form
Russian/English text into structured fields, and stores everything in a local
SQLite file so you can slice offers by price, size, district and availability.

## Why a user account, not a bot

A Telegram **bot** can only read messages in a group where it has been added and
given admin rights. You don't control `@batumiarendachat`, so that route is
closed. This app instead signs in as **your own Telegram account** over MTProto
(via [Telethon](https://docs.telethon.dev)), joins the public chat like any
member, and reads the history. The login is a normal Telegram sign-in: you get a
code in the Telegram app, and a session file is cached locally so you only do it
once.

## Setup

```bash
cd /Users/leo/dev/georgia
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env     # then fill in TG_API_ID / TG_API_HASH / TG_PHONE
```

Get `api_id` and `api_hash` (free, instant) at <https://my.telegram.org> →
*API development tools*.

```bash
.venv/bin/python -m batumi_rent login    # one-time, asks for the SMS/app code
.venv/bin/python -m batumi_rent fetch --limit 5000
```

`fetch` with an existing database only pulls messages newer than what you
already have. To keep digging further back through the archive:

```bash
.venv/bin/python -m batumi_rent fetch --older --limit 5000   # repeat as needed
.venv/bin/python -m batumi_rent watch                        # follow live posts
```

## Analysing

```bash
# market overview, sliced by district / layout / building
python -m batumi_rent stats --term long

# long-term 2-3 room places under $600 from the last 3 weeks, owners only
python -m batumi_rent list --term long --rooms 2 3 --max-price 600 \
       --days 21 --no-agent --sort price

# best value: cheapest $/m² with sea view, at least 50 m²
python -m batumi_rent best --sea-view --min-area 50 --no-agent

# full original text of the matches, with contacts
python -m batumi_rent list --district Химш --max-price 500 --full

# hand the data to a spreadsheet or pandas
python -m batumi_rent export --days 90 --out data/listings.csv
```

Useful filters (all combinable): `--deal rent_offer|rent_seek|sale|any`,
`--term long|daily`, `--min-price/--max-price` (USD), `--rooms N [N...]`,
`--min-area/--max-area`, `--district`, `--complex`, `--furnished/--no-furnished`,
`--pets/--no-pets`, `--sea-view`, `--parking`, `--no-agent`, `--with-photos`,
`--days`, `--text`, `--has-price`, `--has-area`, `--no-dedupe`.
Sort with `--sort price|price-desc|value|area|date|oldest`.

Since everything is plain SQLite, ad-hoc questions work too:

```bash
sqlite3 data/rentals.db "SELECT district, COUNT(*), ROUND(AVG(price_usd))
  FROM v_offers WHERE term='long' GROUP BY 1 ORDER BY 3"
```

## The dashboard

A filterable board of every flat, live at
**<https://leoamigood.github.io/batumi-rent/>** — sorted by value per m², with
filters for budget, size, layout, street and features, and the original post one
click away.

The published copy is built with `--strip-contacts`, which clears the phone and
handle fields *and* redacts numbers from the post text itself — the posters put
those in a group chat, not on the open web. The local copy under `dashboard/`
keeps them and is gitignored.

```bash
.venv/bin/python -m batumi_rent fetch      # new posts
sh tools/deploy_pages.sh                   # rebuild docs/ and push; Pages redeploys
.venv/bin/python tools/build_dashboard.py  # local copy, contacts intact
```

Open the local one with `open dashboard/index.html`.

## How it's put together

| file | role |
|---|---|
| `config.py` | env/`.env` loading, chat-name normalisation |
| `db.py` | schema and upserts — `messages` (raw) + `listings` (parsed) |
| `parser.py` | the text → fields extraction, the part that decides data quality |
| `collect.py` | Telethon: join, backfill, live watch, re-parse |
| `analyze.py` | filter → SQL, tables, CSV/JSON export, summaries |
| `cli.py` | argparse front end |
| `tools/build_dashboard.py` | exports `data.js` for the dashboard |
| `docs/` | the static site GitHub Pages serves |

Raw message text is stored separately from the parsed fields on purpose. When
you improve a regex in `parser.py`, re-apply it to the whole archive without
touching the network:

```bash
python -m batumi_rent reparse
```

## What the parser extracts

Price (with `$`/`₾`/`€`/`лари`/`у.е.` and ranges, normalised to USD via the rates
in `.env`), deal type (offer / wanted / sale), term (long / daily), layout
(`2+1`, studio, `N-комнатная`), rooms and bedrooms, area in m², floor and total
floors, district, street address, building complex, furnished / pets / sea view /
parking, availability date, phone and `@username`, and whether the poster looks
like an agent.

Measured against 40,000 messages (a week of the chat): price 93%, rooms 87%,
furnished 88%, address 76%, area 76%, floor 65%, district 38%. Many ads carry no
verb at all — just a block of address, layout, area, floor and price — so
structural evidence alone is enough to count as a listing.

Two fields are weak because the chat simply does not state them: `available_from`
(2%, most ads say only "заселение по договору") and `is_agent` (5%). Use the post
date as the availability proxy instead. Anything it isn't confident about is left `NULL` rather than guessed — a
missing value is easy to filter out, a wrong price quietly corrupts the analysis.

### Duplicates

Around **60% of posts are repeats**. One flat is typically posted 7–8 times, by
*different accounts* — agencies reposting the same stock with the wording
retyped. Neither a text hash nor the sender id catches that, so listings are
identified by what they are (price, size, rooms, floor) and collapsed to the most
recent copy. Pass `--no-dedupe` to see every post.

Check parser accuracy against the sample posts any time:

```bash
python tests/test_parser.py
```

## Notes

- The session file and database live in `data/` and are gitignored — the
  session grants access to your Telegram account, so don't share it.
- **This chat is busy: roughly 5,500 messages a day**, so `--limit 5000`
  covers less than a day. Building a few months of history means running
  `fetch --older --limit 5000` repeatedly; the archive accumulates in the same
  database and every command reads across all of it.
- Telegram rate-limits bulk history reads. If you hit a `FloodWait`, `fetch`
  stops cleanly and tells you; just re-run it later, it resumes where it left off.
- `GEL_PER_USD` / `EUR_PER_USD` in `.env` are static; update them when you want
  USD comparisons to reflect a newer rate, then run `reparse`.
