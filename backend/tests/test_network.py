import hashlib
import os
import shutil
import sqlite3

import pytest

from app.core.network import NetworkDataError, NetworkProvider, load_network
from app.core.routing import cheapest_route

from .conftest import make_db, make_net

GOOD = [("H1", "0,0", 1, ""), ("H2", "3,4", 1, ""), ("A", "1,1", 0, "H1;H2"), ("B", "9,9", 0, "")]
GOOD_LINKS = [("H1", "H2")]


def test_real_db_counts(net):
    assert len(net.cities) == 711
    assert len(net.hubs) == 40
    assert len(net.hub_links) == 151
    unserved = [n for n in net.cities if not net.is_served(n)]
    assert len(unserved) == 10
    # "no service" is exactly "non-hub with empty connected_hubs" on this database
    assert unserved == [n for n, c in net.cities.items() if not c.is_hub and not c.connected_hubs]
    assert len(net.served) == 701


def test_loading_does_not_touch_the_file(real_db, net):
    before = (real_db.stat().st_mtime_ns, real_db.stat().st_size)
    again = load_network(real_db)
    assert (real_db.stat().st_mtime_ns, real_db.stat().st_size) == before
    assert again.fingerprint == net.fingerprint
    assert net.fingerprint.sha256 == hashlib.sha256(real_db.read_bytes()).hexdigest()
    assert net.fingerprint.id == "sha256:" + net.fingerprint.sha256
    assert not list(real_db.parent.glob("locations.db-*"))


def test_good_sample_loads(tmp_path):
    n = load_network(make_db(tmp_path / "ok.db", GOOD, GOOD_LINKS))
    assert n.has_flight("H1", "H2") and n.has_flight("H2", "H1")
    assert n.has_flight("A", "H1") and n.has_flight("H2", "A")
    assert not n.has_flight("A", "B") and not n.is_served("B")
    assert n.cities["A"].connected_hubs == ("H1", "H2")


def replaced(index, row):
    rows = list(GOOD)
    rows[index] = row
    return rows


BAD_CASES = {
    "coordinate_text": (replaced(2, ("A", "abc", 0, "H1")), GOOD_LINKS, "coordinate"),
    "coordinate_one_number": (replaced(2, ("A", "12", 0, "H1")), GOOD_LINKS, "coordinate"),
    "coordinate_space": (replaced(2, ("A", "1, 2", 0, "H1")), GOOD_LINKS, "coordinate"),
    "coordinate_three": (replaced(2, ("A", "1,2,3", 0, "H1")), GOOD_LINKS, "coordinate"),
    "coordinate_null": (replaced(2, ("A", None, 0, "H1")), GOOD_LINKS, "coordinate"),
    "is_hub_two": (replaced(2, ("A", "1,1", 2, "H1")), GOOD_LINKS, "is_hub"),
    "is_hub_text": (replaced(2, ("A", "1,1", "1", "H1")), GOOD_LINKS, "is_hub"),
    "spoke_unknown": (replaced(2, ("A", "1,1", 0, "Nowhere")), GOOD_LINKS, "unknown city"),
    "spoke_non_hub": (replaced(2, ("A", "1,1", 0, "B")), GOOD_LINKS, "non-hub"),
    "spoke_empty_item": (replaced(2, ("A", "1,1", 0, "H1;;H2")), GOOD_LINKS, "unknown city"),
    "spoke_padded": (replaced(2, ("A", "1,1", 0, "H1; H2")), GOOD_LINKS, "unknown city"),
    "spoke_duplicate": (replaced(2, ("A", "1,1", 0, "H1;H1")), GOOD_LINKS, "duplicate hub"),
    "spoke_null": (replaced(2, ("A", "1,1", 0, None)), GOOD_LINKS, "connected_hubs"),
    "hub_with_connected_hubs": (replaced(0, ("H1", "0,0", 1, "H2")), GOOD_LINKS, "must have empty"),
    "link_self_loop": (GOOD, [("H1", "H1")], "self-loop"),
    "link_unknown": (GOOD, [("H1", "Nowhere")], "unknown city"),
    "link_non_hub": (GOOD, [("H1", "A")], "non-hub"),
    "duplicate_city": (GOOD + [("A", "5,5", 0, "")], GOOD_LINKS, "duplicate city_name"),
    "empty_name": (GOOD + [("", "5,5", 0, "")], GOOD_LINKS, "city_name"),
}


@pytest.mark.parametrize("case", sorted(BAD_CASES))
def test_bad_sample_db_is_rejected(tmp_path, case):
    rows, links, message = BAD_CASES[case]
    path = make_db(tmp_path / "bad.db", rows, links)
    with pytest.raises(NetworkDataError, match=message):
        load_network(path)


