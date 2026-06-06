"""Tests for /approve — both fake-mode and the real merge path (mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("FAULTLINE_FAKE_AGENT", "1")
    monkeypatch.setenv("FAULTLINE_FAKE_SCENARIO", "n_plus_one")
    monkeypatch.setenv("GITLAB_PROJECT_PATH", "demo/faultline-victim")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-fake")
    from server.rollbacks import REGISTRY

    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _client() -> TestClient:
    from server.main import app

    return TestClient(app)


def _stage_one(client: TestClient) -> str:
    """Run the fake investigation once to populate a rollback. Return its id."""
    with client.stream("POST", "/investigate", json={"service": "faultline-victim-frontend"}) as r:
        for _ in r.iter_lines():
            pass
    return client.get("/pending").json()["pending"][0]["rollback_id"]


def test_approve_fake_mode_marks_merged() -> None:
    client = _client()
    rb_id = _stage_one(client)
    r = client.post(f"/approve/{rb_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["rollback"]["status"] == "merged"
    assert body["merged"]["state"] == "merged"


def test_approve_real_path_calls_gitlab_rest(monkeypatch) -> None:
    # Flip out of fake-agent mode for the action, but keep fake-stream so the
    # rollback gets staged through the deterministic path.
    monkeypatch.setenv("FAULTLINE_FAKE_AGENT", "1")

    client = _client()
    rb_id = _stage_one(client)

    # Now disable fake for the action layer only by stubbing _fake().
    from server import gitlab_actions

    monkeypatch.setattr(gitlab_actions, "_fake", lambda: False)

    fake_ready = AsyncMock(return_value={"iid": 77, "title": "revert", "draft": False})
    fake_merge = AsyncMock(return_value={"iid": 77, "state": "merged", "merge_commit_sha": "abc123"})

    with patch.object(gitlab_actions, "mark_mr_ready", fake_ready), patch.object(
        gitlab_actions, "merge_mr", fake_merge
    ):
        # Re-import the route's view of these names (they were imported into main).
        from server import main as server_main

        monkeypatch.setattr(server_main, "mark_mr_ready", fake_ready)
        monkeypatch.setattr(server_main, "merge_mr", fake_merge)
        r = client.post(f"/approve/{rb_id}")

    assert r.status_code == 200
    body = r.json()
    assert body["rollback"]["status"] == "merged"
    fake_ready.assert_awaited_once_with("demo/faultline-victim", 77)
    fake_merge.assert_awaited_once_with("demo/faultline-victim", 77)


def test_approve_merge_failure_marks_failed(monkeypatch) -> None:
    client = _client()
    rb_id = _stage_one(client)

    from server import gitlab_actions, main as server_main

    monkeypatch.setattr(gitlab_actions, "_fake", lambda: False)
    fake_ready = AsyncMock(return_value={"iid": 77, "draft": False})
    fake_merge = AsyncMock(side_effect=RuntimeError("405 not_acceptable: pipeline must succeed"))
    monkeypatch.setattr(server_main, "mark_mr_ready", fake_ready)
    monkeypatch.setattr(server_main, "merge_mr", fake_merge)

    r = client.post(f"/approve/{rb_id}")
    assert r.status_code == 502
    assert "merge failed" in r.json()["detail"]

    pending = client.get("/pending").json()["pending"]
    assert pending == []  # status went to 'failed', not 'pending'

    # Inspect via registry directly
    from server.rollbacks import REGISTRY

    rb = REGISTRY.get(rb_id)
    assert rb is not None
    assert rb.status == "failed"
    assert "pipeline must succeed" in (rb.last_error or "")


def test_double_approve_is_noop() -> None:
    client = _client()
    rb_id = _stage_one(client)
    first = client.post(f"/approve/{rb_id}")
    assert first.status_code == 200
    second = client.post(f"/approve/{rb_id}")
    assert second.status_code == 200
    assert second.json().get("note") == "already acted on"
