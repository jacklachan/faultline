"""Tests for the FastAPI server (fake-agent mode only).

We force the fake agent path so the tests have no dependency on Vertex AI
or GitLab and can run on the CI venv.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch):
    monkeypatch.setenv("FAULTLINE_FAKE_AGENT", "1")
    monkeypatch.setenv("FAULTLINE_FAKE_SCENARIO", "n_plus_one")
    monkeypatch.setenv("GITLAB_PROJECT_PATH", "demo/faultline-victim")
    # Clear registry between tests
    from server.rollbacks import REGISTRY

    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _client() -> TestClient:
    from server.main import app

    return TestClient(app)


def test_health() -> None:
    r = _client().get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def _stream_events(client: TestClient, body: dict) -> list[dict]:
    """Run /investigate and collect all parsed SSE events."""
    with client.stream("POST", "/investigate", json=body) as r:
        assert r.status_code == 200
        events: list[dict] = []
        current_data: list[str] = []
        for line in r.iter_lines():
            if not line:
                if current_data:
                    events.append(json.loads("\n".join(current_data)))
                    current_data = []
                continue
            if line.startswith("data:"):
                current_data.append(line[len("data:") :].lstrip())
        if current_data:
            events.append(json.loads("\n".join(current_data)))
    return events


def test_investigate_stream_shape() -> None:
    events = _stream_events(
        _client(),
        {"service": "faultline-victim-frontend", "window_minutes": 15, "scenario": "n_plus_one"},
    )
    types = [e["type"] for e in events]
    # Must include the 8-step narration (StepEvents), tool calls, a rollback
    # staging event, and a final event.
    assert "step" in types
    assert "tool_call" in types
    assert "tool_result" in types
    assert "rollback_staged" in types
    assert types[-1] == "final"


def test_investigate_populates_registry() -> None:
    client = _client()
    _stream_events(
        client,
        {"service": "faultline-victim-frontend", "scenario": "bad_dep"},
    )
    pending = client.get("/pending").json()["pending"]
    assert len(pending) == 1
    rb = pending[0]
    assert rb["project_id"] == "demo/faultline-victim"
    assert rb["status"] == "pending"
    assert rb["mr_iid"] == 77


def test_approve_unknown_rollback_returns_404() -> None:
    r = _client().post("/approve/does-not-exist")
    assert r.status_code == 404


def test_approve_marks_registry_acted_on() -> None:
    client = _client()
    _stream_events(client, {"service": "faultline-victim-frontend"})
    rb_id = client.get("/pending").json()["pending"][0]["rollback_id"]
    r = client.post(f"/approve/{rb_id}")
    assert r.status_code == 200
    # Phase 7 walks the registry through approved -> merged (fake-mode jumps
    # straight to merged). Either way it must not stay 'pending'.
    assert r.json()["rollback"]["status"] in ("approved", "merged")

    # /pending only shows status=pending, so the approved one should drop off.
    assert client.get("/pending").json()["pending"] == []


def test_scenario_default_n_plus_one() -> None:
    """When no scenario is supplied, the env default is used."""
    events = _stream_events(_client(), {"service": "faultline-victim-frontend"})
    # The fake suspect for n_plus_one starts with sha a1b2c3d4.
    rb_events = [e for e in events if e["type"] == "rollback_staged"]
    assert rb_events
    assert rb_events[0]["suspect_commit_sha"].startswith("a1b2c3d4")
