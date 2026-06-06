"""Victim data service.

Returns a tiny JSON payload. This is the leaf of the chain and the most
common place we plant regressions (see regressions.py + items_query.py).
"""

from __future__ import annotations

from fastapi import FastAPI

from . import items_query, regressions
from .telemetry import init_telemetry, instrument_app


def create_app() -> FastAPI:
    init_telemetry("faultline-victim-data")
    app = FastAPI(title="victim-data")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "data"}

    @app.get("/items")
    async def items() -> dict[str, object]:
        # Regression hooks fire before the query so symptoms like 5xx /
        # crashloop / memory growth surface immediately.
        await regressions.apply_data_regression()
        mode = regressions.current_mode()
        if mode == "n_plus_one":
            payload = await items_query.fetch_items_n_plus_one()
        else:
            payload = await items_query.fetch_items_batched()
        return {"items": payload, "regression_mode": mode or None}

    instrument_app(app)
    return app


app = create_app()
