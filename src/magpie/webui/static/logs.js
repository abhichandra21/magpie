// magpie logs — live tail via SSE.

const $ = (s, r = document) => r.querySelector(s);

const state = {
  level: "all",
  query: "",
  autoscroll: true,
  count: 0,
  src: null,
};

function fmtTs(t) {
  const d = new Date(t * 1000);
  const hh = d.getHours().toString().padStart(2, "0");
  const mm = d.getMinutes().toString().padStart(2, "0");
  const ss = d.getSeconds().toString().padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

function setStatus(text, cls) {
  const s = $("#status");
  s.classList.remove("live", "err");
  s.innerHTML = `<span class="dot"></span>${text}`;
  if (cls) s.classList.add(cls);
}

function escapeHtml(str) {
  return (str || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function append(line) {
  const li = document.createElement("li");
  const lvl = (line.level || "INFO").toUpperCase();
  li.dataset.level = lvl;
  li.dataset.logger = line.logger || "";
  li.title = `${line.logger || ""}  —  click to copy`;
  li.dataset.text = `${line.logger} ${line.message}`.toLowerCase();
  li.dataset.copy = `${fmtTs(line.ts)}  ${lvl}  ${line.logger || ""}  ${line.message || ""}`;
  li.innerHTML = `
    <span class="ts">${fmtTs(line.ts)}</span>
    <span class="lvl ${lvl}">${lvl}</span>
    <span class="msg">${escapeHtml(line.message || "")}</span>
  `;
  li.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(li.dataset.copy);
      li.classList.add("copied");
      setTimeout(() => li.classList.remove("copied"), 600);
    } catch { /* noop */ }
  });
  applyFilter(li);
  $("#lines").appendChild(li);
  state.count += 1;
  $("#line-count").textContent = `${state.count.toLocaleString()} lines`;
  if (state.autoscroll) {
    const pane = $("#pane");
    pane.scrollTop = pane.scrollHeight;
  }
  // cap DOM to avoid runaway memory
  const lines = $("#lines");
  while (lines.children.length > 4000) lines.removeChild(lines.firstChild);
}

function applyFilter(el) {
  const okLevel = state.level === "all" || el.dataset.level === state.level;
  const okQuery = !state.query || el.dataset.text.includes(state.query);
  el.classList.toggle("hidden", !(okLevel && okQuery));
}

function reapplyAll() {
  $("#lines").querySelectorAll("li").forEach(applyFilter);
}

function bindFilters() {
  $("#levels").addEventListener("click", (e) => {
    const btn = e.target.closest(".fbtn");
    if (!btn) return;
    state.level = btn.dataset.level;
    $("#levels").querySelectorAll(".fbtn").forEach((b) =>
      b.classList.toggle("active", b === btn)
    );
    reapplyAll();
  });
  $("#search").addEventListener("input", (e) => {
    state.query = e.target.value.trim().toLowerCase();
    reapplyAll();
  });
  $("#autoscroll").addEventListener("change", (e) => {
    state.autoscroll = e.target.checked;
  });
  $("#clear").addEventListener("click", () => {
    $("#lines").innerHTML = "";
    state.count = 0;
    $("#line-count").textContent = "0 lines";
  });
}

function connect() {
  setStatus("connecting…");
  if (state.src) state.src.close();
  const src = new EventSource("/api/logs/stream");
  state.src = src;
  src.addEventListener("bootstrap", (e) => {
    const lines = JSON.parse(e.data);
    lines.forEach(append);
    setStatus(`live  ·  streaming`, "live");
  });
  src.onmessage = (e) => {
    if (!e.data) return;
    try { append(JSON.parse(e.data)); } catch { /* ignore */ }
  };
  src.onerror = () => {
    setStatus("disconnected · retrying", "err");
    src.close();
    setTimeout(connect, 1500);
  };
}

(async function init() {
  bindFilters();
  // bootstrap fallback if SSE blocked
  try {
    const t = await fetch("/api/logs?limit=200").then((r) => r.json());
    (t.lines || []).forEach(append);
  } catch { /* noop */ }
  connect();
})();
