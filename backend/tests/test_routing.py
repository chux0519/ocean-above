import decimal

import pytest

from app.core.pricing import price_path
from app.core.routing import Route, RouteError, candidate_paths, cheapest_route

from .conftest import make_net

Z = "0" * 30


def small():
    """
    H1(0,0) -- H2(30,0) -- H3(30,40) -- H4(90,40)      hub chain, plus H1 -- H3 (50 km)
    A, B: non-hubs on H1.  C: non-hub on H3 and H4.  D: non-hub on H4.  U: no service.
    I1, I2: two hubs linked to each other but not to the rest (an island), E: non-hub on I1.
    """
    return make_net(
        [
            ("H1", "0,0", 1, ""), ("H2", "30,0", 1, ""), ("H3", "30,40", 1, ""), ("H4", "90,40", 1, ""),
            ("A", "0,-10", 0, "H1"), ("B", "-10,0", 0, "H1"), ("C", "60,40", 0, "H3;H4"),
            ("D", "90,50", 0, "H4"), ("U", "5,5", 0, ""),
            ("I1", "500,500", 1, ""), ("I2", "503,504", 1, ""), ("E", "500,510", 0, "I1"),
        ],
        [("H1", "H2"), ("H2", "H3"), ("H3", "H4"), ("H1", "H3"), ("I1", "I2")],
    )


def ok(result) -> Route:
    assert isinstance(result, Route), result
    return result


def test_direct_hub_to_hub():
    r = ok(cheapest_route(small(), "H1", "H2"))
    assert r.cities == ("H1", "H2") and r.transfers == ()
    assert r.distance == "30." + Z and r.price == "3.9" + "0" * 29


def test_direct_hub_to_hub_beats_the_detour():
    r = ok(cheapest_route(small(), "H1", "H3"))  # direct 50 km vs 30 + 40 via H2
    assert r.cities == ("H1", "H3") and r.distance == "50." + Z


def test_non_hub_to_its_hub_both_directions():
    n = small()
    assert ok(cheapest_route(n, "A", "H1")).cities == ("A", "H1")
    assert ok(cheapest_route(n, "H1", "A")).cities == ("H1", "A")
    assert ok(cheapest_route(n, "A", "H1")).distance == "10." + Z


def test_non_hubs_sharing_a_hub():
    r = ok(cheapest_route(small(), "A", "B"))
    assert r.cities == ("A", "H1", "B") and r.transfers == ("H1",)
    assert [l.distance for l in r.legs] == ["10." + Z, "10." + Z]
    assert r.distance == "20." + Z and r.price == "2.6" + "0" * 29


def test_multiple_transfers():
    r = ok(cheapest_route(small(), "A", "D"))
    assert r.cities == ("A", "H1", "H3", "H4", "D")
    assert r.transfers == ("H1", "H3", "H4")
    assert r.distance == "130." + Z  # 10 + 50 + 60 + 10
    assert r.price == "16.9" + "0" * 29


def test_picks_the_cheaper_of_the_destinations_hubs():
    r = ok(cheapest_route(small(), "H2", "C"))  # via H3: 40 + 30; via H4: 40 + 60 + 30
    assert r.cities == ("H2", "H3", "C")
    r = ok(cheapest_route(small(), "D", "C"))  # via H4 directly: 10 + 30
    assert r.cities == ("D", "H4", "C")


def test_non_hub_is_never_a_transfer_point():
    # X sits exactly between the two hubs and flies to both, but the hubs are not linked:
    # H1 -> X -> H2 would need a change at a non-hub.
    n = make_net([("H1", "0,0", 1, ""), ("H2", "10,0", 1, ""), ("X", "5,0", 0, "H1;H2"),
                  ("H3", "5,100", 1, "")], [("H1", "H3"), ("H3", "H2")])
    r = ok(cheapest_route(n, "H1", "H2"))
    assert r.cities == ("H1", "H3", "H2")
    n2 = make_net([("H1", "0,0", 1, ""), ("H2", "10,0", 1, ""), ("X", "5,0", 0, "H1;H2")])
    assert cheapest_route(n2, "H1", "H2").code == "unreachable"
    assert ok(cheapest_route(n2, "X", "H2")).cities == ("X", "H2")


def test_tie_prefers_fewer_legs_then_names():
    # H2 lies on the segment H1-H3: direct (10) ties with H1-H2-H3 (4 + 6) -> fewer legs.
    n = make_net([("H1", "0,0", 1, ""), ("H2", "4,0", 1, ""), ("H3", "10,0", 1, "")],
                 [("H1", "H2"), ("H2", "H3"), ("H1", "H3")])
    assert ok(cheapest_route(n, "H1", "H3")).cities == ("H1", "H3")
    # Irrational tie: sqrt(2) + sqrt(2) vs sqrt(8); equal after rounding to 30 dp -> fewer legs.
    n = make_net([("H1", "0,0", 1, ""), ("H2", "1,1", 1, ""), ("H3", "2,2", 1, "")],
                 [("H1", "H2"), ("H2", "H3"), ("H1", "H3")])
    assert ok(cheapest_route(n, "H1", "H3")).cities == ("H1", "H3")
    assert len(candidate_paths(n, "H1", "H3")) == 2
    # Regression (found by the generated-graph cross-check): sqrt(2) + sqrt(8) vs sqrt(18). The
    # candidate list lost the direct flight when edge weights were rounded to 28 digits.
    n = make_net([("Ka", "0,0", 1, ""), ("Kb", "-1,-1", 1, ""), ("Kc", "2,2", 1, "")],
                 [("Ka", "Kb"), ("Ka", "Kc"), ("Kb", "Kc")])
    assert set(candidate_paths(n, "Kb", "Kc")) == {("Kb", "Kc"), ("Kb", "Ka", "Kc")}
    assert ok(cheapest_route(n, "Kb", "Kc")).cities == ("Kb", "Kc")
    with decimal.localcontext() as c:  # candidates must not depend on the caller's context
        c.prec = 5
        hostile = make_net([("Ka", "0,0", 1, ""), ("Kb", "-1,-1", 1, ""), ("Kc", "2,2", 1, "")],
                           [("Ka", "Kb"), ("Ka", "Kc"), ("Kb", "Kc")])
    assert hostile.hub_paths == n.hub_paths
    # Same cost, same number of legs: the alphabetically first city sequence.
    n = make_net([("S", "0,0", 1, ""), ("Mb", "5,5", 1, ""), ("Ma", "5,-5", 1, ""), ("T", "10,0", 1, "")],
                 [("S", "Mb"), ("S", "Ma"), ("Mb", "T"), ("Ma", "T")])
    assert ok(cheapest_route(n, "S", "T")).cities == ("S", "Ma", "T")
    assert ok(cheapest_route(n, "T", "S")).cities == ("T", "Ma", "S")
    # The same tie through the endpoints' own hubs.
    n = make_net([("Hb", "0,3", 1, ""), ("Ha", "0,-3", 1, ""), ("P", "-4,0", 0, "Hb;Ha"), ("Q", "4,0", 0, "Ha;Hb")])
    assert ok(cheapest_route(n, "P", "Q")).cities == ("P", "Ha", "Q")


