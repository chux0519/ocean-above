"""ReplyGuard: every number and every in-db city name in LLM text is traced by value (design §4.5).

A reply may contain
- a number only when it equals a value the records show (display values such as `$1,160.71` or
  `8,928.6 km`; the counts `count`, `leg_count`, `transfer_count`) or a number the user wrote in
  this message. A number with a unit must match a value with that unit: `$x` / `x USD` only a
  price, `x km` only a distance. A number with any other unit (miles, hours, k, %, ...) fails;
- a city of locations.db only when that city is in the records or the user named it (in this
  message or an earlier one of the conversation);
- a date or a time only when the records, the user's message or today's date have it;
- a claim that a ticket was booked / confirmed only when the records hold a booking (a ticket
  booked by the user's click, or a non-empty ticket list).
Values a tool merely echoes from the LLM's own arguments (`query`, `filter`, error messages, an
unknown quote id) are not records. Record ids and a list numbered 1, 2, 3 are not numbers.
Anything else is a problem, and the caller replaces the whole reply with a code template.

Known gaps (design §11): numbers written as words, cities that are not in locations.db, cities
written in another script (prompt: always use the English name of the records), and a one-word
city name written in lower case (skipped on purpose: `nice`, `split`, `mobile` are also words).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

# Letters NFKD does not decompose (same table as core.places.fold; a test keeps the two equal on
# every city of locations.db), plus typographic apostrophes and hyphens that LLMs like to write.
_EXTRA_FOLDS = str.maketrans({
    "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ı": "i",
    "’": "'", "‘": "'", "ʼ": "'", "‐": "-", "‑": "-",
})


def _fold_char(ch: str) -> str:
    decomposed = unicodedata.normalize("NFKD", ch)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.casefold().translate(_EXTRA_FOLDS)


def fold(name: str) -> str:
    """Matching key: case-insensitive, accent-insensitive, whitespace-normalized."""
    return " ".join("".join(_fold_char(ch) for ch in name).split())


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Folded text plus, for every folded character, the index of its source character."""
    out: list[str] = []
    origin: list[int] = []
    for i, ch in enumerate(text):
        for f in _fold_char(ch):
            out.append(f)
            origin.append(i)
    return "".join(out), origin


@dataclass(frozen=True)
class Mention:
    text: str  # as written
    cities: tuple[str, ...]  # the db cities it can mean (one when written exactly)


class CityIndex:
    """Finds the cities of locations.db in free text."""

    def __init__(self, names: Iterable[str]):
        self.names = frozenset(unicodedata.normalize("NFC", n) for n in names)
        by_key: dict[str, list[str]] = {}
        for name in self.names:
            by_key.setdefault(fold(name), []).append(name)
        self.by_key = {k: tuple(sorted(v)) for k, v in by_key.items() if k}
        keys = sorted(self.by_key, key=len, reverse=True)  # longest first: `New York` before `York`
        body = "|".join(r"\s+".join(re.escape(part) for part in key.split(" ")) for key in keys)
        self._pattern = re.compile(rf"(?<![^\W_])(?:{body})(?![^\W_])") if keys else None

    def mentions(self, text: str, proper_only: bool = True) -> list[Mention]:
        """Cities mentioned in `text`. With `proper_only`, a one-word name counts only when it is
        written with a capital letter."""
        if self._pattern is None or not text:
            return []
        text = unicodedata.normalize("NFC", text)
        folded, origin = _fold_with_map(text)
        found = []
        for m in self._pattern.finditer(folded):
            written = text[origin[m.start()]: origin[m.end() - 1] + 1]
            key = " ".join(m.group(0).split())
            if proper_only and key.isalpha() and not written[:1].isupper():
                continue
            exact = " ".join(written.split())
            cities = (exact,) if exact in self.names else self.by_key[key]
            found.append(Mention(written, cities))
        return found


