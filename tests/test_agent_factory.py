"""Tests for the agent factory (no live Vertex AI calls).

We stub google.adk so the factory can be exercised without the full ADK
dependency tree, and assert the LlmAgent was constructed with:

  - the policy as `instruction`
  - the correct default model (or whatever VERTEX_AI_MODEL overrides to)
  - all four telemetry function tools
  - the GitLab toolset when include_gitlab=True

Phase 8 covers the live agent-runs-end-to-end path.
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_adk_stubs() -> dict[str, MagicMock]:
    captured: dict[str, MagicMock] = {}

    # mcp + google.adk.tools.mcp_tool stubs (same as test_gitlab_tools)
    mcp_mod = types.ModuleType("mcp")
    mcp_mod.StdioServerParameters = MagicMock(name="StdioServerParameters")

    g = types.ModuleType("google")
    g_adk = types.ModuleType("google.adk")
    g_adk_agents = types.ModuleType("google.adk.agents")
    g_adk_agents.LlmAgent = MagicMock(name="LlmAgent")
    g_adk_tools = types.ModuleType("google.adk.tools")
    g_adk_tools_mcp = types.ModuleType("google.adk.tools.mcp_tool")
    g_adk_tools_mcp.McpToolset = MagicMock(name="McpToolset")
    g_adk_tools_mcp_mgr = types.ModuleType("google.adk.tools.mcp_tool.mcp_session_manager")
    g_adk_tools_mcp_mgr.StreamableHTTPConnectionParams = MagicMock(
        name="StreamableHTTPConnectionParams"
    )
    g_adk_tools_mcp_mgr.StdioConnectionParams = MagicMock(name="StdioConnectionParams")

    captured["LlmAgent"] = g_adk_agents.LlmAgent
    captured["McpToolset"] = g_adk_tools_mcp.McpToolset
    captured["StreamableHTTPConnectionParams"] = (
        g_adk_tools_mcp_mgr.StreamableHTTPConnectionParams
    )

    sys.modules["mcp"] = mcp_mod
    sys.modules["google"] = g
    sys.modules["google.adk"] = g_adk
    sys.modules["google.adk.agents"] = g_adk_agents
    sys.modules["google.adk.tools"] = g_adk_tools
    sys.modules["google.adk.tools.mcp_tool"] = g_adk_tools_mcp
    sys.modules["google.adk.tools.mcp_tool.mcp_session_manager"] = g_adk_tools_mcp_mgr
    return captured


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "fake-project")
    monkeypatch.setenv("GOOGLE_CLOUD_REGION", "us-central1")
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-fake")
    monkeypatch.delenv("VERTEX_AI_MODEL", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    for name in ("agent.agent", "agent.tools_gitlab"):
        sys.modules.pop(name, None)
    yield


def _fresh():
    return importlib.import_module("agent.agent")


def test_default_model_is_gemini_2_5_flash():
    captured = _install_adk_stubs()
    mod = _fresh()
    mod.build_agent()

    captured["LlmAgent"].assert_called_once()
    kwargs = captured["LlmAgent"].call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash"


def test_model_override_via_env(monkeypatch):
    monkeypatch.setenv("VERTEX_AI_MODEL", "gemini-2.5-pro")
    captured = _install_adk_stubs()
    mod = _fresh()
    mod.build_agent()

    assert captured["LlmAgent"].call_args.kwargs["model"] == "gemini-2.5-pro"


def test_instruction_is_investigation_policy():
    captured = _install_adk_stubs()
    mod = _fresh()
    mod.build_agent()

    kwargs = captured["LlmAgent"].call_args.kwargs
    assert "1. READ THE INCIDENT SIGNAL" in kwargs["instruction"]
    assert "8. STOP." in kwargs["instruction"]


def test_telemetry_tools_attached():
    captured = _install_adk_stubs()
    mod = _fresh()
    mod.build_agent(include_gitlab=False)

    tools = captured["LlmAgent"].call_args.kwargs["tools"]
    # Four telemetry callables, no MCPToolset when include_gitlab=False.
    assert len(tools) == 4
    names = {t.__name__ for t in tools}
    assert names == {
        "query_error_logs",
        "read_metric",
        "fetch_recent_traces",
        "list_dependency_edges",
    }


def test_gitlab_toolset_added_when_enabled():
    captured = _install_adk_stubs()
    mod = _fresh()
    mod.build_agent(include_gitlab=True)

    tools = captured["LlmAgent"].call_args.kwargs["tools"]
    # 4 telemetry + 1 MCPToolset
    assert len(tools) == 5
    captured["McpToolset"].assert_called_once()


def test_vertex_env_flag_set():
    import os
    captured = _install_adk_stubs()
    mod = _fresh()
    mod.build_agent()
    assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "true"
    # REGION is mirrored to LOCATION (google-genai reads LOCATION).
    assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-central1"


def test_missing_project_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    _install_adk_stubs()
    mod = _fresh()
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_PROJECT"):
        mod.build_agent()


def test_missing_region_raises(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_REGION", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    _install_adk_stubs()
    mod = _fresh()
    with pytest.raises(RuntimeError, match="GOOGLE_CLOUD_REGION"):
        mod.build_agent()
