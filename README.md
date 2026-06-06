# Faultline

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Live incident root-cause investigation agent.**

When a production service breaks, Faultline autonomously:

1. Reads Google Cloud telemetry (Logging / Trace / Monitoring) to identify what broke.
2. Walks the service dependency graph to find the **root** cause, not just the first red service.
3. Pulls recent GitLab commits & diffs via the official GitLab MCP server.
4. Matches the failure symptom (latency creep / 5xx spike / OOM) to the diff type and converges on **one** most-likely offending commit.
5. Drafts a blameless postmortem, opens a GitLab issue linking the suspect commit, and stages a **DRAFT** rollback merge request.
6. **Stops.** A human reviews and clicks Approve before anything merges.

Built for the **Google Cloud Rapid Agent Hackathon — GitLab track**.

---

## Architecture

```
+--------------------+        +----------------------+        +------------------+
|  Web console (SSE) | <----- |  FastAPI server      | -----> |  Vertex AI       |
|  web/index.html    |        |  server/main.py      |        |  Gemini + ADK    |
+--------------------+        +----------------------+        +------------------+
                                        |                              |
                                        |                       +------+-------+
                                        |                       | GitLab MCP   |
                                        |                       | toolset      |
                                        |                       +------+-------+
                                        v                              v
                              +-------------------+          +-------------------+
                              |  Cloud Logging /  |          |   GitLab repo     |
                              |  Trace / Monitor  |          |  (victim_service) |
                              +-------------------+          +-------------------+
                                        ^
                                        |  OpenTelemetry
                              +-------------------+
                              |  victim_service   |
                              |  frontend->auth-> |
                              |  data (Cloud Run) |
                              +-------------------+
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

## Setup (work in progress — phase 0 scaffold)

Prereqs: Python 3.11+, `gcloud` CLI, a GCP project with Vertex AI + Cloud Logging/Trace/Monitoring APIs enabled, a GitLab account with a project + personal access token, a GitHub repo to host this code.

```powershell
# 1. clone + venv
git clone <your-fork>.git
cd faultline
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. copy env, fill in values (NEVER commit .env)
copy .env.example .env
# edit .env

# 3. (later phases) auth gcloud + run server
gcloud auth application-default login
uvicorn server.main:app --reload
```

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

## Build phases

- [x] **Phase 0** — scaffold, MIT license, env contract, README skeleton.
- [x] **Phase 1** — victim_service (3-role FastAPI chain, one image), OpenTelemetry, Dockerfile, Cloud Run deploy script, regression toggle.
- [x] **Phase 2** — telemetry read tools (Cloud Logging/Trace/Monitoring) with `FAULTLINE_FAKE_TELEMETRY=1` fixture mode keyed by `FAULTLINE_FAKE_SCENARIO`.
- [x] **Phase 3** — GitLab MCP toolset (`McpToolset` + `StreamableHTTPConnectionParams` on `<gitlab>/api/v4/mcp` with `PRIVATE-TOKEN` header). Tool allowlist limited to `search`, `get_merge_request_commits`, `get_merge_request_diffs`, `get_merge_request`, `create_issue`, `create_merge_request`. Live de-risk via `python -m scripts.gitlab_smoke`.
- [x] **Phase 4** — Gemini ADK `LlmAgent` factory wiring `gemini-2.5-flash` on Vertex AI + `INVESTIGATION_POLICY` as system prompt + telemetry function tools + GitLab MCP toolset.
- [ ] Phase 5 — FastAPI server + SSE step streaming.
- [ ] Phase 6 — web console UI.
- [ ] Phase 7 — human Approve gate -> merge + redeploy.
- [ ] Phase 8 — plant regression, end-to-end demo, tests, architecture diagram.

## Hackathon constraints

| Rule | How Faultline complies |
|---|---|
| Gemini on Vertex AI only at runtime | Single LLM call site in `agent/agent.py` via `google-adk` Vertex AI backend |
| Agent Builder / ADK orchestration | `google-adk` drives all reasoning + tool calls |
| GitLab integration load-bearing | All commit reads + issue/MR writes go via GitLab MCP toolset |
| Google Cloud observability | Cloud Logging / Trace / Monitoring (no Datadog/Elastic/etc.) |
| Original code | No prior repos referenced or copied |
| Public + MIT in first commit | This repo, this LICENSE, committed at phase 0 |

## License

[MIT](LICENSE) © 2026 mohit
