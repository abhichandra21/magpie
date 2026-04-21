// Server-backed directory picker. Mounts inline; calls onPick(path).

const $ = (s, r = document) => r.querySelector(s);

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) {
    const text = await r.text();
    let detail = text;
    try { detail = JSON.parse(text).detail || text; } catch {}
    throw new Error(detail);
  }
  return r.json();
}

function fmtBytes(n) {
  if (n == null) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export async function mountPicker(host, { startPath = "", onPick, onClose }) {
  const el = document.createElement("div");
  el.className = "picker";
  el.innerHTML = `
    <div class="picker-head">
      <span class="crumbs"></span>
      <span class="spacer"></span>
      <button type="button" class="pick-this">pick this folder</button>
      <button type="button" class="close-x" title="close (Esc)">×</button>
    </div>
    <div class="picker-body"></div>
  `;
  host.replaceChildren(el);

  const crumbs = el.querySelector(".crumbs");
  const body = el.querySelector(".picker-body");
  const pickThis = el.querySelector(".pick-this");
  let currentPath = startPath;

  async function load(path) {
    body.innerHTML = `<div class="picker-empty">loading…</div>`;
    let data;
    try {
      const u = new URL("/api/browse", window.location.origin);
      if (path) u.searchParams.set("path", path);
      data = await jget(u.toString());
    } catch (e) {
      body.innerHTML = `<div class="picker-empty">cannot list: ${e.message}</div>`;
      return;
    }
    currentPath = data.path;
    pickThis.dataset.path = data.path;

    crumbs.innerHTML = "";
    data.crumbs.forEach((c, i) => {
      if (i > 0) {
        const sep = document.createElement("span");
        sep.className = "picker-sep";
        sep.textContent = "/";
        crumbs.appendChild(sep);
      }
      const span = document.createElement("span");
      span.className = "picker-crumb";
      if (i === data.crumbs.length - 1) span.classList.add("current");
      span.textContent = c.name || "/";
      span.addEventListener("click", () => load(c.path));
      crumbs.appendChild(span);
    });

    body.innerHTML = "";
    if (!data.dirs.length && !data.files.length) {
      body.innerHTML = `<div class="picker-empty">empty folder</div>`;
      return;
    }
    data.dirs.forEach((d) => {
      const row = document.createElement("div");
      row.className = "picker-row dir";
      row.innerHTML = `<span class="icon">▸</span><span class="name">${d.name}</span>`;
      row.addEventListener("click", () => load(d.path));
      body.appendChild(row);
    });
    data.files.forEach((f) => {
      const row = document.createElement("div");
      row.className = "picker-row file";
      row.innerHTML = `<span class="icon">◎</span><span class="name">${f.name}</span><span class="sz">${fmtBytes(f.size)}</span>`;
      row.addEventListener("click", () => onPick(f.path));
      body.appendChild(row);
    });
  }

  pickThis.addEventListener("click", () => onPick(currentPath));
  el.querySelector(".close-x").addEventListener("click", () => onClose());
  document.addEventListener("keydown", function escClose(e) {
    if (e.key === "Escape") {
      onClose();
      document.removeEventListener("keydown", escClose);
    }
  });

  await load(startPath);
}
