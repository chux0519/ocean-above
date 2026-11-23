import decimal
import threading
from decimal import Decimal

import pytest

from app.core import places
from app.core.network import load_network
from app.core.pricing import InvalidPathError, fmt_km, fmt_usd, price_path
from app.core.routing import cheapest_route

from .conftest import make_net

ZEROS = "0" * 30


def triangle():
    return make_net(
        [("A", "0,0", 1, ""), ("B", "3,4", 1, ""), ("C", "3,0", 1, ""), ("N", "0,4", 0, "A"),
         ("M", "6,8", 0, "B")],
        [("A", "B"), ("B", "C")],
    )


def test_3_4_5_triangle():
    p = price_path(triangle(), ["A", "B"])
    assert p.distance == "5." + ZEROS
    assert p.price == "0.65" + "0" * 28
    assert fmt_km(p.distance) == "5.0 km"
    assert fmt_usd(p.price) == "$0.65"
    assert [(leg.origin, leg.destination, leg.distance) for leg in p.legs] == [("A", "B", "5." + ZEROS)]


def test_multi_leg_sum_and_transfers():
    p = price_path(triangle(), ["N", "A", "B", "C"])  # 4 + 5 + 4
    assert [leg.distance for leg in p.legs] == ["4." + ZEROS, "5." + ZEROS, "4." + ZEROS]
    assert p.distance == "13." + ZEROS
    assert p.price == "1.69" + "0" * 28
    assert p.transfers == ("A", "B")


def test_stored_value_is_not_display_rounded():
    n = make_net([("A", "0,0", 1, ""), ("B", "1,1", 1, "")], [("A", "B")])
    p = price_path(n, ["A", "B"])
    assert p.distance == "1.414213562373095048801688724210"  # sqrt(2) to 30 dp
    assert p.price == "0.183847763108502356344219534147"  # sqrt(2) * 0.13 to 30 dp
    assert fmt_km(p.distance) == "1.4 km" and fmt_usd(p.price) == "$0.18"
    # the price comes from the unrounded distance, not from the displayed 1.4 km
    assert Decimal(p.price) != Decimal("1.4") * Decimal("0.13")
    assert len(p.distance.split(".")[1]) == 30 and len(p.price.split(".")[1]) == 30


def test_real_route_price_is_distance_times_rate_at_high_precision(net):
    r = cheapest_route(net, "Guangzhou", "Lima")
    ctx = decimal.Context(prec=80)
    legs_sum = Decimal(0)
    for leg in r.legs:  # not sum(): that would use the thread's 28-digit default context
        legs_sum = ctx.add(legs_sum, Decimal(leg.distance))
    assert abs(legs_sum - Decimal(r.distance)) <= Decimal(len(r.legs)) * Decimal("1e-30")
    assert abs(ctx.multiply(Decimal(r.distance), Decimal("0.13")) - Decimal(r.price)) <= Decimal("1e-30")


@pytest.mark.parametrize(
    "price, shown",
    [
        ("0.005", "$0.01"),
        ("0.004999999999999999999999999999", "$0.00"),
        ("1.125", "$1.13"),  # half up; half even would give 1.12
        ("2.675", "$2.68"),
        ("1160.714999999999999999999999999999", "$1,160.71"),
        ("1160.715000000000000000000000000000", "$1,160.72"),
        ("0.65", "$0.65"),
    ],
)
def test_fmt_usd_rounds_half_up_at_the_cent(price, shown):
    assert fmt_usd(price) == shown


def test_fmt_km_one_decimal_half_up():
    assert fmt_km("8928.568945427627255214100107910851") == "8,928.6 km"
    assert fmt_km("0.25") == "0.3 km" and fmt_km("0.35") == "0.4 km"
    assert fmt_km("0.249999999999999999999999999999") == "0.2 km"


