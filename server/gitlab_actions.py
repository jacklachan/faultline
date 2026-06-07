"""Direct GitLab REST calls used by /approve.

Faultline deliberately keeps the **merge** action out of the agent's toolset
(see ``agent/tools_gitlab.py``). The community ``@zereight/mcp-gitlab`` MCP
server does ship a ``merge_merge_request`` tool — we just do not register
it. The only path that can merge an MR in this system is ``POST /approve``
from the FastAPI server, which calls the REST API directly. The human
Approve click is the only thing that flips a Draft MR to Ready and merges
it; the agent has no tool wired up to do so.

Two operations:

  mark_mr_ready : flip a Draft MR to Ready (strip the "Draft:" title prefix).
  merge_mr      : accept the MR; GitLab CI then redeploys the victim.

A third helper (``revert_commit_via_rest`` + ``open_draft_mr_via_rest``) is
used by the post-investigation REST fallback in ``server/investigate.py``
when the agent's MCP-side ``create_merge_request`` call cannot create the
Draft rollback MR. See that file for the contract.

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


async def _candidate_revert_shas(
    client: httpx.AsyncClient, project_path: str, sha: str
) -> list[tuple[str, str]]:
    """Return ordered (sha, reason) candidates the revert flow should try.

    Strategy:
      1. If the agent's ``sha`` hint is reachable in the repo AND it is the
         tip of a merged MR, use that MR's ``merge_commit_sha`` — that is
         the agent's actual verdict.
      2. If the hint resolves but isn't in a merged MR (rare), try it
         directly.
      3. Latest merged MR on the project (demo-convenience: usually the
         freshest /demo/plant).
      4. Default-branch HEAD.

    Caller is expected to try them in order, falling through on revert
    conflict / 4xx. We surface the verdict over convenience instead of the
    other way round; convenience only matters when the verdict cannot apply
    (e.g. it has already been reverted, or it was a hallucination).
    """
    base = _gitlab_base()
    pid = _project_url_id(project_path)
    default = os.getenv("GITLAB_DEFAULT_BRANCH", "main")
    out: list[tuple[str, str]] = []

    # (1) + (2): inspect the agent's hint
    if sha:
        mrs_resp = await client.get(
            f"{base}/api/v4/projects/{pid}/repository/commits/{sha}/merge_requests",
            headers=_headers(),
        )
        if mrs_resp.status_code == 200:
            for m in mrs_resp.json():
                if m.get("state") == "merged" and m.get("merge_commit_sha"):
                    out.append((m["merge_commit_sha"], f"agent verdict (MR !{m.get('iid')})"))
                    break
        probe = await client.get(
            f"{base}/api/v4/projects/{pid}/repository/commits/{sha}",
            headers=_headers(),
        )
        if probe.status_code == 200 and not any(s == sha for s, _ in out):
            out.append((sha, "agent verdict (direct)"))

    # (3) latest merged MR — convenience fallback
    latest = await client.get(
        f"{base}/api/v4/projects/{pid}/merge_requests",
        headers=_headers(),
        params={"state": "merged", "order_by": "updated_at", "sort": "desc", "per_page": 1},
    )
    if latest.status_code == 200:
        items = latest.json()
        if items and items[0].get("merge_commit_sha"):
            ms = items[0]["merge_commit_sha"]
            if not any(s == ms for s, _ in out):
                out.append((ms, f"latest merged MR !{items[0].get('iid')}"))

    # (4) default branch HEAD — last resort
    head = await client.get(
        f"{base}/api/v4/projects/{pid}/repository/commits",
        headers=_headers(),
        params={"ref_name": default, "per_page": 1},
    )
    if head.status_code == 200:
        items = head.json()
        if items and not any(s == items[0]["id"] for s, _ in out):
            out.append((items[0]["id"], "default-branch HEAD"))

    return out


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
        candidates = await _candidate_revert_shas(client, project_path, sha)
        if not candidates:
            raise RuntimeError("no revertable commits found")

        create_branch_url = f"{base}/api/v4/projects/{pid}/repository/branches"
        last_error: Exception | None = None

        for cand_sha, reason in candidates:
            log.info(
                "revert attempt: sha=%s reason=%s onto branch=%s",
                cand_sha[:8], reason, branch,
            )

            # (re)create the revert branch from default.
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
            if br_resp.status_code >= 400:
                last_error = RuntimeError(
                    f"create branch failed for {cand_sha[:8]}: {br_resp.text[:200]}"
                )
                continue

            # Revert with mainline=1 (works for merge commits); on a non-merge
            # commit GitLab will reject mainline -> retry without it.
            revert_url = f"{base}/api/v4/projects/{pid}/repository/commits/{cand_sha}/revert"
            applied = False
            for payload in ({"branch": branch, "mainline": 1}, {"branch": branch}):
                resp = await client.post(revert_url, headers=_headers(), json=payload)
                if resp.status_code == 200:
                    result = resp.json()
                    result["_picked_sha"] = cand_sha
                    result["_picked_reason"] = reason
                    return result
                if resp.status_code == 400 and "mainline" in resp.text.lower():
                    continue
                last_error = RuntimeError(
                    f"revert {cand_sha[:8]} ({reason}) -> {resp.status_code}: {resp.text[:200]}"
                )
                break
            if applied:
                break

            # Clean up the branch so the next candidate has a fresh start.
            await client.delete(
                f"{base}/api/v4/projects/{pid}/repository/branches/{branch}",
                headers=_headers(),
            )

        raise last_error or RuntimeError("all revert candidates exhausted")


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
