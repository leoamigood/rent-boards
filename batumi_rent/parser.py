"""Turn a free-form chat post into structured rental fields.

Posts in Batumi rental chats are mostly Russian, some English, occasionally
Georgian, and written by humans with no template at all. Everything here is
best-effort: a field stays None rather than being guessed wrong, because a NULL
is easy to filter out later while a wrong price silently poisons the analysis.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

PARSER_VERSION = 9

# ---------------------------------------------------------------- normalising

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_SPACES = dict.fromkeys(map(ord, "   ​\t"), " ")


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_DASHES).translate(_SPACES)
    return text.lower().replace("ё", "е")


# ------------------------------------------------------------------- currency

CURRENCY_TOKENS = [
    ("USD", r"\$|usd|у\.?\s?е\.?\b|дол+ар\w*|дол+\.?\b|бакс\w*|\bдол\b"),
    ("GEL", r"₾|\bgel\b|лар[ие]\w*|\bლარი\b|\bl\b(?=\s|$)"),
    ("EUR", r"€|\beur\b|евро"),
]
_CUR_ANY = "|".join(p for _, p in CURRENCY_TOKENS)


def _currency_of(token: str) -> str | None:
    for code, pattern in CURRENCY_TOKENS:
        if re.fullmatch(pattern, token, re.IGNORECASE):
            return code
    for code, pattern in CURRENCY_TOKENS:
        if re.search(pattern, token, re.IGNORECASE):
            return code
    return None


# ---------------------------------------------------------------------- price

_NUM = r"\d{1,3}(?:[ .,]\d{3})+|\d{2,6}"

# number followed by a currency marker:  "500 $", "1 200usd", "800лари"
_PRICE_NUM_CUR = re.compile(rf"(?P<lo>{_NUM})\s*(?:-|—|до)\s*(?P<hi>{_NUM})\s*(?P<cur>{_CUR_ANY})"
                            rf"|(?P<n>{_NUM})\s*(?P<cur2>{_CUR_ANY})", re.IGNORECASE)
# currency marker followed by a number:  "$500", "usd 1200"
_PRICE_CUR_NUM = re.compile(rf"(?P<cur>{_CUR_ANY})\s*(?P<n>{_NUM})", re.IGNORECASE)
# price word followed by a bare number:  "цена 450", "аренда: 500"
_PRICE_WORD = re.compile(
    rf"(?:цена|стоимость|стоит|аренда|арендная плата|плата|rent|price|ფასი)"
    rf"[^\d\n]{{0,20}}(?P<n>{_NUM})", re.IGNORECASE)

# Contexts where a number is definitely not the monthly rent.
_NOT_PRICE_BEFORE = re.compile(
    r"(депозит|залог|комисси\w*|коммунал\w*|оплата за свет|тел\w*|тел\.|whatsapp|"
    r"viber|номер|счет|\+995)[^\d\n]{0,12}$", re.IGNORECASE)
_NOT_PRICE_AFTER = re.compile(
    r"^\s*(кв\.?\s?м|м2|м²|метр\w*|эт\w*|%|год\w*|г\.|шт|комн\w*|человек|мест)",
    re.IGNORECASE)

_SURCHARGE_BEFORE = re.compile(r"[+]\s*$")
_DEPOSIT_AFTER = re.compile(r"^[^.\n]{0,25}(депозит|залог|комисси|возвраща\w*)",
                            re.IGNORECASE)

PRICE_MIN, PRICE_MAX = 50, 100_000

# ----------------------------------------------------------------- VND prices
# Vietnamese ads quote rent in millions of dong — "5,5 млн", "15 миллионов VND",
# "7,5–8 млн донгов" — with dollars appearing only as an occasional aside. The
# figures are far outside the band the dollar/lari logic above is tuned for, so
# they get their own pass rather than a wider net that would loosen that one.
_VND_MILLIONS = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[.,]\d{1,2})?)\s*"
    r"(?:(?:-|–|—|до)\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*)?"
    r"(?:млн|миллион\w*|mln|mil(?:lion)?s?|triệu|trieu|tr)\b", re.IGNORECASE)
_VND_PLAIN = re.compile(
    r"(?<![\d.,])(\d{1,3}(?:[ .,]\d{3}){1,3})\s*(?:vnd|₫|донг\w*)\b", re.IGNORECASE)
# Utilities, deposits and per-unit tariffs are quoted in the same units.
_NOT_RENT_VND = re.compile(
    r"(коммунал\w*|электр\w*|вод[аыу]|интернет|уборк\w*|депозит|залог|комисси\w*|"
    r"киловатт|квт|свет|газ|счетчик\w*|штраф)[^\d\n]{0,30}$", re.IGNORECASE)
# A lari figure in millions would be a sale price, not Vietnamese rent.
_GEL_NEAR = re.compile(r"^\D{0,12}(лар[ие]\w*|gel|₾)", re.IGNORECASE)

VND_MIN, VND_MAX = 2.5e6, 400e6


def extract_price_vnd(text: str) -> tuple[float | None, float | None]:
    """Monthly rent in dong, or (None, None). Second value is a range's top."""
    best: tuple[int, float, float | None] | None = None
    for m in _VND_MILLIONS.finditer(text):
        before = text[max(0, m.start() - 34):m.start()]
        if _NOT_RENT_VND.search(before) or _SURCHARGE_BEFORE.search(before):
            continue
        if _GEL_NEAR.match(text[m.end():m.end() + 16]):
            continue
        lo = _to_number(m.group(1))
        if lo is None:
            continue
        lo *= 1e6
        if not (VND_MIN <= lo <= VND_MAX):
            continue
        hi = _to_number(m.group(2)) * 1e6 if m.group(2) else None
        if hi is not None and not (VND_MIN <= hi <= VND_MAX):
            hi = None
        score = 2 if re.search(r"(цена|стоим|аренд|плата|месяц|rent|снять|сдам)",
                               before, re.IGNORECASE) else 0
        if best is None or score > best[0]:
            best = (score, lo, hi)
    if best:
        return best[1], best[2]

    for m in _VND_PLAIN.finditer(text):
        before = text[max(0, m.start() - 34):m.start()]
        if _NOT_RENT_VND.search(before):
            continue
        value = _to_number(m.group(1))
        if value is not None and VND_MIN <= value <= VND_MAX:
            return value, None
    return None, None


