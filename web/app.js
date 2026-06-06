// Faultline web console.
//
// Posts an investigation request to /investigate, reads back the
// Server-Sent Events stream, and renders one card per event.
//
// EventSource doesn't support POST, so we use fetch() + ReadableStream
// and parse the SSE wire format ourselves.

"use strict";

(function () {
  const form = document.getElementById("setup-form");
  const startBtn = document.getElementById("start");
  const resetBtn = document.getElementById("reset");
  const statusEl = document.getElementById("status");
  const streamEl = document.getElementById("stream");
  const rollbackSection = document.getElementById("rollback-section");
  const rollbackCard = document.getElementById("rollback-card");

  let currentAbort = null;
  let startedAt = null;
  let elapsedTimer = null;
  const elapsedEl = document.getElementById("elapsed");
  const emptyStream = document.getElementById("empty-stream");

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = kind ? "status-" + kind : "muted";
  }

  function clearStream() {
    streamEl.innerHTML = "";
    rollbackCard.innerHTML = "";
    rollbackSection.classList.add("hidden");
    if (emptyStream) emptyStream.classList.remove("hidden");
  }

  function startElapsed() {
    startedAt = Date.now();
    if (elapsedTimer) clearInterval(elapsedTimer);
    elapsedTimer = setInterval(() => {
      const s = Math.floor((Date.now() - startedAt) / 1000);
      if (elapsedEl) elapsedEl.textContent = `t+${s}s`;
    }, 1000);
  }

  function stopElapsed() {
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
  }

  function makeRow(ev) {
    const li = document.createElement("li");
    li.className = "row-" + ev.type;
    const pill = document.createElement("span");
    pill.className = "pill pill-" + ev.type;
    pill.textContent = ev.type.replace("_", " ");
    li.appendChild(pill);

    const body = document.createElement("div");
    body.className = "row-body";
    body.appendChild(renderEventBody(ev));
    li.appendChild(body);
    return li;
  }

  function renderEventBody(ev) {
    const frag = document.createElement("div");
    if (ev.type === "step") {
      const h = document.createElement("strong");
      h.textContent = "Step " + ev.step;
      frag.appendChild(h);
      const t = document.createElement("p");
      t.textContent = ev.text;
      frag.appendChild(t);
      return frag;
    }
    if (ev.type === "tool_call") {
      const h = document.createElement("strong");
      h.textContent = ev.name + "(...)";
      frag.appendChild(h);
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(ev.args || {}, null, 2);
      frag.appendChild(pre);
      return frag;
    }
    if (ev.type === "tool_result") {
      const h = document.createElement("strong");
      h.textContent = ev.name + " ->";
      frag.appendChild(h);
      const pre = document.createElement("pre");
      pre.textContent = ev.result_preview || "(empty)";
      frag.appendChild(pre);
      return frag;
    }
    if (ev.type === "rollback_staged") {
      const h = document.createElement("strong");
      h.textContent = "Draft rollback MR staged";
      frag.appendChild(h);
      const p = document.createElement("p");
      p.innerHTML =
        "Suspect commit <code>" +
        escapeHtml(ev.suspect_commit_sha || "?") +
        "</code> on <code>" +
        escapeHtml(ev.project_id) +
        "</code>.";
      frag.appendChild(p);
      return frag;
    }
    if (ev.type === "final") {
      const h = document.createElement("strong");
      h.textContent = "Investigation complete";
      frag.appendChild(h);
      const t = document.createElement("p");
      t.textContent = ev.summary;
      frag.appendChild(t);
      return frag;
    }
    if (ev.type === "error") {
      const h = document.createElement("strong");
      h.textContent = "Error";
      frag.appendChild(h);
      const t = document.createElement("p");
      t.textContent = ev.message;
      frag.appendChild(t);
      return frag;
    }
    const t = document.createElement("pre");
    t.textContent = JSON.stringify(ev, null, 2);
    frag.appendChild(t);
    return frag;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function showRollback(ev) {
    rollbackSection.classList.remove("hidden");
    rollbackCard.innerHTML = "";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML =
      "Suspect <code>" +
      escapeHtml(ev.suspect_commit_sha || "?") +
      "</code> on <code>" +
      escapeHtml(ev.project_id) +
      "</code>";
    rollbackCard.appendChild(meta);

    const links = document.createElement("div");
    links.className = "links";
    if (ev.issue_url) {
      links.appendChild(linkEl("Postmortem issue", ev.issue_url));
    }
    if (ev.mr_url) {
      links.appendChild(linkEl("Draft rollback MR", ev.mr_url));
    }
    rollbackCard.appendChild(links);

    const row = document.createElement("div");
    row.className = "row";
    const btn = document.createElement("button");
    btn.id = "approve";
    btn.textContent = "Approve rollback";
    btn.addEventListener("click", () => approveRollback(ev.rollback_id, btn));
    row.appendChild(btn);
    const note = document.createElement("span");
    note.id = "approve-status";
    note.className = "muted";
    row.appendChild(note);
    rollbackCard.appendChild(row);
  }

  function linkEl(label, href) {
    const a = document.createElement("a");
    a.href = href;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = label;
    return a;
  }

  async function approveRollback(rollbackId, btn) {
    btn.disabled = true;
    const note = document.getElementById("approve-status");
    note.textContent = "merging via GitLab MCP...";
    try {
      const r = await fetch("/approve/" + encodeURIComponent(rollbackId), {
        method: "POST",
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail || ("HTTP " + r.status));
      note.textContent = "status: " + (body.rollback ? body.rollback.status : "ok");
    } catch (err) {
      note.textContent = "approve failed: " + err.message;
      btn.disabled = false;
    }
  }

  // SSE parser: yields parsed JSON objects from a fetch response body.
  async function* sseEvents(response) {
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const dataLines = [];
        for (const line of chunk.split("\n")) {
          if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).replace(/^ /, ""));
          }
        }
        if (dataLines.length) {
          try {
            yield JSON.parse(dataLines.join("\n"));
          } catch (e) {
            console.warn("bad SSE chunk", chunk, e);
          }
        }
      }
    }
  }

  let currentSource = null;

  function handleEvent(ev) {
    streamEl.appendChild(makeRow(ev));
    streamEl.lastElementChild.scrollIntoView({ behavior: "smooth", block: "end" });
    if (ev.type === "tool_call") setStatus(`calling ${ev.name}…`, "running");
    if (ev.type === "tool_result") setStatus(`${ev.name} returned — agent reasoning`, "running");
    if (ev.type === "rollback_staged") {
      showRollback(ev);
      setStatus("rollback staged — review and Approve below", "done");
    }
    if (ev.type === "final") setStatus("investigation complete", "done");
    if (ev.type === "error") setStatus("error: " + (ev.message || "see card below"), "error");
  }

  function startInvestigation(payload) {
    clearStream();
    if (emptyStream) emptyStream.classList.add("hidden");
    setStatus("opening stream…", "running");
    startElapsed();
    startBtn.disabled = true;

    // Native EventSource (GET) is the most reliable SSE path: proxies do
    // not hold the response, browsers do not buffer it, and reconnection
    // is automatic.
    const qs = new URLSearchParams({
      service: payload.service,
      window_minutes: String(payload.window_minutes),
    });
    if (payload.scenario) qs.set("scenario", payload.scenario);
    if (payload.project_id) qs.set("project_id", payload.project_id);

    if (currentSource) currentSource.close();
    const es = new EventSource(`/investigate?${qs.toString()}`);
    currentSource = es;

    es.addEventListener("ready", () => {
      setStatus("agent is investigating — first event will appear below", "running");
    });

    const handler = (e) => {
      try {
        handleEvent(JSON.parse(e.data));
      } catch (err) {
        console.warn("bad SSE event", e.data, err);
      }
    };
    [
      "step",
      "tool_call",
      "tool_result",
      "rollback_staged",
      "final",
      "error",
    ].forEach((t) => es.addEventListener(t, handler));

    es.addEventListener("final", () => {
      es.close();
      currentSource = null;
      startBtn.disabled = false;
      stopElapsed();
    });
    es.addEventListener("error", (e) => {
      // EventSource fires `error` on the connection itself when the server
      // closes the stream. We do not want to flag that as an investigation
      // error — only flag if no events ever arrived.
      if (es.readyState === EventSource.CLOSED) {
        if (!streamEl.children.length) {
          setStatus("connection closed before any events", "error");
        }
        startBtn.disabled = false;
        currentSource = null;
        stopElapsed();
      }
    });
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const payload = {
      service: fd.get("service"),
      window_minutes: Number(fd.get("window_minutes")) || 15,
    };
    const scenario = fd.get("scenario");
    if (scenario) payload.scenario = scenario;
    const proj = fd.get("project_id");
    if (proj) payload.project_id = proj;
    startInvestigation(payload);
  });

  resetBtn.addEventListener("click", () => {
    if (currentSource) {
      currentSource.close();
      currentSource = null;
    }
    stopElapsed();
    clearStream();
    setStatus("idle");
    startBtn.disabled = false;
  });

  setStatus("idle");
})();
