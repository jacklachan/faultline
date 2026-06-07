# Devpost submission — field-by-field

Paste these into the matching fields on devpost.com.

---

## Project name

```
Faultline
```

## Tagline (max 200 chars; aim for under 130)

```
Autonomous incident root-cause investigator: ranks suspect GitLab commits by causal fit, not recency. Gemini 3.1 Pro + Google ADK + GitLab MCP. Human-gated rollback.
```

## Track

```
GitLab
```

## Categories / tags

```
Agent Builder, Gemini 3, MCP, GitLab, Vertex AI, Cloud Run, Cloud Monitoring, OpenTelemetry, FastAPI, Incident Response, SRE
```

---

## Inspiration

```
Every team's incident playbook starts the same way: which service is actually broken, which commit caused it, where is the rollback. Senior SREs do that triage in their head in under a minute. Junior engineers and new hires need ten. That ten-minute gap is mechanical — read dashboards, scroll commits, match a symptom to a diff — exactly the work an agent should do.

The interesting question is not "can an LLM read GitLab and Cloud Monitoring." It can. The question is "can it pick the right commit." Most auto-RCA tools rank suspect commits by recency or by fuzzy embedding similarity. That gives you a noisy list. Faultline started from the observation that human SREs do not rank by recency — they rank by causal fit between the symptom class and the change class. Latency creep matches a query-loop change, not a typo fix. A 5xx spike matches a new dependency, not a docs PR. We wanted to bake that reasoning step into a Gemini agent.
```

## What it does

```
Faultline is an autonomous Gemini agent that runs an eight-step incident investigation policy end-to-end, gated by a human Approve click.

1. Reads Google Cloud telemetry — Cloud Monitoring, Cloud Logging — to identify the alerting service and symptom class.
2. Walks the service dependency graph to find the real source of the cascade, not the first red alert.
3. Pulls recent commits + merge requests on the suspect service via the GitLab MCP server.
4. Reads the suspect diffs through GitLab MCP.
5. Matches symptom class to change class — the key reasoning step. Latency creep -> added query / N+1 / sync call. 5xx spike -> new dependency or auth change. OOM / crashloop -> pool or allocation change. Ranks candidate commits by causal fit, not by recency.
6. Converges on one most-likely offending commit with explicit confidence and a stated causal chain.
7. Drafts a blameless postmortem, opens a GitLab issue linking the suspect commit, and stages a DRAFT rollback merge request — all through the GitLab MCP server.
8. Stops. Surfaces everything to the human. Awaits explicit Approve.

When the human clicks Approve, the FastAPI server (not the agent) strips the Draft prefix and merges the MR via GitLab REST. The merge fires the victim's GitLab CI and the service recovers.

The agent literally cannot merge: the merge tool is not in the registered toolset. The Approve click is the only path to a merge.
```

## How we built it

```
Reasoning runtime: Gemini 3.1 Pro (preview) on Vertex AI's `global` endpoint, called via Google ADK's LlmAgent. Temperature 0 for deterministic investigations.

Orchestration: Google ADK (`google-adk`). LlmAgent + Runner + InMemorySessionService. The eight-step investigation policy is baked verbatim into the system prompt at agent/prompt.py — not improvised at runtime.

Telemetry: Cloud Monitoring + Cloud Logging via the google-cloud SDK, registered as ADK function tools. The victim service exports OpenTelemetry traces to Cloud Trace.

GitLab integration (load-bearing): community `@zereight/mcp-gitlab` MCP server, launched as a stdio child process by ADK's McpToolset(connection_params=StdioConnectionParams(...)). Six tools registered: list_commits, list_merge_requests, get_merge_request, get_merge_request_diffs, create_issue, create_merge_request. No merge tool is registered — that is a deliberate architectural constraint on the agent.

Server: FastAPI with Server-Sent Events. GET /investigate streams every step the agent takes live to the browser. POST /demo/plant creates a real regression commit on GitLab and flips the live victim's regression-mode env on Cloud Run. POST /approve flips the Draft MR to Ready and merges via GitLab REST.

Victim service: a three-role FastAPI chain (frontend -> auth -> data) sharing one Docker image. SERVICE_NAME env var picks the role. Real OpenTelemetry trace propagation between the three Cloud Run services.

Frontend: vanilla HTML/JS/CSS. EventSource over GET (not POST/fetch) because Chrome buffers POST-SSE responses. Policy-step badges on each tool_call card. Live elapsed timer.

Deploy: Cloud Build + Cloud Run for both the Faultline server and all three victim services. Artifact Registry for images. GitHub Actions runs the pytest suite on every push.
```

## Challenges we ran into