def _to_number(raw: str) -> float | None:
    cleaned = re.sub(r"[ .,](?=\d{3}\b)", "", raw)   # thousands separators only
    cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _price_candidates(text: str) -> list[tuple[float, float | None, str | None, int, int]]:
    """Return (amount, upper_bound, currency, score, position) candidates."""
    out: list[tuple[float, float | None, str | None, int, int]] = []

    def consider(raw: str, cur: str | None, span: tuple[int, int],
                 base: int, raw_hi: str | None = None) -> None:
        value = _to_number(raw)
        if value is None or not (PRICE_MIN <= value <= PRICE_MAX):
            return
        before = text[max(0, span[0] - 30):span[0]]
        after = text[span[1]:span[1] + 12]
        tail = text[span[1]:span[1] + 40]
        if _NOT_PRICE_BEFORE.search(before) or _NOT_PRICE_AFTER.match(after):
            return
        if _SURCHARGE_BEFORE.search(before) or _DEPOSIT_AFTER.match(tail):
            return
        if re.search(r"\d\s*$", before) or re.match(r"^\s*\d", after):
            return                                    # part of a longer digit run
        score = base
        if re.search(r"(цена|стоим|аренд|плата|rent|price|месяц|month|мес\b|"
                     r"на год|\U0001f4b5|\U0001f4b0)", before + tail, re.IGNORECASE):
            score += 2
        high = _to_number(raw_hi) if raw_hi else None
        if high is not None and not (PRICE_MIN <= high <= PRICE_MAX):
            high = None
        out.append((value, high, cur, score, span[0]))

    for m in _PRICE_NUM_CUR.finditer(text):
        if m.group("n"):
            consider(m.group("n"), _currency_of(m.group("cur2")), m.span("n"), 5)
        else:
            consider(m.group("lo"), _currency_of(m.group("cur")), m.span("lo"), 5,
                     raw_hi=m.group("hi"))
    for m in _PRICE_CUR_NUM.finditer(text):
        consider(m.group("n"), _currency_of(m.group("cur")), m.span("n"), 5)
    for m in _PRICE_WORD.finditer(text):
        consider(m.group("n"), None, m.span("n"), 3)
    return out


