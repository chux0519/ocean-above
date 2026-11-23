"""FastAPI entry point. Run from backend/: `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000`."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from . import api
from .core.network import NetworkProvider

DEFAULT_DB = Path(__file__).resolve().parents[2] / "locations.db"


def create_app(db_path: str | Path | None = None, settings: api.Settings | None = None,
               clock=None, llm=None, agent_kwargs: dict | None = None) -> FastAPI:
    """`settings` default to the environment; `clock`, `llm` (a fake LLM) and `agent_kwargs` are for tests."""
    settings = settings or api.Settings.from_env()
    path = Path(db_path or os.environ.get("LOCATIONS_DB") or DEFAULT_DB)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.networks = NetworkProvider(path)
        app.state.networks.current()  # fail at startup, not on the first request, if the db is bad
        api.install(app, settings, app.state.networks, clock, llm=llm, agent_kwargs=agent_kwargs)
        yield
        await api.shutdown(app)

    app = FastAPI(title="Ocean Above API", lifespan=lifespan)
    api.mount(app)

    @app.get("/health")
    def health(request: Request) -> dict:
        net = request.app.state.networks.current()
        return {
            "status": "ok",
            "network": {
                "cities": len(net.cities),
                "hubs": len(net.hubs),
                "hub_links": len(net.hub_links),
                "unserved": len(net.cities) - len(net.served),
                "fingerprint": net.fingerprint.id,
            },
        }

    @app.get("/api/cities")
    def cities(request: Request) -> dict:
        """Debug listing straight from the loaded network (database order)."""
        net = request.app.state.networks.current()
        return {
            "fingerprint": net.fingerprint.id,
            "count": len(net.cities),
            "cities": [
                {
                    "name": c.name,
                    "coordinate": [str(c.x), str(c.y)],
                    "is_hub": c.is_hub,
                    "served": net.is_served(c.name),
                    "connected_hubs": list(c.connected_hubs),
                }
                for c in net.cities.values()
            ],
        }

    return app


app = create_app()
