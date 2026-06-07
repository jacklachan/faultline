"""FastAPI server: SSE-wrapped Faultline agent + static console.

Endpoints:

  GET  /health                         — liveness probe
  POST /investigate                    — start an investigation; streams SSE
  GET  /pending                        — list staged rollbacks awaiting approval
  POST /approve/{rollback_id}          — (phase 7) merge the rollback MR
  GET  /                               — serves web/ static console (phase 6)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response


def _require_demo_secret(x_faultline_token: str | None = Header(default=None)) -> None:
    """Gate mutating endpoints behind a shared secret.

    If ``FAULTLINE_DEMO_SECRET`` is unset, the endpoints are open (dev / first
    deploy). In production set the env var; every POST to /demo/* and
    /approve/* then requires ``X-Faultline-Token: <secret>`` as a header.
    """
    expected = os.getenv("FAULTLINE_DEMO_SECRET")
    if not expected:
        return
    if x_faultline_token != expected:
        raise HTTPException(status_code=401, detail="missing or invalid X-Faultline-Token header")


import os  # noqa: E402
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

import asyncio

from .demo import plant_regression
from .gitlab_actions import mark_mr_ready, merge_mr
from .investigate import stream_investigation
from .rollbacks import REGISTRY
from .victim_control import drive_real_traffic, set_data_regression


log = logging.getLogger(__name__)

app = FastAPI(title="Faultline", version="0.5.0-phase5")


class InvestigateRequest(BaseModel):
    service: str = Field(..., description="The alerting service, e.g. faultline-victim-frontend.")
    window_minutes: int = Field(15, ge=1, le=240)
    scenario: str | None = Field(
        None,
        description="Offline scenario hint: n_plus_one, slow_query, bad_dep, leaky.",
    )
    project_id: str | None = Field(
        None,
        description="GitLab project path. Defaults to GITLAB_PROJECT_PATH env.",
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "phase": "5"}


async def _investigate_stream(
    request: Request,
    *,
    service: str,
    window_minutes: int,
    scenario: str | None,
    project_id: str | None,
) -> EventSourceResponse:
    async def _gen():
        # Initial padding flushes any intermediate proxy buffer so the browser
        # sees the stream as live immediately.
        yield {"event": "ready", "data": json.dumps({"type": "ready"})}
        async for ev in stream_investigation(
            service=service,
            window_minutes=window_minutes,
            scenario=scenario,
            project_id=project_id,
        ):
            if await request.is_disconnected():
                break
            yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}

    resp = EventSourceResponse(_gen())
    # nginx + Envoy honour this; tells proxies not to buffer the response.
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.post("/investigate")
async def investigate_post(req: InvestigateRequest, request: Request) -> EventSourceResponse:
    """Stream an investigation as Server-Sent Events (POST form)."""
    return await _investigate_stream(
        request,
        service=req.service,
        window_minutes=req.window_minutes,
        scenario=req.scenario,
        project_id=req.project_id,
    )


@app.get("/investigate")
async def investigate_get(
    request: Request,
    service: str,
    window_minutes: int = 15,
    scenario: str | None = None,
    project_id: str | None = None,
) -> EventSourceResponse:
    """Stream an investigation as Server-Sent Events (GET form).

    Provided so the browser can use the native ``EventSource`` API, which is
    more reliable across proxies + Chrome extensions than POST + fetch
    streaming.
    """
    return await _investigate_stream(
        request,
        service=service,
        window_minutes=window_minutes,
        scenario=scenario,
        project_id=project_id,
    )


@app.post("/demo/plant")
async def demo_plant(
    scenario: str = "n_plus_one",
    x_faultline_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_demo_secret(x_faultline_token)
    """Plant a fresh regression end-to-end:

      1. create a real GitLab branch + commit + merged MR
      2. flip the live victim data service's REGRESSION_MODE env var
      3. drive real traffic at the victim for ~60s so Cloud Monitoring
         sees a real p95 anomaly the next investigation can read.

    No synthetic data — all of these are real-world side effects on real
    services. Idempotent: each call creates a fresh branch + MR.
    """
    try:
        planted = await plant_regression(scenario)
    except Exception as exc:
        log.exception("demo plant (GitLab side) failed")
        raise HTTPException(status_code=502, detail=f"plant failed: {exc}") from exc

    # Flip the live victim Cloud Run env so the regression is real.
    try:
        flip = await set_data_regression(scenario)
        planted["victim_flipped"] = flip
    except Exception as exc:
        log.warning("Cloud Run env flip failed: %s", exc)
        planted["victim_flipped"] = {"error": str(exc)}

    # Generate real Cloud Monitoring signal in the background.
    asyncio.create_task(drive_real_traffic(duration_seconds=90, rps=4))
    planted["load_started"] = True
    return planted


@app.post("/demo/reset")
async def demo_reset(
    x_faultline_token: str | None = Header(default=None),
) -> dict[str, Any]:
    """Clear REGRESSION_MODE on the live victim so latency returns to baseline."""
    _require_demo_secret(x_faultline_token)
    try:
        return await set_data_regression("")
    except Exception as exc:
        log.exception("demo reset failed")
        raise HTTPException(status_code=502, detail=f"reset failed: {exc}") from exc


@app.get("/pending")
def pending() -> dict[str, Any]:
    return {"pending": [r.to_dict() for r in REGISTRY.all() if r.status == "pending"]}


@app.post("/approve/{rollback_id}")
async def approve(
    rollback_id: str,
    x_faultline_token: str | None = Header(default=None),
) -> dict[str, Any]:
    _require_demo_secret(x_faultline_token)
    """Mark the draft rollback MR Ready, then merge it.

    Merging triggers the victim_service ``.gitlab-ci.yml`` deploy job, which
    redeploys the rolled-back code to Cloud Run. We do not wait for the
    pipeline to finish here — the UI shows the merge succeeded and the
    deploy progress is visible in GitLab CI.
    """
    rb = REGISTRY.get(rollback_id)
    if rb is None:
        raise HTTPException(status_code=404, detail="unknown rollback_id")
    if rb.status != "pending":
        return {"rollback": rb.to_dict(), "note": "already acted on"}

    REGISTRY.set_status(rollback_id, "approved")
    try:
        await mark_mr_ready(rb.project_id, rb.mr_iid)
        merged = await merge_mr(rb.project_id, rb.mr_iid)
        REGISTRY.set_status(rollback_id, "merged")
        # Real recovery: clear REGRESSION_MODE on the live victim so the next
        # Cloud Monitoring sample shows latency falling back to baseline.
        try:
            await set_data_regression("")
        except Exception as exc:
            log.warning("post-merge victim reset failed: %s", exc)
        return {"rollback": REGISTRY.get(rollback_id).to_dict(), "merged": merged}  # type: ignore[union-attr]
    except Exception as exc:
        log.exception("approve failed for rollback %s", rollback_id)
        REGISTRY.set_status(rollback_id, "failed", error=f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"merge failed: {exc}") from exc


# Static console mounted last so / does not steal API routes.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class _NoCacheStaticFiles(StaticFiles):
    """Force fresh fetches so users do not run with a stale console build."""

    def file_response(self, *args, **kwargs) -> Response:  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp


if _WEB_DIR.is_dir():
    app.mount("/", _NoCacheStaticFiles(directory=str(_WEB_DIR), html=True), name="web")
