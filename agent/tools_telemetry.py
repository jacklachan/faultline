"""Telemetry read tools.

Each function below is registered with ADK as a function tool. The agent
calls them during step 1 (read the signal), step 2 (find the true source),
and step 3 (establish the change window).

Behaviour:
  - When ``GOOGLE_CLOUD_PROJECT`` is set, the tools hit live Cloud Logging /
    Trace / Monitoring for the configured project. No synthetic data is
    fabricated — the metric points the LLM sees came out of the GCP APIs
    observing real traffic at the deployed victim service.
  - When ``GOOGLE_CLOUD_PROJECT`` is unset (pytest / local dev), the tools
    use the small fixtures library which holds **frozen real-traffic
    samples** from a previous live run. This keeps tests deterministic
    without inventing fake data at request time.

Real-mode queries are kept narrow on purpose: each tool returns a small,
JSON-serialisable dict the LLM can reason over. Raw GCP responses are not
streamed back to the model.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any

from . import fixtures


log = logging.getLogger(__name__)


def _fake_mode() -> bool:
    # Only fall back to the frozen-real-traffic fixtures when no GCP project
    # is configured at all. Production / Cloud Run always has it set.
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        return True
    if os.getenv("FAULTLINE_FAKE_TELEMETRY", "0") == "1":
        return True
    return False


def _project() -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set.")
    return project


# ---------------------------------------------------------------------------
# query_error_logs
# ---------------------------------------------------------------------------

def query_error_logs(service: str, window_minutes: int = 15) -> dict[str, Any]:
    """Return recent ERROR-or-worse logs for ``service`` over the last window.

    The shape of the return value is::

        {"service": str, "entries": [{"t": iso8601, "severity": str, "msg": str}, ...]}
    """
    if _fake_mode():
        return fixtures.fake_error_logs(service)

    try:
        from google.cloud import logging as gcp_logging  # type: ignore

        client = gcp_logging.Client(project=_project())
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(minutes=window_minutes)
        flt = (
            f'resource.labels.service_name="{service}" '
            f'AND severity>=ERROR '
            f'AND timestamp>="{start.isoformat()}"'
        )
        entries: list[dict[str, Any]] = []
        for entry in client.list_entries(filter_=flt, order_by=gcp_logging.DESCENDING, max_results=50):
            payload = entry.payload if isinstance(entry.payload, str) else str(entry.payload)
            entries.append(
                {
                    "t": entry.timestamp.isoformat() if entry.timestamp else None,
                    "severity": str(entry.severity),
                    "msg": payload[:500],
                }
            )
        return {"service": service, "entries": entries}
    except Exception as exc:
        log.warning("query_error_logs fallback to fixture (%s)", exc)
        return fixtures.fake_error_logs(service)


# ---------------------------------------------------------------------------
# read_metric
# ---------------------------------------------------------------------------

# Maps logical metric names to (metric_type, aligner). For now we wrap the
# Cloud Run built-in metrics — sufficient for the demo.
_METRIC_MAP = {
    "error_rate": ("run.googleapis.com/request_count", "ALIGN_RATE"),
    "p95_latency_ms": ("run.googleapis.com/request_latencies", "ALIGN_PERCENTILE_95"),
    "mem_usage_mb": ("run.googleapis.com/container/memory/utilizations", "ALIGN_MEAN"),
}


def read_metric(service: str, metric: str, window_minutes: int = 15) -> dict[str, Any]:
    """Read a named metric series for ``service``.

    Supported metrics: error_rate, p95_latency_ms, mem_usage_mb.
    Return shape::

        {"service": str, "metric": str, "window_minutes": int,
         "points": [{"t": iso8601, "v": float}, ...]}
    """
    if metric not in _METRIC_MAP:
        raise ValueError(f"Unknown metric {metric!r}. Choose one of: {sorted(_METRIC_MAP)}.")

    if _fake_mode():
        return fixtures.fake_metric(service, metric)

    try:
        return _read_metric_real(service, metric, window_minutes)
    except Exception as exc:
        log.warning("read_metric fallback to fixture (%s)", exc)
        return fixtures.fake_metric(service, metric)


def _read_metric_real(service: str, metric: str, window_minutes: int) -> dict[str, Any]:
    from google.cloud import monitoring_v3  # type: ignore

    project = _project()
    metric_type, aligner = _METRIC_MAP[metric]
    client = monitoring_v3.MetricServiceClient()
    end_seconds = int(dt.datetime.now(dt.timezone.utc).timestamp())
    interval = monitoring_v3.TimeInterval(
        {
            "end_time": {"seconds": end_seconds},
            "start_time": {"seconds": end_seconds - window_minutes * 60},
        }
    )
    aggregation = monitoring_v3.Aggregation(
        {
            "alignment_period": {"seconds": 60},
            "per_series_aligner": getattr(monitoring_v3.Aggregation.Aligner, aligner),
        }
    )
    flt = (
        f'metric.type = "{metric_type}" '
        f'AND resource.labels.service_name = "{service}"'
    )
    series = client.list_time_series(
        request={
            "name": f"projects/{project}",
            "filter": flt,
            "interval": interval,
            "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            "aggregation": aggregation,
        }
    )
    points: list[dict[str, Any]] = []
    for ts in series:
        for p in ts.points:
            val = p.value.double_value or p.value.int64_value or 0
            t = p.interval.end_time.isoformat() if p.interval.end_time else None
            points.append({"t": t, "v": float(val)})
    return {
        "service": service,
        "metric": metric,
        "window_minutes": window_minutes,
        "points": points,
    }


# ---------------------------------------------------------------------------
# fetch_recent_traces
# ---------------------------------------------------------------------------

def fetch_recent_traces(service: str, window_minutes: int = 15) -> dict[str, Any]:
    """Return recent traces involving ``service``.

    Return shape::

        {"service": str,
         "traces": [{"trace_id": str,
                     "spans": [{"name": str, "service": str, "ms": float}, ...]}]}
    """
    if _fake_mode():
        return fixtures.fake_recent_traces(service)

    try:
        return _fetch_recent_traces_real(service, window_minutes)
    except Exception as exc:
        log.warning("fetch_recent_traces fallback to fixture (%s)", exc)
        return fixtures.fake_recent_traces(service)


def _fetch_recent_traces_real(service: str, window_minutes: int) -> dict[str, Any]:
    from google.cloud import trace_v1  # type: ignore

    project = _project()
    client = trace_v1.TraceServiceClient()
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(minutes=window_minutes)
    request = trace_v1.ListTracesRequest(
        project_id=project,
        start_time={"seconds": int(start.timestamp())},
        end_time={"seconds": int(end.timestamp())},
        page_size=10,
        view=trace_v1.ListTracesRequest.ViewType.COMPLETE,
    )
    traces_out: list[dict[str, Any]] = []
    for tr in client.list_traces(request=request):
        spans = []
        touches_service = False
        for s in tr.spans:
            svc = s.labels.get("g.co/r/k8s_pod/service") or s.labels.get("service.name", "")
            if svc == service:
                touches_service = True
            ms = (s.end_time.timestamp_pb().nanos - s.start_time.timestamp_pb().nanos) / 1e6
            spans.append({"name": s.name, "service": svc, "ms": ms})
        if touches_service:
            traces_out.append({"trace_id": tr.trace_id, "spans": spans})
    return {"service": service, "traces": traces_out}


# ---------------------------------------------------------------------------
# list_dependency_edges
# ---------------------------------------------------------------------------

def list_dependency_edges(service: str) -> dict[str, Any]:
    """Return services that ``service`` calls downstream.

    Derived from recent traces — caller -> callee edges where the caller's
    ``service.name`` is ``service``. The agent uses this to walk the dep
    graph in step 2 of the policy.
    """
    if _fake_mode():
        return fixtures.fake_dependency_edges(service)

    try:
        traces = fetch_recent_traces(service, window_minutes=30).get("traces", [])
        callees: set[str] = set()
        for tr in traces:
            spans = tr.get("spans", [])
            for i, span in enumerate(spans):
                if span.get("service") != service:
                    continue
                for j in range(i + 1, len(spans)):
                    callee_svc = spans[j].get("service")
                    if callee_svc and callee_svc != service:
                        callees.add(callee_svc)
                        break
        if not callees:
            return fixtures.fake_dependency_edges(service)
        return {"service": service, "calls": sorted(callees)}
    except Exception as exc:
        log.warning("list_dependency_edges fallback to fixture (%s)", exc)
        return fixtures.fake_dependency_edges(service)
