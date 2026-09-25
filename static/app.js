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
  if (name === "settings") { loadSettings(); loadModels(); loadFonts(); loadGpu(); }
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
      <input type="password" id="${prefix}-key" placeholder="${m.api_key_set ? "leave blank to keep the stored key" : "sk-…"}" autocomplete="off" value="${m.api_key_set ? "" : v(m.api_key)}"></label>
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
          <span class="${m.api_key_set ? "ok" : "warn"}" title="${m.api_key_set ? v(m.api_key) : ""}">${m.api_key_set ? "key set" : "no key"}</span>
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
const BADGE = { queued: "queued", running: "running", done: "done", partial: "partial", failed: "failed", paused: "paused", cancelled: "cancelled" };
// Fractional progress of each pipeline stage within the current page, so the bar
// advances smoothly during a single page instead of jumping page-by-page.
const STAGE_POS = { detect: 0.05, ocr: 0.3, translate: 0.8, inpaint: 0.95, typeset: 1.0 };
const STAGE_LABEL = { detect: "Detecting", ocr: "Reading text", translate: "Translating", inpaint: "Cleaning", typeset: "Typesetting" };

function renderJobs(jobs) {
  const el = $("#jobs-list");
  if (!jobs.length) {
    el.innerHTML = '<p class="muted">No jobs yet — upload some pages.</p>';
    return;
  }
  el.innerHTML = jobs.map((j) => {
    const terminal = ["done", "partial", "failed", "cancelled"].includes(j.status);
    let pct;
    if (terminal) {
      pct = j.pages_total ? Math.round((100 * j.pages_done) / j.pages_total) : 0;
    } else {
      const stagePos = STAGE_POS[j.stage] || 0;
      pct = j.pages_total ? Math.round((100 * (j.pages_done + stagePos)) / j.pages_total) : 0;
    }
    const modelName = modelMap[j.model_id] ? modelMap[j.model_id].name : "default";
    const pageLabel = (j.status === "running" && j.stage)
      ? `<span class="stage">${STAGE_LABEL[j.stage] || esc(j.stage)} · page ${j.pages_done + 1}/${j.pages_total}</span>`
      : `<span>${j.pages_done}/${j.pages_total} pages</span>`;
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
          ${pageLabel}
          <span>${j.blocks_found} blocks &middot; ${j.blocks_ok} translated</span>
          <span>${j.tokens_used} tok</span>
          <span>$${(j.cost_usd || 0).toFixed(6)}</span>
        </div>
        ${j.error ? `<div class="job-error">${esc(j.error)}</div>` : ""}
        <div class="job-actions">${jobActions(j)}</div>
      </div>`;
  }).join("");
}

async function loadJobs() {
  renderJobs(await api("/api/jobs"));
}

// ---- cost tally (total cost for the selected period; defaults to Day) --------
let tallyPeriod = "day";
const TALLY_LABEL = { day: "today", week: "this week", month: "this month", year: "this year" };

async function loadTally() {
  let t;
  try {
    t = await api("/api/stats/cost?period=" + encodeURIComponent(tallyPeriod));
  } catch (e) {
    $("#tally-cost").textContent = "—";
    $("#tally-detail").textContent = "cost tally unavailable";
    return;
  }
  const w = t.window || {};
  const all = t.all_time || {};
  $("#tally-cost").textContent = "$" + (w.cost_usd || 0).toFixed(6);
  $("#tally-window").textContent = TALLY_LABEL[t.period] || t.period;
  $("#tally-detail").textContent =
    `${w.jobs || 0} job${w.jobs === 1 ? "" : "s"} · ${w.pages_done || 0} pages · ` +
    `${w.tokens || 0} tok  —  all time $${(all.cost_usd || 0).toFixed(6)} ` +
    `(${all.tokens || 0} tok, ${all.jobs || 0} jobs)` +
    ((w.tokens || 0) > 0 && !(w.cost_usd > 0)
      ? "  ·  model rates are 0, so cost reads $0 — set $/1M tokens in Settings" : "");
}

async function deleteJob(id) {
  if (!confirm("Delete this job?")) return;
  await fetch("/api/jobs/" + id, { method: "DELETE" });
  loadJobs();
}

function jobActions(j) {
  const b = [];
  if (j.status === "running") b.push(`<button class="btn small" onclick="pauseJob(${j.id})">Pause</button>`);
  if (["paused", "cancelled", "failed"].includes(j.status)) b.push(`<button class="btn small" onclick="startJob(${j.id})">Start</button>`);
  // A "partial" job has failed pages that Resume will retry (done pages are skipped).
  if (j.status === "partial") b.push(`<button class="btn small" onclick="startJob(${j.id})">Resume</button>`);
  if (["queued", "running", "paused"].includes(j.status)) b.push(`<button class="btn small" onclick="stopJob(${j.id})">Stop</button>`);
  if (j.status === "done" || j.status === "partial") {
    b.push(`<button class="btn small" onclick="openViewer(${j.id})">View</button>`);
    b.push(`<a class="btn small" href="/api/jobs/${j.id}/download">Download</a>`);
    b.push(`<button class="btn small" onclick="rerenderAll(${j.id})">Re-render all</button>`);
  }
  b.push(`<button class="btn small" onclick="deleteJob(${j.id})">Delete</button>`);
  return b.join("");
}

async function jobAction(id, action) {
  await api(`/api/jobs/${id}/${action}`, { method: "POST" });
  loadJobs();
}
function startJob(id) { jobAction(id, "start"); }
function pauseJob(id) { jobAction(id, "pause"); }
function stopJob(id) { jobAction(id, "stop"); }

// Re-render EVERY page with the current pipeline — the way to apply a pipeline
// fix to a job produced by older code. Re-spends tokens and re-runs every page,
// so it is confirmed first.
async function rerenderAll(id) {
  if (!confirm("Re-render EVERY page of this job with the current pipeline?\n\n"
    + "Finished pages are redone from scratch — this spends API tokens again and "
    + "can take a long time.")) return;
  await api(`/api/jobs/${id}/rerender`, { method: "POST" });
  loadJobs();
}

async function clearAllJobs() {
  if (!confirm("Delete ALL jobs and their files? This cannot be undone.")) return;
  await api("/api/jobs", { method: "DELETE" });
  loadJobs();
}

$("#refresh-jobs").addEventListener("click", loadJobs);
$("#clear-all-jobs").addEventListener("click", clearAllJobs);

// ---------- upload ----------
// Dropzone: the area is the visible affordance; the file input sits transparently
// on top of it (so a click anywhere opens the picker and `required` can still be
// focused). Drop handling is explicit so the highlight and the file list are
// deterministic across browsers.
const dropzone = $("#upload-dropzone");
const fileInput = $("#upload-files");
const fileListEl = $("#upload-filelist");
const UPLOAD_EXTS = [".jpg", ".jpeg", ".png", ".webp", ".cbz", ".zip"];

function renderUploadFileList() {
  const files = fileInput.files;
  if (!files || !files.length) {
    fileListEl.innerHTML = "";
    return;
  }
  const names = Array.from(files).map((f) => f.name);
  const shown = names.slice(0, 6);
  const extra = names.length - shown.length;
  fileListEl.innerHTML =
    `<div class="dropzone-count">${files.length} file${files.length === 1 ? "" : "s"} selected</div>` +
    `<ul>${shown.map((n) => `<li>${esc(n)}</li>`).join("")}` +
    (extra > 0 ? `<li class="dropzone-more">+${extra} more</li>` : "") +
    `</ul>`;
}

fileInput.addEventListener("change", renderUploadFileList);

["dragenter", "dragover"].forEach((ev) =>
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    e.stopPropagation();
    dropzone.classList.add("dragover");
  })
);

dropzone.addEventListener("dragleave", (e) => {
  e.preventDefault();
  e.stopPropagation();
  if (!dropzone.contains(e.relatedTarget)) dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", (e) => {
  e.preventDefault();
  e.stopPropagation();
  dropzone.classList.remove("dragover");
  const dropped = Array.from((e.dataTransfer && e.dataTransfer.files) || []);
  if (!dropped.length) return;
  const ok = dropped.filter((f) => UPLOAD_EXTS.some((x) => f.name.toLowerCase().endsWith(x)));
  const skipped = dropped.length - ok.length;
  if (!ok.length) {
    $("#upload-status").textContent = `Unsupported file type — use ${UPLOAD_EXTS.join(" ")}.`;
    return;
  }
  const dt = new DataTransfer();
  ok.forEach((f) => dt.items.add(f));
  fileInput.files = dt.files;
  renderUploadFileList();
  $("#upload-status").textContent = skipped
    ? `${skipped} file(s) skipped (unsupported type).` : "";
});

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
    renderUploadFileList();  // reset() fires no change event, so clear the list here
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
  $("#settings-source-lang").value = s.source_lang || "auto";
  $("#settings-llm-timeout").value = s.llm_timeout || "300";
  $("#settings-llm-retries").value = s.llm_max_retries != null ? s.llm_max_retries : "2";
  $("#settings-provider-fail-limit").value =
    s.provider_fail_limit != null ? s.provider_fail_limit : "3";
  $("#settings-caption-vlm").value = s.caption_vlm === "false" ? "false" : "true";
  // Output language (engine's list; English default) + the engine/Advanced sections.
  if ($("#settings-target-lang")) $("#settings-target-lang").value = s.target_lang || "ENG";
  loadEngineUI(s);
  loadAdvancedUI(s);
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
    source_lang: $("#settings-source-lang").value,
    llm_timeout: $("#settings-llm-timeout").value || "300",
    llm_max_retries: $("#settings-llm-retries").value || "0",
    provider_fail_limit: $("#settings-provider-fail-limit").value || "3",
    caption_vlm: $("#settings-caption-vlm").value || "true",
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

// ---------- gpu ----------
async function loadGpu() {
  const g = await api("/api/gpu");
  $("#gpu-worker-url").value = g.worker_url || "";
  const dev = $("#gpu-device");
  if (dev) dev.value = g.device || "auto";
  const badge = $("#gpu-status-badge");
  let label, cls;
  if (g.worker_url) {
    label = g.status === "connected" ? "External GPU" : "External GPU (down)";
    cls = g.status === "connected" ? "done" : "failed";
  } else if (g.effective_device === "cuda") {
    label = g.backend === "rocm" ? "Local GPU (ROCm)" : "Local GPU (CUDA)";
    cls = "done";
  } else {
    label = g.cuda_available ? "CPU (set device=cuda)" : "CPU only";
    cls = "muted";
  }
  badge.textContent = label;
  badge.className = "badge " + cls;
  const res = $("#gpu-resolved");
  if (res) {
    const parts = ["Effective device: " + g.effective_device];
    if (g.cuda_available) parts.push("CUDA available · backend: " + g.backend);
    res.textContent = parts.join(" — ");
  }
}

async function saveGpu() {
  const url = $("#gpu-worker-url").value.trim();
  const device = $("#gpu-device").value;
  const status = $("#gpu-status");
  status.textContent = "Saving\u2026";
  try {
    await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gpu_worker_url: url, device: device }),
    });
    status.textContent = "Saved.";
    setTimeout(() => (status.textContent = ""), 1500);
    loadGpu();
  } catch (err) {
    status.textContent = "Error: " + err.message;
  }
}
$("#gpu-save").addEventListener("click", saveGpu);

// ---------- logs ----------
// Several surfaces now (main / access / lifecycle / engine / engine-calls / errors); the
// picker chooses which one, and the default stays exactly what it always was: the main log.
async function loadLogs() {
  const lines = $("#logs-lines").value;
  const file = ($("#logs-file") && $("#logs-file").value) || "mangafill";
  const url = file === "mangafill"
    ? `/api/logs?lines=${lines}`
    : `/api/logs/view?file=${encodeURIComponent(file)}&lines=${lines}`;
  try {
    const r = await fetch(url);
    $("#logs-view").textContent = r.ok ? await r.text() : `log unavailable (HTTP ${r.status})`;
  } catch (e) {
    $("#logs-view").textContent = `log unavailable: ${e.message}`;
  }
  const dl = $("#logs-download");
  if (dl) {
    dl.href = file === "mangafill"
      ? `/api/logs?lines=100000`
      : `/api/logs/view?file=${encodeURIComponent(file)}&lines=100000`;
    dl.setAttribute("download", `${file}.log`);
  }
  renderLogFileSummary();
}

// One line per surface: size + last write, so an empty/stale file is obvious at a glance.
async function renderLogFileSummary() {
  const el = $("#logs-files");
  if (!el) return;
  try {
    const files = await api("/api/logs/files");
    el.innerHTML = files.map((f) => {
      const state = f.exists ? `${(f.size / 1024).toFixed(1)} KB · ${esc(f.modified || "")}` : "not written yet";
      return `<span class="log-file${f.name === (($("#logs-file") || {}).value || "mangafill") ? " current" : ""}">`
        + `<b>${esc(f.name)}</b> ${state}</span>`;
    }).join(" · ");
  } catch {
    el.textContent = "";
  }
}
$("#refresh-logs").addEventListener("click", loadLogs);
$("#logs-lines").addEventListener("change", loadLogs);
if ($("#logs-file")) $("#logs-file").addEventListener("change", loadLogs);
setInterval(() => {
  const logsTab = $("#tab-logs");
  if (logsTab.classList.contains("active") && $("#logs-auto").checked) loadLogs();
}, 5000);

