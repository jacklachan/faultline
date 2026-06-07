# Faultline — 3-minute filmed demo

Total target runtime: **2:50**. Buffer is everything.

## Pre-roll (do off-camera before you hit record)

1. Open https://faultline-1083927168045.us-central1.run.app in a fresh
   incognito window. Hit Ctrl+Shift+R to bust cache. You should see
   "Reasoning: Gemini 3.1 Pro" and a green ⚡ Plant + investigate button.
2. Open https://gitlab.com/mohitlalith07/faultline in a second tab. The
   issues + merge requests tabs should be empty-ish (the agent will fill
   them).
3. Make sure your screen recording captures the browser only, not your
   desktop chrome.
4. Have ELEVATOR_PITCH.md open in a side window so you can paraphrase.

## Camera setup

- 1080p screen recording, no webcam overlay.
- Move browser to its own monitor / virtual desktop; clear notifications.
- Voice-only narration over screen; no facecam. Saves time on retakes.

## Beat sheet (2:50)

### 0:00 → 0:18 · Hero / problem

Camera: Faultline landing page. Cursor still.

> "Production just broke. The on-call's first ten minutes are
> mechanical detective work: which service is actually broken, which
> commit caused it, where's the safe rollback. Faultline gets a Gemini
> 3 agent through that triage in sixty seconds."

### 0:18 → 0:38 · The differentiator (the one thing competitors will not have)

Camera: Scroll down so the "The idea" panel fills the screen.

> "Most auto-RCA agents rank suspect commits by recency. Faultline
> ranks them by symptom-class to change-type fit. Latency creep matches
> a commit that added a query loop, not a rename. A 5xx spike matches
> a new dependency. OOM matches a pool change. That symptom-to-change
> reasoning is in step five of the policy baked into the system prompt.
> It's the difference between a list of recent merges and an actual
> verdict."

### 0:38 → 0:55 · Click Plant + investigate

Camera: scroll back up. Hover the green button. Click.

> "I'm clicking Plant + investigate. The server commits a believable
> perf regression to my real GitLab project, flips the regression flag
> on the live victim service running on Cloud Run, then kicks the
> agent."

(Cards start streaming. Don't read every one.)

### 0:55 → 1:50 · Agent does the eight-step policy

Camera: stream pane, slowly. Pause occasionally so judges can read.

Narrate the highlights as cards land:

> "Step 1: it's reading Cloud Monitoring metrics — real p95 latency
> and error rates from the deployed victim. Step 2: it walks the
> service dependency graph instead of trusting the first red alert.
> See — it just switched focus from frontend to data. Step 3: it's
> hitting the GitLab MCP server to list recent commits. Step 4: it's
> reading the suspect diff. Step 5 is the one I mentioned — matching
> the change type to the symptom class. Step 7: it just opened the
> postmortem issue and tried to stage a Draft rollback MR."

(Wait for the orange `rollback staged` card.)

> "And there's the staged rollback. Real GitLab issue, real Draft
> merge request, link in the card."

### 1:50 → 2:15 · The agent never merges — hard architectural constraint

Camera: hover the Approve button. Don't click yet.

> "Here's the part that's not just a prompt rule. The merge tool is
> deliberately not registered on the agent's toolset. Even if the
> model hallucinated a merge tool call, the ADK MCP client would
> 404. The Approve button is the only path that can merge. The
> human is in the loop by design, not by policy."

### 2:15 → 2:40 · Approve → recovery

Camera: click Approve. Card flips to "merged".

> "I click Approve. The server strips the Draft prefix and merges the
> MR via GitLab REST. Notice the status flips to merged. Behind the
> scenes Faultline also clears the regression flag on Cloud Run, so
> the victim service recovers."

Switch to the GitLab tab. Show the merged MR.

### 2:40 → 2:50 · Outro

Camera: back to landing page.

> "Built on Gemini 3.1 Pro, Google ADK, the GitLab MCP server,
> Cloud Run, and Cloud Monitoring. Public repo, MIT, link in the
> description."

## Things to NOT say

- Don't say "Gemini 2.5 Flash" anywhere. The runtime is 3.1 Pro.
- Don't apologise for any tool failure during the demo. The REST
  fallback exists exactly to make `create_merge_request` failures
  invisible to the user; just say "the host handled the MR creation".
- Don't read the full agent JSON tool calls aloud. Let them scroll.
- Don't open DevTools.

## Fallback if the live demo errors out

1. Stop recording.
2. From your repo root: `bash deploy_server_cloudrun.sh` (~3 min).
3. Re-shoot from "Click Plant + investigate" beat.

## What to put in the YouTube description

```
Faultline — autonomous incident root-cause investigator
Built for the Google Cloud Rapid Agent Hackathon (GitLab track)
Gemini 3.1 Pro on Vertex AI · Google ADK · @zereight/mcp-gitlab MCP server

Live demo:  https://faultline-1083927168045.us-central1.run.app
Repo:       https://github.com/jacklachan/faultline
Submission: see SUBMISSION.md in the repo

Built by mohit (jacklachan on GitHub).
```

## Upload + submit

- Upload to YouTube as **unlisted**, not private (judges need to view
  without login).
- Paste the unlisted URL into Devpost's video field.
- Use DEVPOST_FORM.md for the rest of the form text.