# "1400$ 1200$ на год" — the old price is struck through in the Telegram
# formatting, which the plain text does not preserve. The second figure wins.
_DISCOUNT = re.compile(rf"({_NUM})\s*(?P<c1>{_CUR_ANY})\s*({_NUM})\s*(?P<c2>{_CUR_ANY})",
                       re.IGNORECASE)


def extract_price(text: str) -> tuple[float | None, float | None, str | None]:
    vnd, vnd_hi = extract_price_vnd(text)
    if vnd is not None:
        return vnd, vnd_hi, "VND"

    if m := _DISCOUNT.search(text):
        was, now = _to_number(m.group(1)), _to_number(m.group(3))
        if was and now and now < was and PRICE_MIN <= now <= PRICE_MAX:
            return now, None, _currency_of(m.group("c2"))

    candidates = _price_candidates(text)
    if not candidates:
        return None, None, None
    # Highest confidence wins; among equals take the earliest, which is where
    # ads put the headline figure. Deposits and surcharges are dropped above
    # rather than being out-competed on amount.
    value, high, cur, _, _ = min(candidates, key=lambda c: (-c[3], c[4]))
    return value, high, cur


def to_usd(price: float | None, currency: str | None, gel_per_usd: float,
           eur_per_usd: float, vnd_per_usd: float = 25_500.0) -> float | None:
    if price is None:
        return None
    match currency:
        case "GEL":
            return round(price / gel_per_usd, 2)
        case "EUR":
            return round(price / eur_per_usd, 2)
        case "VND":
            return round(price / vnd_per_usd, 2)
        case _:
            # Bare numbers are quoted in dollars by convention. An earlier rule
            # read a large bare number as lari; it never once fired on the
            # Batumi archive it was written for, and misread Vietnamese posts,
            # so it is gone. A bare figure too large to be rent is caught by the
            # sale reclassification in parse() instead.
            return round(price, 2)


# ----------------------------------------------------------------- deal / term

_RENT_OFFER = re.compile(
    r"сда(?:м|ю|ется|ётся|ем|етс)|сдаётся|сдается|в аренду|аренда|арендую|"
    r"for rent|rent out|to let|ქირავდება", re.IGNORECASE)
_RENT_SEEK = re.compile(
    r"сни(?:му|мем)|ищу\s+(?:кварт|жиль|студи|дом|апарт)|ищем\s+(?:кварт|жиль)|"
    r"нужна\s+кварт|требуется\s+кварт|looking for|wanted|need (?:a )?(?:flat|apartment)",
    re.IGNORECASE)
_SALE = re.compile(r"прода(?:м|ю|ется|ётся|жа)|for sale|იყიდება", re.IGNORECASE)

_DAILY = re.compile(
    r"посуточн\w*|на сутки|\bсутк\w*|краткосрочн\w*|daily|per night|за ночь|"
    r"на неделю|weekly", re.IGNORECASE)
_LONG = re.compile(
    r"долгосрочн\w*|длительн\w*|на длительный|помесячно|в месяц|на месяц|"
    r"на год|от\s*\d+\s*мес|long[- ]?term|monthly|per month", re.IGNORECASE)


def extract_deal(text: str) -> tuple[str, str]:
    # Whichever intent is stated first wins. Sale ads routinely mention renting
    # further down (agency footers, "аренда" in a list of services), so an
    # elif-chain that preferred the rental verb misfiled them as offers.
    found = [(m.start(), kind) for kind, m in (
        ("rent_seek", _RENT_SEEK.search(text)),
        ("rent_offer", _RENT_OFFER.search(text)),
        ("sale", _SALE.search(text)),
    ) if m]
    deal = min(found)[1] if found else "other"

    if _DAILY.search(text):
        term = "daily"
    elif _LONG.search(text):
        term = "long"
    else:
        term = "unknown"
    return deal, term