@pytest.mark.parametrize(
    "value, km, usd",
    [
        ("0", "0.0 km", "$0.00"),
        ("5", "5.0 km", "$5.00"),
        ("999.94", "999.9 km", "$999.94"),
        ("999.95", "1,000.0 km", "$999.95"),  # rounding carries into the separator
        ("999.995", "1,000.0 km", "$1,000.00"),
        ("1000", "1,000.0 km", "$1,000.00"),
        ("12345.678", "12,345.7 km", "$12,345.68"),
        ("999999.994999999999999999999999999999", "1,000,000.0 km", "$999,999.99"),
        ("1000000", "1,000,000.0 km", "$1,000,000.00"),
        ("1234567.891000000000000000000000000000", "1,234,567.9 km", "$1,234,567.89"),
        ("1234567890.125", "1,234,567,890.1 km", "$1,234,567,890.13"),
    ],
)
def test_display_uses_thousands_separators(value, km, usd):
    assert fmt_km(value) == km
    assert fmt_usd(value) == usd


def test_display_matches_the_contract_fixture_values(net):
    r = cheapest_route(net, "Guangzhou", "Lima")
    assert (fmt_km(r.distance), fmt_usd(r.price)) == ("8,928.6 km", "$1,160.71")
    assert [fmt_km(leg.distance) for leg in r.legs] == ["1,362.1 km", "1,872.4 km", "4,097.1 km", "1,597.0 km"]
    assert fmt_km(places.nearest_served(net, "Abu Dhabi")[0].distance) == "350.9 km"


def test_formatting_ignores_the_callers_decimal_context():
    with decimal.localcontext() as c:
        c.prec = 3
        c.rounding = decimal.ROUND_DOWN
        assert fmt_usd("123456.785") == "$123,456.79"
        assert fmt_km("123456.75") == "123,456.8 km"


@pytest.mark.parametrize(
    "path, message",
    [
        (["A"], "at least two"),
        (["A", "Z"], "unknown city"),
        (["A", "C"], "no direct flight"),  # both hubs, but no hub_links row
        (["N", "B"], "no direct flight"),  # N only connects to A
        (["N", "M"], "no direct flight"),
        (["A", "B", "A"], "twice"),
        (["A", "N", "A"], "twice"),
    ],
)
def test_price_path_refuses_unbookable_itineraries(path, message):
    with pytest.raises(InvalidPathError, match=message):
        price_path(triangle(), path)


def test_price_path_refuses_transfer_at_non_hub():
    n = make_net(
        [("H1", "0,0", 1, ""), ("H2", "9,0", 1, ""), ("X", "4,0", 0, "H1;H2")], []
    )
    with pytest.raises(InvalidPathError, match="non-hub"):
        price_path(n, ["H1", "X", "H2"])


# --- thread consistency (plan test 7) ----------------------------------------------------------


def quote_strings(db):
    n = load_network(db)  # fresh network: nothing cached from another thread
    out = []
    for a, b in [("Guangzhou", "Lima"), ("Tromsø", "Wrocław"), ("Shanghai", "Beijing")]:
        r = cheapest_route(n, a, b)
        out += [r.distance, r.price, *[leg.distance for leg in r.legs], fmt_km(r.distance), fmt_usd(r.price)]
    out += [x.distance for x in places.nearest_served(n, "Abu Dhabi")]
    return out


def test_quotes_are_byte_identical_across_threads_and_contexts(real_db):
    main = quote_strings(real_db)

    results = {}

    def default_context_thread():
        results["prec"] = decimal.getcontext().prec
        results["default"] = quote_strings(real_db)

    def hostile_context_thread():
        decimal.getcontext().prec = 6
        decimal.getcontext().rounding = decimal.ROUND_UP
        results["hostile"] = quote_strings(real_db)

    for target in (default_context_thread, hostile_context_thread):
        t = threading.Thread(target=target)
        t.start()
        t.join()
    assert results["prec"] == 28  # a new thread really starts with the 28-digit default
    assert results["default"] == main
    assert results["hostile"] == main
    with decimal.localcontext() as c:
        c.prec = 4
        assert quote_strings(real_db) == main