def test_missing_file_table_or_garbage(tmp_path):
    with pytest.raises(NetworkDataError):
        load_network(tmp_path / "absent.db")
    assert not (tmp_path / "absent.db").exists()  # read-only open never creates the file
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    with pytest.raises(NetworkDataError):
        load_network(empty)
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"not a database" * 100)
    with pytest.raises(NetworkDataError):
        load_network(garbage)


def test_reversed_duplicate_link_is_one_flight():
    n = make_net(GOOD, [("H1", "H2"), ("H2", "H1")])
    assert len(n.hub_links) == 1


def test_decimal_coordinates_are_accepted():
    n = make_net([("H1", "0.5,0", 1, ""), ("H2", "3.5,4", 1, "")], [("H1", "H2")])
    assert cheapest_route(n, "H1", "H2").distance == "5." + "0" * 30


# --- fingerprint / provider ---------------------------------------------------------------------


def test_provider_reloads_when_the_file_changes(tmp_path, real_db):
    path = tmp_path / "locations.db"
    shutil.copy(real_db, path)
    provider = NetworkProvider(path)
    first = provider.current()
    assert provider.current() is first

    # mtime changes, content does not: same network, same fingerprint id
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert provider.current() is first
    assert provider.current(strict=True) is first

    quote_before = cheapest_route(first, "Guangzhou", "Lima")
    con = sqlite3.connect(path)
    con.execute("update locations set coordinate = '1,1' where city_name = 'Guangzhou'")
    con.commit()
    con.close()
    second = provider.current()
    assert second is not first
    assert second.fingerprint.id != first.fingerprint.id
    assert second.cities["Guangzhou"].x == 1
    quote_after = cheapest_route(second, "Guangzhou", "Lima")
    assert quote_after.fingerprint == second.fingerprint.id != quote_before.fingerprint
    assert quote_after.price != quote_before.price
    assert first.cities["Guangzhou"].x != 1  # the old Network object is untouched


def test_provider_strict_detects_change_hidden_from_stat(tmp_path, real_db):
    path = tmp_path / "locations.db"
    shutil.copy(real_db, path)
    provider = NetworkProvider(path)
    first = provider.current()
    st = path.stat()
    con = sqlite3.connect(path)
    con.execute("update locations set coordinate = '2,2' where city_name = 'Guangzhou'")
    con.commit()
    con.close()
    assert path.stat().st_size == st.st_size
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))  # same size, same mtime, new content
    assert provider.current() is first  # documented limit of the cheap check
    assert provider.current(strict=True).fingerprint.id != first.fingerprint.id


def test_provider_never_serves_stale_network_after_bad_edit(tmp_path, real_db):
    path = tmp_path / "locations.db"
    shutil.copy(real_db, path)
    provider = NetworkProvider(path)
    provider.current()
    con = sqlite3.connect(path)
    con.execute("update locations set coordinate = 'oops' where city_name = 'Guangzhou'")
    con.commit()
    con.close()
    for _ in range(2):
        with pytest.raises(NetworkDataError):
            provider.current()
    con = sqlite3.connect(path)
    con.execute("update locations set coordinate = '3,3' where city_name = 'Guangzhou'")
    con.commit()
    con.close()
    assert provider.current().cities["Guangzhou"].x == 3


def test_wal_mode_database_is_refused(tmp_path):
    """Named regression (T2 review, finding 1): committed changes in `-wal` are outside the bytes
    the fingerprint hashes, so strict change detection would miss them. Refuse such a file."""
    import sqlite3

    from app.core.network import NetworkProvider

    path = make_db(tmp_path / "wal.db", [("H1", "0,0", 1, ""), ("H2", "3,4", 1, "")], [("H1", "H2")])
    provider = NetworkProvider(path)
    assert provider.current(strict=True).cities["H2"].x == 3

    admin = sqlite3.connect(path)
    try:
        admin.execute("pragma journal_mode = wal")
        admin.execute("update locations set coordinate = '30,40' where city_name = 'H2'")
        admin.commit()
        for strict in (True, False):
            with pytest.raises(NetworkDataError, match="WAL"):
                provider.current(strict=strict)
        with pytest.raises(NetworkDataError, match="WAL"):
            load_network(path)
        admin.execute("pragma journal_mode = delete")
    finally:
        admin.close()
    assert provider.current(strict=True).cities["H2"].x == 30  # readable again, with the new data


def test_leftover_non_empty_wal_file_is_refused(tmp_path):
    from app.core.network import NetworkProvider

    path = make_db(tmp_path / "left.db", [("H1", "0,0", 1, ""), ("H2", "3,4", 1, "")], [("H1", "H2")])
    provider = NetworkProvider(path)
    provider.current()
    wal = tmp_path / "left.db-wal"
    wal.write_bytes(b"x" * 32)
    with pytest.raises(NetworkDataError, match="-wal"):
        provider.current(strict=True)
    wal.write_bytes(b"")  # an empty one is harmless
    assert provider.current(strict=True).cities["H2"].y == 4
