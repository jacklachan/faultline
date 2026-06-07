"""The /investigate streaming engine.

Two paths:

  * **Real path** (default): runs the live ADK ``LlmAgent`` against Vertex AI,
    converts ADK ``Event``s into our SSE event shape, and writes a staged
    rollback into the in-memory registry when the agent reports one.

  * **Fake path** (``FAULTLINE_FAKE_AGENT=1``): emits a canned step sequence
    matching the active ``FAULTLINE_FAKE_SCENARIO``. This lets phase 6 UI work
    proceed without Vertex / GitLab credentials and keeps the demo flow
    parallel to the real run.

Both paths yield the same ``dict`` shape, so the SSE endpoint code in
``main.py`` is identical.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator

from .events import (
    ErrorEvent,
    FinalEvent,
    RollbackStagedEvent,
    StepEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .rollbacks import REGISTRY


log = logging.getLogger(__name__)


def _fake_agent() -> bool:
    return os.getenv("FAULTLINE_FAKE_AGENT", "0") == "1"


# ---------------------------------------------------------------------------
# Fake path
# ---------------------------------------------------------------------------

_FAKE_SUSPECT_BY_SCENARIO = {
    "n_plus_one": ("a1b2c3d4", "feat(data): batch user lookups by id (N+1 fix attempt)"),
    "slow_query": ("e5f6a7b8", "perf(data): drop redundant index on items.updated_at"),
    "bad_dep": ("c9d0e1f2", "chore(data): swap json client to ujson"),
    "leaky": ("3a4b5c6d", "feat(data): in-memory result cache"),
}


async def _fake_run(scenario: str, project_id: str) -> AsyncGenerator[dict[str, Any], None]:
    """Emit a deterministic step trace for offline UI dev + demo dry-runs."""
    sha, msg = _FAKE_SUSPECT_BY_SCENARIO.get(
        scenario, ("0000000", "feat: unknown scenario")
    )

    async def _emit(ev) -> dict[str, Any]:
        await asyncio.sleep(0.4)  # let the UI feel like work is happening
        return ev.to_dict()

    yield await _emit(StepEvent(step=1, text=f"Reading alert signal on faultline-victim-frontend (scenario={scenario})."))
    yield await _emit(ToolCallEvent(name="read_metric", args={"service": "faultline-victim-frontend", "metric": "error_rate"}))
    yield await _emit(ToolResultEvent(name="read_metric", result_preview="error_rate spiked starting -5m on frontend"))

    yield await _emit(StepEvent(step=2, text="Walking dependency graph to find the true source."))
    yield await _emit(ToolCallEvent(name="list_dependency_edges", args={"service": "faultline-victim-frontend"}))
    yield await _emit(ToolResultEvent(name="list_dependency_edges", result_preview="frontend -> [auth, data]"))
    yield await _emit(ToolCallEvent(name="read_metric", args={"service": "faultline-victim-data", "metric": "p95_latency_ms"}))
    yield await _emit(ToolResultEvent(name="read_metric", result_preview="p95 latency on data jumped from 80ms to 260ms"))
    yield await _emit(StepEvent(step=2, text="Root cause is faultline-victim-data, not frontend (cascade)."))

    yield await _emit(StepEvent(step=3, text="Establishing the change window: looking at recent merges to data."))
    yield await _emit(ToolCallEvent(name="search", args={"scope": "commits", "id": project_id}))
    yield await _emit(ToolResultEvent(name="search", result_preview=f"3 recent commits, most recent: {sha[:8]} {msg!r}"))

    yield await _emit(StepEvent(step=4, text=f"Reading suspect diff at commit {sha[:8]}."))
    yield await _emit(ToolCallEvent(name="get_merge_request_diffs", args={"id": project_id, "merge_request_iid": 42}))
    yield await _emit(ToolResultEvent(name="get_merge_request_diffs", result_preview="diff touches data/items endpoint loop"))

    yield await _emit(StepEvent(step=5, text=f"Symptom-to-change fit: scenario={scenario} matches the change type in this diff."))

    yield await _emit(StepEvent(step=6, text=f"Converging on commit {sha[:8]} as the most likely offender. Confidence: high."))

    yield await _emit(StepEvent(step=7, text="Opening postmortem issue + DRAFT rollback merge request via GitLab MCP."))
    yield await _emit(ToolCallEvent(name="create_issue", args={"id": project_id, "title": f"[faultline] Suspected regression: {sha[:8]}"}))
    issue_url = f"https://gitlab.com/{project_id}/-/issues/101"
    yield await _emit(ToolResultEvent(name="create_issue", result_preview=f"issue#101 created at {issue_url}"))
    yield await _emit(ToolCallEvent(name="create_merge_request", args={"id": project_id, "draft": True, "title": f"Draft: revert {sha[:8]}"}))
    mr_url = f"https://gitlab.com/{project_id}/-/merge_requests/77"
    yield await _emit(ToolResultEvent(name="create_merge_request", result_preview=f"draft MR!77 at {mr_url}"))

    rb = REGISTRY.stage(
        project_id=project_id,
        issue_url=issue_url,
        mr_url=mr_url,
        mr_iid=77,
        suspect_commit_sha=sha,
    )
    yield await _emit(
        RollbackStagedEvent(
            rollback_id=rb.rollback_id,
            issue_url=issue_url,
            mr_url=mr_url,
            mr_iid=77,
            project_id=project_id,
            suspect_commit_sha=sha,
        )
    )

    yield await _emit(StepEvent(step=8, text="STOP. Awaiting human approval before merging."))
    yield await _emit(
        FinalEvent(
            summary=(
                f"Suspect commit {sha[:8]} on {project_id} causes the "
                f"{scenario} symptom. Draft rollback MR is staged; click "
                f"Approve to merge and trigger redeploy."
            )
        )
    )


# ---------------------------------------------------------------------------
# Real path (ADK Runner)
# ---------------------------------------------------------------------------

def _extract_text(content: Any) -> str:
    if content is None:
        return ""
    parts = getattr(content, "parts", None)
    if not parts:
        return ""
    out: list[str] = []
    for p in parts:
        t = getattr(p, "text", None)
        if t:
            out.append(t)
    return "".join(out)


def _result_preview(value: Any, limit: int = 240) -> str:
    try:
        return json.dumps(value, default=str)[:limit]
    except Exception:
        return str(value)[:limit]


import re


def _extract_suspect_sha(text: str) -> str | None:
    """Pull a 40-char or 8-char hex SHA out of agent narration."""
    m = re.search(r"\b([0-9a-f]{40})\b", text)
    if m:
        return m.group(1)
    m = re.search(r"\b([0-9a-f]{7,12})\b", text)
    return m.group(1) if m else None


async def _real_run(
    *,
    scenario: str,
    service: str,
    window_minutes: int,
    project_id: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Run the real ADK agent and convert its events to our SSE shape."""
    # Local imports so a missing ADK install only matters when we actually run.
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    from agent.agent import build_agent

    agent = build_agent(include_gitlab=True)
    session_service = InMemorySessionService()
    runner = Runner(
        agent=agent,
        app_name="faultline",
        session_service=session_service,
        auto_create_session=True,
    )

    user_msg_text = (
        f"Incident: service '{service}' is alerting over the last "
        f"{window_minutes} minutes. Scenario hint (offline replay only): "
        f"{scenario}. The project to investigate in GitLab is {project_id}. "
        "Follow the 8-step investigation policy. Stage a DRAFT rollback MR "
        "and stop for approval — do not merge."
    )
    new_message = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_msg_text)],
    )

    step_counter = 0
    seen_rollback = False
    last_text = ""
    last_suspect_sha: str | None = None
    mr_create_failed = False
    deferred_final: dict[str, Any] | None = None

    # Heuristic step labels surfaced as `tool_call.policy_step` so the UI can
    # show which of the 8 policy steps a given tool call belongs to.
    POLICY_STEPS = {
        "read_metric": (1, "Read the incident signal"),
        "query_error_logs": (1, "Read the incident signal"),
        "list_dependency_edges": (2, "Find the true source"),
        "fetch_recent_traces": (2, "Find the true source"),
        "list_commits": (3, "Establish the change window"),
        "list_merge_requests": (3, "Establish the change window"),
        "get_merge_request": (4, "Read the suspect diff"),
        "get_merge_request_diffs": (4, "Read the suspect diff"),
        "create_issue": (7, "Action: open postmortem issue"),
        "create_merge_request": (7, "Action: stage DRAFT rollback MR"),
    }

    async for event in runner.run_async(
        user_id="demo",
        session_id="demo",
        new_message=new_message,
    ):
        # Tool calls
        try:
            calls = event.get_function_calls() if hasattr(event, "get_function_calls") else []
        except Exception:
            calls = []
        for c in calls or []:
            name = getattr(c, "name", "tool")
            args = getattr(c, "args", {}) or {}
            args_dict = dict(args)
            tc = ToolCallEvent(name=name, args=args_dict).to_dict()
            label = POLICY_STEPS.get(name)
            if label:
                tc["policy_step"] = label[0]
                tc["policy_label"] = label[1]
            # Sniff a suspect SHA out of any tool arg so we can still fall
            # back even when the agent jumps straight to create_issue without
            # ever narrating the SHA in plain text.
            for v in args_dict.values():
                if isinstance(v, str):
                    found = _extract_suspect_sha(v)
                    if found:
                        last_suspect_sha = found
                        break
            yield tc

        # Tool results
        try:
            responses = (
                event.get_function_responses()
                if hasattr(event, "get_function_responses")
                else []
            )
        except Exception:
            responses = []
        for r in responses or []:
            name = getattr(r, "name", "tool")
            response = getattr(r, "response", None)
            preview = _result_preview(response)
            yield ToolResultEvent(name=name, result_preview=preview).to_dict()
            # Also mine the preview for a 40-hex sha.
            extracted = _extract_suspect_sha(preview)
            if extracted:
                last_suspect_sha = extracted

            if name == "create_merge_request":
                # Treat string-encoded JSON payloads (zereight wrapper) the same.
                parsed = response
                if isinstance(response, dict) and "content" in response:
                    parts = response.get("content") or []
                    if parts and isinstance(parts, list):
                        inner = parts[0].get("text") if isinstance(parts[0], dict) else None
                        try:
                            parsed = json.loads(inner) if isinstance(inner, str) else inner
                        except Exception:
                            parsed = None
                if isinstance(parsed, dict) and parsed.get("iid") and not seen_rollback:
                    mr_url = parsed.get("web_url") or parsed.get("url", "")
                    mr_iid = int(parsed.get("iid"))
                    sha = parsed.get("sha", "") or last_suspect_sha or ""
                    rb = REGISTRY.stage(
                        project_id=project_id,
                        issue_url="",
                        mr_url=mr_url,
                        mr_iid=mr_iid,
                        suspect_commit_sha=sha,
                    )
                    yield RollbackStagedEvent(
                        rollback_id=rb.rollback_id,
                        issue_url="",
                        mr_url=mr_url,
                        mr_iid=mr_iid,
                        project_id=project_id,
                        suspect_commit_sha=sha,
                    ).to_dict()
                    seen_rollback = True
                else:
                    # Agent's MR create failed (branch missing, etc.). Mark for fallback.
                    mr_create_failed = True

        # Plain text narration
        text = _extract_text(getattr(event, "content", None))
        if text and not (calls or responses):
            partial = getattr(event, "partial", False)
            if not partial:
                step_counter += 1
                yield StepEvent(step=step_counter, text=text.strip()).to_dict()
                last_text = text
                last_suspect_sha = _extract_suspect_sha(text) or last_suspect_sha

        if hasattr(event, "is_final_response"):
            try:
                if event.is_final_response():
                    last_text = text or last_text
                    if text:
                        last_suspect_sha = _extract_suspect_sha(text) or last_suspect_sha
                    # Hold the final event. If the post-loop REST fallback
                    # successfully stages a rollback we want the user to see
                    # "rollback staged" BEFORE the agent's summary, since the
                    # summary text usually apologises for not being able to
                    # create the MR.
                    deferred_final = FinalEvent(
                        summary=text.strip() or "Investigation complete."
                    ).to_dict()
            except Exception:
                pass

    # Fallback: if agent named a suspect but could not stage the MR via MCP,
    # finish the job with a direct GitLab REST revert + draft MR. Step 7 of the
    # policy must complete deterministically.
    fallback_failed = False
    if not seen_rollback and last_suspect_sha:
        try:
            from .gitlab_actions import open_draft_mr_via_rest, revert_commit_via_rest
            import os as _os
            from urllib.parse import quote

            short = last_suspect_sha[:8]
            revert_branch = f"faultline-revert-{short}"

            # Step a: create the revert commit + branch.
            revert_resp = await revert_commit_via_rest(project_id, last_suspect_sha, revert_branch)

            # Step b: open the draft MR.
            mr_resp = await open_draft_mr_via_rest(
                project_id,
                source_branch=revert_branch,
                target_branch=_os.getenv("GITLAB_DEFAULT_BRANCH", "main"),
                title=f"Revert {short}",
                description=(
                    f"Auto-staged by Faultline. Reverts suspect commit "
                    f"`{last_suspect_sha}` identified during investigation of "
                    f"`{service}`."
                ),
            )

            mr_url = mr_resp.get("web_url", "")
            mr_iid = int(mr_resp.get("iid", 0) or 0)
            rb = REGISTRY.stage(
                project_id=project_id,
                issue_url="",
                mr_url=mr_url,
                mr_iid=mr_iid,
                suspect_commit_sha=last_suspect_sha,
            )
            yield RollbackStagedEvent(
                rollback_id=rb.rollback_id,
                issue_url="",
                mr_url=mr_url,
                mr_iid=mr_iid,
                project_id=project_id,
                suspect_commit_sha=last_suspect_sha,
            ).to_dict()
        except Exception as exc:
            log.exception("post-investigation REST fallback failed")
            fallback_failed = True
            yield ErrorEvent(
                message=f"Could not auto-stage rollback MR: {type(exc).__name__}: {exc}"
            ).to_dict()

    # Emit the final event LAST so the UI sees rollback_staged + verdict before
    # the agent's summary (which often apologises for the MCP-side failure).
    if deferred_final is not None:
        if seen_rollback and not fallback_failed:
            deferred_final["summary"] = (
                "Investigation complete. Suspect commit identified and DRAFT "
                "rollback MR staged in GitLab. Click Approve below to merge."
            )
        yield deferred_final


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def stream_investigation(
    *,
    service: str,
    window_minutes: int = 15,
    scenario: str | None = None,
    project_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield SSE-shaped dicts describing the agent's investigation step by step."""
    scenario = (scenario or os.getenv("FAULTLINE_FAKE_SCENARIO", "n_plus_one")).strip().lower()
    project_id = project_id or os.getenv("GITLAB_PROJECT_PATH", "demo/faultline-victim")

    try:
        if _fake_agent():
            async for ev in _fake_run(scenario, project_id):
                yield ev
        else:
            async for ev in _real_run(
                scenario=scenario,
                service=service,
                window_minutes=window_minutes,
                project_id=project_id,
            ):
                yield ev
    except Exception as exc:  # surface to the UI rather than dropping the stream
        log.exception("investigation stream failed")
        yield ErrorEvent(message=f"{type(exc).__name__}: {exc}").to_dict()
