"""Live victim-service control helpers.

These let the server flip the deployed Cloud Run ``data`` service in or out of
regression mode for real, and drive enough real traffic that Cloud Monitoring
sees a real anomaly. No synthetic data — every metric the agent reads in
``/investigate`` comes from Cloud Monitoring observing actual traffic that
this process generated.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Iterable

import httpx


log = logging.getLogger(__name__)


_DATA_SERVICE = "faultline-victim-data"


def _project() -> str:
    p = os.getenv("GOOGLE_CLOUD_PROJECT")
    if not p:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT not set on the server")
    return p


def _region() -> str:
    return (
        os.getenv("GOOGLE_CLOUD_REGION")
        or os.getenv("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )


async def set_data_regression(mode: str) -> dict[str, str]:
    """Set the REGRESSION_MODE env var on the live victim ``data`` service.

    Empty ``mode`` clears the regression (service recovers).
    """
    from google.cloud import run_v2

    project = _project()
    region = _region()
    name = f"projects/{project}/locations/{region}/services/{_DATA_SERVICE}"
    client = run_v2.ServicesAsyncClient()

    service = await client.get_service(request={"name": name})

    # Cloud Run keeps env vars on each container in the revision template.
    container = service.template.containers[0]
    new_env: list[run_v2.EnvVar] = []
    seen = False
    for ev in container.env:
        if ev.name == "REGRESSION_MODE":
            seen = True
            if mode:
                new_env.append(run_v2.EnvVar(name="REGRESSION_MODE", value=mode))
        else:
            new_env.append(ev)
    if not seen and mode:
        new_env.append(run_v2.EnvVar(name="REGRESSION_MODE", value=mode))
    container.env = new_env

    op = await client.update_service(request={"service": service})
    result = await op.result()
    log.info("set REGRESSION_MODE=%r on %s -> %s", mode, _DATA_SERVICE, result.uri)
    return {"service": _DATA_SERVICE, "mode": mode, "url": result.uri}


def _victim_frontend_url() -> str:
    url = os.getenv("VICTIM_SERVICE_URL")
    if url:
        return url.rstrip("/")
    # Fall back to convention based on the deployed services in our project.
    project_num = os.getenv("GOOGLE_CLOUD_PROJECT_NUMBER", "1083927168045")
    return f"https://faultline-victim-frontend-{project_num}.{_region()}.run.app"


async def drive_real_traffic(duration_seconds: int = 60, rps: int = 4) -> None:
    """Hit the victim frontend ``rps`` requests per second for ``duration``.

    Runs as a fire-and-forget background task so /demo/plant returns quickly.
    Generates the real Cloud Monitoring signal the investigation reads.
    """
    url = _victim_frontend_url() + "/"
    end = asyncio.get_event_loop().time() + duration_seconds
    interval = 1.0 / max(rps, 1)
    sent = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        while asyncio.get_event_loop().time() < end:
            try:
                await client.get(url)
                sent += 1
            except Exception as exc:
                log.debug("load gen request failed: %s", exc)
            await asyncio.sleep(interval)
    log.info("real-traffic burst done: %d requests over %ds", sent, duration_seconds)
