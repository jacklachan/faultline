# Faultline — elevator pitches

## ⏱ 15-second pitch (use when stopped)

> Faultline reads your Cloud telemetry, matches the failure *symptom*
> against the *kind of change* in your recent GitLab commits, and tells
> you which commit caused it. Then it stages the revert and waits for
> you to click Approve.

## ⏱ 30-second pitch (use for judges or intro)

> When production breaks, on-call's first ten minutes are a guessing
> game between three thousand recent commits. Faultline collapses that
> to one. It reads Google Cloud telemetry to find the *kind* of failure
> — latency creep, 5xx spike, OOM — then asks the GitLab MCP server for
> recent diffs and ranks them by how well the change *type* explains
> the symptom *type*. Not by recency, not by author, not by fuzzy
> embedding similarity. By causal fit. The agent names one suspect
> commit, drafts the postmortem, opens a Draft rollback MR — then
> stops. A human clicks Approve. Built on Gemini 3.1 Pro plus Google
> ADK plus the GitLab MCP server.

## ⏱ 60-second pitch (use if you have stage time)

> Every team's incident playbook starts the same way: which service is
> the real source of the cascade, which commit caused it, where's the
> safe rollback. Senior SREs do that triage in their head in under a
> minute. Faultline gets a Gemini 3 agent to that same conclusion.
>
> The trick is in step five of an eight-step policy baked into the
> system prompt. Most "auto-RCA" agents rank suspect commits by
> recency or fuzzy similarity. Faultline ranks them by **symptom-class
> to change-type fit**: a latency creep matches a commit that added a
> query loop, not a commit that renamed a method. A 5xx spike matches
> a new dependency or auth change. OOM matches a pool or allocation
> change. That single reasoning step is the difference between an
> agent that surfaces a random pile of recent merges and an agent that
> names the offender.
>
> Everything else is honest engineering: Google ADK orchestrates,
> Gemini 3.1 Pro reasons, the GitLab MCP server gives the agent
> superpowers over commits and merge requests, the FastAPI server
> wraps the loop in a Server-Sent Events stream so the human sees
> every step live, and the merge button is deliberately not in the
> agent's toolset — the human Approve click is the only thing that
> can merge.
>
> Open the live URL, click Plant + investigate, and you watch the
> whole loop happen on real GitLab in under sixty seconds.