# --------------------------------------------------------------- rooms / size

_LAYOUT = re.compile(r"\b([1-5])\s*\+\s*([0-2])\b")
_STUDIO = re.compile(r"студи\w*|\bstudio\b|апартамент[- ]студи", re.IGNORECASE)
_ROOMS_NUM = re.compile(r"\b([1-6])\s*[- ]?\s*(?:х|x)?\s*комн\w*", re.IGNORECASE)
_ROOMS_WORD = {
    "однокомнат": 1, "1-комнат": 1, "двухкомнат": 2, "двукомнат": 2,
    "трехкомнат": 3, "трёхкомнат": 3, "четырехкомнат": 4, "четырёхкомнат": 4,
    "пятикомнат": 5,
}
_BEDROOMS_EN = re.compile(
    r"\b([1-5])\s*[-–]?\s*(?:bed\s?rooms?|bedrooms?|beds?\b|br\b|bdr\b)",
    re.IGNORECASE)
# Vietnamese ads count bedrooms rather than using the Batumi "2+1" notation.
_BEDROOMS_RU = re.compile(r"\b([1-5])\s*(?:-|\s)?\s*спал[ье]\w*", re.IGNORECASE)
_BEDROOMS_VN = re.compile(r"\b([1-5])\s*(?:pn\b|phòng ngủ|phong ngu)", re.IGNORECASE)
_BEDROOMS_WORD = {"одна спальн": 1, "две спальн": 2, "двумя спальн": 2,
                  "три спальн": 3, "тремя спальн": 3, "четыре спальн": 4}
_FLAT_SLANG = {"однушк": 1, "двушк": 2, "трешк": 3, "трёшк": 3, "студийк": 0}

_AREA = re.compile(
    r"(?<![\d,.])(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:кв\.?\s*м\.?|кв\.?метр\w*|"
    r"[мm]\s*[²2]\b|м\.?кв|квадратн\w*\s*метр\w*|sq\.?\s?m|sqm|"
    r"m2\b|мет\w*\s*вуон|кв\b)", re.IGNORECASE)
_AREA_WORD = re.compile(r"(?:площад\w*|area)[^\d\n]{0,10}(\d{1,4}(?:[.,]\d{1,2})?)",
                        re.IGNORECASE)
AREA_MIN, AREA_MAX = 8, 600

_FLOOR_OF = re.compile(
    r"\b(\d{1,2})\s*эт\w*\.?,?\s*(?:из|/)\s*(\d{1,2})"            # 12 этаж из 24
    r"|\b(\d{1,2})\s*(?:/|из)\s*(\d{1,2})\b(?=[^\d]{0,12}эт|\s*$)"  # 3/7 эт
    r"|эт\w*\.?\s*:?\s*(\d{1,2})\s*(?:/|из)\s*(\d{1,2})"           # этаж 3/7
    r"|\b(\d{1,2})(?:st|nd|rd|th)?\s*floor\s*(?:of|/)\s*(\d{1,2})",  # 9th floor of 24
    re.IGNORECASE)
_FLOOR = re.compile(r"\b(\d{1,2})\s*(?:-?[ыи]?й)?\s*эт\w*|эт\w*\.?\s*:?\s*(\d{1,2})\b|"
                    r"\b(\d{1,2})(?:st|nd|rd|th)?\s*floor\b", re.IGNORECASE)


