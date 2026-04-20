// magpie webui — main page.
import { mountPicker } from "/static/picker.js";

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
  const j = text ? JSON.parse(text) : {};
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

function fmtDuration(ms) {
  if (!ms && ms !== 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 90) return `${s.toFixed(1)}s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  return `${m}m ${rs.toString().padStart(2, "0")}s`;
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

// ---------- endpoints ----------

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
    setHint(`endpoints: ${e.message}`, "invalid");
  }
}

// ---------- path validation + picker ----------

let validateTimer = null;
let lastValidation = null;

function setHint(text, cls) {
  const hint = $("#path-hint");
  hint.textContent = text;
  hint.classList.remove("valid", "invalid");
  if (cls) hint.classList.add(cls);
  const inp = $("#path");
  inp.classList.remove("valid", "invalid");
  if (cls) inp.classList.add(cls);
}

async function validatePath(raw) {
  if (!raw) {
    setHint("paste a path, or browse — magpie will recurse into folders", null);
    $("#go").disabled = false;
    lastValidation = null;
    return;
  }
  try {
    const v = await jpost("/api/validate", { path: raw });
    lastValidation = v;
    if (!v.exists) {
      setHint(v.error || "does not exist", "invalid");
      $("#go").disabled = true;
      return;
    }
    if (v.kind === "file") {
      if (v.images === 1) {
        setHint(`single file · ${v.resolved}`, "valid");
        $("#go").disabled = false;
      } else {
        setHint(v.error || "unsupported file type", "invalid");
        $("#go").disabled = true;
      }
      return;
    }
    if (v.kind === "dir") {
      if (v.images > 0) {
        setHint(`folder · ${v.images} photo${v.images === 1 ? "" : "s"} found (recursive)`, "valid");
        $("#go").disabled = false;
      } else {
        setHint("no jpg/jpeg/heic/heif files found inside", "invalid");
        $("#go").disabled = true;
      }
      return;
    }
    setHint(v.error || "unknown kind", "invalid");
    $("#go").disabled = true;
  } catch (e) {
    setHint(e.message, "invalid");
    $("#go").disabled = true;
  }
}

function bindPathInput() {
  const inp = $("#path");
  inp.addEventListener("input", () => {
    clearTimeout(validateTimer);
    validateTimer = setTimeout(() => validatePath(inp.value.trim()), 240);
  });
  $("#browse").addEventListener("click", openPicker);
}

let pickerOpen = false;
async function openPicker() {
  if (pickerOpen) return closePicker();
  pickerOpen = true;
  await mountPicker($("#picker-host"), {
    startPath: $("#path").value.trim(),
    onPick: (p) => {
      $("#path").value = p;
      validatePath(p);
      closePicker();
    },
    onClose: closePicker,
  });
}
function closePicker() {
  pickerOpen = false;
  $("#picker-host").replaceChildren();
}

// ---------- recent runs + cabinet ----------

async function loadRunsSection() {
  let runs = [];
  try {
    runs = await jget("/api/runs");
  } catch (e) {
    return;
  }

  // recent runs table
  const tbody = $("#runs-tbody");
  tbody.innerHTML = "";
  const top = runs.slice(0, 12);
  const maxMs = Math.max(1, ...top.map((r) => r.total_ms || 0));
  if (!top.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="4" style="text-align:center; padding:1.4rem; color:var(--muted); font-style:italic;">No runs yet — tag something above.</td>`;
    tbody.appendChild(tr);
  } else {
    top.forEach((r) => {
      const tr = document.createElement("tr");
      const failed = r.failed
        ? `<span class="bad">${r.failed} <span style="color:var(--muted); font-style:normal;">failed</span></span>`
        : "";
      const skipped = r.skipped ? `${r.skipped} <span style="color:var(--muted); font-style:normal;">skipped</span>` : "";
      const tagged = `<em>${r.tagged}</em><span style="color:var(--muted);">tagged</span>`;
      tr.innerHTML = `
        <td>${fmtTimestamp(r.timestamp)}</td>
        <td class="model">${(r.models && r.models.length) ? r.models.join(" · ") : "—"}</td>
        <td class="counts">${tagged} ${skipped} ${failed}</td>
        <td class="bar"><div class="track"><div class="fill" style="--pct:${Math.min(100, (100 * (r.total_ms || 0)) / maxMs)}%"></div></div></td>
      `;
      tbody.appendChild(tr);
    });
  }

  // top-of-page summary
  try {
    const s = await jget("/api/stats");
    $("#stat-summary").textContent = `${s.tagged_total} total · ${s.tagged_week} this week`;
  } catch { /* noop */ }

  // cabinet — latest run with tagged
  const shelves = $("#shelves");
  shelves.innerHTML = "";
  const latest = runs.find((r) => r.tagged > 0);
  if (!latest) {
    $("#cabinet-meta").textContent = "empty";
    shelves.innerHTML = `<div class="empty" style="grid-column:1/-1;">The cabinet is empty.<small>tag something above</small></div>`;
    return;
  }
  let detail;
  try { detail = await jget(`/api/runs/${encodeURIComponent(latest.id)}`); }
  catch { return; }
  const tagged = detail.rows.filter((r) => r.status === "tagged");
  $("#cabinet-meta").textContent =
    `${fmtTimestamp(detail.meta.timestamp)} · ${tagged.length} photo${tagged.length === 1 ? "" : "s"}`;
  const tpl = $("#card");
  tagged.slice(0, 12).forEach((row) => {
    const node = tpl.content.firstElementChild.cloneNode(true);
    const img = node.querySelector("img");
    img.src = `/api/thumb?path=${encodeURIComponent(row.path)}`;
    img.alt = row.caption || "";
    node.querySelector(".cap").textContent = row.caption || "";
    const kws = node.querySelector(".kws");
    const list = Array.isArray(row.keywords) ? row.keywords : [];
    list.slice(0, 8).forEach((k) => {
      const s = document.createElement("span");
      s.textContent = k;
      kws.appendChild(s);
    });
    if (list.length > 8) {
      const s = document.createElement("span");
      s.textContent = `+${list.length - 8}`;
      s.style.fontStyle = "italic";
      kws.appendChild(s);
    }
    shelves.appendChild(node);
  });
}