// ---------- engine: URL + switch, and the Advanced tab (v0.28) ----------
// The ENGINE (upstream manga-image-translator service) runs the pipeline; these controls
// point the app at it and tune it. Everything here is optional — with the URL empty the
// app runs its own built-in pipeline exactly as it did before, and every Advanced value
// starts at the fixed value the app has always used.
let ENGINE_OPTIONS = null;

async function engineOptions() {
  if (!ENGINE_OPTIONS) ENGINE_OPTIONS = await api("/api/engine/options");
  return ENGINE_OPTIONS;
}

function fillSelect(sel, items, value) {
  if (!sel) return;
  sel.innerHTML = (items || []).map((o) => {
    // Items are either bare values (the engine's size lists: 1024/1536/…) or
    // {value,label} pairs (detectors, inpainters, languages) — handle both.
    const isObj = o !== null && typeof o === "object";
    const v = isObj ? o.value : o;
    const l = isObj ? o.label : o;
    const selected = String(v) === String(value) ? " selected" : "";
    return `<option value="${esc(String(v))}"${selected}>${esc(String(l))}</option>`;
  }).join("");
}

async function loadEngineUI(prefetched) {
  const s = prefetched || await api("/api/settings");
  if ($("#engine-url")) $("#engine-url").value = s.engine_url || "";
  if ($("#engine-enabled")) $("#engine-enabled").checked = s.engine_enabled === "true";
  const o = await engineOptions();
  fillSelect($("#settings-target-lang"), o.target_languages, s.target_lang || "ENG");
  const st = await api("/api/engine/status").catch(() => null);
  const badge = $("#engine-status-badge");
  if (badge && st) {
    badge.textContent = st.active
      ? (st.reachable ? `running${st.queue != null ? ` · queue ${st.queue}` : ""}` : "configured, unreachable")
      : (st.url ? "configured but off" : "not configured");
    badge.classList.toggle("ok", !!st.active && !!st.reachable);
    badge.classList.toggle("warn", !!st.url && (!st.active || !st.reachable));
    if ($("#engine-resolved")) {
      $("#engine-resolved").textContent = st.active
        ? `Pages are translated by ${st.url} (${st.reachable ? "reachable" : "NOT reachable: " + (st.detail || "?")}).`
        : `Using the built-in pipeline.${st.url ? " Engine URL is set but the switch is off." : ""}`;
    }
  }
}

