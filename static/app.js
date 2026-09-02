// Manga Fill dashboard — vanilla JS against the JSON API.

const $ = (s) => document.querySelector(s);
const TAB_KEY = "mangafill.tab";

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail || msg; } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function setActiveTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  try { localStorage.setItem(TAB_KEY, name); } catch {}
}

function switchTab(name) {
  setActiveTab(name);
  if (name === "logs") loadLogs();
  if (name === "settings") { loadSettings(); loadModels(); loadFonts(); }
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

// ---------- models (shared state) ----------
let modelMap = {};   // id -> full model dict

async function loadModels() {
  const models = await api("/api/models");
  modelMap = {};
  models.forEach((m) => (modelMap[m.id] = m));
  renderModels(models);
  renderModelSelect(models);
}

function modelFields(prefix, m) {
  const v = (x) => esc(x == null ? "" : x);
  return `
    <label class="field"><span>Model id</span>
      <input type="text" id="${prefix}-name" placeholder="e.g. gpt-4o-mini" value="${v(m.name)}"></label>
    <label class="field"><span>API base URL</span>
      <input type="text" id="${prefix}-base" placeholder="https://api.openai.com/v1" value="${v(m.base_url)}"></label>
    <label class="field"><span>API key</span>
      <input type="password" id="${prefix}-key" placeholder="sk-…" autocomplete="off" value="${v(m.api_key)}"></label>
    <div class="pricing">
      <div class="pricing-title">Pricing — $/1M tokens (peak = standard rate; off-peak optional)</div>
      <div class="pricing-grid">
        <label class="field"><span>Input (peak)</span>
          <input type="number" step="any" min="0" id="${prefix}-price-in" value="${v(m.price_in ?? 0)}"></label>
        <label class="field"><span>Output (peak)</span>
          <input type="number" step="any" min="0" id="${prefix}-price-out" value="${v(m.price_out ?? 0)}"></label>
        <label class="field"><span>Input (off-peak)</span>
          <input type="number" step="any" min="0" id="${prefix}-offpeak-in" value="${v(m.offpeak_in)}"></label>
        <label class="field"><span>Output (off-peak)</span>
          <input type="number" step="any" min="0" id="${prefix}-offpeak-out" value="${v(m.offpeak_out)}"></label>
        <label class="field"><span>Off-peak start (UTC)</span>
          <input type="time" id="${prefix}-offpeak-start" value="${v(m.offpeak_start)}"></label>
        <label class="field"><span>Off-peak end (UTC)</span>
          <input type="time" id="${prefix}-offpeak-end" value="${v(m.offpeak_end)}"></label>
      </div>
    </div>`;
}

function collectModelPayload(prefix) {
  const g = (k) => $(`#${prefix}-${k}`).value.trim();
  const num = (k) => { const s = g(k); return s === "" ? null : (parseFloat(s) || 0); };
  return {
    name: g("name"),
    base_url: g("base"),
    api_key: g("key"),
    price_in: parseFloat(g("price-in")) || 0,
    price_out: parseFloat(g("price-out")) || 0,
    offpeak_in: num("offpeak-in"),
    offpeak_out: num("offpeak-out"),
    offpeak_start: g("offpeak-start") || null,
    offpeak_end: g("offpeak-end") || null,
  };
}

function renderModels(models) {
  const el = $("#models-list");
  if (!models.length) {
    el.innerHTML = '<p class="muted">No models yet — add one to start translating.</p>';
    return;
  }
  el.innerHTML = models.map((m) => `
    <div class="model-card">
      <div class="model-row">
        <div class="model-info">
          <span class="model-name">${esc(m.name)}</span>
          <span class="muted model-base">${esc(m.base_url)}</span>
          <span class="${m.api_key ? "ok" : "warn"}">${m.api_key ? "key set" : "no key"}</span>
        </div>
        <div class="model-row-actions">
          <button class="btn small model-toggle" id="toggle-${m.id}" onclick="toggleModel(${m.id})" title="Edit model">▾</button>
          <button class="btn small danger model-remove" onclick="removeModel(${m.id})" title="Remove model">−</button>
        </div>
      </div>
      <div class="model-edit hidden" id="model-edit-${m.id}">
        ${modelFields("model-edit-" + m.id, m)}
        <div class="toolbar">
          <button class="btn primary" onclick="saveModel(${m.id})">Save</button>
          <span id="model-status-${m.id}" class="muted"></span>
        </div>
      </div>
    </div>
  `).join("");
}

function renderModelSelect(models) {
  const sel = $("#upload-model");
  const prev = sel.value;
  sel.innerHTML = models.map((m) => `<option value="${m.id}">${esc(m.name)}</option>`).join("");
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  else if (models.length) sel.value = String(models[0].id);
  if (!models.length) sel.innerHTML = '<option value="">(no models — add one in Settings)</option>';
}

function toggleModel(id) {
  const edit = $(`#model-edit-${id}`);
  const btn = $(`#toggle-${id}`);
  edit.classList.toggle("hidden");
  btn.textContent = edit.classList.contains("hidden") ? "▾" : "▴";
}

async function saveModel(id) {
  const payload = collectModelPayload("model-edit-" + id);
  if (!payload.name || !payload.base_url) {
    alert("Model id and API base URL are required.");
    return;
  }
  const status = $(`#model-status-${id}`);
  status.textContent = "Saving…";
  try {
    await api("/api/models/" + id, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status.textContent = "Saved.";
    loadModels();
  } catch (err) {
    status.textContent = "Error: " + err.message;
  }
}

async function removeModel(id) {
  const m = modelMap[id];
  if (!confirm(`Remove model "${m ? m.name : id}"?`)) return;
  await fetch("/api/models/" + id, { method: "DELETE" });
  loadModels();
}

// Add-model editor (populated in JS, shares fields with the per-model edit forms).
$("#model-add").addEventListener("click", () => {
  $("#model-editor").innerHTML = `
    ${modelFields("model-new", {})}
    <div class="toolbar">
      <button class="btn primary" id="model-save">Save model</button>
      <button class="btn" id="model-cancel">Cancel</button>
    </div>`;
  $("#model-editor").classList.remove("hidden");
  $("#model-new-name").focus();
});

$("#model-editor").addEventListener("click", async (e) => {
  if (e.target.id === "model-cancel") {
    $("#model-editor").classList.add("hidden");
    return;
  }
  if (e.target.id !== "model-save") return;
  const payload = collectModelPayload("model-new");
  if (!payload.name || !payload.base_url) {
    alert("Model id and API base URL are required.");
    return;
  }
  try {
    await api("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#model-editor").classList.add("hidden");
    loadModels();
  } catch (err) {
    alert("Error: " + err.message);
  }
});

// ---------- jobs ----------
const BADGE = { queued: "queued", running: "running", done: "done", partial: "partial", failed: "failed" };

function renderJobs(jobs) {
  const el = $("#jobs-list");
  if (!jobs.length) {
    el.innerHTML = '<p class="muted">No jobs yet — upload some pages.</p>';
    return;
  }
  el.innerHTML = jobs.map((j) => {
    const pct = j.pages_total ? Math.round((100 * j.pages_done) / j.pages_total) : 0;
    const viewable = j.status === "done" || j.status === "partial";
    const modelName = modelMap[j.model_id] ? modelMap[j.model_id].name : "default";
    return `
      <div class="job ${j.error ? "error-box" : ""}">
        <div class="job-head">
          <span class="job-name">${esc(j.name)}</span>
          <span class="badge ${BADGE[j.status] || "queued"}">${esc(j.status)}</span>
          <span class="badge muted">${esc(modelName)}</span>
          <span class="muted" style="margin-left:auto">#${j.id}</span>
        </div>
        <div class="progress"><span style="width:${pct}%"></span></div>
        <div class="job-meta">
          <span>${j.pages_done}/${j.pages_total} pages</span>
          <span>${j.blocks_found} blocks &middot; ${j.blocks_ok} translated</span>
          <span>${j.tokens_used} tok</span>
          <span>$${(j.cost_usd || 0).toFixed(6)}</span>
        </div>
        ${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ""}
        <div class="job-actions">
          ${viewable ? `<button class="btn small" onclick="openViewer(${j.id})">View</button>` : ""}
          ${viewable ? `<a class="btn small" href="/api/jobs/${j.id}/download">Download</a>` : ""}
          <button class="btn small" onclick="deleteJob(${j.id})">Delete</button>
        </div>
      </div>`;
  }).join("");
}

async function loadJobs() {
  renderJobs(await api("/api/jobs"));
}

async function deleteJob(id) {
  if (!confirm("Delete this job?")) return;
  await fetch("/api/jobs/" + id, { method: "DELETE" });
  loadJobs();
}

$("#refresh-jobs").addEventListener("click", loadJobs);

// ---------- upload ----------
$("#upload-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const files = $("#upload-files").files;
  if (!files.length) return;
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  fd.append("name", $("#upload-name").value);
  fd.append("output_mode", $("#upload-mode").value);
  const modelId = $("#upload-model").value;
  if (modelId) fd.append("model_id", modelId);
  const status = $("#upload-status");
  status.textContent = "Uploading\u2026";
  try {
    const job = await api("/api/jobs", { method: "POST", body: fd });
    status.textContent = `Job #${job.id} queued (${job.pages_total} pages).`;
    $("#upload-form").reset();
    switchTab("jobs");
    loadJobs();
  } catch (err) {
    status.textContent = "Error: " + err.message;
  }
});

// ---------- settings ----------
async function loadSettings() {
  const s = await api("/api/settings");
  $("#settings-mode").value = s.output_mode;
  // Apply the saved default output mode to the upload form too (the user can
  // still override it per job).
  $("#upload-mode").value = s.output_mode;
  $("#settings-dry-run").checked = s.dry_run === "true";
}

// ---------- fonts ----------
async function loadFonts() {
  const data = await api("/api/fonts");
  const selected = data.selected;
  $("#fonts-list").innerHTML = data.fonts.map((f) => {
    const disabled = !f.available;
    const badge = f.default ? ' <span class="badge">default</span>' : "";
    const swatch = f.available
      ? `<img class="font-preview" src="/api/fonts/preview/${f.id}" alt="${esc(f.name)} preview">`
      : `<div class="font-preview placeholder">unavailable</div>`;
    const checked = f.id === selected && !disabled;
    return `
      <label class="font-card${disabled ? " unavailable" : ""}">
        <input type="radio" name="font" value="${f.id}" ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
        ${swatch}
        <div class="font-info">
          <span class="font-name">${esc(f.name)}${badge}</span>
          <span class="font-style">${esc(f.style)}</span>
          <span class="font-license">${esc(f.license)}</span>
        </div>
      </label>`;
  }).join("");
  // Surface the effective fallback when the selected face is unavailable.
  const note = $("#fonts-note");
  if (data.resolved && data.resolved !== data.selected) {
    const effective = (data.fonts.find((f) => f.id === data.resolved) || {}).name || "DejaVu";
    note.textContent = "Selected font unavailable — using " + effective + " instead.";
  } else {
    note.textContent = "";
  }
  document.querySelectorAll('input[name="font"]').forEach((r) => {
    r.addEventListener("change", () => saveFont(r.value));
  });
}

async function saveFont(id) {
  const status = $("#fonts-status");
  status.textContent = "Saving\u2026";
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ font: id }),
    });
    status.textContent = "Saved.";
    setTimeout(() => (status.textContent = ""), 1500);
  } catch (err) {
    status.textContent = "Error: " + err.message;
  }
}

