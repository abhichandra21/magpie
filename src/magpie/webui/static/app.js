// magpie webui — drive tag jobs + browse history.

const $ = (s, r = document) => r.querySelector(s);

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const text = await r.text();
  const json = text ? JSON.parse(text) : {};
  if (!r.ok) {
    const msg = json.detail || r.statusText;
    throw new Error(msg);
  }
  return json;
}

function fmtDuration(ms) {
  if (!ms && ms !== 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 90) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  return `${m}m${rs.toString().padStart(2, "0")}s`;
}
function fmtTimestamp(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return iso; }
}
function fmtUnixClock(t) {
  if (!t) return "--:--:--";
  return new Date(t * 1000).toLocaleTimeString(undefined, { hour12: false });
}
function padNum(n, w = 3) { return n.toString().padStart(w, "0"); }

function renderEmpty(root, msg, hint) {
  root.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = msg;
  if (hint) {
    const sm = document.createElement("small");
    sm.textContent = hint;
    div.appendChild(sm);
  }
  root.appendChild(div);
}

// ---------- stats + endpoints ----------

async function loadStats() {
  try {
    const s = await jget("/api/stats");
    $("#stat-all").textContent = s.tagged_total;
    $("#stat-week").textContent = s.tagged_week;
    $("#stat-ep").textContent = (s.last_models || [])[0] || "—";
  } catch { /* noop */ }
}

async function loadEndpoints() {
  try {
    const { default: def, endpoints } = await jget("/api/endpoints");
    const sel = $("#endpoint");
    sel.innerHTML = "";
    for (const ep of endpoints) {
      const opt = document.createElement("option");
      opt.value = ep.name;
      opt.textContent = `${ep.name} · ${ep.model}`;
      if (ep.name === def) opt.selected = true;
      sel.appendChild(opt);
    }
  } catch (e) {
    $("#run-status").textContent = `endpoints: ${e.message}`;
    $("#run-status").classList.add("err");
  }
}

// ---------- cabinet + ledger ----------

async function loadCabinet(runs) {
  const shelves = $("#shelves");
  const meta = $("#cabinet-meta");
  shelves.innerHTML = "";
  const latest = runs.find((r) => r.tagged > 0);
  if (!latest) {
    meta.textContent = "empty";
    renderEmpty(shelves, "The cabinet is empty.",
      "tag something above — specimens will appear here");
    return;
  }
  const detail = await jget(`/api/runs/${encodeURIComponent(latest.id)}`);
  const tagged = detail.rows.filter((r) => r.status === "tagged");
  meta.textContent = `${fmtTimestamp(detail.meta.timestamp)} · ${tagged.length} specimens · ${(detail.meta.models || []).join(", ")}`;
  const tpl = $("#specimen");
  tagged.forEach((row, i) => {
    const node = tpl.content.firstElementChild.cloneNode(true);
    const img = $(".plate img", node);
    img.src = `/api/thumb?path=${encodeURIComponent(row.path)}`;
    img.alt = row.caption || "";
    $(".cap", node).textContent = row.caption || "";
    $(".caption-num", node).textContent = `pl. ${padNum(i + 1)}`;
    $(".model", node).textContent = row.model || "";
    $(".dur", node).textContent = fmtDuration(parseInt(row.duration_ms, 10));
    const kwList = $(".keywords", node);
    const keywords = Array.isArray(row.keywords) ? row.keywords : [];
    const MAX = 9;
    keywords.slice(0, MAX).forEach((kw) => {
      const li = document.createElement("li");
      li.textContent = kw;
      kwList.appendChild(li);
    });
    if (keywords.length > MAX) {
      const li = document.createElement("li");
      li.textContent = `+${keywords.length - MAX}`;
      li.style.fontStyle = "italic";
      kwList.appendChild(li);
    }
    node.style.animationDelay = `${60 + i * 45}ms`;
    shelves.appendChild(node);
  });
}

async function loadLedger(runs) {
  const list = $("#entries");
  list.innerHTML = "";
  if (!runs.length) {
    renderEmpty(list, "No runs yet.",
      "csv logs live in ~/.local/share/magpie/runs");
    return;
  }
  const maxMs = Math.max(1, ...runs.map((r) => r.total_ms || 0));
  const tpl = $("#ledger-row");
  runs.forEach((r, i) => {
    const node = tpl.content.firstElementChild.cloneNode(true);
    $(".num", node).textContent = padNum(runs.length - i);
    $(".ts", node).textContent = fmtTimestamp(r.timestamp);
    $(".mdl", node).textContent = (r.models && r.models.length) ? r.models.join(" · ") : "—";
    const pct = Math.min(100, (100 * (r.total_ms || 0)) / maxMs);
    $(".bar", node).style.setProperty("--pct", `${pct}%`);
    const failed = r.failed ? ` · <span style="color:var(--bad)">${r.failed} failed</span>` : "";
    const skipped = r.skipped ? ` · ${r.skipped} skipped` : "";
    $(".counts", node).innerHTML = `<em>${r.tagged}</em>tagged${skipped}${failed}`;
    node.style.animationDelay = `${50 + i * 25}ms`;
    list.appendChild(node);
  });
}

