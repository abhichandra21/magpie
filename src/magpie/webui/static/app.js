// magpie webui — read-only dashboard over ~/.local/share/magpie/runs.

const $ = (sel, root = document) => root.querySelector(sel);

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

function fmtDuration(ms) {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  const s = ms / 1000;
  if (s < 90) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  const rs = Math.round(s - m * 60);
  return `${m} m ${rs.toString().padStart(2, "0")} s`;
}

function fmtTimestamp(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function padNum(n, width = 3) {
  return n.toString().padStart(width, "0");
}

function renderEmpty(root, message, hint) {
  root.innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.textContent = message;
  if (hint) {
    const sm = document.createElement("small");
    sm.textContent = hint;
    div.appendChild(sm);
  }
  root.appendChild(div);
}

async function loadStats() {
  const s = await jget("/api/stats");
  $("#count-week").innerHTML = `<em>${s.tagged_week}</em>`;
  $("#count-all").innerHTML = `<em>${s.tagged_total}</em>`;
  const models = s.last_models || [];
  $("#last-endpoint").textContent = models[0] ? models[0] : "—";
  $("#volume").textContent = `no. ${padNum(Math.max(1, s.runs))}`;
}

async function loadCabinet(runs) {
  const shelves = $("#shelves");
  const meta = $("#cabinet-meta");
  shelves.innerHTML = "";

  const latest = runs.find((r) => r.tagged > 0);
  if (!latest) {
    meta.textContent = "no specimens yet";
    renderEmpty(
      shelves,
      "The cabinet is empty.",
      "run `magpie tag <folder>` to pin your first specimen",
    );
    return;
  }

  const detail = await jget(`/api/runs/${encodeURIComponent(latest.id)}`);
  const tagged = detail.rows.filter((r) => r.status === "tagged");
  meta.innerHTML = `
    <span>${fmtTimestamp(detail.meta.timestamp)}</span>
    &nbsp;·&nbsp;
    <span>${tagged.length} specimens</span>
    &nbsp;·&nbsp;
    <span>${(detail.meta.models || []).join(", ") || "unknown model"}</span>
  `;

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
    const MAX_TOKENS = 9;
    keywords.slice(0, MAX_TOKENS).forEach((kw) => {
      const li = document.createElement("li");
      li.textContent = kw;
      kwList.appendChild(li);
    });
    if (keywords.length > MAX_TOKENS) {
      const li = document.createElement("li");
      li.textContent = `+${keywords.length - MAX_TOKENS} more`;
      li.style.fontStyle = "italic";
      kwList.appendChild(li);
    }
    if (!keywords.length && row.keyword_count) {
      const li = document.createElement("li");
      li.textContent = `${row.keyword_count} keywords`;
      kwList.appendChild(li);
    }

    node.style.animationDelay = `${80 + i * 55}ms`;
    shelves.appendChild(node);
  });
}

async function loadLedger(runs) {
  const list = $("#entries");
  list.innerHTML = "";
  if (!runs.length) {
    renderEmpty(list, "No runs recorded yet.",
      "CSV logs live in ~/.local/share/magpie/runs");
    return;
  }

  const maxMs = Math.max(1, ...runs.map((r) => r.total_ms || 0));
  const tpl = $("#ledger-row");

  runs.forEach((r, i) => {
    const node = tpl.content.firstElementChild.cloneNode(true);
    $(".num", node).textContent = padNum(runs.length - i);
    $(".ts", node).textContent = fmtTimestamp(r.timestamp);
    $(".mdl", node).textContent =
      (r.models && r.models.length ? r.models.join(" · ") : "—");
    const pct = Math.min(100, (100 * (r.total_ms || 0)) / maxMs);
    $(".bar", node).style.setProperty("--pct", `${pct}%`);
    const failed = r.failed ? ` · <span style="color:#ff9aa2">${r.failed} failed</span>` : "";
    const skipped = r.skipped ? ` · ${r.skipped} skipped` : "";
    $(".counts", node).innerHTML = `<em>${r.tagged}</em>tagged${skipped}${failed}`;
    node.style.animationDelay = `${60 + i * 35}ms`;
    list.appendChild(node);
  });
}

(async function init() {
  try {
    await loadStats();
  } catch (e) {
    console.error("stats failed", e);
  }
  try {
    const runs = await jget("/api/runs");
    await loadCabinet(runs);
    await loadLedger(runs);
  } catch (e) {
    console.error("runs failed", e);
    renderEmpty(
      $("#shelves"),
      "Could not reach the server.",
      "is `magpie ui` still running?",
    );
  }
})();
