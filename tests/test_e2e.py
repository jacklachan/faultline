"""Phase-8 end-to-end fake-mode test.

Drives the full demo loop from inside one Python process:

    POST /investigate (SSE)  ->  agent streams 8-step investigation
                              ->  staged rollback appears in registry
    GET  /pending             ->  exactly that rollback is visible
    POST /approve/{id}        ->  marks merged via GitLab REST (faked)
    GET  /pending             ->  empty again

If this passes, the demo path Faultline ships is wired end-to-end: telemetry
reads, dependency walk, suspect commit naming, postmortem + draft MR
creation, and the human-gated merge. The only thing this test does not cover
is the actual Vertex AI + GitLab + Cloud Run round-trip, which lives in
phase-1/3/8 manual runbook in README.md.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    monkeypatch.setenv("FAULTLINE_FAKE_AGENT", "1")
    monkeypatch.setenv("GITLAB_PROJECT_PATH", "demo/faultline-victim")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-fake")
    from server.rollbacks import REGISTRY

    REGISTRY.clear()
    yield
    REGISTRY.clear()


def _collect(client: TestClient, scenario: str) -> list[dict]:
    with client.stream(
        "POST", "/investigate", json={"service": "faultline-victim-frontend", "scenario": scenario}
    ) as r:
        events: list[dict] = []
        buf: list[str] = []
        for line in r.iter_lines():
            if not line:
                if buf:
                    events.append(json.loads("\n".join(buf)))
                    buf = []
                continue
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
        if buf:
            events.append(json.loads("\n".join(buf)))
    return events


@pytest.mark.parametrize(
    "scenario,expected_sha_prefix",
    [
        ("n_plus_one", "a1b2c3d4"),
        ("slow_query", "e5f6a7b8"),
        ("bad_dep", "c9d0e1f2"),
        ("leaky", "3a4b5c6d"),
    ],
)
def test_full_loop_per_scenario(scenario, expected_sha_prefix):
    from server.main import app

    client = TestClient(app)

    events = _collect(client, scenario)
    types = [e["type"] for e in events]

    # Policy structure visible in the stream.
    assert types[0] == "step"
    assert "rollback_staged" in types
    assert types[-1] == "final"

    rollback = next(e for e in events if e["type"] == "rollback_staged")
    assert rollback["suspect_commit_sha"].startswith(expected_sha_prefix)

    # /pending sees it.
    pending = client.get("/pending").json()["pending"]
    assert len(pending) == 1
    rb_id = pending[0]["rollback_id"]
    assert rb_id == rollback["rollback_id"]

    # Approve walks it through to merged.
    approve = client.post(f"/approve/{rb_id}").json()
    assert approve["rollback"]["status"] == "merged"
    assert approve["merged"]["state"] == "merged"

    # No more pending.
    assert client.get("/pending").json()["pending"] == []