# --- numbers, dates, ids --------------------------------------------------------------------
_ID = re.compile(r"\b[a-z]{1,3}_[0-9a-f]{6,}\b")
# Any digit run is a number, whatever is in front of it (`OA1234`, `RMB6800`, `_950_`).
_NUMBER = re.compile(r"(?<![\d.,])\d+(?:,\d{3})*(?:\.\d+)?")
_LIST_MARKER = re.compile(r"(?m)^([ \t>*-]*)(\d{1,2})([.)])(?=\s)")
_USD_BEFORE = re.compile(r"(?:\$|US\$|USD\s?|US\s?dollars?\s)\s*$", re.I)
_UNIT_AFTER = re.compile(
    r"\s*(?:(?P<km>km\b|kilomet(?:er|re)s?\b|公里|千米)"
    r"|(?P<usd>USD\b|US\s?dollars?\b|dollars?\b|美元|美金)"
    r"|(?P<bad>(?i:miles?|mi|hours?|hrs?|h|minutes?|mins?|days?|nights?|weeks?|k|K|thousand|million|"
    r"percent|am|pm|a\.m\.|p\.m\.)\b|%|万|千|小时|分钟|英里|天))")
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2})?)?Z?")
_TIME = re.compile(r"(?<![\d:.,])(\d{1,2}):(\d{2})(?::\d{2})?(?![\d:])")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MONTH_RE = "|".join(sorted(_MONTHS, key=len, reverse=True))
_MONTH_FIRST = re.compile(
    rf"(?i)\b({_MONTH_RE})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?!\d)(?:,?\s+(\d{{4}}))?")
_DAY_FIRST = re.compile(
    rf"(?i)(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_RE})\b\.?(?:,?\s+(\d{{4}}))?")


def _decimal(token: str) -> Decimal | None:
    try:
        return Decimal(token.replace(",", ""))
    except InvalidOperation:
        return None


@dataclass
class _Scan:
    numbers: list[tuple[str, Decimal, str | None]]  # (written, value, unit: "usd" | "km" | "bad" | None)
    dates: list[tuple[str, tuple[int | None, int, int]]]  # (written, (year or None, month, day))
    times: list[tuple[str, str]]  # (written, "HH:MM")
    ids: list[str]