def extract_rooms(text: str) -> tuple[int | None, int | None, str | None]:
    """-> (rooms, bedrooms, layout label). '2+1' = 2 bedrooms + 1 living room."""
    if m := _LAYOUT.search(text):
        bed, living = int(m.group(1)), int(m.group(2))
        return bed + living, bed, f"{bed}+{living}"
    if _STUDIO.search(text):
        return 1, 0, "studio"
    if m := _ROOMS_NUM.search(text):
        n = int(m.group(1))
        return n, max(n - 1, 0), f"{n} комн"
    for word, n in _ROOMS_WORD.items():
        if word in text:
            return n, max(n - 1, 0), f"{n} комн"
    if m := _BEDROOMS_EN.search(text):
        n = int(m.group(1))
        return n + 1, n, f"{n} bedroom"
    if m := _BEDROOMS_RU.search(text):
        n = int(m.group(1))
        return n + 1, n, f"{n} bedroom"
    if m := _BEDROOMS_VN.search(text):
        n = int(m.group(1))
        return n + 1, n, f"{n} bedroom"
    for word, n in _BEDROOMS_WORD.items():
        if word in text:
            return n + 1, n, f"{n} bedroom"
    for word, n in _FLAT_SLANG.items():
        if word in text:
            return max(n, 1), n, "studio" if n == 0 else f"{n} bedroom"
    return None, None, None


def extract_area(text: str) -> float | None:
    for regex in (_AREA, _AREA_WORD):
        for m in regex.finditer(text):
            value = _to_number(m.group(1))
            if value is not None and AREA_MIN <= value <= AREA_MAX:
                return value
    return None


def extract_floor(text: str) -> tuple[int | None, int | None]:
    if m := _FLOOR_OF.search(text):
        groups = [g for g in m.groups() if g]
        if len(groups) >= 2:
            floor, total = int(groups[0]), int(groups[1])
            if 0 < floor <= total <= 60:
                return floor, total
    if m := _FLOOR.search(text):
        for g in m.groups():
            if g and 0 < int(g) <= 60:
                return int(g), None
    return None, None


# ------------------------------------------------------------------- location

DISTRICTS: dict[str, tuple[str, ...]] = {
    "Старый Батуми": ("старый батуми", "старый город", "old town", "old batumi"),
    "Новый бульвар": ("новый бульвар", "new boulevard", "нов. бульвар"),
    "Старый бульвар": ("старый бульвар", "old boulevard"),
    "Химшиашвили": ("химшиашвили", "khimshiashvili"),
    "Горгасали": ("горгасали", "gorgasali"),
    "Багратиони": ("багратиони", "bagrationi"),
    "Мелашвили": ("мелашвили", "melashvili"),
    "Пушкина": ("пушкина", "pushkin"),
    "Лермонтова": ("лермонтова", "lermontov"),
    "Джавахишвили": ("джавахишвили", "javakhishvili"),
    "Тбел Абусеридзе": ("тбел абусеридзе", "абусеридзе", "abuseridze"),
    "Шериф Химшиашвили": ("шериф", "sheriff"),
    "Ангиса": ("ангиса", "angisa"),
    "Аэропорт": ("аэропорт", "airport"),
    "Гонио": ("гонио", "gonio"),
    "Квариати": ("квариати", "kvariati"),
    "Сарпи": ("сарпи", "sarpi"),
    "Махинджаури": ("махинджаури", "makhinjauri"),
    "Зеленый мыс": ("зеленый мыс", "зелёный мыс", "green cape"),
    "Кобулети": ("кобулети", "kobuleti"),
    "Центр": ("центр города", "в центре", "city center", "city centre"),
}

COMPLEXES: tuple[str, ...] = (
    "orbi city", "orbi residence", "orbi sea towers", "orbi beach tower",
    "orbi twin towers", "orbi", "next", "porta batumi", "alliance palace",
    "alliance centropolis", "alliance privilege", "metro city", "calligraphy",
    "black sea tower", "white sails", "sea towers", "batumi view", "grand marine",
    "magnolia", "dreamland oasis", "gumbati", "arcgroup", "archi", "riviera",
    "green cape", "mgzavrebi", "wyndham", "le palais", "sunrise", "cosmos",
)

STREETS: tuple[str, ...] = (
    "згвиспири", "згвиспирис", "химшиашвили", "шартава", "лориа", "парнаваза",
    "такаишвили", "горгасали", "багратиони", "мелашвили", "пушкина", "лермонтова",
    "джавахишвили", "абусеридзе", "инасаридзе", "кобаладзе", "ниношвили",
    "зубалашвили", "гогебашвили", "тавдадебули", "пиросмани", "руставели",
    "чавчавадзе", "церетели", "бараташвили", "гамсахурдиа", "маяковского",
    "палиашвили", "чичинадзе", "асатиани", "халваши", "мазниашвили",
    "гришашвили", "эркомаишвили", "аллея героев", "славы", "демократиули",
    "апхазская", "ангиса", "леха и марии качиньских", "качиньских", "фридона халваши",
)

