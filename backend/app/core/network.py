"""Read-only view of locations.db: cities, hubs, flights, and a fingerprint of the file.

locations.db is the single source of truth. This module only reads it (sqlite `mode=ro`),
validates it strictly (bad data raises, nothing is silently repaired) and builds an immutable
`Network`. Nothing here is hard-coded or copied from the database.
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

_COORD_RE = re.compile(r"-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?\Z")


class NetworkDataError(Exception):
    """locations.db is missing, unreadable or violates the documented format."""


@dataclass(frozen=True)
class Fingerprint:
    """Identity of the database file a Network was built from.

    `id` (the content hash) is what quotes store and compare; mtime/size are only the cheap
    change detector used by `NetworkProvider`.
    """

    sha256: str
    size: int
    mtime_ns: int

    @property
    def id(self) -> str:
        return f"sha256:{self.sha256}"


@dataclass(frozen=True)
class City:
    name: str
    x: Decimal
    y: Decimal
    is_hub: bool
    connected_hubs: tuple[str, ...]  # non-hub only, in database order


@dataclass(frozen=True, eq=False)
class Network:
    cities: Mapping[str, City]  # database order
    hub_links: frozenset[frozenset[str]]
    fingerprint: Fingerprint
    # Derived, filled once by `build_network`; never mutated afterwards.
    neighbors: Mapping[str, frozenset[str]] = field(repr=False, default_factory=dict)
    hubs: tuple[str, ...] = ()
    served: tuple[str, ...] = ()
    fold_index: Mapping[str, tuple[str, ...]] = field(repr=False, default_factory=dict)
    hub_paths: Mapping[tuple[str, str], tuple[tuple[str, ...], ...]] = field(
        repr=False, default_factory=dict
    )

    def has_flight(self, a: str, b: str) -> bool:
        return b in self.neighbors.get(a, ())

    def is_served(self, name: str) -> bool:
        """A city has service when at least one direct flight touches it."""
        return bool(self.neighbors.get(name))


def _fail(msg: str) -> None:
    raise NetworkDataError(msg)


def build_network(
    location_rows: Iterable[tuple[Any, Any, Any, Any]],
    link_rows: Iterable[tuple[Any, Any]],
    fingerprint: Fingerprint,
) -> Network:
    """Validate raw rows (`city_name, coordinate, is_hub, connected_hubs` and `hub_a, hub_b`)
    and build the Network. Used for the real database and for generated test graphs alike."""
    raw: list[tuple[str, Decimal, Decimal, bool, str]] = []
    seen: set[str] = set()
    for row in location_rows:
        if len(row) != 4:
            _fail(f"locations row has {len(row)} columns, expected 4")
        name, coord, is_hub, connected = row
        if not isinstance(name, str) or not name or name != name.strip() or ";" in name:
            _fail(f"bad city_name: {name!r}")
        if name in seen:
            _fail(f"duplicate city_name: {name!r}")
        seen.add(name)
        if not isinstance(coord, str) or not _COORD_RE.match(coord):
            _fail(f"bad coordinate for {name!r}: {coord!r} (expected 'x,y')")
        if isinstance(is_hub, bool) or is_hub not in (0, 1) or not isinstance(is_hub, int):
            _fail(f"bad is_hub for {name!r}: {is_hub!r} (expected 0 or 1)")
        if not isinstance(connected, str):
            _fail(f"bad connected_hubs for {name!r}: {connected!r}")
        xs, ys = coord.split(",")
        raw.append((name, Decimal(xs), Decimal(ys), bool(is_hub), connected))

    hub_names = {name for name, _, _, is_hub, _ in raw if is_hub}
    cities: dict[str, City] = {}
    adj: dict[str, set[str]] = {name: set() for name in seen}
    for name, x, y, is_hub, connected in raw:
        if is_hub:
            if connected != "":
                _fail(f"hub {name!r} must have empty connected_hubs, got {connected!r}")
            spokes: tuple[str, ...] = ()
        else:
            spokes = tuple(connected.split(";")) if connected else ()
            if len(set(spokes)) != len(spokes):
                _fail(f"duplicate hub in connected_hubs of {name!r}: {connected!r}")
            for hub in spokes:
                if hub not in seen:
                    _fail(f"connected_hubs of {name!r} names an unknown city: {hub!r}")
                if hub not in hub_names:
                    _fail(f"connected_hubs of {name!r} names a non-hub city: {hub!r}")
                adj[name].add(hub)
                adj[hub].add(name)
        cities[name] = City(name, x, y, is_hub, spokes)

    links: set[frozenset[str]] = set()
    for row in link_rows:
        if len(row) != 2:
            _fail(f"hub_links row has {len(row)} columns, expected 2")
        a, b = row
        for end in (a, b):
            if end not in seen:
                _fail(f"hub_links names an unknown city: {end!r}")
            if end not in hub_names:
                _fail(f"hub_links names a non-hub city: {end!r}")
        if a == b:
            _fail(f"hub_links has a self-loop: {a!r}")
        links.add(frozenset((a, b)))  # (a, b) and (b, a) describe the same two-way flight
        adj[a].add(b)
        adj[b].add(a)

    # Function-level imports: places and routing depend on this module.
    from .places import build_fold_index
    from .routing import build_hub_paths

    net = Network(
        cities=cities,
        hub_links=frozenset(links),
        fingerprint=fingerprint,
        neighbors={name: frozenset(ns) for name, ns in adj.items()},
        hubs=tuple(name for name in cities if cities[name].is_hub),
        served=tuple(name for name in cities if adj[name]),
        fold_index=build_fold_index(cities),
    )
    object.__setattr__(net, "hub_paths", build_hub_paths(net))
    return net


def _stat(path: Path) -> tuple[int, int]:
    try:
        st = path.stat()
    except OSError as exc:
        raise NetworkDataError(f"cannot stat {path}: {exc}") from exc
    return st.st_mtime_ns, st.st_size


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise NetworkDataError(f"cannot read {path}: {exc}") from exc


def _refuse_wal(path: Path) -> None:
    """A database in WAL mode keeps committed changes in `<name>-wal`, outside the bytes the
    fingerprint hashes, so a change could go unnoticed. Such a file is refused, not read."""
    try:
        with path.open("rb") as fh:
            header = fh.read(20)
        wal = Path(f"{path}-wal")
        wal_size = wal.stat().st_size if wal.exists() else 0
    except OSError as exc:
        raise NetworkDataError(f"cannot read {path}: {exc}") from exc
    if len(header) >= 20 and (header[18] == 2 or header[19] == 2):
        raise NetworkDataError(f"{path} is in WAL journal mode; use a rollback-journal database")
    if wal_size:
        raise NetworkDataError(f"{path} has a non-empty -wal file; checkpoint it and switch off WAL")


def _read_rows(path: Path) -> tuple[list[tuple], list[tuple]]:
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        try:
            locations = con.execute(
                "SELECT city_name, coordinate, is_hub, connected_hubs FROM locations ORDER BY rowid"
            ).fetchall()
            links = con.execute("SELECT hub_a, hub_b FROM hub_links ORDER BY rowid").fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        raise NetworkDataError(f"cannot read {path}: {exc}") from exc
    return locations, links


def load_network(path: str | Path) -> Network:
    """Load and validate the database. The hash and the rows are taken from the same file state:
    if the file changes while it is being read, the read is repeated."""
    path = Path(path)
    for _ in range(3):
        before = _stat(path)
        _refuse_wal(path)
        digest = _sha256(path)
        locations, links = _read_rows(path)
        if _stat(path) == before and _sha256(path) == digest:
            return build_network(locations, links, Fingerprint(digest, before[1], before[0]))
    raise NetworkDataError(f"{path} keeps changing while being read")


class NetworkProvider:
    """Hands out the Network for the current content of the database file.

    `current()` stats the file on every call; when mtime or size differ from the loaded state the
    content is hashed, and the Network is rebuilt only if the hash differs too. `strict=True`
    hashes unconditionally (for booking re-validation, where a missed change is not acceptable).
    A database that became invalid raises instead of serving the previous Network.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._net: Network | None = None
        self._stat: tuple[int, int] | None = None

    def current(self, strict: bool = False) -> Network:
        with self._lock:
            stat = _stat(self.path)
            if self._net is not None:
                try:
                    _refuse_wal(self.path)  # also when the main file's bytes did not change
                except NetworkDataError:
                    self._net = None
                    raise
                if stat == self._stat and not strict:
                    return self._net
                if _sha256(self.path) == self._net.fingerprint.sha256 and _stat(self.path) == stat:
                    self._stat = stat
                    return self._net
            self._net = None  # never serve a stale network after a failed reload
            net = load_network(self.path)
            self._net = net
            self._stat = (net.fingerprint.mtime_ns, net.fingerprint.size)
            return net
