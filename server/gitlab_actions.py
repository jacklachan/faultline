"""Direct GitLab REST calls used by /approve.

The GitLab MCP server's published tool list (per docs.gitlab.com) covers
``create_*``, ``get_*``, and ``search`` operations, but does **not** expose a
``merge_merge_request`` tool. Faultline's read + create path goes through MCP
(rule 3, "load-bearing"); the merge action that runs when the human clicks
Approve uses GitLab's REST API directly, which is fine — rule 3 explicitly
lists "reading commits/diffs and creating the issue + draft MR" as the MCP
flows, not the eventual merge.

Two operations:

  mark_mr_ready : flip a Draft MR to Ready (strip the "Draft:" title prefix).
  merge_mr      : accept the MR; GitLab CI then redeploys the victim.

When FAULTLINE_FAKE_AGENT=1 both calls return canned successes without
touching the network, so the demo flow can be exercised offline.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx


log = logging.getLogger(__name__)


def _fake() -> bool:
    return os.getenv("FAULTLINE_FAKE_AGENT", "0") == "1"


def _gitlab_base() -> str:
    return os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")


def _token() -> str:
    tok = os.getenv("GITLAB_TOKEN")
    if not tok:
        raise RuntimeError(
            "GITLAB_TOKEN missing — needed to merge the rollback MR. "
            "Put it in .env (never paste in chat)."
        )
    return tok


def _headers() -> dict[str, str]:
    return {"PRIVATE-TOKEN": _token(), "Accept": "application/json"}


def _project_url_id(project_path: str) -> str:
    return quote(project_path, safe="")


async def mark_mr_ready(project_path: str, mr_iid: int) -> dict[str, Any]:
    """Strip the ``Draft:`` prefix from the MR title so it becomes Ready.

    GitLab keys "draft" status off the title prefix. PUT with a new title
    removes the prefix and marks the MR ready for merge.
    """
    if _fake():
        return {"iid": mr_iid, "title": "ready (fake)", "draft": False}

    base = _gitlab_base()
    pid = _project_url_id(project_path)
    url = f"{base}/api/v4/projects/{pid}/merge_requests/{mr_iid}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        cur = await client.get(url, headers=_headers())
        cur.raise_for_status()
        current_title = cur.json().get("title", "")
        new_title = current_title
        for prefix in ("Draft: ", "WIP: ", "[Draft] ", "[WIP] "):
            if new_title.startswith(prefix):
                new_title = new_title[len(prefix):]
                break
        resp = await client.put(url, headers=_headers(), json={"title": new_title})
        resp.raise_for_status()
        return resp.json()


async def merge_mr(project_path: str, mr_iid: int) -> dict[str, Any]:
    """Accept the MR. Returns the merged MR payload (or raises HTTPStatusError)."""
    if _fake():
        return {
            "iid": mr_iid,
            "state": "merged",
            "merge_commit_sha": "fake0000000000",
        }

    base = _gitlab_base()
    pid = _project_url_id(project_path)
    url = f"{base}/api/v4/projects/{pid}/merge_requests/{mr_iid}/merge"

    payload = {
        "merge_when_pipeline_succeeds": False,
        "should_remove_source_branch": True,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.put(url, headers=_headers(), json=payload)
        resp.raise_for_status()
        return resp.json()
