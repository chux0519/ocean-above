import os
import sqlite3
from pathlib import Path

import pytest
from hypothesis import settings

# Hypothesis profiles. "ci" (used by tool/run_tests.sh) is deterministic: the same examples on
# every run, no example database. "dev" (default) keeps random exploration and the local database.
# Select with HYPOTHESIS_PROFILE=ci|dev. Must run before the test modules are imported.
settings.register_profile("ci", derandomize=True, database=None)
settings.register_profile("dev")
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))

from app.core.network import Fingerprint, build_network, load_network

REAL_DB = Path(__file__).resolve().parents[2] / "locations.db"
TEST_FP = Fingerprint("test", 0, 0)


@pytest.fixture(scope="session")
def real_db() -> Path:
    return REAL_DB


@pytest.fixture(scope="session")
def net(real_db):
    return load_network(real_db)


def make_net(locations, links=()):
    """Network from rows in database format: (name, 'x,y', is_hub, 'Hub1;Hub2')."""
    return build_network(locations, links, TEST_FP)


def make_db(path, locations, links=()):
    """A sqlite file with the two tables but without constraints, so bad rows can be stored."""
    con = sqlite3.connect(path)
    con.execute("create table locations (city_name, coordinate, is_hub, connected_hubs)")
    con.execute("create table hub_links (hub_a, hub_b)")
    con.executemany("insert into locations values (?,?,?,?)", locations)
    con.executemany("insert into hub_links values (?,?)", links)
    con.commit()
    con.close()
    return path
