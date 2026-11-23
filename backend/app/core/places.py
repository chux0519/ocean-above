"""City name resolution and nearest-served-city lookup. Deterministic; never invents a city."""
from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Literal, Mapping

from .pricing import new_context, point_distance, stored

if TYPE_CHECKING:
    from .network import City, Network

MAX_SUGGESTIONS = 5
SUGGESTION_CUTOFF = 0.6

# Letters that NFKD does not decompose into base letter + combining mark.
_EXTRA_FOLDS = str.maketrans(
    {"ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "æ": "ae", "œ": "oe", "ı": "i"}
)


def fold(name: str) -> str:
    """Matching key: case-insensitive, accent-insensitive, whitespace-normalized."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().translate(_EXTRA_FOLDS).split())


def build_fold_index(cities: Iterable[str]) -> Mapping[str, tuple[str, ...]]:
    """fold key -> every city with that key (a list on purpose: `San Jose` and `San José` share
    a key, and a one-to-one dict would silently drop one of them)."""
    index: dict[str, list[str]] = {}
    for name in cities:
        index.setdefault(fold(name), []).append(name)
    return {key: tuple(sorted(names)) for key, names in index.items()}


@dataclass(frozen=True)
class Resolution:
    status: Literal["ok", "ambiguous", "unknown"]
    query: str
    city: str | None = None  # canonical name, status "ok" only
    similar_names: tuple[str, ...] = ()  # "ok": other cities equal after folding
    candidates: tuple[str, ...] = ()  # "ambiguous": all matches; "unknown": close names (<= 5)


def resolve(net: "Network", name: str) -> Resolution:
    """Exact name first (NFC, trimmed); then the folded key, which must hit exactly one city.
    Several hits are returned as `ambiguous`, never auto-picked."""
    if not isinstance(name, str):
        return Resolution("unknown", repr(name))
    exact = unicodedata.normalize("NFC", name).strip()
    key = fold(exact)
    same_key = net.fold_index.get(key, ())
    if exact in net.cities:
        return Resolution("ok", name, exact, tuple(n for n in same_key if n != exact))
    if len(same_key) == 1:
        return Resolution("ok", name, same_key[0])
    if same_key:
        return Resolution("ambiguous", name, candidates=same_key)
    if not key:
        return Resolution("unknown", name)
    close = difflib.get_close_matches(
        key, list(net.fold_index), n=MAX_SUGGESTIONS, cutoff=SUGGESTION_CUTOFF
    )
    names = [n for k in close for n in net.fold_index[k]]
    return Resolution("unknown", name, candidates=tuple(names[:MAX_SUGGESTIONS]))


@dataclass(frozen=True)
class Nearby:
    city: str
    distance: str  # straight-line km, 30 dp (same arithmetic as leg distances)


def nearest_served(
    net: "Network", city: str, k: int = 3, exclude: Iterable[str] = ()
) -> tuple[Nearby, ...]:
    """The k served cities closest to `city` (a canonical name), nearest first; ties by name.
    `city` itself and everything in `exclude` are skipped."""
    origin: "City" = net.cities[city]
    skip = {city, *exclude}
    ctx = new_context()
    ranked = sorted(
        (
            (point_distance(origin.x, origin.y, c.x, c.y, ctx), name)
            for name in net.served
            if name not in skip
            for c in (net.cities[name],)
        ),
    )
    return tuple(Nearby(name, stored(d, ctx)) for d, name in ranked[: max(k, 0)])
