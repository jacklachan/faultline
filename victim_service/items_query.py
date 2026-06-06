"""Data-layer query module for the victim ``data`` service.

This is where the **believable suspect commit** lives. The clean path returns
the items in a single batched call. The "broken" path simulates an N+1
fetch — one query per item id — which is the bug Faultline's policy is
designed to spot from a latency-creep signal.

A real-world regression commit might:
  * import a new ORM helper that lazily refetches each row,
  * add a `for id in ids: db.get(id)` loop where a `db.get_many(ids)`
    used to live,
  * or flip a feature flag that turns off the batcher.

We model all three flavours as a single REGRESSION_MODE toggle in
regressions.py so the demo can pick the symptom.
"""

from __future__ import annotations

import asyncio


_ITEMS = (
    {"id": 1, "name": "alpha"},
    {"id": 2, "name": "beta"},
    {"id": 3, "name": "gamma"},
)


async def fetch_items_batched() -> list[dict]:
    """The clean implementation: one batched call, ~5ms."""
    await asyncio.sleep(0.005)
    return list(_ITEMS)


async def fetch_items_n_plus_one() -> list[dict]:
    """The regression: N sequential single-row fetches.

    Each fetch is ~8ms; with 25 lookups this dominates request time. This is
    exactly the kind of change that produces a latency-creep symptom without
    showing up as 5xx errors — the workload still succeeds, just slowly.
    """
    out: list[dict] = []
    for _ in range(25):
        await asyncio.sleep(0.008)
    out.extend(_ITEMS)
    return out
