"""Cheapest route between two cities, changing planes only at hubs.

Price is proportional to distance, so cheapest = shortest. A valid route is
`origin, hub, ..., hub, destination`: every intermediate city is a hub and every leg is an
operating flight. Selection works in two steps:

1. At network build time, for every ordered hub pair, the candidate hub-to-hub paths
   (`build_hub_paths`, networkx).
2. Per query, every "hub the origin can reach directly x hub the destination can reach directly"
   combination is expanded into full city sequences; each one is priced by the canonical
   `pricing.price_path`, and the minimum by (price, number of legs, city names) wins.

networkx only proposes paths. Its lengths are never shown, stored or compared against canonical
prices; the ranking is always done on `price_path` results.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Literal, Mapping

import networkx as nx

from .. import tracing
from . import places
from .pricing import Leg, Priced, new_context, point_distance, price_path

if TYPE_CHECKING:
    from .network import Network

ALGORITHM = "hub all-pairs candidates (networkx) + endpoint enumeration; tie-break: legs, names"
ALGORITHM_ID = "hub-candidates+enumeration"  # short name for the `core.routing` span (#35)

_UNIT_PLACES = 30  # integer edge weights are leg distances in units of 1e-30 km

HubPaths = Mapping[tuple[str, str], tuple[tuple[str, ...], ...]]


def build_hub_paths(net: "Network") -> HubPaths:
    """For every ordered pair of connected hubs (a, b), a != b: all simple hub-only paths whose
    length is within a few 1e-30 km of the shortest one.

    Why "within a few units" and not just "the shortest": networkx works on integer weights (leg
    distance rounded to 30 dp), while the canonical price sums unrounded legs and rounds once.
    The two can disagree by at most (legs/2 + 1) units for the distance and 1/0.13 < 8 units for
    the price rounding, so a path that is cheapest or tied by canonical price always lies within
    `slack` units of the integer optimum. Paths that differ by a real amount differ by vastly
    more than that, so in practice the list has one entry, or the members of an exact tie.
    """
    ctx = new_context()
    graph = nx.Graph()
    graph.add_nodes_from(net.hubs)
    for link in net.hub_links:
        a, b = sorted(link)
        ca, cb = net.cities[a], net.cities[b]
        d = point_distance(ca.x, ca.y, cb.x, cb.y, ctx)
        # ctx.scaleb / ctx.to_integral_exact: the method forms without a context argument would
        # silently round to the calling thread's default 28 digits.
        graph.add_edge(a, b, w=int(ctx.to_integral_exact(ctx.scaleb(d, _UNIT_PLACES))))

    slack = 2 * (len(net.hubs) + 2) + 16
    paths: dict[tuple[str, str], tuple[tuple[str, ...], ...]] = {}
    for component in nx.connected_components(graph):
        members = sorted(component)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                found: list[tuple[str, ...]] = []
                best: int | None = None
                # Yen's algorithm: simple paths in order of non-decreasing weight.
                for path in nx.shortest_simple_paths(graph, a, b, weight="w"):
                    weight = nx.path_weight(graph, path, "w")
                    if best is None:
                        best = weight
                    elif weight > best + slack:
                        break
                    found.append(tuple(path))
                paths[(a, b)] = tuple(found)
                paths[(b, a)] = tuple(p[::-1] for p in found)
    return paths


@dataclass(frozen=True)
class Route:
    origin: str
    destination: str
    cities: tuple[str, ...]
    legs: tuple[Leg, ...]  # per leg: origin, destination, distance (km, 30 dp)
    transfers: tuple[str, ...]  # intermediate cities, all hubs
    distance: str  # km, 30 dp
    price: str  # USD, 30 dp
    fingerprint: str  # id of the network the route was computed on
    similar_names: Mapping[str, tuple[str, ...]]  # endpoint -> cities with the same folded name


@dataclass(frozen=True)
class CityIssue:
    """Why one endpoint of a request cannot be used."""

    role: Literal["origin", "destination"]
    code: Literal["unknown_city", "ambiguous_city", "no_service"]
    query: str  # what the caller passed
    city: str | None = None  # canonical name (no_service)
    candidates: tuple[str, ...] = ()  # ambiguous_city: all matches; unknown_city: close names
    nearest_served: tuple[places.Nearby, ...] = ()  # no_service: up to 3, other endpoint excluded


@dataclass(frozen=True)
class RouteError:
    code: Literal["unknown_city", "ambiguous_city", "same_city", "no_service", "unreachable"]
    issues: tuple[CityIssue, ...] = ()  # per-endpoint details; `code` is the first issue's code
    cities: tuple[str, ...] = ()  # canonical names involved (same_city, unreachable)
    fingerprint: str = ""


def candidate_paths(net: "Network", origin: str, destination: str) -> list[tuple[str, ...]]:
    """Every city sequence worth pricing between two distinct served cities (canonical names)."""
    co, cd = net.cities[origin], net.cities[destination]
    entry = (origin,) if co.is_hub else co.connected_hubs
    exit_ = (destination,) if cd.is_hub else cd.connected_hubs
    out: list[tuple[str, ...]] = []
    for h1 in entry:
        head = () if co.is_hub else (origin,)
        for h2 in exit_:
            tail = () if cd.is_hub else (destination,)
            middles = ((h1,),) if h1 == h2 else net.hub_paths.get((h1, h2), ())
            out.extend(head + middle + tail for middle in middles)
    return out


def _rank(priced: Priced) -> tuple[Decimal, int, tuple[str, ...]]:
    return (Decimal(priced.price), len(priced.legs), priced.cities)


def cheapest_route(net: "Network", origin: str, destination: str) -> Route | RouteError:
    """Resolve both names and return the cheapest valid route, or a typed error."""
    # `core.routing` (#35): algorithm, fingerprint, candidates, legs; no city names
    with tracing.span("core.routing", {"ocean.routing.algorithm": ALGORITHM_ID,
                                       "ocean.network.fingerprint": net.fingerprint.id}) as span:
        result = _cheapest_route(net, origin, destination, span)
        if isinstance(result, RouteError):
            span.set_attribute("ocean.routing.code", result.code)
        else:
            span.set_attributes({"ocean.routing.code": "ok", "ocean.routing.legs": len(result.legs)})
        return result


def _cheapest_route(net: "Network", origin: str, destination: str, span) -> Route | RouteError:
    fp = net.fingerprint.id
    resolved = {
        "origin": places.resolve(net, origin),
        "destination": places.resolve(net, destination),
    }
    issues: list[CityIssue] = []
    for role, res in resolved.items():
        if res.status == "unknown":
            issues.append(CityIssue(role, "unknown_city", res.query, candidates=res.candidates))
        elif res.status == "ambiguous":
            issues.append(CityIssue(role, "ambiguous_city", res.query, candidates=res.candidates))
    if issues:
        return RouteError(issues[0].code, tuple(issues), fingerprint=fp)

    a, b = resolved["origin"].city, resolved["destination"].city
    assert a is not None and b is not None
    if a == b:
        return RouteError("same_city", cities=(a,), fingerprint=fp)

    for role, city, other in (("origin", a, b), ("destination", b, a)):
        if not net.is_served(city):
            nearest = places.nearest_served(net, city, k=3, exclude=(other,))
            issues.append(
                CityIssue(role, "no_service", resolved[role].query, city, nearest_served=nearest)
            )
    if issues:
        return RouteError("no_service", tuple(issues), cities=(a, b), fingerprint=fp)

    candidates = candidate_paths(net, a, b)
    span.set_attribute("ocean.routing.candidates", len(candidates))
    if not candidates:
        return RouteError("unreachable", cities=(a, b), fingerprint=fp)
    best = min((price_path(net, path) for path in candidates), key=_rank)
    similar = {
        res.city: res.similar_names for res in resolved.values() if res.city and res.similar_names
    }
    return Route(
        origin=a,
        destination=b,
        cities=best.cities,
        legs=best.legs,
        transfers=best.transfers,
        distance=best.distance,
        price=best.price,
        fingerprint=fp,
        similar_names=similar,
    )