# "Адрес: Такаишвили 12", "📍Улица Згвиспири дом 10L", "⭕️ Шартава 16"
_ADDRESS_MARKED = re.compile(
    r"(?:адрес|улица|ул\.|street)\s*:?\s*"
    r"(?:улица\s*|ул\.\s*)?([а-яa-z][а-яa-z.\- ]{2,28}?)\s*(?:дом\s*|д\.\s*)?(\d{1,3}\s*[a-zа-я]?)\b",
    re.IGNORECASE)
_ADDRESS_STREET = re.compile(
    r"\b(" + "|".join(STREETS) + r")\s*(?:дом\s*|д\.\s*)?(\d{1,3}\s*[a-zа-я]?)?\b",
    re.IGNORECASE)

_SEA_LINE = re.compile(r"перв(?:ая|ой) лини\w*|first line|на берегу", re.IGNORECASE)


def extract_address(text: str) -> str | None:
    if m := _ADDRESS_MARKED.search(text):
        street = m.group(1).strip(" .-")
        if len(street) > 2 and not street.isdigit():
            return f"{street.title()} {m.group(2).strip()}"
    if m := _ADDRESS_STREET.search(text):
        street = m.group(1).title()
        return f"{street} {m.group(2).strip()}" if m.group(2) else street
    return None


# Chats that cover a whole country need the city as its own dimension. Aliases
# are matched against the normalised (lowercased, ё→е) text.
CITIES: dict[str, tuple[str, ...]] = {
    "Nha Trang": ("нячанг", "нha trang", "nha trang", "нha-trang", "нячанге", "нячанга"),
    "Da Nang": ("дананг", "да нанг", "да-нанг", "da nang", "danang", "дананге", "днг"),
    "Phu Quoc": ("фукуок", "фу куок", "phu quoc", "phuquoc", "фукуоке", "фукуока"),
    "Ho Chi Minh": ("хошимин", "сайгон", "ho chi minh", "saigon", "hcmc", "хчм"),
    "Hanoi": ("ханой", "hanoi", "ha noi", "ханое"),
    "Mui Ne": ("муйне", "муй не", "mui ne", "муйнe"),
    "Vung Tau": ("вунгтау", "вунг тау", "vung tau"),
    "Da Lat": ("далат", "da lat", "dalat", "далате"),
    "Hoi An": ("хойан", "хой ан", "hoi an", "хойане"),
    "Phan Thiet": ("фантьет", "phan thiet"),
    "Cam Ranh": ("камрань", "cam ranh", "камрани"),
    "Quy Nhon": ("куинен", "quy nhon", "куинён"),
    "Hue": ("хюэ", "\bhue\b"),
    "Ha Long": ("халонг", "ha long", "halong"),
    "Batumi": ("батуми", "batumi"),
}


def extract_city(text: str) -> str | None:
    best: tuple[int, str] | None = None
    for name, aliases in CITIES.items():
        for alias in aliases:
            pos = text.find(alias) if "\\b" not in alias else -1
            if pos >= 0 and (best is None or pos < best[0]):
                best = (pos, name)
                break
    return best[1] if best else None


def extract_location(text: str) -> tuple[str | None, str | None, str | None, str | None]:
    district = next((name for name, aliases in DISTRICTS.items()
                     if any(a in text for a in aliases)), None)
    complex_name = next((c.title() for c in COMPLEXES if c in text), None)
    return district, complex_name, extract_address(text), extract_city(text)


# ---------------------------------------------------------------------- flags

# Most ads never say "furnished" — they list the white goods instead.
_FURNISHED = re.compile(
    r"с мебелью|мебель|меблирован\w*|обставлен\w*|furnished|техник\w*|"
    r"посудомо\w*|стиральн\w*|микроволнов\w*|духовк\w*|кондиционер\w*|"
    r"холодильник\w*|чайник\w*|комплекты белья|вся посуда|washing machine|"
    r"dishwasher|microwave|fridge", re.IGNORECASE)