$("#settings-save").addEventListener("click", async () => {
  const payload = {
    output_mode: $("#settings-mode").value,
    dry_run: $("#settings-dry-run").checked ? "true" : "false",
  };
  const status = $("#settings-status");
  status.textContent = "Saving\u2026";
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    status.textContent = "Saved.";
    setTimeout(() => (status.textContent = ""), 2000);
  } catch (err) {
    status.textContent = "Error: " + err.message;
  }
});

// ---------- logs ----------
async function loadLogs() {
  const lines = $("#logs-lines").value;
  const r = await fetch("/api/logs?lines=" + lines);
  $("#logs-view").textContent = await r.text();
}
$("#refresh-logs").addEventListener("click", loadLogs);
$("#logs-lines").addEventListener("change", loadLogs);
setInterval(() => {
  const logsTab = $("#tab-logs");
  if (logsTab.classList.contains("active") && $("#logs-auto").checked) loadLogs();
}, 5000);

// ---------- side-by-side viewer ----------
let viewerState = { jobId: null, index: 0, total: 0 };

async function openViewer(jobId, index = 0) {
  const job = await api("/api/jobs/" + jobId);
  const pages = job.pages || [];
  if (!pages.length) return;
  viewerState = { jobId, index: 0, total: pages.length };
  $("#viewer").classList.remove("hidden");
  showPage(Math.min(index, pages.length - 1));
}