```
1. Gemini 3 model access. Every Gemini 3 model id returned 404 from the regional Vertex endpoint (us-central1) even though the model card rendered fine in the Vertex AI Model Garden. After probing eight model ids across four regions, the actual fix was setting GOOGLE_CLOUD_LOCATION=global only when the model id starts with "gemini-3" — Gemini 3 family models live exclusively on the global Vertex endpoint, regional endpoints don't serve them.

2. The agent's MCP create_merge_request kept failing because the zereight MCP server requires the source branch to exist BEFORE the MR can be opened, and it ships no create_branch tool. The pragmatic fix was to register the four read tools + create_issue + create_merge_request on the agent's toolset and treat the create_merge_request failure as an expected fallback signal: when MCP fails, the FastAPI server takes over and creates the revert branch + revert commit + Draft MR via GitLab REST. The agent's step 7 always completes, even when MCP can't.

3. Gemini 3.1 Pro at temperature 0 turned out to be aggressive about declaring "investigation complete" after step 2 if Cloud Monitoring metrics were still propagating from the just-flipped Cloud Run env. Fixed by pre-fetching the three most recent merged MRs on the GitLab project in /investigate and injecting them into the incident message so the agent literally cannot claim "no recent changes" — the changes are in the prompt.

4. Browser SSE buffering. Chrome buffered POST-SSE responses under ~4KB until the connection closed; small JSON events piled silently. Switched the wire from POST+fetch+ReadableStream to GET + native EventSource and added an X-Accel-Buffering: no header to defeat Envoy buffering.

5. GitLab's revert endpoint returns 201, not 200, on success. A 200-only success check threw the perfectly-good revert response away as a 4xx and the rollback never showed up in the UI.
```

## Accomplishments we're proud of

```
- The symptom-class -> change-type reasoning step actually works on a real Gemini 3.1 Pro live run, not just in fake-mode tests. The agent reads metrics + walks the dep graph + finds the planted commit + names it by causal fit, not by recency.
- The "agent cannot merge" guarantee is enforced at the tool-schema level, not by prompt rules. Even an LLM hallucination cannot create a merge tool call because there is no such tool registered on the toolset.
- Full end-to-end loop on the public live URL with no off-camera setup: one click plants a real GitLab commit, real Cloud Monitoring sees real traffic, the agent finds the bug on real Gemini 3, Approve merges a real MR.
- 46 unit + integration tests, GitHub Actions green on every push.
- Honest engineering trail in 30+ commits with a real reviewer-loop forcing the project from "looks right" to "actually right" (e.g., flushing out the gemini-3 regional vs global endpoint bug).
```

## What we learned

```
- "Load-bearing MCP" needs an explicit honesty paragraph. The agent's MCP create_merge_request call genuinely cannot complete in this project's GitLab without a pre-existing branch — that's a tool-surface gap in zereight, not a Faultline bug. The right answer is to be clear that MCP carries all the read tools + create_issue + the create_merge_request attempt, and the REST fallback exists for the step-7 last mile.
- Gemini 3.1 Pro is genuinely a different beast from 2.5 Flash for procedural tasks. Lower temperature is required for deterministic agent loops, and you need to inject ground-truth context into the prompt rather than trust the model to ask for it.
- Vertex AI Gemini 3 models live on the `global` endpoint, full stop. Documentation buries this; production teams will rediscover it. Worth a callout in any agentic Vertex sample.
- The single most expensive class of bug in agent engineering is "code is right, deployed config is wrong" — the deploy script was pinning VERTEX_AI_MODEL to gemini-2.5-flash via a default in the script itself, so the static HTML correctly said "Gemini 3" while the runtime was silently calling 2.5. Acceptance test the runtime, not the HTML.
```

## What's next for Faultline

```
- Open up the symptom -> change-type map. The four classes baked into the prompt today (latency creep, 5xx spike, memory growth, crashloop) handle most Cloud Run incidents but a real on-call platform needs at least a dozen, plus a learned matcher tuned on the team's own historical postmortems.
- Multi-repo investigations. The current agent investigates one GitLab project; real incidents span the service + a shared library + a config repo. The dep graph walker can already find the right service; the MCP toolset just needs project ids per call.
- A "playback" mode: stage the same investigation on a historical incident from the team's archive so the agent's verdict can be compared against the human's actual postmortem. That's the only fair benchmark for "did the agent pick the right commit."
- Replacing the in-memory rollback registry with a real persistent store (Cloud Firestore or Cloud SQL) so multiple concurrent investigations survive Cloud Run cold-start.
- Bring the merge action under the same Draft -> Ready -> Approve flow used today but extend Approve to also auto-open an internal incident-channel post via Slack MCP. Two MCP integrations, one Approve click.
```

## Built with

```
google-cloud-aiplatform, google-adk, google-genai, gemini-3.1-pro-preview, google-cloud-run, google-cloud-monitoring, google-cloud-logging, opentelemetry-sdk, fastapi, sse-starlette, httpx, uvicorn, python, pytest, gitlab-mcp, mcp, javascript, html, css, docker, cloud-build, artifact-registry
```

## Try it out (links)

```
Live demo:   https://faultline-1083927168045.us-central1.run.app
Code:        https://github.com/jacklachan/faultline
Submission:  https://github.com/jacklachan/faultline/blob/main/SUBMISSION.md
Demo script: https://github.com/jacklachan/faultline/blob/main/DEMO.md
```

## Demo video (YouTube)

```
PASTE YOUR UNLISTED YOUTUBE URL HERE
```

---

## Things to verify before clicking Submit on Devpost

1. The "Try it out" Live demo URL opens. Hit Ctrl+Shift+R in incognito. Page says "Reasoning: Gemini 3.1 Pro (preview) on Vertex AI". Plant + investigate button visible.
2. Click Plant + investigate end-to-end in that incognito tab. Confirm the rollback card + Approve work in <90s.
3. The GitHub repo About panel has a description + the live demo URL. Empty About kills the Design score.
4. The MIT LICENSE is visible at the top of the GitHub repo page (it usually auto-detects).
5. The demo video is uploaded as UNLISTED, not private. Judges need to view without a Google login.
6. The Devpost video field has the YouTube URL pasted.