_NO_FURNITURE = re.compile(r"без мебели|unfurnished|пуста[яй]", re.IGNORECASE)
_PETS_OK = re.compile(r"можно с животн\w*|с животными можно|pet[- ]friendly|"
                      r"можно с котом|можно с собак\w*|животные можно|"
                      r"[\u2705\u2714]\ufe0f?\s*[\U0001f415\U0001f408\U0001f436\U0001f431]",
                      re.IGNORECASE)
_PETS_NO = re.compile(r"без животн\w*|животные нельзя|не с животными|no pets|"
                      r"без домашних животн\w*|нельзя с животн\w*|"
                      r"[\u274c\u26d4\U0001f6ab]\ufe0f?\s*[\U0001f415\U0001f408\U0001f436\U0001f431]",
                      re.IGNORECASE)
_SEA_VIEW = re.compile(r"вид на море|sea view|видом на море|panorama of the sea",
                       re.IGNORECASE)
_PARKING = re.compile(r"парковк\w*|паркинг|parking|гараж", re.IGNORECASE)
_AGENT = re.compile(r"агент\w*|агентств\w*|ри[еэ]лтор\w*|realtor|комисси\w*|"
                    r"услуги риелтора", re.IGNORECASE)
# "Комиссия с клиента не взымается" is an agency saying the tenant pays nothing,
# so it belongs with the no-fee posts rather than with the commission ones.
_NO_AGENT = re.compile(r"без комисси\w*|без посредник\w*|от собственник\w*|"
                       r"собственник|хозяин|no commission|owner|"
                       r"комисси\w*[^.\n]{0,20}не\s*вз[иы]ма\w*|комиссии нет",
                       re.IGNORECASE)


def _tri(yes: re.Pattern[str], no: re.Pattern[str], text: str) -> int | None:
    if no.search(text):
        return 0
    if yes.search(text):
        return 1
    return None


# -------------------------------------------------------------------- contact

_PHONE = re.compile(r"(?:\+?995[\s\-]?)?(?:5\d{2}|\(\d{2,4}\))[\s\-]?\d{2}[\s\-]?\d{2}[\s\-]?\d{2}\b"
                    r"|\+\d{1,3}[\s\-]?\d{2,3}[\s\-]?\d{2,3}[\s\-]?\d{2,4}")
_USERNAME = re.compile(r"@([a-z][a-z0-9_]{4,31})", re.IGNORECASE)

_AVAILABLE = re.compile(
    r"(?:свободн\w*|заселение|доступн\w*|освобождается|available)[^\n]{0,25}?"
    r"(\d{1,2}\s*(?:янв|фев|мар|апр|ма[йя]|июн|июл|авг|сен|окт|ноя|дек)\w*|"
    r"\d{1,2}[./]\d{1,2}(?:[./]\d{2,4})?|сейчас|сегодня|завтра|now)", re.IGNORECASE)


def extract_contacts(raw_text: str) -> tuple[str | None, str | None]:
    phone = None
    if m := _PHONE.search(raw_text):
        digits = re.sub(r"\D", "", m.group(0))
        if 9 <= len(digits) <= 15:
            phone = "+" + digits if not digits.startswith("995") else "+" + digits
    username = None
    if m := _USERNAME.search(raw_text):
        username = "@" + m.group(1)
    return phone, username


# ------------------------------------------------------------------ assembling

def detect_lang(text: str) -> str:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "unknown"
    cyr = sum(1 for c in letters if "Ѐ" <= c <= "ӿ")
    geo = sum(1 for c in letters if "Ⴀ" <= c <= "ჿ")
    if geo / len(letters) > 0.3:
        return "ka"
    return "ru" if cyr / len(letters) > 0.3 else "en"


def dup_key(text: str, sender_id: int | None) -> str:
    """Fingerprint for spotting the same ad reposted (a habit in these chats)."""
    core = re.sub(r"[^\w]+", "", normalise(text))[:300]
    return hashlib.sha1(f"{sender_id}|{core}".encode()).hexdigest()[:16]


