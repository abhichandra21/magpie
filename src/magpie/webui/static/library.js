// magpie library — grid + PhotoSwipe lightbox.
import PhotoSwipeLightbox from "https://unpkg.com/photoswipe@5/dist/photoswipe-lightbox.esm.min.js";

const $ = (s, r = document) => r.querySelector(s);

async function jget(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function bytes(n) {
  if (!n && n !== 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

const state = {
  libraries: [],
  library: null,
  filter: "all",
  offset: 0,
  limit: 60,
  total: 0,
  loaded: 0,
  lightbox: null,
};

function loadStateFromUrl() {
  const p = new URLSearchParams(window.location.search);
  state.library = p.get("lib");
  state.filter = p.get("f") || "all";
}

function saveStateToUrl() {
  const p = new URLSearchParams();
  if (state.library) p.set("lib", state.library);
  if (state.filter && state.filter !== "all") p.set("f", state.filter);
  const q = p.toString();
  history.replaceState(null, "", q ? `?${q}` : "/library");
}

async function loadLibraries() {
  const { libraries } = await jget("/api/libraries");
  state.libraries = libraries || [];

  const chips = $("#lib-chips");
  chips.innerHTML = "";
  if (!state.libraries.length) {
    $("#empty-hint").hidden = false;
    $("#gallery").hidden = true;
    $("#library-meta").textContent = "";
    return;
  }
  $("#empty-hint").hidden = true;
  $("#gallery").hidden = false;
  state.libraries.forEach((lib, i) => {
    const el = document.createElement("button");
    el.type = "button";
    el.className = "lib-chip";
    if (!lib.exists) el.classList.add("missing");
    const label = lib.name;
    const count = lib.exists
      ? `${lib.count.toLocaleString()} photo${lib.count === 1 ? "" : "s"}`
      : "missing";
    el.innerHTML = `<span>${label}</span><span class="count">${count}</span>`;
    el.addEventListener("click", () => {
      if (!lib.exists) return;
      state.library = lib.name;
      state.offset = 0;
      state.loaded = 0;
      saveStateToUrl();
      paintChips();
      loadPage(true);
    });
    chips.appendChild(el);
  });

  // pick saved library, or the first with content
  if (!state.library || !state.libraries.some((l) => l.name === state.library)) {
    const first = state.libraries.find((l) => l.exists) || state.libraries[0];
    state.library = first ? first.name : null;
  }
  paintChips();
  if (state.library) loadPage(true);
}

function paintChips() {
  $("#lib-chips").querySelectorAll(".lib-chip").forEach((el, i) => {
    el.classList.toggle("active", state.libraries[i].name === state.library);
  });
  $(".lib-filter").querySelectorAll(".fbtn").forEach((el) => {
    el.classList.toggle("active", el.dataset.filter === state.filter);
  });
}

async function loadPage(reset) {
  if (!state.library) return;
  if (reset) {
    $("#gallery").innerHTML = "";
    state.offset = 0;
    state.loaded = 0;
  }
  const u = new URL(`/api/library/${encodeURIComponent(state.library)}`, location.origin);
  u.searchParams.set("offset", state.offset);
  u.searchParams.set("limit", state.limit);
  u.searchParams.set("filter", state.filter);

  $("#library-meta").textContent = "loading…";
  let data;
  try {
    data = await jget(u.toString());
  } catch (e) {
    $("#library-meta").textContent = `error: ${e.message}`;
    return;
  }

  state.total = data.meta.total;
  state.offset += state.limit;

  const gallery = $("#gallery");
  for (const item of data.items) {
    const a = document.createElement("a");
    a.href = `/api/image?path=${encodeURIComponent(item.path)}`;
    a.target = "_blank";
    a.rel = "noopener";
    // PhotoSwipe needs original dimensions; fall back to a 3:2 box if unknown.
    let w = item.width || 0;
    let h = item.height || 0;
    // /api/image caps the long edge at 2048; rescale dims accordingly so PS layout
    // matches the actual served bitmap.
    const long = Math.max(w, h);
    if (long > 2048 && long > 0) {
      const k = 2048 / long;
      w = Math.round(w * k);
      h = Math.round(h * k);
    }
    if (!w || !h) { w = 2048; h = 1365; }
    a.dataset.pswpWidth = w;
    a.dataset.pswpHeight = h;
    a.innerHTML = `
      <img src="/api/thumb?path=${encodeURIComponent(item.path)}" alt="${escapeHtml(item.caption || item.name)}">
      <span class="badge ${item.tagged ? "tagged" : "untagged"}">${item.tagged ? "tagged" : "untagged"}</span>
      <div class="tile-cap">${escapeHtml(item.caption || item.name)}</div>
    `;
    a.dataset.caption = item.caption || "";
    a.dataset.keywords = (item.keywords || []).join("|");
    a.dataset.rel = item.rel;
    a.dataset.size = bytes(item.size || 0);
    gallery.appendChild(a);
    state.loaded += 1;
  }

  const metaText = `${state.library}  ·  ${state.loaded.toLocaleString()} of ${state.total.toLocaleString()} shown  ·  filter: ${state.filter}`;
  $("#library-meta").textContent = metaText;

  $("#load-more").hidden = state.offset >= state.total;
  ensureLightbox();
}

function ensureLightbox() {
  if (state.lightbox) return;
  state.lightbox = new PhotoSwipeLightbox({
    gallery: "#gallery",
    children: "a",
    pswpModule: () => import("https://unpkg.com/photoswipe@5/dist/photoswipe.esm.min.js"),
    bgOpacity: 0.96,
    showHideAnimationType: "fade",
  });
  state.lightbox.on("uiRegister", () => {
    state.lightbox.pswp.ui.registerElement({
      name: "magpie-caption",
      order: 9,
      isButton: false,
      appendTo: "root",
      html: "",
      onInit: (el) => {
        el.className = "pswp__caption";
        state.lightbox.pswp.on("change", () => updateCaption(el));
      },
    });
  });
  state.lightbox.init();
}

function updateCaption(el) {
  const slide = state.lightbox.pswp.currSlide;
  if (!slide || !slide.data || !slide.data.element) {
    el.innerHTML = "";
    return;
  }
  const a = slide.data.element;
  const caption = a.dataset.caption || "";
  const keywords = (a.dataset.keywords || "").split("|").filter(Boolean);
  const rel = a.dataset.rel || "";
  const size = a.dataset.size || "";
  el.innerHTML = `
    <div class="pcap">${caption ? escapeHtml(caption) : "<em style='color:var(--muted);'>untagged</em>"}</div>
    ${keywords.length ? `<div class="pkw">${keywords.map((k) => `<span>${escapeHtml(k)}</span>`).join("")}</div>` : ""}
    <div class="pmeta">${escapeHtml(rel)} ${size ? "· " + size : ""}</div>
  `;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function bindFilters() {
  $(".lib-filter").addEventListener("click", (e) => {
    const btn = e.target.closest(".fbtn");
    if (!btn) return;
    state.filter = btn.dataset.filter;
    saveStateToUrl();
    paintChips();
    loadPage(true);
  });
  $("#load-more-btn").addEventListener("click", () => loadPage(false));
}

(async function init() {
  loadStateFromUrl();
  bindFilters();
  await loadLibraries();
})();
