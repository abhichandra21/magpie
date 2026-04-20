// magpie webui — settings page.

const $ = (s, r = document) => r.querySelector(s);

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function jput(url, body) {
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const text = await r.text();
  const j = text ? JSON.parse(text) : {};
  if (!r.ok) throw new Error(j.detail || r.statusText);
  return j;
}

function setStatus(text, cls) {
  const el = $("#status");
  el.textContent = text;
  el.classList.remove("ok", "err");
  if (cls) el.classList.add(cls);
}

function addEndpointRow(prefill) {
  const tpl = $("#ep-row");
  const row = tpl.content.firstElementChild.cloneNode(true);
  if (prefill) {
    row.querySelector(".name").value = prefill.name || "";
    row.querySelector(".url").value = prefill.url || "";
    row.querySelector(".model").value = prefill.model || "";
    if (prefill.has_api_key) {
      row.querySelector(".api_key").placeholder = "(unchanged · stored key in use)";
    }
  }
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
  const names = [...$("#endpoints").querySelectorAll(".name")]
    .map((i) => i.value.trim())
    .filter(Boolean);
  sel.innerHTML = "";
  names.forEach((n) => {
    const o = document.createElement("option");
    o.value = n; o.textContent = n;
    if (n === wanted) o.selected = true;
    sel.appendChild(o);
  });
  if (!names.includes(wanted) && names.length) sel.value = names[0];
}

function readEndpoints(existingKeyMap) {
  const rows = [...$("#endpoints").querySelectorAll(".endpoint-row")];
  return rows.map((row) => {
    const name = row.querySelector(".name").value.trim();
    const apiKey = row.querySelector(".api_key").value;
    return {
      name,
      url: row.querySelector(".url").value.trim(),
      model: row.querySelector(".model").value.trim(),
      // empty input keeps existing key (no overwrite). Only send when user typed.
      api_key: apiKey || (existingKeyMap[name] ? "__KEEP__" : ""),
    };
  });
}

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
    setStatus("loaded · " + cfg.config_path, "ok");
  } catch (e) {
    setStatus(`load failed: ${e.message}`, "err");
  }
}

async function save() {
  setStatus("saving…");
  // build endpoints, preserving existing api_key when input was left blank.
  let cur;
  try { cur = await jget("/api/config"); } catch { cur = { endpoints: [] }; }
  const keyMap = Object.fromEntries(cur.endpoints.map((e) => [e.name, e.has_api_key]));
  const endpoints = readEndpoints(keyMap);

  // Translate __KEEP__ marker by fetching nothing — server has no GET-key path,
  // so we emit an empty string and rely on the user to retype if they rotated.
  // To preserve, we simply omit the api_key field when value is __KEEP__.
  const cleaned = endpoints.map(({ api_key, ...rest }) =>
    api_key === "__KEEP__" ? rest : { ...rest, api_key }
  );

  const body = {
    default_endpoint: $("#default_endpoint").value,
    max_keywords: parseInt($("#max_keywords").value, 10),
    concurrency: parseInt($("#concurrency").value, 10),
    endpoints: cleaned,
  };
  try {
    await jput("/api/config", body);
    setStatus("saved", "ok");
    await load();
  } catch (e) {
    setStatus(`save failed: ${e.message}`, "err");
  }
}

function bind() {
  $("#cfg-form").addEventListener("submit", (e) => { e.preventDefault(); save(); });
  $("#reload").addEventListener("click", load);
  $("#add-ep").addEventListener("click", () => addEndpointRow());
}

(async function init() {
  bind();
  await load();
})();
