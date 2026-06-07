# Faultline

[![tests](https://github.com/jacklachan/faultline/actions/workflows/tests.yml/badge.svg)](https://github.com/jacklachan/faultline/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Gemini 3.1 Pro](https://img.shields.io/badge/LLM-Gemini%203.1%20Pro%20%E2%80%A2%20Vertex%20AI-4285F4)](https://cloud.google.com/vertex-ai)
[![Google ADK](https://img.shields.io/badge/Orchestration-Google%20ADK-34A853)](https://github.com/google/adk-python)
[![GitLab MCP](https://img.shields.io/badge/Integration-GitLab%20MCP-FC6D26)](https://github.com/zereight/gitlab-mcp)

> **Production just broke. On-call wastes 10 minutes finding which service is at fault and which commit caused it. Faultline does it in seconds — autonomous Gemini agent, GitLab MCP load-bearing, human-gated rollback.**

Submitted to the **Google Cloud Rapid Agent Hackathon — GitLab track**.
**Live demo:** https://faultline-1083927168045.us-central1.run.app — click the green **⚡ Plant + investigate** button. Full walkthrough in [SUBMISSION.md](SUBMISSION.md) and [DEMO.md](DEMO.md).

---

## What it does

When a production service breaks, Faultline autonomously:

1. Reads Google Cloud telemetry (Logging / Trace / Monitoring) to identify what broke.
2. Walks the service dependency graph to find the **root** cause, not just the first red service.
3. Pulls recent GitLab commits + diffs via the **official GitLab MCP server**.
4. Matches the failure symptom (latency creep / 5xx spike / OOM) to the diff *type* and converges on **one** most-likely offending commit.
5. Drafts a blameless postmortem, opens a GitLab issue linking the suspect commit, and stages a **DRAFT** rollback merge request.
6. **Stops.** A human reviews and clicks Approve before anything merges. Merge fires the victim's GitLab CI, which redeploys the reverted code to Cloud Run. Recovery in under a minute.

---

## Architecture

```
   ┌─────────────────────┐        ┌──────────────────────────┐
   │  Web console (SSE)  │ <───── │  FastAPI server          │
   │  web/index.html     │ ─────> │  POST /investigate (SSE) │
   │                     │        │  GET  /pending           │
   │  Approve button ────┼──────> │  POST /approve/{id}      │
   └─────────────────────┘        └────────────┬─────────────┘
                                               │
                                  ┌────────────┴─────────────┐
                                  │   ADK LlmAgent           │
                                  │   gemini-2.5-flash       │
                                  │   8-step INVESTIGATION   │
                                  │   _POLICY system prompt  │
                                  └────────────┬─────────────┘
                                               │
                ┌──────────────────────────────┼──────────────────────────────┐
                │                              │                              │
                ▼                              ▼                              ▼
    ┌──────────────────────┐    ┌───────────────────────────┐    ┌──────────────────────────┐
    │ telemetry tools      │    │ GitLab McpToolset         │    │ /approve action (REST)   │
    │ (function tools)     │    │ stdio child process       │    │ PUT mr (title strip)     │
    │   query_error_logs   │    │ npx @zereight/mcp-gitlab  │    │ PUT mr/merge             │
    │   read_metric        │    │   search                  │    └────────────┬─────────────┘
    │   fetch_traces       │    │   get_mr_diffs            │                 │
    │   list_dep_edges     │    │   create_issue            │                 │
    └──────────┬───────────┘    │   create_merge_request    │                 │
               │                └─────────────┬─────────────┘                 │
               ▼                              ▼                               ▼
    ┌────────────────────┐         ┌──────────────────────┐        ┌────────────────────┐
    │ Cloud Logging /    │         │ GitLab project       │ <──────│ MR merged →        │
    │ Trace / Monitoring │         │   victim_service     │        │ .gitlab-ci.yml     │
    └──────────┬─────────┘         │   on gitlab.com      │        │ redeploys victim   │
               ▲                   └──────────────────────┘        └─────────┬──────────┘
               │ OpenTelemetry                                                │
               │                                                              ▼
    ┌──────────┴────────────────────────────────────────────────────────────────────┐
    │  victim_service on Cloud Run  (one image, three roles via SERVICE_NAME env)   │
    │      frontend  ──HTTP──>  auth                                                 │
    │      frontend  ──HTTP──>  data  ──>  items_query  (clean | n_plus_one)         │
    │                                                                                │
    │  REGRESSION_MODE env var selects the symptom class: n_plus_one | slow_query |  │
    │  bad_dep | leaky. Flipping it on data is what triggers the incident Faultline  │
    │  investigates.                                                                 │
    └────────────────────────────────────────────────────────────────────────────────┘
```

The agent's reasoning runtime is **Gemini on Vertex AI** (mandated by hackathon rules). Orchestration is **Google ADK** (Agent Development Kit). The GitLab integration is **load-bearing** — all commit/diff reads and all issue/MR creation flow through the official GitLab MCP server.

## Repo layout

```
agent/             ADK agent, system prompt (investigation policy), tools
server/            FastAPI app wrapping the agent + SSE streaming endpoint
web/               Streaming investigation console (vanilla HTML/JS)
victim_service/    Demo 3-service chain (frontend -> auth -> data), Dockerfile, GitLab CI
tests/             pytest suite
.env.example       Documented env-var contract
requirements.txt   Python deps
LICENSE            MIT
```

## Quickstart

Prereqs: Python 3.11+, `gcloud` CLI, a GCP project with Vertex AI + Cloud Logging/Trace/Monitoring APIs enabled, a GitLab account with a project + personal access token.

```powershell
# 1. clone + venv
git clone https://github.com/jacklachan/faultline.git
cd faultline
cd faultline
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. copy env, fill in values (NEVER commit .env)
Copy-Item .env.example .env
notepad .env   # GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_REGION, GITLAB_PROJECT_PATH, GITLAB_TOKEN

# 3. auth gcloud
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com run.googleapis.com `
  cloudbuild.googleapis.com artifactregistry.googleapis.com `
  logging.googleapis.com cloudtrace.googleapis.com monitoring.googleapis.com

# 4. run locally (full mode)
uvicorn server.main:app --reload

# OR run locally with everything faked (no GCP / GitLab needed)
$env:FAULTLINE_FAKE_AGENT='1'; $env:FAULTLINE_FAKE_TELEMETRY='1'
uvicorn server.main:app --reload
```

### Deploy to Cloud Run

```bash
# from git-bash / WSL
bash victim_service/deploy_cloudrun.sh    # the demo victim
bash deploy_server_cloudrun.sh            # Faultline itself
```

### Run the demo

```bash
python -m scripts.plant_regression --scenario n_plus_one
python -m victim_service.load_gen --url <frontend-url> --rps 5 --duration 180
# Now open the Faultline URL in a browser, click Start, then Approve.
```

See [DEMO.md](DEMO.md) for the full timed walkthrough.

## Deploying the victim service

Phase 1 ships a 3-stage chain (`frontend` → `auth` → `data`) packaged as a
single Docker image. The `SERVICE_NAME` env var picks which role runs, so the
same image becomes three distinct Cloud Run services with their own URLs and
their own `service.name` in Cloud Trace.

```bash
# from git-bash / WSL on Windows
export GOOGLE_CLOUD_PROJECT=advance-casing-498313-t1
export GOOGLE_CLOUD_REGION=us-central1
bash victim_service/deploy_cloudrun.sh
```

Trigger a bug class at deploy time by setting `REGRESSION_MODE` to one of:

| value         | symptom class            | what it does                      |
|---------------|--------------------------|-----------------------------------|
| `n_plus_one`  | latency creep            | 25 sequential 8ms "queries"       |
| `slow_query`  | latency creep            | one 600ms blocking call           |
| `bad_dep`     | sudden 5xx spike         | raises on every request           |
| `leaky`       | memory growth / OOM      | retains 1 MiB per request         |
| *(unset)*     | clean                    | normal behaviour                  |

In a real Faultline run, the suspect commit is the GitLab commit that flips
this env var (or imports a new dep that triggers a path here). Faultline's
job is to read the telemetry, identify the symptom class, read the recent
diffs, and converge on the commit that changed `REGRESSION_MODE`.

Drive traffic from your laptop with:

```bash
python -m victim_service.load_gen --url https://faultline-victim-frontend-xxxxx.run.app/ --rps 5 --duration 120
```

## Investigation policy

The agent's behaviour is governed by a fixed 8-step policy baked verbatim into the system prompt at [agent/prompt.py](agent/prompt.py). It does **not** improvise. It always halts before merging.

## Demo runbook

End-to-end loop in under a minute. Steps run from the repo root unless noted.

### 0. one-time setup

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env   # fill in GOOGLE_CLOUD_PROJECT, GITLAB_PROJECT_PATH, GITLAB_TOKEN
gcloud auth application-default login
gcloud services enable aiplatform.googleapis.com run.googleapis.com `
  cloudbuild.googleapis.com artifactregistry.googleapis.com `
  logging.googleapis.com cloudtrace.googleapis.com monitoring.googleapis.com
```

### 1. deploy the victim (clean)

```bash
bash victim_service/deploy_cloudrun.sh
```

Note the three Cloud Run URLs the script prints. Put the frontend URL in `.env` as `VICTIM_SERVICE_URL`.

### 2. de-risk the GitLab MCP wiring

```bash
python -m scripts.gitlab_smoke
```

Confirms `search`, `get_merge_request_diffs`, `create_issue`, and `create_merge_request` all work against your project. Creates a `[faultline-smoke]` issue + DRAFT MR you can close.

### 3. start the Faultline server

```bash
uvicorn server.main:app --host 0.0.0.0 --port 8080
```

Open `http://localhost:8080` — you should see the console.

### 4. plant the regression

In another shell:

```bash
python -m scripts.plant_regression --scenario n_plus_one
```

This commits + merges `perf(data): tune query path for n_plus_one workload` to your victim repo, then `gcloud run services update`s the data service with `REGRESSION_MODE=n_plus_one`. Latency on `data` starts climbing immediately.

### 5. drive traffic

```bash
python -m victim_service.load_gen --url <frontend Cloud Run URL>/ --rps 5 --duration 180
```

### 6. investigate

In the web console (or via `curl -N -X POST -d '{"service":"faultline-victim-frontend"}'`), kick off an investigation. Watch the agent:

* read error rate + latency metrics on `frontend`,
* walk the dep graph and switch focus to `data`,
* find your `perf(data): tune query path...` commit,
* explain why the diff fits the latency-creep symptom,
* open a postmortem issue + DRAFT rollback MR,
* stop and surface the Approve button.

### 7. approve

Click **Approve rollback** in the console. Faultline strips the `Draft:` prefix, merges the MR, and the victim's `.gitlab-ci.yml` redeploys the reverted code to Cloud Run. The load generator's error rate / p95 should drop back to baseline within ~1 minute.

### Troubleshooting

| symptom                                  | likely fix                                                    |
|------------------------------------------|---------------------------------------------------------------|
| `GOOGLE_CLOUD_PROJECT is not set`        | edit `.env`, restart the server                               |
| Stream stops immediately, error event    | `gcloud auth application-default login` not run               |
| `gitlab_smoke` fails on `create_issue`   | token missing `api` scope, or wrong `GITLAB_PROJECT_PATH`     |
| `/approve` returns 502 "pipeline must succeed" | open the merge-request page, fix the CI job, retry      |
| Demo-only, no GCP / GitLab access        | set `FAULTLINE_FAKE_AGENT=1` and `FAULTLINE_FAKE_TELEMETRY=1` |

## Build phases

- [x] **Phase 0** — scaffold, MIT license, env contract, README skeleton.
- [x] **Phase 1** — victim_service (3-role FastAPI chain, one image), OpenTelemetry, Dockerfile, Cloud Run deploy script, regression toggle.
- [x] **Phase 2** — telemetry read tools (Cloud Logging/Trace/Monitoring) with `FAULTLINE_FAKE_TELEMETRY=1` fixture mode keyed by `FAULTLINE_FAKE_SCENARIO`.
- [x] **Phase 3** — GitLab MCP toolset (`McpToolset` + `StdioConnectionParams` launching `npx -y @zereight/mcp-gitlab`). Tool allowlist: `list_commits`, `get_merge_request`, `get_merge_request_diffs`, `list_merge_requests`, `create_issue`, `create_merge_request`. **No merge tool is registered** — the human Approve gate uses REST. Live de-risk via `python -m scripts.gitlab_smoke`. (GitLab's first-party MCP server is Ultimate-tier; community `@zereight/mcp-gitlab` works on free-tier projects.)
- [x] **Phase 4** — Gemini ADK `LlmAgent` factory wiring `gemini-3.1-pro-preview` on Vertex AI + `INVESTIGATION_POLICY` as system prompt + telemetry function tools + GitLab MCP toolset.
- [x] **Phase 5** — FastAPI server: `POST /investigate` (SSE), `GET /pending`, `POST /approve/{rb}` stub, in-memory rollback registry. `FAULTLINE_FAKE_AGENT=1` switches to a canned step sequence for offline UI dev.
- [x] **Phase 6** — web console: form-driven incident setup, live SSE stream renders one card per event type, rollback card with deep links to issue + draft MR and an Approve button. Vanilla HTML/JS/CSS, no build step.
- [x] **Phase 7** — `POST /approve/{rollback_id}` strips the `Draft:` title prefix and merges the MR via GitLab REST (`PUT /merge_requests/:iid` + `PUT /merge_requests/:iid/merge`). Merge fires the victim's `.gitlab-ci.yml`, which redeploys to Cloud Run. Failure path writes the error back into the registry as `status=failed`.
- [x] **Phase 8** — `items_query.py` carries the real N+1 vs batched code paths so the suspect commit is a believable refactor diff. `scripts/plant_regression.py` automates the "land the bug" step on the victim GitLab repo. End-to-end fake test covers all four scenarios → staged rollback → approved → merged. Architecture diagram + demo runbook below.

## Hackathon constraints

| Rule | How Faultline complies |
|---|---|
| Gemini 3 on Vertex AI only at runtime | Single LLM call site in `agent/agent.py`, `model=gemini-3.1-pro-preview`, via `google-adk` Vertex AI backend |
| Agent Builder / ADK orchestration | `google-adk` drives all reasoning + tool calls |
| GitLab integration load-bearing | All commit reads + issue/MR writes go via GitLab MCP toolset |
| Google Cloud observability | Cloud Logging / Trace / Monitoring (no Datadog/Elastic/etc.) |
| Original code | No prior repos referenced or copied |
| Public + MIT in first commit | This repo, this LICENSE, committed at phase 0 |

## License

[MIT](LICENSE) © 2026 mohit