// ---------- job submit + polling ----------

function setProgress(job) {
  $("#progress").hidden = false;
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

  const log = $("#p-log");
  const seen = new Set([...log.querySelectorAll("li")].map((li) => li.dataset.id));
  (job.events || [])
    .filter((e) => e.kind === "file")
    .forEach((e, i) => {
      const id = `${e.ts}-${i}-${e.data.path}`;
      if (seen.has(id)) return;
      const li = document.createElement("li");
      li.dataset.id = id;
      const trail = (e.data.path || "").split("/").slice(-2).join("/");
      li.innerHTML = `
        <span class="tsc">${fmtUnixClock(e.ts)}</span>
        <span class="pth">${trail}</span>
        <span class="cap">${(e.data.caption || "").slice(0, 90)}</span>`;
      log.appendChild(li);
    });
  log.scrollTop = log.scrollHeight;
}

async function pollJob(jobId) {
  const status = $("#run-status");
  status.classList.remove("invalid");
  while (true) {
    let job;
    try { job = await jget(`/api/jobs/${jobId}`); }
    catch (e) { status.textContent = `lost: ${e.message}`; return; }
    setProgress(job);
    if (job.status === "running" || job.status === "queued") {
      const cur = job.current ? job.current.split("/").pop() : "";
      status.textContent = `${job.status}${cur ? " · " + cur : ""}`;
      await new Promise((r) => setTimeout(r, 800));
      continue;
    }
    if (job.status === "failed") {
      status.textContent = `failed: ${job.error || `${job.failed} file(s) failed`}`;
    } else {
      status.textContent =
        `done · ${job.tagged} tagged${job.skipped ? ` · ${job.skipped} skipped` : ""}${job.failed ? ` · ${job.failed} failed` : ""}`;
    }
    $("#go").disabled = false;
    await loadRunsSection();
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
    try {
      const { id } = await jpost("/api/jobs", {
        path,
        endpoint: $("#endpoint").value || null,
        hint: $("#hint").value,
        force: $("#force").checked,
      });
      $("#p-log").innerHTML = "";
      await pollJob(id);
    } catch (e) {
      status.textContent = e.message;
      btn.disabled = false;
    }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      $("#run-form").dispatchEvent(new Event("submit", { cancelable: true }));
    }
  });
}

// ---------- init ----------

(async function init() {
  bindPathInput();
  bindForm();
  await loadEndpoints();
  await loadRunsSection();
  // resume in-flight job, if any
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