async function saveEngine() {
  const status = $("#engine-status");
  try {
    await api("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        engine_url: $("#engine-url").value.trim(),
        engine_enabled: $("#engine-enabled").checked ? "true" : "false",
      }),
    });
    if (status) status.textContent = "saved";
    await loadEngineUI();
  } catch (e) {
    if (status) status.textContent = "save failed: " + e.message;
  }
}

async function testEngine() {
  const status = $("#engine-status");
  if (status) status.textContent = "testing…";
  try {
    const h = await api("/api/engine/probe");
    if (status) status.textContent = h.ok
      ? `OK${h.queue != null ? ` · queue ${h.queue}` : ""} · ${h.detail}`
      : `FAILED · ${h.detail}`;
  } catch (e) {
    if (status) status.textContent = "FAILED · " + e.message;
  }
  await loadEngineUI();
}

async function loadAdvancedUI(prefetched) {
  const s = prefetched || await api("/api/settings");
  const o = await engineOptions();
  const d = o.defaults;
  fillSelect($("#engine-lettering"), o.lettering_modes, s.engine_lettering || "ours");
  fillSelect($("#engine-second-pass"), o.second_pass_modes, s.engine_second_pass || "off");
  fillSelect($("#engine-preset-mode"), o.preset_modes, s.engine_preset_mode || "per-language");
  renderPresetNote(o, s.engine_preset_mode || "per-language");
  fillSelect($("#engine-detector"), o.detectors, s.engine_detector || d.detector);
  fillSelect($("#engine-detection-size"), o.detection_sizes, s.engine_detection_size || d.detection_size);
  fillSelect($("#engine-render-direction"), o.render_directions, s.engine_render_direction || d.render_direction);
  fillSelect($("#engine-inpainter"), o.inpainters, s.engine_inpainter || d.inpainter);
  fillSelect($("#engine-inpainting-size"), o.inpainting_sizes, s.engine_inpainting_size || d.inpainting_size);
  const setNum = (sel, v) => { if ($(sel)) $(sel).value = v; };
  setNum("#engine-box-threshold", s.engine_box_threshold || d.box_threshold);
  setNum("#engine-unclip-ratio", s.engine_unclip_ratio || d.unclip_ratio);
  setNum("#engine-mask-dilation", s.engine_mask_dilation_offset || d.mask_dilation_offset);
}

