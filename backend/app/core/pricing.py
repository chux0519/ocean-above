"""Canonical distance and price computation.

`price_path` is the ONLY place in the system that turns a city sequence into a distance and a
price. Quotes, booking re-validation and tests all call it. Everything runs in an explicit Decimal
context (prec 50, ROUND_HALF_EVEN) that is created per call, so results never depend on the
calling thread's default context (28 digits).

Stored values are strings with 30 decimal places ("high-precision", not "exact": a leg distance is
exact only when dx² + dy² is a perfect square). Rounding to display precision happens only in
`fmt_km` / `fmt_usd`, and a displayed value is never fed back into a calculation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # no runtime import: network.py depends on this module
    from .network import Network

PRECISION = 50
STORED_PLACES = 30
PRICE_PER_KM = Decimal("0.13")  # US dollars per km (take-home requirement 6)
FORMULA = (
    "price = sum(sqrt(dx^2 + dy^2)) * 0.13 USD; Decimal prec 50, ROUND_HALF_EVEN, stored to 30 dp"
)

_Q_STORED = Decimal(f"1e-{STORED_PLACES}")
_Q_KM = Decimal("0.1")
_Q_CENT = Decimal("0.01")


class InvalidPathError(ValueError):
    """The city sequence is not a bookable itinerary on this network."""


def new_context() -> Context:
    """A fresh canonical context. Fresh per computation: Context objects are mutable (flags)."""
    return Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)


def point_distance(ax: Decimal, ay: Decimal, bx: Decimal, by: Decimal, ctx: Context) -> Decimal:
    """Euclidean distance in km at context precision, not quantized."""
    dx = ctx.subtract(bx, ax)
    dy = ctx.subtract(by, ay)
    return ctx.sqrt(ctx.add(ctx.multiply(dx, dx), ctx.multiply(dy, dy)))


def stored(value: Decimal, ctx: Context) -> str:
    """Quantize to the stored precision (30 dp) and render as a plain decimal string."""
    return format(value.quantize(_Q_STORED, context=ctx), "f")


@dataclass(frozen=True)
class Leg:
    origin: str
    destination: str
    distance: str  # km, 30 dp


@dataclass(frozen=True)
class Priced:
    cities: tuple[str, ...]
    legs: tuple[Leg, ...]
    distance: str  # km, 30 dp: the sum of the unrounded leg distances, quantized once
    price: str  # USD, 30 dp: unrounded distance * 0.13, quantized once

    @property
    def transfers(self) -> tuple[str, ...]:
        return self.cities[1:-1]


def price_path(net: "Network", cities: Sequence[str]) -> Priced:
    """Price an itinerary given as canonical city names, origin first.

    Refuses anything that is not bookable: unknown names, a leg that is not an operating flight,
    a transfer at a non-hub, or a city visited twice.
    """
    cities = tuple(cities)
    if len(cities) < 2:
        raise InvalidPathError("an itinerary needs at least two cities")
    if len(set(cities)) != len(cities):
        raise InvalidPathError("an itinerary cannot visit a city twice")
    for name in cities:
        if name not in net.cities:
            raise InvalidPathError(f"unknown city: {name!r}")
    for name in cities[1:-1]:
        if not net.cities[name].is_hub:
            raise InvalidPathError(f"transfer at a non-hub city: {name!r}")

    ctx = new_context()
    legs: list[Leg] = []
    total: Decimal | None = None
    for a, b in zip(cities, cities[1:]):
        if not net.has_flight(a, b):
            raise InvalidPathError(f"no direct flight between {a!r} and {b!r}")
        ca, cb = net.cities[a], net.cities[b]
        d = point_distance(ca.x, ca.y, cb.x, cb.y, ctx)
        legs.append(Leg(a, b, stored(d, ctx)))
        total = d if total is None else ctx.add(total, d)
    assert total is not None
    price = ctx.multiply(total, PRICE_PER_KM)
    return Priced(cities, tuple(legs), stored(total, ctx), stored(price, ctx))


def _grouped(value: str, quantum: Decimal) -> str:
    """Round half up to `quantum` in the canonical context, then add thousands separators."""
    rounded = Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP, context=new_context())
    return format(rounded, ",f")  # already rounded: formatting only inserts the commas


def fmt_km(distance: str) -> str:
    """Display only: 1 decimal place, half up, thousands separators. '8928.568…' -> '8,928.6 km'."""
    return f"{_grouped(distance, _Q_KM)} km"


def fmt_usd(price: str) -> str:
    """Display only: cents, half up, thousands separators. '1160.713…' -> '$1,160.71'."""
    return f"${_grouped(price, _Q_CENT)}"
