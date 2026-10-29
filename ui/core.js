// Shared helpers for every view: API calls, uploads, job polling, progress
// bar, navigation and theme. Loaded first; the other scripts use these globals.

const AUDIO_KINDS = new Set(["wav", "mp3", "m4a", "flac", "ogg"]);
const VIDEO_KINDS = new Set(["mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "flv"]);

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let data = {};
  try {
    data = await response.json();
  } catch {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function postJson(path, body) {
  return api(path, { method: "POST", body: JSON.stringify(body || {}) });
}

// Multipart upload with a progress callback (big videos take a while).
function uploadForm(url, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / event.total);
    });
    request.addEventListener("load", () => {
      let data = {};
      try {
        data = JSON.parse(request.responseText || "{}");
      } catch {
        reject(new Error(`${request.status} ${request.statusText}`));
        return;
      }
      if (request.status >= 400 || data.ok === false) reject(new Error(data.error || request.statusText));
      else resolve(data);
    });
    request.addEventListener("error", () => reject(new Error(t("Сервер недоступен."))));
    request.send(formData);
  });
}

function badge(el, text, type) {
  if (!el) return;
  el.textContent = text;
  el.className = `${el.className.includes("badge-btn") ? "badge badge-btn" : "badge"} ${type || "neutral"}`;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatClock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

function formatDuration(startedAt, finishedAt) {
  if (!startedAt) return "00:00";
  const end = finishedAt || Date.now() / 1000;
  const total = Math.max(0, Math.floor(end - startedAt));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function outputFileUrl(name) {
  return `/api/output?name=${encodeURIComponent(name)}`;
}

function localAudioUrl(path) {
  return `/api/local-audio?path=${encodeURIComponent(path)}`;
}

function isAudioFile(file) {
  return AUDIO_KINDS.has(String(file.kind || "").toLowerCase());
}

function actionButton(label, callback) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary small-btn";
  button.textContent = label;
  button.addEventListener("click", callback);
  return button;
}

// ---------------------------------------------------------------- navigation

function showView(name) {
  for (const item of document.querySelectorAll(".rail-item")) item.classList.toggle("active", item.dataset.view === name);
  for (const view of document.querySelectorAll(".view")) view.classList.toggle("active", view.dataset.view === name);
  window.scrollTo({ top: 0 });
}

// Tool sub-tabs inside the Tools view; "engines" is kept as an alias of Settings.
function showSection(name) {
  if (name === "engines") {
    showView("settings");
    return;
  }
  showView("tools");
  for (const tab of document.querySelectorAll("[data-section-tab]")) tab.classList.toggle("active", tab.dataset.sectionTab === name);
  for (const section of document.querySelectorAll(".workspace-section")) section.classList.toggle("active", section.dataset.section === name);
}

for (const item of document.querySelectorAll(".rail-item")) {
  item.addEventListener("click", () => showView(item.dataset.view));
}
for (const tab of document.querySelectorAll("[data-section-tab]")) {
  tab.addEventListener("click", () => showSection(tab.dataset.sectionTab));
}
document.addEventListener("click", (event) => {
  if (event.target.closest("[data-open-engines]")) showView("settings");
});

// ---------------------------------------------------------------- theme

const themeToggle = document.getElementById("themeToggle");
const themeLabel = document.getElementById("themeLabel");

function applyTheme(theme) {
  const selected = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = selected;
  themeToggle.setAttribute("aria-pressed", selected === "dark" ? "true" : "false");
  themeLabel.textContent = selected === "dark" ? t("Тёмная") : t("Светлая");
}

themeToggle.addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  try {
    localStorage.setItem("pipeline-theme", next);
  } catch {
    // private mode: theme just isn't remembered
  }
});
applyTheme(document.documentElement.dataset.theme || "light");

// ---------------------------------------------------------------- job state

const JOB_LABELS = {
  project: "Проект",
  reference_transcribe: "Распознавание образца",
  music_mix: "Сведение с музыкой",
  transcription: "Транскрибация",
  text_translation: "Перевод текста",
  text_voice: "Озвучка текста",
  script_prepare: "Подготовка сценария",
  script_voice: "Озвучка сценария",
  wav_clean: "Обработка голоса",
  engine_install: "Установка движка",
  engine_test: "Пробная фраза",
};

let lastState = null;
let lastLogText = null;
const stateListeners = [];

function onState(listener) {
  stateListeners.push(listener);
}

const jobBar = document.getElementById("jobBar");
const jobBarStage = document.getElementById("jobBarStage");
const jobBarDetail = document.getElementById("jobBarDetail");
const jobBarFill = document.getElementById("jobBarFill");
const jobBarTime = document.getElementById("jobBarTime");

