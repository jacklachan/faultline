"""Plant a believable suspect commit on the victim's GitLab repo.

Faultline finds the bug by reading recent GitLab commits to the victim
service. For a convincing demo we need an actual commit in your GitLab
project that flips REGRESSION_MODE on. This script does that, end-to-end:

    1. clones GITLAB_PROJECT_PATH into a temp dir
    2. edits .gitlab-ci.yml to set REGRESSION_MODE=<scenario>
    3. commits + pushes to a new branch
    4. opens a merge request via GitLab REST (just like a human dev would)
    5. immediately merges that MR so the change reaches the default branch

The agent then sees this commit when it walks recent history during step 3
of the investigation policy. After the human clicks Approve, Faultline's
draft rollback MR reverts this same commit.

Usage:
    python -m scripts.plant_regression --scenario n_plus_one

Run AFTER you have deployed victim_service to Cloud Run via
deploy_cloudrun.sh — the deploy reads REGRESSION_MODE from Cloud Run's
env vars, so plant_regression also calls `gcloud run services update`
to flip the env var on the running service. That is what actually causes
the symptoms Faultline sees.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx


def _env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"FAIL: env var {name} is not set", file=sys.stderr)
        sys.exit(2)
    return v


def _gitlab_base() -> str:
    return os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")


def _run(cmd: list[str], cwd: Path) -> None:
    print("$", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(cwd))


def _flip_cloud_run_env(scenario: str) -> None:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    region = os.getenv("GOOGLE_CLOUD_REGION")
    if not project or not region:
        print(
            "skip cloud-run flip: GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_REGION not set; "
            "you must update the data service env yourself."
        )
        return
    cmd = [
        "gcloud",
        "run",
        "services",
        "update",
        "faultline-victim-data",
        f"--region={region}",
        f"--project={project}",
        f"--update-env-vars=REGRESSION_MODE={scenario}",
    ]
    print("$", " ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--scenario",
        choices=("n_plus_one", "slow_query", "bad_dep", "leaky"),
        default="n_plus_one",
    )
    p.add_argument(
        "--no-cloud-run-flip",
        action="store_true",
        help="Skip updating the live Cloud Run env var (Git commit only).",
    )
    args = p.parse_args()

    project = _env("GITLAB_PROJECT_PATH")
    token = _env("GITLAB_TOKEN")
    base = _gitlab_base()
    default_branch = os.getenv("GITLAB_DEFAULT_BRANCH", "main")
    branch = f"plant-{args.scenario}"

    repo_url = base.replace("https://", f"https://oauth2:{token}@") + f"/{project}.git"

    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "repo"
        _run(["git", "clone", "--depth", "1", repo_url, str(wd)], Path(tmp))
        _run(["git", "checkout", "-b", branch], wd)

        ci_path = wd / "victim_service" / ".gitlab-ci.yml"
        if not ci_path.is_file():
            print(f"FAIL: {ci_path} not found in victim repo. Did you push the scaffold?", file=sys.stderr)
            sys.exit(3)
        text = ci_path.read_text(encoding="utf-8")
        if 'REGRESSION_MODE: ""' in text:
            text = text.replace('REGRESSION_MODE: ""', f'REGRESSION_MODE: "{args.scenario}"')
        else:
            print("FAIL: did not find REGRESSION_MODE line to flip in .gitlab-ci.yml", file=sys.stderr)
            sys.exit(4)
        ci_path.write_text(text, encoding="utf-8")

        _run(["git", "add", "victim_service/.gitlab-ci.yml"], wd)
        _run(
            ["git", "commit", "-m", f"perf(data): tune query path for {args.scenario} workload"],
            wd,
        )
        _run(["git", "push", "-u", "origin", branch], wd)

    # Open the MR via GitLab REST so the suspect commit has a real MR record.
    pid = quote(project, safe="")
    headers = {"PRIVATE-TOKEN": token, "accept": "application/json"}
    mr_payload = {
        "source_branch": branch,
        "target_branch": default_branch,
        "title": f"perf(data): tune query path for {args.scenario} workload",
        "description": "Planted by scripts/plant_regression.py for the Faultline demo.",
    }
    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            f"{base}/api/v4/projects/{pid}/merge_requests",
            headers=headers,
            json=mr_payload,
        )
        r.raise_for_status()
        mr = r.json()
        iid = mr["iid"]
        print(f"opened MR !{iid} -> {mr['web_url']}")

        merge = client.put(
            f"{base}/api/v4/projects/{pid}/merge_requests/{iid}/merge",
            headers=headers,
            json={"should_remove_source_branch": True},
        )
        merge.raise_for_status()
        print(f"merged MR !{iid}, sha={merge.json().get('merge_commit_sha')!r}")

    if args.no_cloud_run_flip:
        print("skip cloud-run flip (--no-cloud-run-flip).")
    else:
        _flip_cloud_run_env(args.scenario)

    print(f"\nDone. Drive traffic, wait ~2 minutes, then start an investigation on faultline-victim-frontend.")


if __name__ == "__main__":
    main()
