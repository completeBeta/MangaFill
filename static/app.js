// Manga Fill dashboard — vanilla JS against the JSON API.

const $ = (s) => document.querySelector(s);

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

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (name === "logs") loadLogs();
  if (name === "settings") loadSettings();
}

// ---------- tabs ----------
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
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
    return `
      <div class="job ${j.error ? "error-box" : ""}">
        <div class="job-head">
          <span class="job-name">${esc(j.name)}</span>
          <span class="badge ${BADGE[j.status] || "queued"}">${esc(j.status)}</span>
          <span class="muted" style="margin-left:auto">#${j.id}</span>
        </div>
        <div class="progress"><span style="width:${pct}%"></span></div>
        <div class="job-meta">
          <span>${j.pages_done}/${j.pages_total} pages</span>
          <span>${j.blocks_found} blocks &middot; ${j.blocks_ok} translated</span>
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
const MODEL_BASE = {
  "deepseek-v4-flash": "https://api.deepseek.com/v1",
  "deepseek-v4-pro": "https://api.deepseek.com/v1",
  "meta-llama/llama-3.1-8b-instruct": "https://openrouter.ai/api/v1",
};

async function loadSettings() {
  const s = await api("/api/settings");
  if (s.model in MODEL_BASE) {
    $("#settings-model").value = s.model;
    $("#settings-model-custom").classList.add("hidden");
  } else {
    $("#settings-model").value = "__custom__";
    $("#settings-model-custom").value = s.model || "";
    $("#settings-model-custom").classList.remove("hidden");
  }
  $("#settings-base-url").value = s.base_url;
  $("#settings-deepseek-key").value = s.deepseek_api_key;
  $("#settings-openrouter-key").value = s.openrouter_api_key;
  $("#settings-mode").value = s.output_mode;
  $("#settings-dry-run").checked = s.dry_run === "true";
  // keep the upload page's per-job default in sync
  $("#upload-mode").value = s.output_mode;
}

$("#settings-model").addEventListener("change", () => {
  const v = $("#settings-model").value;
  if (v === "__custom__") {
    $("#settings-model-custom").classList.remove("hidden");
    $("#settings-model-custom").focus();
  } else {
    $("#settings-model-custom").classList.add("hidden");
    if (MODEL_BASE[v]) $("#settings-base-url").value = MODEL_BASE[v];
  }
});

$("#settings-save").addEventListener("click", async () => {
  let model = $("#settings-model").value;
  if (model === "__custom__") model = $("#settings-model-custom").value.trim();
  const payload = {
    model,
    base_url: $("#settings-base-url").value.trim(),
    deepseek_api_key: $("#settings-deepseek-key").value.trim(),
    openrouter_api_key: $("#settings-openrouter-key").value.trim(),
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
    $("#upload-mode").value = $("#settings-mode").value;
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
loadJobs();
loadSettings();
setInterval(loadJobs, 2500);
