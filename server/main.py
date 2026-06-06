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

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .gitlab_actions import mark_mr_ready, merge_mr
from .investigate import stream_investigation
from .rollbacks import REGISTRY


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


@app.post("/investigate")
async def investigate(req: InvestigateRequest, request: Request) -> EventSourceResponse:
    """Stream an investigation as Server-Sent Events.

    Each SSE message is a JSON object with at least ``{"type": ...}``. See
    ``server/events.py`` for the full set of types the UI can render.
    """

    async def _gen():
        async for ev in stream_investigation(
            service=req.service,
            window_minutes=req.window_minutes,
            scenario=req.scenario,
            project_id=req.project_id,
        ):
            if await request.is_disconnected():
                break
            yield {"event": ev.get("type", "message"), "data": json.dumps(ev)}

    return EventSourceResponse(_gen())


@app.get("/pending")
def pending() -> dict[str, Any]:
    return {"pending": [r.to_dict() for r in REGISTRY.all() if r.status == "pending"]}


@app.post("/approve/{rollback_id}")
async def approve(rollback_id: str) -> dict[str, Any]:
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
        return {"rollback": REGISTRY.get(rollback_id).to_dict(), "merged": merged}  # type: ignore[union-attr]
    except Exception as exc:
        log.exception("approve failed for rollback %s", rollback_id)
        REGISTRY.set_status(rollback_id, "failed", error=f"{type(exc).__name__}: {exc}")
        raise HTTPException(status_code=502, detail=f"merge failed: {exc}") from exc


# Static console mounted last so / does not steal API routes.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="web")