async function refreshHistory() {
  try {
    const runs = await jget("/api/runs");
    await Promise.all([loadCabinet(runs), loadLedger(runs)]);
  } catch (e) {
    renderEmpty($("#shelves"), "Could not reach server.", e.message);
  }
}

// ---------- running a job ----------

function setProgress(job) {
  const section = $("#progress");
  section.hidden = false;
  const total = job.total || 1;
  const done = (job.tagged || 0) + (job.skipped || 0) + (job.failed || 0);
  const pct = total > 0 ? Math.min(100, (100 * done) / total) : 0;
  $("#meter-fill").style.width = `${pct}%`;
  $("#p-done").textContent = done;
  $("#p-total").textContent = job.total || "?";
  $("#p-tagged").textContent = job.tagged || 0;
  $("#p-skipped").textContent = job.skipped || 0;
  $("#p-failed").textContent = job.failed || 0;
  $("#p-current").textContent = job.current || "—";
  $("#p-spark").hidden = job.status !== "running";

  // append only new file-events to the log (idempotent via data-id)
  const log = $("#p-log");
  const existing = new Set([...log.querySelectorAll("li")].map((li) => li.dataset.id));
  (job.events || [])
    .filter((e) => e.kind === "file")
    .forEach((e, idx) => {
      const id = `${e.ts}-${idx}-${e.data.path}`;
      if (existing.has(id)) return;
      const li = document.createElement("li");
      li.dataset.id = id;
      li.innerHTML = `
        <span class="tsc">${fmtUnixClock(e.ts)}</span>
        <span class="pth">${(e.data.path || "").split("/").slice(-2).join("/")}</span>
        <span class="cap">${e.data.caption || ""}</span>`;
      log.appendChild(li);
    });
  log.scrollTop = log.scrollHeight;
}

async function pollJob(jobId) {
  const status = $("#run-status");
  status.classList.remove("err");
  while (true) {
    let job;
    try {
      job = await jget(`/api/jobs/${jobId}`);
    } catch (e) {
      status.textContent = `lost job: ${e.message}`;
      status.classList.add("err");
      return;
    }
    setProgress(job);
    if (job.status === "running" || job.status === "queued") {
      status.textContent = `${job.status} · ${job.current ? job.current.split("/").pop() : ""}`;
      await new Promise((r) => setTimeout(r, 800));
      continue;
    }
    if (job.status === "failed") {
      status.textContent = `failed: ${job.error || `${job.failed} file(s) failed`}`;
      status.classList.add("err");
    } else {
      status.textContent = `done · ${job.tagged} tagged · ${job.skipped} skipped${job.failed ? ` · ${job.failed} failed` : ""}`;
    }
    $("#go").disabled = false;
    // refresh history after completion
    await loadStats();
    await refreshHistory();
    return;
  }
}

function bindForm() {
  $("#run-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const path = $("#path").value.trim();
    if (!path) return;
    const btn = $("#go");
    const status = $("#run-status");
    btn.disabled = true;
    status.textContent = "submitting…";
    status.classList.remove("err");
    try {
      const { id } = await jpost("/api/jobs", {
        path,
        endpoint: $("#endpoint").value || null,
        hint: $("#hint").value,
        force: $("#force").checked,
      });
      // reset log
      $("#p-log").innerHTML = "";
      await pollJob(id);
    } catch (e) {
      status.textContent = e.message;
      status.classList.add("err");
      btn.disabled = false;
    }
  });

  // ⌘-Enter / Ctrl-Enter submits
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      $("#run-form").dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });
}

// ---------- init ----------

(async function init() {
  bindForm();
  await loadEndpoints();
  await loadStats();
  await refreshHistory();
  // if a job was already running when the page loaded, resume polling the newest
  try {
    const jobs = await jget("/api/jobs");
    const active = jobs.find((j) => j.status === "running" || j.status === "queued");
    if (active) {
      $("#go").disabled = true;
      setProgress(active);
      pollJob(active.id);
    }
  } catch { /* noop */ }
})();
