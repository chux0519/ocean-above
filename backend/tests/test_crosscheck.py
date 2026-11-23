"""Production routing vs the independent reference (tests/reference.py)."""
import time
from decimal import Context, Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.core.network import NetworkDataError
from app.core.routing import Route, RouteError, cheapest_route

from .conftest import make_net
from .reference import RefGraph

BUDGET_SECONDS = 120  # plan: above this, switch to a fixed-seed sample and say so in acceptance


def check_pair(net, a, b, expected, ref_served):
    """Compare one ordered pair; returns the Route (or None) after structural checks."""
    got = cheapest_route(net, a, b)
    if not (ref_served(a) and ref_served(b)):
        assert isinstance(got, RouteError) and got.code == "no_service", (a, b, got)
        return None
    if expected is None:
        assert isinstance(got, RouteError) and got.code == "unreachable", (a, b, got)
        return None
    assert isinstance(got, Route), (a, b, got)
    assert got.price == expected["price"], (a, b, got.cities, expected)
    assert got.distance == expected["distance"], (a, b, got.cities, expected)
    assert got.cities == expected["cities"], (a, b, got.price, expected)
    return got


def check_structure(net, ref, route):
    cities = route.cities
    assert cities[0] == route.origin and cities[-1] == route.destination
    assert len(set(cities)) == len(cities)
    assert all(c in ref.hubs for c in cities[1:-1]), cities  # transfers only at hubs
    assert route.transfers == cities[1:-1]
    assert len(route.legs) == len(cities) - 1
    for leg, a, b in zip(route.legs, cities, cities[1:]):
        assert (leg.origin, leg.destination) == (a, b)
        assert b in ref.flights_from(a), (a, b)  # every leg is an operating flight


@pytest.mark.slow
def test_all_served_city_pairs_on_the_real_db(net, real_db, record_property):
    ref = RefGraph.from_db(real_db)
    served = [c for c in ref.xy if ref.served(c)]
    assert len(served) == 701 and set(served) == set(net.served)

    started = time.perf_counter()
    pairs = direct_pairs = max_legs = 0
    wide = Context(prec=80)
    for origin in served:
        expected = ref.bellman_ford_from(origin)
        direct = set(ref.flights_from(origin))
        for dest in served:
            if dest == origin:
                continue
            route = check_pair(net, origin, dest, expected.get(dest), ref.served)
            assert route is not None, (origin, dest)  # the real network is connected
            check_structure(net, ref, route)
            # take-home 5: when a direct flight exists it is the route
            if dest in direct:
                assert route.cities == (origin, dest)
                direct_pairs += 1
            else:
                assert len(route.cities) > 2
            # distance = sum of legs, price = distance * 0.13 (to the stored precision)
            legs_sum = Decimal(0)
            for leg in route.legs:
                legs_sum = wide.add(legs_sum, Decimal(leg.distance))
            assert abs(legs_sum - Decimal(route.distance)) <= len(route.legs) * Decimal("1e-30")
            exact_price = wide.multiply(Decimal(route.distance), Decimal("0.13"))
            assert abs(exact_price - Decimal(route.price)) <= Decimal("1e-30")
            pairs += 1
            max_legs = max(max_legs, len(route.legs))
    elapsed = time.perf_counter() - started

    assert pairs == 701 * 700
    summary = (f"cross-check: {pairs} ordered served pairs, all equal (price, distance, city sequence); "
               f"{direct_pairs} with a direct flight; longest route {max_legs} legs; {elapsed:.1f}s")
    print("\n" + summary)
    record_property("crosscheck", summary)
    assert elapsed < BUDGET_SECONDS, summary


def test_reference_bellman_ford_agrees_with_exhaustive_search_on_real_hub_pairs(real_db):
    """Checks the reference itself: on the 40-hub graph, relaxation == full enumeration is too
    slow, so compare on a sub-network small enough to enumerate (first 9 hubs + their spokes)."""
    full = RefGraph.from_db(real_db)
    keep_hubs = sorted(full.hubs)[:9]
    rows = [(h, "%s,%s" % full.xy[h], 1, "") for h in keep_hubs]
    for city, hubs in sorted(full.spokes.items()):
        kept = [h for h in hubs if h in keep_hubs]
        if kept:
            rows.append((city, "%s,%s" % full.xy[city], 0, ";".join(kept)))
    links = [(a, b) for a in keep_hubs for b in sorted(full.hub_adj[a]) if b in keep_hubs and a < b]
    ref = RefGraph(rows, links)
    n = make_net(rows, links)
    names = [r[0] for r in rows]
    checked = 0
    for a in names[:40]:
        bf = ref.bellman_ford_from(a)
        for b in names:
            if a == b:
                continue
            brute = ref.brute_force(a, b)
            assert bf.get(b) == brute, (a, b)
            check_pair(n, a, b, brute, ref.served)
            checked += 1
    assert checked > 1000


# --- generated graphs -----------------------------------------------------------------------------

NAMES = ["Ka", "Kb", "Kc", "Kd", "Ke", "Kf", "Kg", "Kh"]


@st.composite
def graphs(draw):
    n = draw(st.integers(2, 8))
    names = draw(st.permutations(NAMES))[:n]
    # A tiny grid forces collinear points, equal-cost alternatives and coincident cities.
    span = draw(st.sampled_from([1, 2, 3, 50]))
    coord = st.integers(-span, span)
    hub_flags = [draw(st.booleans()) or i == 0 for i in range(n)]
    hubs = [name for name, flag in zip(names, hub_flags) if flag]
    rows = []
    for name, flag in zip(names, hub_flags):
        xy = f"{draw(coord)},{draw(coord)}"
        if flag:
            rows.append((name, xy, 1, ""))
        else:
            mine = draw(st.lists(st.sampled_from(hubs), unique=True, max_size=3))
            rows.append((name, xy, 0, ";".join(mine)))
    pairs = [(a, b) for i, a in enumerate(hubs) for b in hubs[i + 1:]]
    density = draw(st.sampled_from([0.3, 0.6, 1.0]))
    links = [p for p in pairs if draw(st.floats(0, 1)) <= density]
    return rows, links


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(graphs())
def test_generated_graphs_match_exhaustive_search(graph):
    rows, links = graph
    net = make_net(rows, links)
    ref = RefGraph(rows, links)
    for a in ref.xy:
        bf = ref.bellman_ford_from(a) if ref.served(a) else {}
        for b in ref.xy:
            if a == b:
                continue
            brute = ref.brute_force(a, b)
            if ref.served(a) and ref.served(b):
                assert bf.get(b) == brute, (a, b)  # the two reference searches agree
            route = check_pair(net, a, b, brute, ref.served)
            if route is not None:
                check_structure(net, ref, route)


@settings(max_examples=100, deadline=None)
@given(graphs(), st.data())
def test_generated_broken_graphs_are_rejected(graph, data):
    rows, links = graph
    kind = data.draw(st.sampled_from(["coord", "self_loop", "bad_ref", "hub_with_spokes"]))
    rows = list(rows)
    if kind == "coord":
        i = data.draw(st.integers(0, len(rows) - 1))
        rows[i] = (rows[i][0], data.draw(st.sampled_from(["", "1", "1;2", "a,b", "1,2,3", " 1,2"])), *rows[i][2:])
    elif kind == "self_loop":
        links = list(links) + [(rows[0][0], rows[0][0])]
    elif kind == "bad_ref":
        links = list(links) + [(rows[0][0], "Nowhere")]
    else:
        rows[0] = (rows[0][0], rows[0][1], 1, rows[0][0])
    with pytest.raises(NetworkDataError):
        make_net(rows, links)