def test_same_city():
    n = small()
    for a, b in [("A", "A"), ("H1", "h1"), (" a ", "A"), ("U", "U")]:
        err = cheapest_route(n, a, b)
        assert isinstance(err, RouteError) and err.code == "same_city"


def test_unknown_city_on_either_or_both_ends(net):
    err = cheapest_route(net, "Shangai", "Lima")
    assert err.code == "unknown_city"
    assert [(i.role, i.code, i.query) for i in err.issues] == [("origin", "unknown_city", "Shangai")]
    assert err.issues[0].candidates[0] == "Shanghai"
    err = cheapest_route(net, "Lima", "Qqqqxxzzy")
    assert [(i.role, i.code, i.candidates) for i in err.issues] == [("destination", "unknown_city", ())]
    err = cheapest_route(net, "Shangai", "Qqqqxxzzy")
    assert [i.role for i in err.issues] == ["origin", "destination"]


def test_ambiguous_city_is_an_error_not_a_guess(net):
    err = cheapest_route(net, "san jose", "Lima")
    assert err.code == "ambiguous_city"
    assert err.issues[0].candidates == ("San Jose", "San José")
    exact = ok(cheapest_route(net, "San José", "Lima"))
    assert exact.origin == "San José" and exact.similar_names == {"San José": ("San Jose",)}
    other = ok(cheapest_route(net, "San Jose", "Lima"))
    assert other.origin == "San Jose" and other.cities != exact.cities


def test_no_service_origin_with_nearest(net):
    err = cheapest_route(net, "Abu Dhabi", "Lima")
    assert err.code == "no_service" and err.cities == ("Abu Dhabi", "Lima")
    (issue,) = err.issues
    assert (issue.role, issue.city) == ("origin", "Abu Dhabi")
    assert [n.city for n in issue.nearest_served] == ["Tirana", "Odesa", "Whitehorse"]
    assert all(net.is_served(n.city) for n in issue.nearest_served)


def test_no_service_destination_with_nearest(net):
    err = cheapest_route(net, "Lima", "stuttgart")
    (issue,) = err.issues
    assert (err.code, issue.role, issue.city, issue.query) == ("no_service", "destination", "Stuttgart", "stuttgart")
    assert issue.nearest_served[0].city == "Novi Sad" and len(issue.nearest_served) == 3


def test_no_service_suggestions_exclude_the_other_endpoint(net):
    err = cheapest_route(net, "Tirana", "Abu Dhabi")
    names = [n.city for n in err.issues[0].nearest_served]
    assert "Tirana" not in names and names[0] == "Odesa" and len(names) == 3


def test_no_service_on_both_ends_reports_both(net):
    err = cheapest_route(net, "Abu Dhabi", "Austin")
    assert err.code == "no_service"
    assert [(i.role, i.city, i.nearest_served[0].city) for i in err.issues] == [
        ("origin", "Abu Dhabi", "Tirana"), ("destination", "Austin", "Copenhagen")]


def test_unreachable_between_disconnected_components():
    n = small()
    for a, b in [("A", "E"), ("E", "A"), ("H1", "I2"), ("I1", "D"), ("C", "I2")]:
        err = cheapest_route(n, a, b)
        assert isinstance(err, RouteError), (a, b)
        assert err.code == "unreachable" and err.cities == (a, b)
    assert ok(cheapest_route(n, "E", "I2")).cities == ("E", "I1", "I2")  # inside the island


def test_isolated_hub_has_no_service():
    n = make_net([("H1", "0,0", 1, ""), ("H2", "3,4", 1, ""), ("Lonely", "1,1", 1, "")], [("H1", "H2")])
    err = cheapest_route(n, "Lonely", "H1")
    assert err.code == "no_service" and [x.city for x in err.issues[0].nearest_served] == ["H2"]


def test_route_carries_fingerprint_and_reprices_identically(net):
    r = ok(cheapest_route(net, "guangzhou", "LIMA"))
    assert (r.origin, r.destination) == ("Guangzhou", "Lima")
    assert r.fingerprint == net.fingerprint.id and r.fingerprint.startswith("sha256:")
    again = price_path(net, r.cities)
    assert (again.distance, again.price, again.legs) == (r.distance, r.price, r.legs)
    assert r.transfers == r.cities[1:-1] and all(net.cities[c].is_hub for c in r.transfers)
