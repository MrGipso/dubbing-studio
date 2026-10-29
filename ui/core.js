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

// ---------------------------------------------------------------- icons, notifications, dialogs

function icon(name, extra = "") {
  return `<svg class="icon${extra ? ` ${extra}` : ""}" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
}

function setLabel(element, iconName, text) {
  element.innerHTML = `${icon(iconName)}${escapeHtml(text)}`;
}

const toastHost = document.createElement("div");
toastHost.className = "toasts";
toastHost.setAttribute("role", "status");
toastHost.setAttribute("aria-live", "polite");
document.body.appendChild(toastHost);

const TOAST_ICONS = { info: "info", ok: "check", warn: "alert", error: "alert" };

// A short message in the corner instead of the browser's alert().
function toast(message, type = "info", timeout = type === "error" ? 9000 : 5000) {
  const item = document.createElement("div");
  item.className = `toast toast-${type}`;
  item.innerHTML = `${icon(TOAST_ICONS[type] || "info")}<div class="toast-text" translate="no"></div>
    <button type="button" class="toast-close" aria-label="${escapeHtml(t("Закрыть"))}">${icon("x")}</button>`;
  item.querySelector(".toast-text").textContent = message;
  const close = () => {
    item.classList.add("leaving");
    setTimeout(() => item.remove(), 200);
  };
  item.querySelector(".toast-close").addEventListener("click", close);
  toastHost.appendChild(item);
  if (timeout) setTimeout(close, timeout);
  return item;
}

function notifyError(error) {
  toast(error?.message || String(error), "error");
}

const confirmDialog = document.createElement("dialog");
confirmDialog.className = "confirm-dialog";
confirmDialog.innerHTML = `
  <div class="confirm-body"><span class="confirm-icon">${icon("alert")}</span><p class="confirm-text" translate="no"></p></div>
  <div class="confirm-actions"><button type="button" class="secondary" data-answer="no"></button><button type="button" data-answer="yes"></button></div>`;
document.body.appendChild(confirmDialog);

// The app's own confirmation dialog instead of confirm(); resolves to true or false.
function askConfirm(message, { ok = t("Продолжить"), cancel = t("Отмена"), danger = false } = {}) {
  return new Promise((resolve) => {
    confirmDialog.querySelector(".confirm-text").textContent = message;
    const yes = confirmDialog.querySelector('[data-answer="yes"]');
    yes.textContent = ok;
    yes.className = danger ? "danger" : "primary";
    confirmDialog.querySelector('[data-answer="no"]').textContent = cancel;
    confirmDialog.classList.toggle("is-danger", danger);
    const finish = (value) => {
      confirmDialog.close();
      resolve(value);
    };
    confirmDialog.onclick = (event) => {
      const button = event.target.closest("[data-answer]");
      if (button) finish(button.dataset.answer === "yes");
      else if (event.target === confirmDialog) finish(false);  // click on the backdrop
    };
    confirmDialog.oncancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    confirmDialog.showModal();
    yes.focus();
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
  tools_install: "Установка ffmpeg",
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

// ---------------------------------------------------------------- desktop window
// In the desktop app (Tauri) files come from the system dialog and dropped
// files arrive as paths, so big videos are copied on disk instead of being
// uploaded through the page. In a browser everything works as before.

const tauri = window.__TAURI__ || null;
const nativeFiles = Boolean(tauri?.dialog);
const nativeDropZones = [];

async function pickNativeFile(kind = "media") {
  const audio = [...AUDIO_KINDS];
  const extensions = kind === "audio" ? audio : [...VIDEO_KINDS, ...audio];
  const result = await tauri.dialog.open({
    multiple: false,
    directory: false,
    filters: [{ name: kind === "audio" ? t("Аудио") : t("Видео и аудио"), extensions }],
  });
  return typeof result === "string" ? result : result?.path || "";
}

function fileName(path) {
  return String(path || "").split(/[\\/]/).pop() || "";
}

if (tauri?.webview) {
  const zoneAt = (position) => {
    const ratio = window.devicePixelRatio || 1;
    const element = document.elementFromPoint(position.x / ratio, position.y / ratio);
    return element && nativeDropZones.find((item) => item.zone.contains(element) && item.zone.offsetParent !== null);
  };
  let hovered = null;
  tauri.webview.getCurrentWebview().onDragDropEvent((event) => {
    const { type, paths: dropped, position } = event.payload;
    const item = type === "leave" ? null : zoneAt(position || { x: -1, y: -1 });
    if (hovered && hovered !== item) hovered.zone.classList.remove("drag-over");
    hovered = item;
    if (item && type !== "drop") item.zone.classList.add("drag-over");
    if (type === "drop" && item) {
      item.zone.classList.remove("drag-over");
      hovered = null;
      const element = document.elementFromPoint(position.x / (window.devicePixelRatio || 1), position.y / (window.devicePixelRatio || 1));
      if (dropped?.length) Promise.resolve(item.onPath(dropped[0], element)).catch(notifyError);
    }
  });
}
if (tauri) {
  postJson("/api/desktop/hello", { dialog: nativeFiles, drops: Boolean(tauri.webview) }).catch(() => {});
}

function setToolPath(target, path) {
  field(target).value = path;
  for (const listener of uploadListeners) listener(target, path);
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
  button.addEventListener("click", async () => {
    const kind = button.dataset.pickFile;
    if (nativeFiles) {
      const zone = document.querySelector(`[data-drop-kind="${kind}"]`);
      const path = await pickNativeFile(/video/i.test(kind) ? "media" : "audio").catch(notifyError);
      if (path && zone?.dataset.target) setToolPath(zone.dataset.target, path);
      return;
    }
    const input = document.querySelector(`[data-file-input="${kind}"]`);
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
      notifyError(error);
    }
  });
}

// onFile gets a dropped File in a browser; onPath gets a path in the desktop window.
function enableDrop(zone, onFile, onPath = null) {
  if (onPath) nativeDropZones.push({ zone, onPath });
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
      notifyError(error);
    }
  }, (path) => setToolPath(zone.dataset.target, path));
}

// ---------------------------------------------------------------- helper tools (ffmpeg)

const toolsBanner = document.getElementById("toolsBanner");
onState((state) => {
  const tools = state.tools || {};
  toolsBanner.hidden = tools.ffmpeg !== false;
  toolsBanner.querySelector("[data-install-ffmpeg]").hidden = !tools.ffmpeg_auto;
  toolsBanner.querySelector("[data-ffmpeg-manual]").hidden = Boolean(tools.ffmpeg_auto);
});
toolsBanner.querySelector("[data-install-ffmpeg]").addEventListener("click", async () => {
  try {
    await postJson("/api/tools/ffmpeg");
    await refreshState();
    await waitForJobCompletion("tools_install", t("Не удалось установить ffmpeg. Подробности в логе."));
    toast(t("ffmpeg установлен."), "ok");
  } catch (error) {
    notifyError(error);
  }
});

setInterval(() => {
  refreshState().catch(() => {});
}, 1500);