// The measured presets come from the engine options endpoint, so the UI never has to
// repeat the numbers: it says what is applied and, when they are on, that the preset wins.
function renderPresetNote(o, mode) {
  const note = $("#engine-preset-note");
  if (!note) return;
  const presets = o.presets || [];
  if (!presets.length) { note.textContent = ""; return; }
  const list = presets.map(p => `${p.lang}: ${p.name}`).join(" · ");
  note.textContent = mode === "off"
    ? `Presets are off — every job uses the values below. Available if you want them: ${list}`
    : `Measured per source language, and they override the values below for the languages `
      + `they cover: ${list}. Jobs left on "auto" detect their language once so the right `
      + `preset applies; the choice is named in each job's log line.`;
}

async function saveAdvanced() {
  const status = $("#advanced-status");
  const payload = {
    engine_lettering: $("#engine-lettering").value,
    engine_second_pass: $("#engine-second-pass").value,
    engine_preset_mode: $("#engine-preset-mode").value,
    engine_detector: $("#engine-detector").value,
    engine_detection_size: $("#engine-detection-size").value,
    engine_render_direction: $("#engine-render-direction").value,
    engine_inpainter: $("#engine-inpainter").value,
    engine_inpainting_size: $("#engine-inpainting-size").value,
    engine_box_threshold: $("#engine-box-threshold").value,
    engine_unclip_ratio: $("#engine-unclip-ratio").value,
    engine_mask_dilation_offset: $("#engine-mask-dilation").value,
  };
  try {
    await api("/api/settings", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (status) status.textContent = "saved";
  } catch (e) {
    if (status) status.textContent = "save failed: " + e.message;
  }
}

if ($("#engine-preset-mode")) {
  $("#engine-preset-mode").addEventListener("change", async () => {
    renderPresetNote(await engineOptions(), $("#engine-preset-mode").value);
  });
}

async function resetAdvanced() {
  const o = await engineOptions();
  const d = o.defaults;
  $("#engine-preset-mode").value = "per-language";
  renderPresetNote(o, "per-language");
  $("#engine-detector").value = d.detector;
  $("#engine-detection-size").value = String(d.detection_size);
  $("#engine-render-direction").value = d.render_direction;
  $("#engine-inpainter").value = d.inpainter;
  $("#engine-inpainting-size").value = String(d.inpainting_size);
  $("#engine-box-threshold").value = d.box_threshold;
  $("#engine-unclip-ratio").value = d.unclip_ratio;
  $("#engine-mask-dilation").value = d.mask_dilation_offset;
  await saveAdvanced();
}

if ($("#engine-save")) $("#engine-save").addEventListener("click", saveEngine);
if ($("#engine-test")) $("#engine-test").addEventListener("click", testEngine);
if ($("#advanced-save")) $("#advanced-save").addEventListener("click", saveAdvanced);
if ($("#advanced-reset")) $("#advanced-reset").addEventListener("click", resetAdvanced);
// The main "Save settings" button also carries the output language (the engine's list).
if ($("#settings-save")) {
  $("#settings-save").addEventListener("click", async () => {
    if (!$("#settings-target-lang")) return;
    try {
      await api("/api/settings", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_lang: $("#settings-target-lang").value }),
      });
    } catch {}
  });
}

