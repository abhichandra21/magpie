// magpie webui — settings page (defaults / endpoints / libraries).
import { mountPicker } from "/static/picker.js";

const $ = (s, r = document) => r.querySelector(s);

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function jpost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const t = await r.text(); const j = t ? JSON.parse(t) : {};
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}
async function jput(url, body) {
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const t = await r.text(); const j = t ? JSON.parse(t) : {};
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

function setStatus(text, cls) {
  const el = $("#status");
  el.textContent = text;
  el.classList.remove("ok", "err");
  if (cls) el.classList.add(cls);
}

// ---------- endpoints ----------

function addEndpointRow(prefill) {
  const tpl = $("#ep-row");
  const row = tpl.content.firstElementChild.cloneNode(true);
  row.dataset.apiKeyDirty = "false";
  if (prefill) {
    row.querySelector(".name").value = prefill.name || "";
    row.querySelector(".url").value = prefill.url || "";
    row.querySelector(".model").value = prefill.model || "";
    if (prefill.has_api_key) {
      row.querySelector(".api_key").placeholder = "(unchanged · stored key in use)";
    }
  }
  row.querySelector(".api_key").addEventListener("input", () => {
    row.dataset.apiKeyDirty = "true";
  });
  row.querySelector(".rm").addEventListener("click", () => {
    row.remove();
    syncDefaultOptions();
  });
  row.querySelector(".name").addEventListener("input", syncDefaultOptions);
  $("#endpoints").appendChild(row);
  syncDefaultOptions();
}

function syncDefaultOptions() {
  const sel = $("#default_endpoint");
  const wanted = sel.value;
  const names = [...$("#endpoints").querySelectorAll(".ep-row .name")]
    .map((i) => i.value.trim()).filter(Boolean);
  sel.innerHTML = "";
  names.forEach((n) => {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    if (n === wanted) o.selected = true;
    sel.appendChild(o);
  });
  if (!names.includes(wanted) && names.length) sel.value = names[0];
}

function readEndpoints() {
  return [...$("#endpoints").querySelectorAll(".ep-row")].map((row) => {
    const name = row.querySelector(".name").value.trim();
    const apiKey = row.querySelector(".api_key").value;
    const out = {
      name,
      url: row.querySelector(".url").value.trim(),
      model: row.querySelector(".model").value.trim(),
    };
    if (apiKey || row.dataset.apiKeyDirty === "true") out.api_key = apiKey;
    return out;
  });
}

// ---------- libraries ----------

function addLibraryRow(prefill) {
  const tpl = $("#lib-row");
  const row = tpl.content.firstElementChild.cloneNode(true);
  const name = row.querySelector(".name");
  const path = row.querySelector(".path");
  const status = row.querySelector(".path-status");
  const pickerHost = row.querySelector(".lib-picker");

  if (prefill) {
    name.value = prefill.name || "";
    path.value = prefill.path || "";
    if (prefill.exists === false) {
      status.textContent = "path missing on disk";
      status.classList.add("err");
    } else if (prefill.exists === true) {
      status.textContent = "ok";
      status.classList.add("ok");
    }
  }

  let timer;
  const validate = async () => {
    const v = path.value.trim();
    if (!v) { status.textContent = ""; status.classList.remove("ok", "err"); return; }
    try {
      const r = await jpost("/api/validate", { path: v });
      status.classList.remove("ok", "err");
      if (!r.exists) { status.textContent = r.error || "does not exist"; status.classList.add("err"); return; }
      if (r.kind !== "dir") { status.textContent = "must be a folder"; status.classList.add("err"); return; }
      status.textContent = `${r.images} photo${r.images === 1 ? "" : "s"} inside`;
      status.classList.add("ok");
    } catch (e) {
      status.textContent = e.message;
      status.classList.add("err");
    }
  };
  path.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(validate, 250); });

  let pickerOpen = false;
  row.querySelector(".browse").addEventListener("click", async () => {
    if (pickerOpen) { pickerHost.replaceChildren(); pickerOpen = false; return; }
    pickerOpen = true;
    await mountPicker(pickerHost, {
      startPath: path.value.trim(),
      onPick: (p) => { path.value = p; pickerHost.replaceChildren(); pickerOpen = false; validate(); },
      onClose: () => { pickerHost.replaceChildren(); pickerOpen = false; },
    });
  });

  row.querySelector(".rm").addEventListener("click", () => row.remove());
  $("#libraries").appendChild(row);
  if (prefill && prefill.path) validate();
}

function readLibraries() {
  return [...$("#libraries").querySelectorAll(".lib-row")]
    .map((row) => ({
      name: row.querySelector(".name").value.trim(),
      path: row.querySelector(".path").value.trim(),
    }))
    .filter((l) => l.name || l.path);
}

// ---------- load + save ----------

async function load() {
  setStatus("loading…");
  try {
    const cfg = await jget("/api/config");
    $("#cfg-path").textContent = cfg.config_path;
    $("#max_keywords").value = cfg.max_keywords;
    $("#concurrency").value = cfg.concurrency;
    $("#endpoints").innerHTML = "";
    cfg.endpoints.forEach(addEndpointRow);
    syncDefaultOptions();
    $("#default_endpoint").value = cfg.default_endpoint;
    $("#libraries").innerHTML = "";
    (cfg.libraries || []).forEach(addLibraryRow);
    setStatus("loaded · " + cfg.config_path, "ok");
  } catch (e) {
    setStatus(`load failed: ${e.message}`, "err");
  }
}

async function save(ev) {
  ev?.preventDefault?.();
  setStatus("saving…");
  const body = {
    default_endpoint: $("#default_endpoint").value,
    max_keywords: parseInt($("#max_keywords").value, 10),
    concurrency: parseInt($("#concurrency").value, 10),
    endpoints: readEndpoints(),
    libraries: readLibraries(),
  };
  try {
    await jput("/api/config", body);
    setStatus("saved", "ok");
    await load();
  } catch (e) {
    setStatus(`save failed: ${e.message}`, "err");
  }
}

(function init() {
  $("#cfg-form").addEventListener("submit", save);
  $("#reload").addEventListener("click", load);
  $("#add-ep").addEventListener("click", () => addEndpointRow());
  $("#add-lib").addEventListener("click", () => addLibraryRow());
  load();
})();
