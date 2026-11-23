"""Independent reference implementation for cross-checking app.core.

Deliberately shares NO code with app/: it reads the database itself, builds its own graph, does
its own Decimal arithmetic and its own search (plain Bellman-Ford relaxation, plus an exhaustive
path enumeration for small graphs). Only the specification is shared:

- flights: hub<->hub rows of hub_links, non-hub<->each hub in its connected_hubs, both directions;
- a route may change planes only at hubs;
- leg distance = Euclidean distance; ticket distance = sum of legs in travel order;
  price = distance * 0.13; arithmetic in Decimal prec 50 ROUND_HALF_EVEN; results quantized to
  30 decimal places;
- cheapest route = smallest quantized price; ties by fewer legs, then by the city-name sequence.
"""
import sqlite3
from decimal import ROUND_HALF_EVEN, Context, Decimal

Q30 = Decimal("0.000000000000000000000000000001")
RATE = Decimal("0.13")
# Partial paths more than this above the best known partial path can never end up tied after
# rounding to 30 dp; anything closer is kept so that ties are decided by the full rule.
KEEP = Decimal("1e-20")


def ctx():
    return Context(prec=50, rounding=ROUND_HALF_EVEN)


class RefGraph:
    def __init__(self, location_rows, link_rows):
        self.xy = {}
        self.hubs = set()
        self.spokes = {}
        self.hub_adj = {}
        for name, coord, is_hub, connected in location_rows:
            x, y = coord.split(",")
            self.xy[name] = (Decimal(x), Decimal(y))
            if is_hub:
                self.hubs.add(name)
                self.hub_adj[name] = set()
            else:
                self.spokes[name] = [h for h in connected.split(";") if h]
        for a, b in link_rows:
            self.hub_adj[a].add(b)
            self.hub_adj[b].add(a)
        self.spoke_cities = {h: [] for h in self.hubs}
        for city, hubs in self.spokes.items():
            for h in hubs:
                self.spoke_cities[h].append(city)

    @classmethod
    def from_db(cls, path):
        con = sqlite3.connect(path)
        try:
            return cls(
                con.execute("select city_name, coordinate, is_hub, connected_hubs from locations").fetchall(),
                con.execute("select hub_a, hub_b from hub_links").fetchall(),
            )
        finally:
            con.close()

    def served(self, city):
        if city in self.hubs:
            return bool(self.hub_adj[city] or self.spoke_cities[city])
        return bool(self.spokes[city])

    def dist(self, a, b, c):
        (ax, ay), (bx, by) = self.xy[a], self.xy[b]
        dx, dy = c.subtract(ax, bx), c.subtract(ay, by)
        return c.sqrt(c.add(c.multiply(dx, dx), c.multiply(dy, dy)))

    def flights_from(self, city):
        if city in self.hubs:
            return sorted(self.hub_adj[city]) + sorted(self.spoke_cities[city])
        return list(self.spokes[city])

    def total(self, path, c):
        s = None
        for a, b in zip(path, path[1:]):
            d = self.dist(a, b, c)
            s = d if s is None else c.add(s, d)
        return s

    def score(self, path, total, c):
        price = c.multiply(total, RATE).quantize(Q30, context=c)
        return (price, len(path) - 1, tuple(path))

    def answer(self, scored, total, c):
        price, _, path = scored
        return {"cities": path, "price": format(price, "f"),
                "distance": format(total.quantize(Q30, context=c), "f")}

    # --- exhaustive enumeration (small graphs only) -------------------------------------------
    def brute_force(self, origin, dest):
        c = ctx()
        best = None
        stack = [(origin,)]
        while stack:
            path = stack.pop()
            last = path[-1]
            if last == dest:
                total = self.total(path, c)
                cand = (self.score(path, total, c), total)
                if best is None or cand[0] < best[0]:
                    best = cand
                continue
            if len(path) > 1 and last not in self.hubs:
                continue  # may not change planes here
            for nxt in self.flights_from(last):
                if nxt not in path:
                    stack.append(path + (nxt,))
        return None if best is None else self.answer(best[0], best[1], c)

    # --- Bellman-Ford over the hubs, one run per origin ---------------------------------------
    def hub_labels(self, origin):
        """hub -> {path from origin ending at that hub: unrounded length}, near-optimal only."""
        c = ctx()
        labels = {h: {} for h in self.hubs}
        if origin in self.hubs:
            labels[origin][(origin,)] = Decimal(0)
        else:
            for h in self.spokes[origin]:
                labels[h][(origin, h)] = self.dist(origin, h, c)
        changed = True
        rounds = 0
        while changed:
            changed = False
            rounds += 1
            assert rounds <= len(self.hubs) + 2, "relaxation did not converge"
            for u in self.hubs:
                for path, length in list(labels[u].items()):
                    for v in self.hub_adj[u]:
                        if v in path:
                            continue
                        new_path = path + (v,)
                        if new_path in labels[v]:
                            continue
                        step = self.dist(u, v, c)
                        new_len = step if len(path) == 1 else c.add(length, step)
                        best_v = min(labels[v].values(), default=None)
                        if best_v is not None and new_len > best_v + KEEP:
                            continue
                        labels[v][new_path] = new_len
                        low = min(labels[v].values())
                        for p in [p for p, l in labels[v].items() if l > low + KEEP]:
                            del labels[v][p]
                        changed = True
        return labels

    def bellman_ford_from(self, origin):
        """dest -> answer dict, for every city reachable from origin (origin excluded)."""
        c = ctx()
        labels = self.hub_labels(origin)
        out = {}
        for dest in self.xy:
            if dest == origin:
                continue
            finals = []
            if dest in self.hubs:
                finals = [(p, l) for p, l in labels[dest].items()]
            else:
                for h in self.spokes[dest]:
                    last = self.dist(h, dest, c)
                    for p, l in labels[h].items():
                        finals.append((p + (dest,), last if len(p) == 1 else c.add(l, last)))
            best = None
            for path, total in finals:
                cand = (self.score(path, total, c), total)
                if best is None or cand[0] < best[0]:
                    best = cand
            if best is not None:
                out[dest] = self.answer(best[0], best[1], c)
        return out

    def nearest_served(self, city, k, exclude=()):
        c = Context(prec=60)
        ranked = sorted(
            (self.dist(city, other, c), other)
            for other in self.xy
            if other != city and other not in exclude and self.served(other)
        )
        return [name for _, name in ranked[:k]]
