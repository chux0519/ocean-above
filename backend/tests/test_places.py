import unicodedata

import pytest

from app.core import places
from app.core.places import fold, nearest_served, resolve

from .conftest import make_net
from .reference import RefGraph


@pytest.mark.parametrize(
    "query, city",
    [
        ("Shanghai", "Shanghai"),
        ("shanghai", "Shanghai"),
        ("SHANGHAI", "Shanghai"),
        ("  Shanghai \n", "Shanghai"),
        ("new   york", "New York"),
        ("bogota", "Bogotá"),
        ("BOGOTÁ", "Bogotá"),
        ("Bogotá", "Bogotá"),  # decomposed input (NFD)
        ("sao paulo", "São Paulo"),
        ("zurich", "Zürich"),
        ("tromso", "Tromsø"),
        ("wroclaw", "Wrocław"),
        ("xi'an", "Xi'an"),
        ("washington, d.c.", "Washington, D.C."),
    ],
)
def test_resolve_case_accents_whitespace(net, query, city):
    res = resolve(net, query)
    assert (res.status, res.city) == ("ok", city)
    assert res.query == query


def test_every_city_resolves_to_itself_exactly(net):
    for name in net.cities:
        res = resolve(net, name)
        assert (res.status, res.city) == ("ok", name)
        res = resolve(net, unicodedata.normalize("NFD", name))
        assert (res.status, res.city) == ("ok", name)


def test_san_jose_pair_is_never_auto_picked(net):
    plain = resolve(net, "San Jose")
    assert (plain.status, plain.city, plain.similar_names) == ("ok", "San Jose", ("San José",))
    accented = resolve(net, "San José")
    assert (accented.status, accented.city, accented.similar_names) == ("ok", "San José", ("San Jose",))
    for query in ("san jose", "SAN JOSE", "san josé", " San  Jose", "SAN JOSÉ"):
        res = resolve(net, query)
        assert res.status == "ambiguous" and res.city is None
        assert res.candidates == ("San Jose", "San José")


def test_the_only_folded_collision_in_the_db_is_san_jose(net):
    collisions = [names for names in net.fold_index.values() if len(names) > 1]
    assert collisions == [("San Jose", "San José")]
    assert sum(len(v) for v in net.fold_index.values()) == len(net.cities)  # nobody dropped


def test_fold_index_keeps_all_colliding_names():
    n = make_net([("Abc", "0,0", 1, ""), ("ABC", "1,1", 1, ""), ("ábc", "2,2", 0, "")])
    assert n.fold_index[fold("abc")] == ("ABC", "Abc", "ábc")
    assert resolve(n, "abc").status == "ambiguous"
    assert resolve(n, "ABC").city == "ABC"
    assert resolve(n, "ABC").similar_names == ("Abc", "ábc")


def test_typo_gives_candidates_not_a_guess(net):
    res = resolve(net, "Shangai")
    assert res.status == "unknown" and res.city is None
    assert res.candidates[0] == "Shanghai"
    assert 1 <= len(res.candidates) <= places.MAX_SUGGESTIONS
    assert all(c in net.cities for c in res.candidates)
    assert "Bogotá" in resolve(net, "bogata").candidates


@pytest.mark.parametrize("query", ["Qqqqxxzzy", "", "   ", "12345", None, 42])
def test_nonexistent_city_is_unknown_without_inventing(net, query):
    res = resolve(net, query)
    assert res.status == "unknown" and res.city is None and res.candidates == ()


def test_suggestions_never_exceed_five_even_with_collisions(net):
    for query in ("San Jos", "Sant", "Port", "San"):
        res = resolve(net, query)
        assert len(res.candidates) <= 5 and all(c in net.cities for c in res.candidates)


# --- nearest served city ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "city, nearest", [("Abu Dhabi", "Tirana"), ("Stuttgart", "Novi Sad"), ("Austin", "Copenhagen")]
)
def test_nearest_served_known_cases(net, city, nearest):
    assert nearest_served(net, city)[0].city == nearest
    assert len(nearest_served(net, city)) == 3


def test_nearest_served_matches_brute_force_for_every_city(net, real_db):
    ref = RefGraph.from_db(real_db)
    for city in net.cities:
        got = nearest_served(net, city, k=5)
        assert [n.city for n in got] == ref.nearest_served(city, 5), city
        assert all(net.is_served(n.city) and n.city != city for n in got)
        assert [n.distance for n in got] == sorted((n.distance for n in got), key=lambda s: float(s))


def test_nearest_served_exclude_and_k(net):
    top = [n.city for n in nearest_served(net, "Abu Dhabi", k=4)]
    assert top[0] == "Tirana"
    assert [n.city for n in nearest_served(net, "Abu Dhabi", k=3, exclude=("Tirana",))] == top[1:4]
    assert [n.city for n in nearest_served(net, "Abu Dhabi", k=1)] == top[:1]
    assert nearest_served(net, "Abu Dhabi", k=0) == ()
    assert len(nearest_served(net, "Abu Dhabi", k=10_000)) == 701


def test_nearest_served_skips_unserved_and_breaks_ties_by_name():
    n = make_net(
        [("H", "0,0", 1, ""), ("Zed", "0,5", 0, "H"), ("Amy", "5,0", 0, "H"), ("Off", "1,0", 0, ""),
         ("Me", "3,3", 0, ""), ("Far", "50,50", 0, "H")],
    )
    got = nearest_served(n, "Me", k=3)
    assert [x.city for x in got] == ["Amy", "Zed", "H"]  # Amy/Zed tie at sqrt(13); "Off" has no service
    assert got[0].distance == got[1].distance == "3.605551275463989293119221267470"
