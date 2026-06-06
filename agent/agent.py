"""Faultline ADK agent factory.

This assembles the runtime agent that drives the investigation:

  Gemini (Vertex AI)  + INVESTIGATION_POLICY system prompt
                      + telemetry function tools (Cloud Logging/Trace/Monitoring)
                      + GitLab MCP toolset (read commits/diffs, create issue + draft MR)

The hackathon rules require the runtime LLM to be Gemini on Vertex AI and
orchestration to be ADK. Both are honoured here.

API surfaces (LlmAgent constructor, Vertex backend env flags, Gemini 2.5
model id) were verified against live Google ADK + Vertex AI docs in phase
4 before this was written.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .prompt import INVESTIGATION_POLICY
from . import tools_telemetry


log = logging.getLogger(__name__)


# Default to Gemini 2.5 Flash on Vertex AI. Cheapest GA Gemini 2.5 model — fits
# the free-tier decision — and supports function calling + MCP tools.
DEFAULT_MODEL = "gemini-2.5-flash"

# Telemetry tools we want the agent to be able to call. These are plain
# Python callables; ADK auto-wraps them as function tools from their signature
# and docstring.
TELEMETRY_TOOL_FUNCS = (
    tools_telemetry.query_error_logs,
    tools_telemetry.read_metric,
    tools_telemetry.fetch_recent_traces,
    tools_telemetry.list_dependency_edges,
)


def _ensure_vertex_env() -> None:
    """Make sure ADK / google-genai will route through Vertex AI, not AI Studio.

    Rule 1 forbids any non-Google LLM. Rule 2 requires Vertex AI specifically
    (not the public AI Studio endpoint). We assert the env and set the flag
    if the user has not.
    """
    if not os.getenv("GOOGLE_CLOUD_PROJECT"):
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set. Faultline runs against Vertex "
            "AI; the project must be provided via .env."
        )
    if not os.getenv("GOOGLE_CLOUD_REGION") and not os.getenv("GOOGLE_CLOUD_LOCATION"):
        raise RuntimeError(
            "GOOGLE_CLOUD_REGION (or GOOGLE_CLOUD_LOCATION) is not set. "
            "Vertex AI is regional; pick e.g. us-central1 and put it in .env."
        )
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    # google-genai reads GOOGLE_CLOUD_LOCATION; mirror our REGION onto it.
    if not os.getenv("GOOGLE_CLOUD_LOCATION"):
        os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ["GOOGLE_CLOUD_REGION"]


def build_agent(*, include_gitlab: bool = True) -> Any:
    """Return a fully-wired ``LlmAgent`` ready for `run_async`.

    Parameters
    ----------
    include_gitlab:
        If True (default), attach the GitLab MCP toolset. Set False for
        local agent-only smoke tests that exercise just the telemetry
        tools.
    """
    _ensure_vertex_env()

    from google.adk.agents import LlmAgent

    model = os.getenv("VERTEX_AI_MODEL") or DEFAULT_MODEL

    tools: list[Any] = list(TELEMETRY_TOOL_FUNCS)
    if include_gitlab:
        from .tools_gitlab import build_gitlab_toolset

        tools.append(build_gitlab_toolset())

    log.info(
        "Building Faultline agent (model=%s, tools=%d, gitlab=%s)",
        model,
        len(tools),
        include_gitlab,
    )

    return LlmAgent(
        name="faultline",
        model=model,
        description=(
            "Autonomous incident root-cause investigator. Reads Google Cloud "
            "telemetry, walks the service dependency graph to find the true "
            "source of a cascade, correlates with recent GitLab commits, and "
            "stages a DRAFT rollback merge request for human approval. Never "
            "merges on its own."
        ),
        instruction=INVESTIGATION_POLICY,
        tools=tools,
    )