_SUBJECT = re.compile(r"кварт|студи|апарт|жиль|дом\b|комнат|flat|apartment|room",
                      re.IGNORECASE)
_LAYOUT_ANY = re.compile(r"\b[1-5]\s*\+\s*[0-2]\b")


def _structure(text: str) -> tuple[bool, float | None, float | None, int | None]:
    has_layout = bool(_LAYOUT_ANY.search(text) or _STUDIO.search(text)
                      or extract_rooms(text)[0] is not None)
    price, _, _ = extract_price(text)
    return has_layout, price, extract_area(text), extract_floor(text)[0]


def is_listing(raw_text: str) -> bool:
    """Does this post look like a rental ad at all?

    A large share of real ads never use a verb like "сдается" — they are just a
    block of address / layout / area / floor / price. Requiring the verb threw
    roughly a fifth of them away, so structural evidence counts on its own.
    """
    if len(raw_text or "") < 25:
        return False
    text = normalise(raw_text)
    deal, _ = extract_deal(text)
    has_layout, price, area, floor = _structure(text)
    facts = sum(x is not None for x in (price, area, floor)) + has_layout

    if deal != "other":
        return bool(_SUBJECT.search(text) or has_layout or facts >= 2)
    # No verb anywhere: a layout plus a price or a size is the giveaway.
    return has_layout and (price is not None or area is not None)


def parse(raw_text: str, *, sender_id: int | None = None,
          gel_per_usd: float = 2.70, eur_per_usd: float = 0.92,
          vnd_per_usd: float = 25_500.0) -> dict[str, Any]:
    text = normalise(raw_text)
    deal, term = extract_deal(text)
    price, price_hi, currency = extract_price(text)
    rooms, bedrooms, layout = extract_rooms(text)
    area = extract_area(text)
    floor, floors_total = extract_floor(text)
    district, complex_name, address, city = extract_location(text)
    phone, contact = extract_contacts(raw_text or "")

    price_usd = to_usd(price, currency, gel_per_usd, eur_per_usd, vnd_per_usd)
    price_max_usd = to_usd(price_hi, currency, gel_per_usd, eur_per_usd, vnd_per_usd)

    # These thresholds are in dollars, so they have to be applied to the
    # converted figure — a Vietnamese rent of 15 million dong is $590, not a
    # sale price.
    if deal == "rent_offer" and (price_usd or 0) >= 10_000:
        deal = "sale"
    if deal == "other" and (layout or rooms) and price_usd:
        # A structured ad with no verb. These chats are for renting, so an ad
        # is an offer unless the figure is plainly a purchase price.
        deal = "sale" if price_usd >= 15_000 else "rent_offer"

    available = None
    if m := _AVAILABLE.search(text):
        available = m.group(1).strip()

    is_agent = 0 if _NO_AGENT.search(text) else (1 if _AGENT.search(text) else None)

    return {
        "deal_type": deal,
        "term": term,
        "price": price,
        "currency": currency,
        "price_usd": price_usd,
        "price_max_usd": price_max_usd,
        "rooms": rooms,
        "bedrooms": bedrooms,
        "layout": layout,
        "area_sqm": area,
        "floor": floor,
        "floors_total": floors_total,
        "district": district or ("Первая линия" if _SEA_LINE.search(text) else None),
        "complex_name": complex_name,
        "address": address,
        "city": city,
        "furnished": _tri(_FURNISHED, _NO_FURNITURE, text),
        "pets": _tri(_PETS_OK, _PETS_NO, text),
        "sea_view": 1 if _SEA_VIEW.search(text) else None,
        "parking": 1 if _PARKING.search(text) else None,
        "available_from": available,
        "is_agent": is_agent,
        "phone": phone,
        "contact": contact,
        "lang": detect_lang(text),
        "dup_key": dup_key(raw_text, sender_id),
        "usd_per_sqm": round(price_usd / area, 2) if price_usd and area else None,
        "parser_version": PARSER_VERSION,
    }
