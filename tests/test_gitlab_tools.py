"""Tests for tools_gitlab.py (no live GitLab calls).

These verify the toolset wiring — API URL, env-var injection into the child
MCP process, and the tool allowlist — by patching the ADK MCP classes. The
actual MCP round-trip is exercised by scripts/gitlab_smoke.py against a real
GitLab project.
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from unittest.mock import MagicMock

import pytest


def _install_stubs() -> dict[str, MagicMock]:
    """Install minimal google.adk + mcp stubs. Return the mock objects."""
    captured: dict[str, MagicMock] = {}

    # mcp.StdioServerParameters
    mcp_mod = types.ModuleType("mcp")
    mcp_mod.StdioServerParameters = MagicMock(name="StdioServerParameters")
    captured["StdioServerParameters"] = mcp_mod.StdioServerParameters

    # google.adk.tools.mcp_tool.mcp_toolset.McpToolset
    # google.adk.tools.mcp_tool.mcp_session_manager.StdioConnectionParams
    g = types.ModuleType("google")
    g_adk = types.ModuleType("google.adk")
    g_adk_tools = types.ModuleType("google.adk.tools")
    g_adk_tools_mcp = types.ModuleType("google.adk.tools.mcp_tool")
    g_adk_tools_mcp_toolset = types.ModuleType("google.adk.tools.mcp_tool.mcp_toolset")
    g_adk_tools_mcp_mgr = types.ModuleType(
        "google.adk.tools.mcp_tool.mcp_session_manager"
    )
    g_adk_tools_mcp_toolset.McpToolset = MagicMock(name="McpToolset")
    g_adk_tools_mcp_mgr.StdioConnectionParams = MagicMock(name="StdioConnectionParams")
    captured["StdioConnectionParams"] = g_adk_tools_mcp_mgr.StdioConnectionParams
    captured["McpToolset"] = g_adk_tools_mcp_toolset.McpToolset

    sys.modules["mcp"] = mcp_mod
    sys.modules["google"] = g
    sys.modules["google.adk"] = g_adk
    sys.modules["google.adk.tools"] = g_adk_tools
    sys.modules["google.adk.tools.mcp_tool"] = g_adk_tools_mcp
    sys.modules["google.adk.tools.mcp_tool.mcp_toolset"] = g_adk_tools_mcp_toolset
    sys.modules["google.adk.tools.mcp_tool.mcp_session_manager"] = g_adk_tools_mcp_mgr
    return captured


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test-fake")
    monkeypatch.delenv("GITLAB_URL", raising=False)
    sys.modules.pop("agent.tools_gitlab", None)
    yield


def _fresh_import():
    return importlib.import_module("agent.tools_gitlab")


def test_api_url_default():
    _install_stubs()
    mod = _fresh_import()
    assert mod.gitlab_api_url() == "https://gitlab.com/api/v4"


def test_api_url_respects_custom_gitlab_url(monkeypatch):
    monkeypatch.setenv("GITLAB_URL", "https://gitlab.example.com/")
    _install_stubs()
    mod = _fresh_import()
    assert mod.gitlab_api_url() == "https://gitlab.example.com/api/v4"


def test_missing_token_raises(monkeypatch):
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    _install_stubs()
    mod = _fresh_import()
    with pytest.raises(RuntimeError, match="GITLAB_TOKEN"):
        mod.build_gitlab_toolset()


def test_stdio_launches_zereight_via_npx():
    captured = _install_stubs()
    mod = _fresh_import()
    mod.build_gitlab_toolset()

    captured["StdioServerParameters"].assert_called_once()
    kwargs = captured["StdioServerParameters"].call_args.kwargs
    # Windows path goes through cmd /c; posix uses npx directly.
    if os.name == "nt":
        assert kwargs["command"] == "cmd"
        assert kwargs["args"][:3] == ["/c", "npx", "-y"]
        assert "@zereight/mcp-gitlab" in kwargs["args"]
    else:
        assert kwargs["command"] == "npx"
        assert kwargs["args"] == ["-y", "@zereight/mcp-gitlab"]


def test_stdio_env_carries_token_and_api_url():
    captured = _install_stubs()
    mod = _fresh_import()
    mod.build_gitlab_toolset()

    env = captured["StdioServerParameters"].call_args.kwargs["env"]
    assert env["GITLAB_PERSONAL_ACCESS_TOKEN"] == "glpat-test-fake"
    assert env["GITLAB_API_URL"] == "https://gitlab.com/api/v4"
    assert env["GITLAB_READ_ONLY_MODE"] == "false"


def test_tool_filter_is_allowlist():
    captured = _install_stubs()
    mod = _fresh_import()
    mod.build_gitlab_toolset()

    kwargs = captured["McpToolset"].call_args.kwargs
    assert kwargs["tool_filter"] == list(mod.GITLAB_TOOL_ALLOWLIST)
    for required in (
        "list_commits",
        "create_issue",
        "create_merge_request",
        "get_merge_request_diffs",
    ):
        assert required in kwargs["tool_filter"]
    # Hard architectural constraint: no merge tool is registered. /approve
    # routes the merge through REST instead.
    assert "merge_merge_request" not in kwargs["tool_filter"]


def test_toolset_uses_stdio_connection_params():
    captured = _install_stubs()
    mod = _fresh_import()
    mod.build_gitlab_toolset()

    captured["StdioConnectionParams"].assert_called_once()
    captured["McpToolset"].assert_called_once()