function showPage(index) {
  if (index < 0 || index >= viewerState.total) return;
  viewerState.index = index;
  $("#viewer-title").textContent = `Job #${viewerState.jobId} \u2014 page ${index + 1}/${viewerState.total}`;
  $("#viewer-pos").textContent = `${index + 1} / ${viewerState.total}`;
  $("#viewer-orig").src = `/api/jobs/${viewerState.jobId}/pages/${index}/original`;
  $("#viewer-trans").src = `/api/jobs/${viewerState.jobId}/pages/${index}/translated`;
}

$("#viewer-prev").addEventListener("click", () => showPage(viewerState.index - 1));
$("#viewer-next").addEventListener("click", () => showPage(viewerState.index + 1));
$("#viewer-close").addEventListener("click", () => $("#viewer").classList.add("hidden"));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#viewer").classList.add("hidden");
  if (e.key === "ArrowLeft" && !$("#viewer").classList.contains("hidden")) showPage(viewerState.index - 1);
  if (e.key === "ArrowRight" && !$("#viewer").classList.contains("hidden")) showPage(viewerState.index + 1);
});

// ---------- boot ----------
setActiveTab(localStorage.getItem(TAB_KEY) || "jobs");
loadJobs();
loadSettings();
loadModels();
loadFonts();
setInterval(loadJobs, 2500);