def _scan(text: str) -> _Scan:
    """Split a text into ids, dates, times and plain numbers (each digit belongs to one of them)."""
    out = _Scan([], [], [], [])

    def take_id(m: re.Match) -> str:
        out.ids.append(m.group(0))
        return " "

    def take_iso(m: re.Match) -> str:
        out.dates.append((m.group(0), (int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        if m.group(4):
            out.times.append((m.group(0), f"{m.group(4)}:{m.group(5)}"))
        return " "

    def take_month_first(m: re.Match) -> str:
        year = int(m.group(3)) if m.group(3) else None
        out.dates.append((m.group(0), (year, _MONTHS[m.group(1).lower()], int(m.group(2)))))
        return " "

    def take_day_first(m: re.Match) -> str:
        year = int(m.group(3)) if m.group(3) else None
        out.dates.append((m.group(0), (year, _MONTHS[m.group(2).lower()], int(m.group(1)))))
        return " "

    def take_time(m: re.Match) -> str:
        out.times.append((m.group(0), f"{int(m.group(1)):02d}:{m.group(2)}"))
        return " "

    text = _ID.sub(take_id, text)
    text = _ISO.sub(take_iso, text)
    text = _MONTH_FIRST.sub(take_month_first, text)
    text = _DAY_FIRST.sub(take_day_first, text)
    text = _TIME.sub(take_time, text)
    text = _LIST_MARKER.sub(_ListMarkers().take, text)
    for m in _NUMBER.finditer(text):
        value = _decimal(m.group(0))
        if value is None:
            continue
        unit = None
        after = _UNIT_AFTER.match(text, m.end())
        if after is not None:
            unit = after.lastgroup
        if _USD_BEFORE.search(text, 0, m.start()):
            unit = "usd" if unit in (None, "usd") else "bad"
        out.numbers.append((m.group(0), value, unit))
    return out


class _ListMarkers:
    """Line-start `1.` / `2)` count as list markers only in sequence: 1, 2, 3 ... A `7.` that does
    not continue a list is a number like any other."""

    def __init__(self) -> None:
        self.next = 1

    def take(self, m: re.Match) -> str:
        n = int(m.group(2))
        if n == 1 or n == self.next:
            self.next = n + 1
            return m.group(1) + " " + " " * len(m.group(3))
        return m.group(0)


# Fields that echo what the LLM itself wrote into a tool call; they prove nothing.
_ECHO_KEYS = frozenset({"query", "filter", "message", "available"})
_ECHO_CODES = frozenset({"not_found", "invalid_arguments", "unknown_tool", "tool_failed", "bad_request"})
_COUNT_KEYS = frozenset({"count", "leg_count", "transfer_count"})


def _strings_and_counts(value: Any, strings: list[str], counts: list[Decimal]) -> None:
    if isinstance(value, dict):
        if value.get("code") in _ECHO_CODES:
            return  # a refused call: everything in it came from the LLM's arguments
        for key, sub in value.items():
            if key in _ECHO_KEYS:
                continue
            if key in _COUNT_KEYS and isinstance(sub, int) and not isinstance(sub, bool):
                counts.append(Decimal(sub))
            else:
                _strings_and_counts(sub, strings, counts)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            _strings_and_counts(sub, strings, counts)
    elif isinstance(value, str):
        strings.append(value)


def _is_booking_evidence(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    if record.get("event") == "ticket_booked_by_user_click":
        return True
    return record.get("code") == "ok" and isinstance(record.get("tickets"), list) and bool(record["tickets"])


# Affirmative "it is booked" phrasing. Negations and instructions ("Nothing is booked until you
# click", "the ticket is only issued once you click") do not match.
_BOOKED = re.compile(
    r"(?ix)"
    r"\b(?:ticket|booking|flight|reservation|trip|seat)s?\b[^.!?\n]{0,40}?"
    r"\b(?:is|are|has\s+been|have\s+been|was|were|got)\s+(?:now\s+|all\s+|successfully\s+)?"
    r"(?:booked|confirmed|issued|reserved)\b(?!\s+(?:only|until|once|after|when|if|as\s+soon))"
    r"|\b(?:I|I've|I\s+have|we|we've|we\s+have)\s+(?:now\s+|just\s+|successfully\s+)?"
    r"(?:booked|issued|reserved|confirmed)\b"
    r"|\bsuccessfully\s+(?:booked|issued|reserved)\b"
    r"|\bbooking\s+(?:is\s+)?(?:confirmed|complete[d]?|successful)\b"
    r"|\byou(?:'re|\s+are)\s+(?:all\s+)?booked\b"
    r"|\bbooked\s+(?:it|this|that)\s+for\s+you\b"
    r"|已(?:经)?(?:为您|帮您|给您|替您|为你|帮你|给你)?(?:出票|订好|预订成功|成功预订|预订|订票|确认预订)"
    r"|(?:出票|预订|订票)成功|订好了")


class ReplyGuard:
    def __init__(self, index: CityIndex, *, records: Iterable[Any] = (), user_text: str = "",
                 earlier_user_texts: Iterable[str] = (), today: date | None = None):
        """`user_text`: the user's message of this turn (its numbers, dates and cities are
        allowed). `earlier_user_texts`: the user's earlier messages in the context - only the
        cities they name are allowed (the user brought them up; a number from three turns ago
        is not something to repeat unchecked)."""
        self.index = index
        self.numbers: dict[str | None, set[Decimal]] = {None: set(), "usd": set(), "km": set()}
        self.booking_evidence = False
        self.cities: set[str] = set()
        self.dates: set[tuple[int, int, int]] = set()
        self.times: set[str] = set()
        self.ids: set[str] = set()
        self._partial_dates: set[tuple[int, int]] = set()  # day + month the user wrote without a year
        if today is not None:
            self.dates.add((today.year, today.month, today.day))
        self._allow_text(user_text, from_user=True)
        for earlier in earlier_user_texts:
            for mention in index.mentions(earlier):
                self.cities.update(mention.cities)
        for record in records:
            self.add_record(record)

    def _allow_text(self, text: str, from_user: bool = False) -> None:
        scan = _scan(text)
        for _, value, unit in scan.numbers:
            self.numbers[None].add(value)  # "$1,160.71" may also be written "1,160.71"
            if unit in ("usd", "km"):
                self.numbers[unit].add(value)
        self.ids.update(scan.ids)
        self.times.update(t for _, t in scan.times)
        for _, (year, month, day) in scan.dates:
            if year is not None:
                self.dates.add((year, month, day))
            elif from_user:  # "tickets booked on 3 March": any year the reply attaches is the LLM's
                self._partial_dates.add((month, day))
        for mention in self.index.mentions(text):
            self.cities.update(mention.cities)

    def add_record(self, record: Any) -> None:
        """Make the values of a tool result (the JSON the LLM was given) allowed."""
        strings: list[str] = []
        counts: list[Decimal] = []
        _strings_and_counts(record, strings, counts)
        self.numbers[None].update(counts)
        self.booking_evidence |= _is_booking_evidence(record)
        for s in strings:
            self._allow_text(s)

    def check(self, text: str) -> list[dict]:
        """Problems in `text`: [{"kind": "number" | "city" | "date" | "time" | "id" | "booking_claim", "value"}].
        Empty when every number, date, id and in-db city can be traced."""
        problems: list[dict] = []
        scan = _scan(text)
        for written, value, unit in scan.numbers:
            if unit == "bad" or value not in self.numbers[unit]:
                problems.append({"kind": "number", "value": written})
        if not self.booking_evidence:
            claim = _BOOKED.search(text)
            if claim:
                problems.append({"kind": "booking_claim", "value": claim.group(0)})
        for written, (year, month, day) in scan.dates:
            known = ((year, month, day) in self.dates if year is not None
                     else (month, day) in self._partial_dates
                     or any((m, d) == (month, day) for _, m, d in self.dates))
            if not known:
                problems.append({"kind": "date", "value": written})
        for written, hhmm in scan.times:
            if hhmm not in self.times:
                problems.append({"kind": "time", "value": written})
        for written in scan.ids:
            if written not in self.ids:
                problems.append({"kind": "id", "value": written})
        for mention in self.index.mentions(text):
            if not self.cities.intersection(mention.cities):
                problems.append({"kind": "city", "value": mention.text})
        return problems


# --- sentence buffering for the stream ------------------------------------------------------
_SENTENCE = re.compile(r".*?(?:[.!?]+(?=\s)\s+|[。！？]+|\n+)", re.S)


class SentenceBuffer:
    """Cuts streamed text into sentences. A cut is made only after sentence punctuation followed
    by white space, after CJK sentence punctuation, or after a line break - never inside a number
    such as `1,160.71`. Each piece keeps its trailing white space, so the pieces concatenate to
    the original text."""

    def __init__(self) -> None:
        self._buf = ""

    def push(self, chunk: str) -> list[str]:
        self._buf += chunk
        out = []
        pos = 0
        while True:
            m = _SENTENCE.match(self._buf, pos)
            if m is None:
                break
            # white space at the very end may still grow; that is harmless, the next piece
            # simply starts with it
            out.append(m.group(0))
            pos = m.end()
        self._buf = self._buf[pos:]
        return out

    def flush(self) -> str:
        rest, self._buf = self._buf, ""
        return rest