// ---------- paste an image straight into the upload queue (upstream has this too) ------
window.addEventListener("paste", (e) => {
  const items = (e.clipboardData && e.clipboardData.items) || [];
  const pasted = [];
  for (const it of items) {
    if (it.kind === "file" && /^image\//.test(it.type)) {
      const f = it.getAsFile();
      if (f) pasted.push(f);
    }
  }
  if (!pasted.length || !fileInput) return;
  e.preventDefault();
  const dt = new DataTransfer();
  Array.from(fileInput.files || []).forEach((f) => dt.items.add(f));
  pasted.forEach((f) => dt.items.add(f));
  fileInput.files = dt.files;
  if (typeof renderUploadFileList === "function") renderUploadFileList();
  const st = $("#upload-status");
  if (st) st.textContent = `pasted ${pasted.length} image${pasted.length > 1 ? "s" : ""} into the queue`;
});

// Populate the engine/Advanced/language controls on load (loadSettings also refreshes them).
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", () => { loadEngineUI().catch(() => {}); });
} else {
  setTimeout(() => { loadEngineUI().catch(() => {}); }, 300);
}

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
  $("#viewer-pos").textContent = `/ ${viewerState.total}`;
  // The page number is an editable box: it always shows the page on screen, so a
  // jump is one click into the field + a number + Enter.
  const box = $("#viewer-page");
  if (box && document.activeElement !== box) box.value = index + 1;
  if (box) box.max = viewerState.total;
  $("#viewer-orig").src = `/api/jobs/${viewerState.jobId}/pages/${index}/original`;
  $("#viewer-trans").src = `/api/jobs/${viewerState.jobId}/pages/${index}/translated`;
}