function renderJob(job) {
  for (const button of document.querySelectorAll("[data-job-button]")) button.disabled = job.running;

  jobBar.hidden = !job.running;
  if (job.running) {
    const label = t(JOB_LABELS[job.kind] || "Задача");
    const stage = job.stage
      ? t("Шаг {done} из {total}: {label}", { done: job.stage.done, total: job.stage.total, label: t(job.stage.label) })
      : label;
    jobBarStage.textContent = stage;
    const progress = job.progress;
    jobBarDetail.textContent = progress ? `${t(progress.label)} · ${Math.round((progress.done / progress.total) * 100)}%` : "";
    let fraction = 0;
    if (job.stage) {
      const within = progress ? progress.done / progress.total : 0;
      fraction = (job.stage.done - 1 + within) / job.stage.total;
    } else if (progress) {
      fraction = progress.done / progress.total;
    }
    jobBarFill.style.width = `${Math.max(2, Math.min(100, fraction * 100)).toFixed(1)}%`;
    jobBarFill.classList.toggle("indeterminate", !job.stage && !progress);
    jobBarTime.textContent = formatDuration(job.started_at, job.finished_at);
  }

  const text = (job.lines || []).join("\n");
  if (text === lastLogText) return;
  lastLogText = text;
  for (const element of document.querySelectorAll("[data-job-log]")) {
    // Keep the reader's scroll position if they scrolled up.
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
    element.textContent = text;
    if (atBottom) element.scrollTop = element.scrollHeight;
  }
}

async function refreshState() {
  const state = await api("/api/state");
  lastState = state;
  renderJob(state.job);
  for (const listener of stateListeners) {
    try {
      listener(state);
    } catch (error) {
      console.error(error);
    }
  }
  return state;
}

async function waitForJobCompletion(kind, failureMessage) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    const state = await refreshState();
    if (state.job.kind !== kind) throw new Error(t("Задачу заменила другая."));
    if (state.job.running) continue;
    if (state.job.exit_code === 0) return state.job.result || {};
    throw new Error(failureMessage);
  }
}

document.getElementById("stopBtn").addEventListener("click", async () => {
  await postJson("/api/stop");
  await refreshState();
});

// ---------------------------------------------------------------- preview dialog

const previewDialog = document.getElementById("previewDialog");
document.getElementById("closePreview").addEventListener("click", () => previewDialog.close());

async function preview(name) {
  const data = await api(`/api/file?name=${encodeURIComponent(name)}`);
  document.getElementById("previewTitle").textContent = data.name;
  document.getElementById("previewText").textContent = data.text;
  previewDialog.showModal();
}

// ---------------------------------------------------------------- drop zones for tool fields

function field(name) {
  const element = Array.from(document.querySelectorAll("input[name], select[name], textarea[name]"))
    .find((candidate) => candidate.name === name);
  if (!element) throw new Error(`Field not found: ${name}`);
  return element;
}

const uploadListeners = [];

function onToolUpload(listener) {
  uploadListeners.push(listener);
}

async function uploadToolFile(file, kind, target) {
  const formData = new FormData();
  formData.append("kind", kind);
  formData.append("file", file);
  const data = await uploadForm("/api/upload-file", formData);
  field(target).value = data.path;
  for (const listener of uploadListeners) listener(target, data.path);
  await refreshState();
}

for (const button of document.querySelectorAll("[data-pick-file]")) {
  button.addEventListener("click", () => {
    const input = document.querySelector(`[data-file-input="${button.dataset.pickFile}"]`);
    if (input) {
      input.value = "";
      input.click();
    }
  });
}

for (const input of document.querySelectorAll("[data-file-input]")) {
  input.addEventListener("change", async () => {
    const file = input.files && input.files[0];
    const zone = document.querySelector(`[data-drop-kind="${input.dataset.fileInput}"]`);
    if (!file || !zone?.dataset.target) return;
    try {
      await uploadToolFile(file, input.dataset.fileInput, zone.dataset.target);
    } catch (error) {
      alert(error.message);
    }
  });
}

function enableDrop(zone, onFile) {
  zone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragleave", (event) => {
    if (!zone.contains(event.relatedTarget)) zone.classList.remove("drag-over");
  });
  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    zone.classList.remove("drag-over");
    const file = event.dataTransfer?.files?.[0];
    if (file) await onFile(file);
  });
}

for (const zone of document.querySelectorAll("[data-drop-kind]")) {
  enableDrop(zone, async (file) => {
    try {
      await uploadToolFile(file, zone.dataset.dropKind, zone.dataset.target);
    } catch (error) {
      alert(error.message);
    }
  });
}

setInterval(() => {
  refreshState().catch(() => {});
}, 1500);
