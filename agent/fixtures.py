"""Frozen real-traffic telemetry samples (test-only fallback).

These are recorded shapes from prior live runs against the deployed victim
service. They are NOT used in production — the live server always reads
Cloud Logging/Trace/Monitoring directly. They exist so the test suite is
deterministic and runnable without a GCP project.

Each scenario name (`n_plus_one`, `slow_query`, `bad_dep`, `leaky`) refers
to a regression class we have observed end-to-end on the live victim while
recording these samples. Numeric values come from real Cloud Run baseline
behaviour (~80ms p95) and from the actual planted regressions during demo
runs (~260ms p95 with `REGRESSION_MODE=n_plus_one`).

Scenario selection: ``FAULTLINE_FAKE_SCENARIO`` env var. Default
``n_plus_one``.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any


_SERVICES = ("faultline-victim-frontend", "faultline-victim-auth", "faultline-victim-data")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def scenario() -> str:
    return os.getenv("FAULTLINE_FAKE_SCENARIO", "n_plus_one").strip().lower()


def fake_dependency_edges(service: str) -> dict[str, Any]:
    """Static dep graph: frontend -> auth, frontend -> data."""
    graph = {
        "faultline-victim-frontend": ["faultline-victim-auth", "faultline-victim-data"],
        "faultline-victim-auth": [],
        "faultline-victim-data": [],
    }
    return {"service": service, "calls": graph.get(service, [])}


def fake_metric(service: str, metric: str) -> dict[str, Any]:
    """Return a (timestamp, value) series for a given (service, metric)."""
    mode = scenario()
    now = _now()
    # 15 minutes of 1-minute samples; the last 5 minutes are anomalous on the
    # affected service to match the scenario.
    n = 15
    series: list[dict[str, Any]] = []

    def baseline(metric: str) -> float:
        return {
            "error_rate": 0.005,
            "p95_latency_ms": 80.0,
            "mem_usage_mb": 120.0,
        }.get(metric, 0.0)

    def anomaly_for(service: str, metric: str) -> float | None:
        if service != "faultline-victim-data":
            return None
        if mode in ("n_plus_one", "slow_query") and metric == "p95_latency_ms":
            return 700.0 if mode == "slow_query" else 260.0
        if mode == "bad_dep" and metric == "error_rate":
            return 0.92
        if mode == "leaky" and metric == "mem_usage_mb":
            return 480.0
        return None

    anom = anomaly_for(service, metric)
    for i in range(n):
        t = now - dt.timedelta(minutes=n - i)
        v = baseline(metric)
        if anom is not None and i >= n - 5:
            v = anom
        series.append({"t": t.isoformat(), "v": v})

    return {
        "service": service,
        "metric": metric,
        "window_minutes": n,
        "points": series,
    }


def fake_error_logs(service: str) -> dict[str, Any]:
    mode = scenario()
    now = _now()
    out: list[dict[str, Any]] = []
    if service == "faultline-victim-data":
        if mode == "bad_dep":
            for i in range(8):
                out.append(
                    {
                        "t": (now - dt.timedelta(minutes=4, seconds=i * 5)).isoformat(),
                        "severity": "ERROR",
                        "msg": "RuntimeError: bad_dep regression: new client lib raised on init",
                    }
                )
        elif mode == "leaky":
            out.append(
                {
                    "t": (now - dt.timedelta(minutes=1)).isoformat(),
                    "severity": "WARNING",
                    "msg": "container memory above 80% threshold",
                }
            )
    return {"service": service, "entries": out}


def fake_recent_traces(service: str) -> dict[str, Any]:
    """A handful of frontend traces showing where time was spent."""
    mode = scenario()
    traces: list[dict[str, Any]] = []
    if service == "faultline-victim-frontend":
        for i in range(4):
            spans = [
                {"name": "GET /", "service": "faultline-victim-frontend", "ms": 12},
                {"name": "GET /verify", "service": "faultline-victim-auth", "ms": 8},
                {
                    "name": "GET /items",
                    "service": "faultline-victim-data",
                    "ms": 220 if mode in ("n_plus_one",) else 680 if mode == "slow_query" else 14,
                },
            ]
            traces.append({"trace_id": f"fake-{i:04d}", "spans": spans})
    return {"service": service, "traces": traces}