// Jump to a typed page number (Enter or blur). Out-of-range input is clamped, and
// junk input restores the current page rather than blanking the box.
function viewerJump() {
  const box = $("#viewer-page");
  if (!box) return;
  const n = parseInt(box.value, 10);
  if (!Number.isFinite(n)) {
    box.value = viewerState.index + 1;
    return;
  }
  const target = Math.min(Math.max(n, 1), viewerState.total) - 1;
  showPage(target);
  box.value = target + 1;
}

$("#viewer-prev").addEventListener("click", () => showPage(viewerState.index - 1));
$("#viewer-next").addEventListener("click", () => showPage(viewerState.index + 1));
$("#viewer-page").addEventListener("change", viewerJump);
$("#viewer-page").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    viewerJump();
    e.target.blur();
  }
});
$("#viewer-close").addEventListener("click", () => $("#viewer").classList.add("hidden"));

// Re-render ONLY the page on screen, with the current pipeline. Queues the job
// (the worker renders it in the background), then polls until it settles and
// refreshes the translated pane so the fix is visible without a manual reload.
$("#viewer-rerender").addEventListener("click", async () => {
  const btn = $("#viewer-rerender");
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Queued\u2026";
  try {
    await api(`/api/jobs/${viewerState.jobId}/pages/${viewerState.index}/rerender`, { method: "POST" });
    for (let i = 0; i < 75; i++) {
      await new Promise((r) => setTimeout(r, 4000));
      showPage(viewerState.index); // re-points the pane at the (rewritten) file
      $("#viewer-trans").src =
        `/api/jobs/${viewerState.jobId}/pages/${viewerState.index}/translated?t=${Date.now()}`;
      let job = null;
      try { job = await api(`/api/jobs/${viewerState.jobId}`); } catch {}
      const pg = ((job && job.pages) || []).find((x) => x.index === viewerState.index);
      if (pg && pg.status === "done" && (!job || job.status !== "running")) break;
      if (pg && pg.status === "failed") break;
    }
    loadJobs();
  } catch (err) {
    alert("Re-render failed: " + err.message);
  }
  btn.disabled = false;
  btn.textContent = label;
});
document.addEventListener("keydown", (e) => {
  // Never hijack keys while the user is typing in a field (the page-number box
  // would otherwise page on every arrow key).
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
  if (e.key === "Escape") $("#viewer").classList.add("hidden");
  if (e.key === "ArrowLeft" && !$("#viewer").classList.contains("hidden")) showPage(viewerState.index - 1);
  if (e.key === "ArrowRight" && !$("#viewer").classList.contains("hidden")) showPage(viewerState.index + 1);
});

// ---------- boot ----------
const bootTab = localStorage.getItem(TAB_KEY) || "jobs";
setActiveTab(bootTab);
if (bootTab === "logs") loadLogs(); // restore log content on refresh, not just the tab
loadJobs();
loadSettings();
loadModels();
loadFonts();
loadGpu();
$("#tally-period").addEventListener("change", (e) => {
  tallyPeriod = e.target.value;
  loadTally();
});
loadTally();
setInterval(loadJobs, 2500);
setInterval(loadTally, 15000); // the tally moves only when a job finishes
