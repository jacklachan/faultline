"""GitLab MCP toolset.

The hackathon track requires the GitLab integration to be load-bearing through
an MCP server. We use the community **@zereight/mcp-gitlab** server (`npx -y
@zereight/mcp-gitlab`) which speaks MCP over stdio and wraps the GitLab REST
API. We picked this over the official `<gitlab>/api/v4/mcp` endpoint because
the official server requires GitLab Ultimate on a group namespace, which is
not available on free-tier personal namespaces — and the demo runs on a
personal gitlab.com project.

The server is launched as a child process per session via ADK's
``StdioConnectionParams`` + ``StdioServerParameters``. Auth is via the env vars
the server reads (``GITLAB_PERSONAL_ACCESS_TOKEN``, ``GITLAB_API_URL``) which
we copy from our ``GITLAB_TOKEN`` / ``GITLAB_URL`` settings.

Tool allowlist
--------------
Only the tools the 8-step investigation policy actually uses are exposed to
the agent. The merge action is **deliberately not** in this list — the human
Approve gate is the only path that merges, and it does so via REST in
``server/gitlab_actions.py``. The agent literally cannot pick a merge tool
because there isn't one registered on its toolset.

    list_commits              - step 3 (recent commits on the suspect service)
    get_merge_request         - MR metadata
    get_merge_request_diffs   - step 4 (read the suspect diff)
    list_merge_requests       - find recent merges
    create_issue              - step 7b (postmortem)
    create_merge_request      - step 7c (DRAFT rollback)
"""

from __future__ import annotations

import logging
import os
from typing import Any


log = logging.getLogger(__name__)


GITLAB_TOOL_ALLOWLIST: tuple[str, ...] = (
    "list_commits",
    "get_merge_request",
    "get_merge_request_diffs",
    "list_merge_requests",
    "create_issue",
    "create_merge_request",
)


def gitlab_api_url() -> str:
    base = os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")
    return f"{base}/api/v4"


def _require_token() -> str:
    token = os.getenv("GITLAB_TOKEN")
    if not token:
        raise RuntimeError(
            "GITLAB_TOKEN is not set. Put your GitLab personal access token "
            "in .env — never paste it in chat. Required scopes: api, "
            "read_repository, write_repository."
        )
    return token


def _stdio_params() -> Any:
    """Build StdioServerParameters launching `npx -y @zereight/mcp-gitlab`."""
    from mcp import StdioServerParameters

    token = _require_token()
    env = {
        **os.environ,
        "GITLAB_PERSONAL_ACCESS_TOKEN": token,
        "GITLAB_API_URL": gitlab_api_url(),
        "GITLAB_READ_ONLY_MODE": "false",
    }
    # On Windows, npx must be invoked via cmd.exe so Node's shim resolves.
    if os.name == "nt":
        return StdioServerParameters(
            command="cmd",
            args=["/c", "npx", "-y", "@zereight/mcp-gitlab"],
            env=env,
        )
    return StdioServerParameters(
        command="npx",
        args=["-y", "@zereight/mcp-gitlab"],
        env=env,
    )


def build_gitlab_toolset() -> Any:
    """Return a configured ``McpToolset`` bound to the GitLab MCP server.

    Phase 4 plugs the return value into ``LlmAgent(tools=[...])`` alongside
    the telemetry function tools.
    """
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams

    conn = StdioConnectionParams(server_params=_stdio_params(), timeout=30.0)

    log.info(
        "GitLab MCP toolset configured (community @zereight/mcp-gitlab, api=%s, tools=%s)",
        gitlab_api_url(),
        ", ".join(GITLAB_TOOL_ALLOWLIST),
    )
    return McpToolset(
        connection_params=conn,
        tool_filter=list(GITLAB_TOOL_ALLOWLIST),
    )
