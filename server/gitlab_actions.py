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


async def _resolve_revertable_sha(client: httpx.AsyncClient, project_path: str, sha: str) -> str:
    """Pick the actual merge commit to revert.

    The agent's ``sha`` hint can point at any historical commit on main —
    including ones that have already been reverted in a prior demo run.
    We prefer the **latest merged MR** on the project because in this demo
    that is by definition the regression we just planted. Only if no
    merged MR exists do we fall back to the agent's hint or the default-
    branch HEAD.

    Returns a SHA that is (a) reachable from the default branch and
    (b) the most recent merge into it, so reverting it has the highest
    chance of cleanly undoing the regression that triggered the
    investigation.
    """
    base = _gitlab_base()
    pid = _project_url_id(project_path)
    default = os.getenv("GITLAB_DEFAULT_BRANCH", "main")

    # (1) latest merged MR on project — usually the fresh demo plant.
    latest = await client.get(
        f"{base}/api/v4/projects/{pid}/merge_requests",
        headers=_headers(),
        params={"state": "merged", "order_by": "updated_at", "sort": "desc", "per_page": 1},
    )
    if latest.status_code == 200:
        items = latest.json()
        if items:
            best = items[0]
            merge_sha = best.get("merge_commit_sha")
            if merge_sha:
                log.info(
                    "resolved revert target -> latest merged MR !%s merge_commit_sha %s (agent hinted %s)",
                    best.get("iid"), merge_sha[:8], (sha or "")[:8],
                )
                return merge_sha

    # (2) agent's hint, if it exists and is reachable.
    if sha:
        probe = await client.get(
            f"{base}/api/v4/projects/{pid}/repository/commits/{sha}",
            headers=_headers(),
        )
        if probe.status_code == 200:
            return sha

    # (3) default-branch HEAD.
    head = await client.get(
        f"{base}/api/v4/projects/{pid}/repository/commits",
        headers=_headers(),
        params={"ref_name": default, "per_page": 1},
    )
    head.raise_for_status()
    items = head.json()
    if not items:
        raise RuntimeError(f"no commits on {default}; cannot revert")
    log.warning("falling back to default-branch HEAD %s for revert", items[0]["id"][:8])
    return items[0]["id"]


async def revert_commit_via_rest(project_path: str, sha: str, branch: str) -> dict[str, Any]:
    """Create a revert commit on a new branch using GitLab's REST revert API.

    Used as a fallback when the agent's MCP-based create_merge_request fails
    because the revert source branch does not yet exist. The GitLab API
    `POST /projects/:id/repository/commits/:sha/revert?branch=...` does both
    the branch create AND the revert commit in one call.

    If the supplied ``sha`` is not reachable from the default branch (e.g. it
    is the source-side commit that the merge superseded), we revert the
    actual default-branch HEAD instead, which is the merge commit that
    landed the regression.
    """
    base = _gitlab_base()
    pid = _project_url_id(project_path)
    default = os.getenv("GITLAB_DEFAULT_BRANCH", "main")

    async with httpx.AsyncClient(timeout=20.0) as client:
        revertable = await _resolve_revertable_sha(client, project_path, sha)

        # Step 1: create the revert branch from the default branch tip.
        # If it already exists from a prior attempt, delete + recreate.
        create_branch_url = f"{base}/api/v4/projects/{pid}/repository/branches"
        br_resp = await client.post(
            create_branch_url,
            headers=_headers(),
            params={"branch": branch, "ref": default},
        )
        if br_resp.status_code >= 400 and "already exists" in br_resp.text.lower():
            await client.delete(
                f"{base}/api/v4/projects/{pid}/repository/branches/{branch}",
                headers=_headers(),
            )
            br_resp = await client.post(
                create_branch_url,
                headers=_headers(),
                params={"branch": branch, "ref": default},
            )
        br_resp.raise_for_status()

        # Step 2: revert ``revertable`` ONTO the freshly created branch.
        # When the target is a merge commit we MUST tell GitLab which parent
        # is the mainline; `mainline=1` = the first parent (i.e. the
        # default-branch tip the merge fast-forwarded from). Without this
        # GitLab returns 400 on merge-commit reverts.
        revert_url = f"{base}/api/v4/projects/{pid}/repository/commits/{revertable}/revert"
        resp = await client.post(
            revert_url,
            headers=_headers(),
            json={"branch": branch, "mainline": 1},
        )
        if resp.status_code >= 400 and "mainline" not in resp.text.lower():
            # Retry without `mainline` for non-merge commits.
            resp = await client.post(
                revert_url, headers=_headers(), json={"branch": branch}
            )
        resp.raise_for_status()
        return resp.json()


async def open_draft_mr_via_rest(
    project_path: str,
    *,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    """Create a DRAFT MR via REST when MCP failed.

    Returns the parsed MR payload including iid + web_url.
    """
    base = _gitlab_base()
    pid = _project_url_id(project_path)
    url = f"{base}/api/v4/projects/{pid}/merge_requests"

    if not title.lower().startswith("draft:"):
        title = f"Draft: {title}"

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            url,
            headers=_headers(),
            json={
                "source_branch": source_branch,
                "target_branch": target_branch,
                "title": title,
                "description": description,
            },
        )
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
