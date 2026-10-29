const form = document.getElementById("runForm");
const runBtn = document.getElementById("runBtn");
const stopBtn = document.getElementById("stopBtn");
const refreshBtn = document.getElementById("refreshBtn");
const transcribeRefBtn = document.getElementById("transcribeRefBtn");
const transcribeTranslateRefBtn = document.getElementById("transcribeTranslateRefBtn");
const useLatestJsonlBtn = document.getElementById("useLatestJsonlBtn");
const dubBtn = document.getElementById("dubBtn");
const useLatestAudioBtn = document.getElementById("useLatestAudioBtn");
const musicMixBtn = document.getElementById("musicMixBtn");
const musicRefreshBtn = document.getElementById("musicRefreshBtn");
const musicVolumeDb = document.querySelector('input[name="musicVolumeDb"]');
const musicVolumeDbValue = document.getElementById("musicVolumeDbValue");
const transcriptionBtn = document.getElementById("transcriptionBtn");
const transcriptionRefreshBtn = document.getElementById("transcriptionRefreshBtn");
const clearTranscriptionBtn = document.getElementById("clearTranscriptionBtn");
const translationBtn = document.getElementById("translationBtn");
const translationRefreshBtn = document.getElementById("translationRefreshBtn");
const useLatestTranslationsBtn = document.getElementById("useLatestTranslationsBtn");
const translationVoiceBtn = document.getElementById("translationVoiceBtn");
const scriptRefreshBtn = document.getElementById("scriptRefreshBtn");
const transcribeScriptRefBtn = document.getElementById("transcribeScriptRefBtn");
const scriptPrepareBtn = document.getElementById("scriptPrepareBtn");
const useLatestScriptJsonlBtn = document.getElementById("useLatestScriptJsonlBtn");
const scriptVoiceBtn = document.getElementById("scriptVoiceBtn");
const youtubeDownloadBtn = document.getElementById("youtubeDownloadBtn");
const youtubeDetectSubsBtn = document.getElementById("youtubeDetectSubsBtn");
const youtubeCheckBtn = document.getElementById("youtubeCheckBtn");
const youtubeUpdateBtn = document.getElementById("youtubeUpdateBtn");
const youtubeRefreshBtn = document.getElementById("youtubeRefreshBtn");
const youtubeSubtitleChips = document.getElementById("youtubeSubtitleChips");
const contentGenerateBtn = document.getElementById("contentGenerateBtn");
const contentRefreshBtn = document.getElementById("contentRefreshBtn");
const useLatestSubtitlesBtn = document.getElementById("useLatestSubtitlesBtn");
const transcribeContentRefBtn = document.getElementById("transcribeContentRefBtn");
const useLatestContentScriptBtn = document.getElementById("useLatestContentScriptBtn");
const contentPrepareVoiceBtn = document.getElementById("contentPrepareVoiceBtn");
const useLatestContentJsonlBtn = document.getElementById("useLatestContentJsonlBtn");
const contentVoiceBtn = document.getElementById("contentVoiceBtn");
const cleanRefreshBtn = document.getElementById("cleanRefreshBtn");
const useLatestCleanWavBtn = document.getElementById("useLatestCleanWavBtn");
const cleanWavBtn = document.getElementById("cleanWavBtn");
const cleanOriginalPlayer = document.getElementById("cleanOriginalPlayer");
const cleanResultPlayer = document.getElementById("cleanResultPlayer");
const themeToggle = document.getElementById("themeToggle");
const themeLabel = document.getElementById("themeLabel");
const jobBadge = document.getElementById("jobBadge");
const llmBadge = document.getElementById("llmBadge");
const ttsBadge = document.getElementById("ttsBadge");
const enginesLogEl = document.getElementById("enginesLog");
const enginesRuntimeEl = document.getElementById("enginesRuntime");
const outputsEl = document.getElementById("outputs");
const musicOutputsEl = document.getElementById("musicOutputs");
const transcriptionOutputsEl = document.getElementById("transcriptionOutputs");
const translationOutputsEl = document.getElementById("translationOutputs");
const scriptOutputsEl = document.getElementById("scriptOutputs");
const youtubeOutputsEl = document.getElementById("youtubeOutputs");
const contentOutputsEl = document.getElementById("contentOutputs");
const cleanOutputsEl = document.getElementById("cleanOutputs");
const fileCount = document.getElementById("fileCount");
const musicFileCount = document.getElementById("musicFileCount");
const transcriptionFileCount = document.getElementById("transcriptionFileCount");
const translationFileCount = document.getElementById("translationFileCount");
const scriptFileCount = document.getElementById("scriptFileCount");
const youtubeFileCount = document.getElementById("youtubeFileCount");
const contentFileCount = document.getElementById("contentFileCount");
const cleanFileCount = document.getElementById("cleanFileCount");
const logEl = document.getElementById("log");
const musicLogEl = document.getElementById("musicLog");
const transcriptionLogEl = document.getElementById("transcriptionLog");
const translationLogEl = document.getElementById("translationLog");
const scriptLogEl = document.getElementById("scriptLog");
const youtubeLogEl = document.getElementById("youtubeLog");
const contentLogEl = document.getElementById("contentLog");
const cleanLogEl = document.getElementById("cleanLog");
const runtimeEl = document.getElementById("runtime");
const musicRuntimeEl = document.getElementById("musicRuntime");
const transcriptionRuntimeEl = document.getElementById("transcriptionRuntime");
const translationRuntimeEl = document.getElementById("translationRuntime");
const scriptRuntimeEl = document.getElementById("scriptRuntime");
const youtubeRuntimeEl = document.getElementById("youtubeRuntime");
const contentRuntimeEl = document.getElementById("contentRuntime");
const cleanRuntimeEl = document.getElementById("cleanRuntime");
const previewDialog = document.getElementById("previewDialog");
const previewTitle = document.getElementById("previewTitle");
const previewText = document.getElementById("previewText");
const closePreview = document.getElementById("closePreview");
const sectionTabs = document.querySelectorAll("[data-section-tab]");
const workspaceSections = document.querySelectorAll("[data-section]");

let lastState = null;
let lastYoutubeSubtitles = null;
let lastOutputsSignature = "";
let lastYoutubeSignature = "";
let lastLogText = null;
const AUDIO_KINDS = new Set(["wav", "mp3", "m4a", "flac", "ogg"]);
const VIDEO_KINDS = new Set(["mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "flv"]);
const TEXT_KINDS = new Set(["txt", "srt", "vtt", "json", "jsonl", "ass"]);
const SUBTITLE_KINDS = new Set(["srt", "vtt", "ttml", "srv3", "ass"]);
const LANGUAGE_IDS = {
  English: "en", Russian: "ru", Ukrainian: "uk", Spanish: "es", Portuguese: "pt",
  German: "de", French: "fr", Italian: "it", Polish: "pl", Bulgarian: "bg",
  Turkish: "tr", Dutch: "nl", Swedish: "sv", Danish: "da", Norwegian: "no",
  Finnish: "fi", Greek: "el", Hebrew: "he", Arabic: "ar", Hindi: "hi",
  Japanese: "ja", Korean: "ko", Chinese: "zh", Malay: "ms", Swahili: "sw",
};

const THEME_STORAGE_KEY = "pipeline-theme";

function storedTheme() {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    return "";
  }
}

function saveTheme(theme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Ignore storage errors in private browser modes.
  }
}

function applyTheme(theme) {
  const selected = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = selected;
  themeToggle.setAttribute("aria-pressed", selected === "dark" ? "true" : "false");
  themeLabel.textContent = selected === "dark" ? "Тёмная" : "Светлая";
}

function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next);
  saveTheme(next);
}

applyTheme(document.documentElement.dataset.theme || storedTheme() || "light");

function languageIdFor(language) {
  return LANGUAGE_IDS[String(language || "").trim()] || "";
}

function numberOr(value, fallback) {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  const number = Number(text);
  return Number.isFinite(number) ? number : fallback;
}

function values() {
  const payload = {};
  for (const input of document.querySelectorAll("input[name], select[name], textarea[name]")) {
    if (input.type === "file") continue;
    if (input.type === "checkbox") {
      payload[input.name] = input.checked;
    } else {
      payload[input.name] = input.value;
    }
  }
  payload.batchSize = Number(payload.batchSize || 8);
  payload.phraseMaxGap = Number(payload.phraseMaxGap || 0.8);
  payload.phraseMaxDuration = Number(payload.phraseMaxDuration || 12);
  payload.noMergePhrases = payload.phraseMerge === false;
  payload.whisperLanguage = payload.sourceLanguage === "auto" ? "auto" : (languageIdFor(payload.sourceLanguage) || "auto");
  payload.dubSpeed = Number(payload.dubSpeed || 1);
  payload.dubTailRoom = Number(payload.dubTailRoom || 0.6);
  payload.dubMaxAutoSpeed = Number(payload.dubMaxAutoSpeed || 1.18);
  payload.dubPostFadeMs = Number(payload.dubPostFadeMs || 40);
  payload.dubPostMinPause = Number(payload.dubPostMinPause || 0.15);
  payload.musicVolumeDb = Number(payload.musicVolumeDb || -18);
  payload.transcribeOutputDir = payload.transcribeOutputDir || "outputs";
  payload.translateTemperature = Number(payload.translateTemperature || 0.2);
  payload.translateOutputDir = payload.translateOutputDir || "outputs";
  payload.translateVoiceSpeed = Number(payload.translateVoiceSpeed || 1);
  payload.translateVoiceTailRoom = Number(payload.translateVoiceTailRoom || 0.8);
  payload.translateVoiceMaxAutoSpeed = Number(payload.translateVoiceMaxAutoSpeed || 1.18);
  payload.translateVoicePostFadeMs = Number(payload.translateVoicePostFadeMs || 40);
  payload.translateVoicePostMinPause = Number(payload.translateVoicePostMinPause || 0.15);
  payload.scriptTemperature = Number(payload.scriptTemperature || 0.4);
  payload.scriptOutputDir = payload.scriptOutputDir || "outputs";
  payload.scriptVoiceSpeed = Number(payload.scriptVoiceSpeed || 1);
  payload.scriptVoiceTailRoom = Number(payload.scriptVoiceTailRoom || 0.8);
  payload.scriptVoiceMaxAutoSpeed = Number(payload.scriptVoiceMaxAutoSpeed || 1.18);
  payload.scriptVoicePostFadeMs = Number(payload.scriptVoicePostFadeMs || 40);
  payload.scriptVoicePostMinPause = Number(payload.scriptVoicePostMinPause || 0.15);
  payload.contentTargetDuration = Number(payload.contentTargetDuration || 10);
  payload.contentRewriteDepth = payload.contentRewriteDepth || "strong";
  payload.contentVisualCount = Number(payload.contentVisualCount || 16);
  payload.contentTemperature = Number(payload.contentTemperature || 0.45);
  payload.contentOutputDir = payload.contentOutputDir || "outputs";
  payload.contentVoiceSpeed = Number(payload.contentVoiceSpeed || 1);
  payload.contentVoiceTailRoom = Number(payload.contentVoiceTailRoom || 0.8);
  payload.contentVoiceMaxAutoSpeed = Number(payload.contentVoiceMaxAutoSpeed || 1.18);
  payload.contentVoicePostFadeMs = Number(payload.contentVoicePostFadeMs || 40);
  payload.contentVoicePostMinPause = Number(payload.contentVoicePostMinPause || 0.15);
  payload.cleanWavFadeMs = Number(payload.cleanWavFadeMs || 40);
  payload.cleanWavMinPause = Number(payload.cleanWavMinPause || 0.15);
  payload.cleanWavAmount = Number(payload.cleanWavAmount || 55);
  payload.cleanWavDeesserFreq = numberOr(payload.cleanWavDeesserFreq, 5200);
  payload.cleanWavDeesserStrength = numberOr(payload.cleanWavDeesserStrength, 55);
  payload.cleanWavEqMudDb = numberOr(payload.cleanWavEqMudDb, -1.8);
  payload.cleanWavEqClarityDb = numberOr(payload.cleanWavEqClarityDb, 2.2);
  payload.cleanWavEqAirDb = numberOr(payload.cleanWavEqAirDb, 1.1);
  payload.cleanWavCompThreshold = numberOr(payload.cleanWavCompThreshold, -20);
  payload.cleanWavCompRatio = numberOr(payload.cleanWavCompRatio, 3);
  payload.cleanWavCompMakeup = numberOr(payload.cleanWavCompMakeup, 2.2);
  payload.cleanWavSatDrive = numberOr(payload.cleanWavSatDrive, 45);
  payload.cleanWavSatMix = numberOr(payload.cleanWavSatMix, 28);
  payload.cleanWavLimiterCeiling = numberOr(payload.cleanWavLimiterCeiling, 0.95);
  payload.cleanWavLoudnessTarget = numberOr(payload.cleanWavLoudnessTarget, -16);
  payload.cleanWavRoomMix = numberOr(payload.cleanWavRoomMix, 10);
  payload.targetLanguageId = languageIdFor(payload.targetLanguage) || payload.targetLanguageId || "";
  return payload;
}

function field(name) {
  const element = Array.from(document.querySelectorAll("input[name], select[name], textarea[name]"))
    .find((candidate) => candidate.name === name);
  if (!element) throw new Error(`Field not found: ${name}`);
  return element;
}

function syncTargetLanguageId() {
  const languageId = languageIdFor(field("targetLanguage").value);
  if (languageId) field("targetLanguageId").value = languageId;
}

function syncWhisperLanguage() {
  const source = field("sourceLanguage").value;
  field("whisperLanguage").value = source === "auto" ? "auto" : (languageIdFor(source) || "auto");
}

function syncWhisperComputeType() {
  // float16 is not available on CPU: switch to int8 automatically.
  const compute = field("whisperComputeType");
  if (field("whisperDevice").value === "cpu" && compute.value.includes("float16")) compute.value = "int8";
}

function syncTranslationTargetToVoiceLanguage() {
  const languageId = languageIdFor(field("translateTargetLanguage").value);
  if (languageId) field("translateVoiceLanguage").value = languageId;
}

function updateVoiceSourceMode() {
  const source = field("translateVoiceSource").value;
  for (const element of document.querySelectorAll("[data-voice-source-field]")) {
    element.hidden = element.dataset.voiceSourceField !== source;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function badge(el, text, type) {
  el.textContent = text;
  el.className = `badge ${type || "neutral"}`;
}

function formatBytes(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDuration(startedAt, finishedAt) {
  if (!startedAt) return "00:00";
  const end = finishedAt || Date.now() / 1000;
  const total = Math.max(0, Math.floor(end - startedAt));
  const minutes = String(Math.floor(total / 60)).padStart(2, "0");
  const seconds = String(total % 60).padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function outputFileUrl(name) {
  return `/api/output?name=${encodeURIComponent(name)}`;
}

function youtubeFileUrl(name) {
  return `/api/youtube-file?name=${encodeURIComponent(name)}`;
}

function localAudioUrl(path) {
  return `/api/local-audio?path=${encodeURIComponent(path)}`;
}

function isAudioFile(file) {
  return AUDIO_KINDS.has(String(file.kind || "").toLowerCase());
}

function isVideoFile(file) {
  return VIDEO_KINDS.has(String(file.kind || "").toLowerCase());
}

function isTextFile(file) {
  return TEXT_KINDS.has(String(file.kind || "").toLowerCase());
}

function isSubtitleFile(file) {
  return SUBTITLE_KINDS.has(String(file.kind || "").toLowerCase());
}

function renderJob(job) {
  runBtn.disabled = job.running;
  dubBtn.disabled = job.running;
  musicMixBtn.disabled = job.running;
  transcriptionBtn.disabled = job.running;
  translationBtn.disabled = job.running;
  translationVoiceBtn.disabled = job.running;
  scriptPrepareBtn.disabled = job.running;
  scriptVoiceBtn.disabled = job.running;
  youtubeDownloadBtn.disabled = job.running;
  youtubeDetectSubsBtn.disabled = job.running;
  youtubeCheckBtn.disabled = job.running;
  youtubeUpdateBtn.disabled = job.running;
  contentGenerateBtn.disabled = job.running;
  transcribeContentRefBtn.disabled = job.running;
  contentPrepareVoiceBtn.disabled = job.running;
  contentVoiceBtn.disabled = job.running;
  cleanWavBtn.disabled = job.running;
  stopBtn.disabled = !job.running;
  transcribeRefBtn.disabled = job.running;
  transcribeTranslateRefBtn.disabled = job.running;
  transcribeScriptRefBtn.disabled = job.running;
  for (const button of document.querySelectorAll("[data-job-button]")) button.disabled = job.running;
  enginesRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  runtimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  musicRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  transcriptionRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  translationRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  scriptRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  youtubeRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  contentRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);
  cleanRuntimeEl.textContent = formatDuration(job.started_at, job.finished_at);

  if (job.running) {
    const label = job.kind === "reference_transcribe"
      ? "Whisper ref"
      : job.kind === "dubbing"
        ? "Dubbing"
        : job.kind === "music_mix"
          ? "Music mix"
          : job.kind === "transcription"
            ? "Transcription"
            : job.kind === "text_translation"
              ? "Translation"
            : job.kind === "text_voice"
              ? "Voice"
              : job.kind === "script_prepare"
              ? "Script prep"
              : job.kind === "script_voice"
                  ? "Script voice"
                  : job.kind === "youtube_download"
                    ? "YouTube"
                    : job.kind === "yt_dlp_update"
                      ? "yt-dlp update"
                      : job.kind === "content_generate"
                        ? "Content"
                        : job.kind === "content_voice_prepare"
                          ? "Content prep"
                          : job.kind === "content_voice"
                            ? "Content voice"
                            : job.kind === "wav_clean"
                              ? "Cleaning WAV"
                              : job.kind === "engine_install"
                                ? "Installing engine"
                                : job.kind === "engine_test"
                                  ? "Engine test"
                                  : job.kind === "translate"
                                    ? "Translating"
                                    : "Running";
    badge(jobBadge, label, "warn");
  }
  else if (job.exit_code === 0) badge(jobBadge, "Done", "ok");
  else if (job.exit_code) badge(jobBadge, `Exit ${job.exit_code}`, "bad");
  else badge(jobBadge, "Idle", "neutral");

  const text = (job.lines || []).join("\n");
  if (text === lastLogText) return;
  lastLogText = text;
  for (const element of [logEl, musicLogEl, transcriptionLogEl, translationLogEl, scriptLogEl, youtubeLogEl, contentLogEl, cleanLogEl, enginesLogEl]) {
    // Keep the user's scroll position if they scrolled up to read.
    const atBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
    element.textContent = text;
    if (atBottom) element.scrollTop = element.scrollHeight;
  }
}

function renderOutputList(container, countElement, files) {
  countElement.textContent = String(files.length);
  container.innerHTML = "";
  for (const file of files) {
    const audio = isAudioFile(file);
    const row = document.createElement("div");
    row.className = audio ? "file-row audio-row" : "file-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(file.name)}</strong>
        <div class="meta">${file.kind.toUpperCase()} · ${formatBytes(file.size)} · ${new Date(file.mtime * 1000).toLocaleString()}</div>
        <div class="download-actions"></div>
      </div>
      ${audio
        ? `<audio controls preload="none" src="${outputFileUrl(file.name)}"></audio>`
        : `<button type="button">Open</button>`}
    `;
    const button = row.querySelector("button");
    if (button) button.addEventListener("click", () => preview(file.name));
    const actions = row.querySelector(".download-actions");
    if (file.name.endsWith(".content.script.txt")) {
      actions.appendChild(actionButton("В сценарий", async () => {
        const data = await api(`/api/file?name=${encodeURIComponent(file.name)}`);
        field("scriptText").value = data.text || "";
        showSection("script");
      }));
      actions.appendChild(actionButton("В озвучку", () => {
        field("contentScriptFile").value = `outputs\\${file.name}`;
        showSection("content");
      }));
    }
    if (file.name.includes(".script.dub.") && file.name.endsWith(".jsonl")) {
      actions.appendChild(actionButton("В озвучку", () => {
        field("contentVoiceJsonl").value = `outputs\\${file.name}`;
        showSection("content");
      }));
    }
    if (audio && file.name.toLowerCase().endsWith(".wav")) {
      actions.appendChild(actionButton("Настроить обработку", () => {
        const path = `outputs\\${file.name}`;
        field("cleanWavInput").value = path;
        updateCleanPreview(path, "original");
        showSection("clean");
      }));
      actions.appendChild(actionButton("Быстро очистить", () => cleanOutputWav(file.name)));
    }
    container.appendChild(row);
  }
}

function renderYouTubeList(files) {
  // Rebuilding the list destroys <audio>/<video> players, so skip identical lists.
  const signature = JSON.stringify(files.map((file) => [file.name, file.size, file.mtime]));
  if (signature === lastYoutubeSignature) return;
  lastYoutubeSignature = signature;
  youtubeFileCount.textContent = String(files.length);
  youtubeOutputsEl.innerHTML = "";
  for (const file of files) {
    const audio = isAudioFile(file);
    const video = isVideoFile(file);
    const text = isTextFile(file);
    const row = document.createElement("div");
    row.className = video ? "file-row video-row" : audio ? "file-row audio-row" : "file-row";

    const media = audio
      ? `<audio controls preload="none" src="${youtubeFileUrl(file.name)}"></audio>`
      : video
        ? `<video controls preload="metadata" src="${youtubeFileUrl(file.name)}"></video>`
        : text
          ? `<button type="button" data-youtube-open="${escapeHtml(file.name)}">Open</button>`
          : "";

    row.innerHTML = `
      <div>
        <strong>${escapeHtml(file.name)}</strong>
        <div class="meta">${file.kind.toUpperCase()} · ${formatBytes(file.size)} · ${new Date(file.mtime * 1000).toLocaleString()}</div>
        <div class="download-actions"></div>
      </div>
      ${media}
    `;

    const actions = row.querySelector(".download-actions");
    if (audio) {
      actions.appendChild(actionButton("В дубляж", () => {
        field("audio").value = file.path;
        showSection("dubbing");
      }));
      actions.appendChild(actionButton("В транскрибацию", () => {
        field("transcribeAudio").value = file.path;
        showSection("transcription");
      }));
      actions.appendChild(actionButton("В музыку", () => {
        field("musicAudio").value = file.path;
        showSection("music");
      }));
    } else if (video) {
      actions.appendChild(actionButton("В транскрибацию", () => {
        field("transcribeVideo").value = file.path;
        showSection("transcription");
      }));
    } else if (isSubtitleFile(file)) {
      actions.appendChild(actionButton("В контент", () => {
        field("contentSubtitles").value = file.path;
        showSection("content");
      }));
    }

    const openButton = row.querySelector("[data-youtube-open]");
    if (openButton) openButton.addEventListener("click", () => previewYouTube(file.name));
    youtubeOutputsEl.appendChild(row);
  }
}

function selectedSubtitleCodes() {
  return String(field("youtubeSubtitleLanguages").value || "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function setSelectedSubtitleCodes(codes) {
  const seen = new Set();
  const unique = [];
  for (const code of codes.map((item) => item.trim()).filter(Boolean)) {
    if (seen.has(code)) continue;
    seen.add(code);
    unique.push(code);
  }
  field("youtubeSubtitleLanguages").value = unique.join(",");
}

function renderSubtitleGroup(title, items, selected) {
  if (!items.length) return;
  const group = document.createElement("div");
  group.className = "subtitle-group";

  const heading = document.createElement("div");
  heading.className = "subtitle-group-title";
  heading.textContent = `${title}: ${items.length}`;
  group.appendChild(heading);

  const list = document.createElement("div");
  list.className = "chip-row";
  for (const item of items) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = selected.has(item.code) ? "subtitle-chip active" : "subtitle-chip";
    button.textContent = item.code;
    button.title = `${item.name}${item.formats?.length ? ` · ${item.formats.join(", ")}` : ""}`;
    button.addEventListener("click", () => {
      const next = selectedSubtitleCodes();
      const index = next.indexOf(item.code);
      if (index >= 0) next.splice(index, 1);
      else next.push(item.code);
      setSelectedSubtitleCodes(next);
      renderSubtitleChips(lastYoutubeSubtitles);
    });
    list.appendChild(button);
  }
  group.appendChild(list);
  youtubeSubtitleChips.appendChild(group);
}

function renderSubtitleChips(data) {
  lastYoutubeSubtitles = data;
  youtubeSubtitleChips.innerHTML = "";
  if (!data) {
    youtubeSubtitleChips.hidden = true;
    return;
  }

  const manual = data.manual || [];
  const automatic = data.automatic || [];
  const selected = new Set(selectedSubtitleCodes());
  youtubeSubtitleChips.hidden = false;

  if (!manual.length && !automatic.length) {
    const empty = document.createElement("div");
    empty.className = "meta";
    empty.textContent = "Субтитры не найдены.";
    youtubeSubtitleChips.appendChild(empty);
    return;
  }

  renderSubtitleGroup("Ручные", manual, selected);
  renderSubtitleGroup("Авто", automatic, selected);
}

function actionButton(label, callback) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "secondary small-btn";
  button.textContent = label;
  button.addEventListener("click", callback);
  return button;
}

async function cleanOutputWav(fileName) {
  try {
    await api("/api/run-clean-wav", {
      method: "POST",
      body: JSON.stringify({
        fileName,
        mode: "soft",
        minPause: 0.15,
        fadeMs: 40,
        normalize: true,
      }),
    });
    await refreshState();
  } catch (error) {
    alert(error.message);
  }
}

function updateCleanPreview(path, target = "original") {
  const player = target === "result" ? cleanResultPlayer : cleanOriginalPlayer;
  if (!path) {
    player.removeAttribute("src");
    player.load();
    return;
  }
  player.src = localAudioUrl(path);
  player.load();
}

function renderOutputs(files) {
  // Rebuilding every list each poll stopped playback and reset scrolling.
  const signature = JSON.stringify(files.map((file) => [file.name, file.size, file.mtime]));
  if (signature === lastOutputsSignature) return;
  lastOutputsSignature = signature;
  renderOutputList(outputsEl, fileCount, files);
  renderOutputList(musicOutputsEl, musicFileCount, files);
  renderOutputList(transcriptionOutputsEl, transcriptionFileCount, files);
  renderOutputList(translationOutputsEl, translationFileCount, files);
  renderOutputList(scriptOutputsEl, scriptFileCount, files);
  renderOutputList(
    contentOutputsEl,
    contentFileCount,
    files.filter((file) => file.name.includes(".content.") || /(^|_)content_\d/.test(file.name)),
  );
  renderOutputList(cleanOutputsEl, cleanFileCount, files);
}

async function refreshState() {
  const state = await api("/api/state");
  lastState = state;
  renderJob(state.job);
  renderOutputs(state.outputs);
  renderYouTubeList(state.youtube || []);
}

async function refreshAll() {
  lastOutputsSignature = "";
  lastYoutubeSignature = "";
  await Promise.all([refreshState(), loadEngineSettings()]);
}

async function preview(name) {
  const data = await api(`/api/file?name=${encodeURIComponent(name)}`);
  previewTitle.textContent = data.name;
  previewText.textContent = data.text;
  previewDialog.showModal();
}

async function previewYouTube(name) {
  const data = await api(`/api/youtube-text?name=${encodeURIComponent(name)}`);
  previewTitle.textContent = data.name;
  previewText.textContent = data.text;
  previewDialog.showModal();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function showSection(name) {
  for (const tab of sectionTabs) {
    tab.classList.toggle("active", tab.dataset.sectionTab === name);
  }
  for (const section of workspaceSections) {
    section.classList.toggle("active", section.dataset.section === name);
  }
}

function updateMusicVolumeLabel() {
  musicVolumeDbValue.textContent = `${musicVolumeDb.value} dB`;
}

const CLEAN_PRESETS = {
  clean: { deesser: false, eq: false, compressor: false, saturation: false, limiter: true, loudness: true, room: false, amount: 35, deesserFreq: 5200, deesserStrength: 45, eqMudDb: -1.0, eqClarityDb: 1.2, eqAirDb: 0.5, compThreshold: -18, compRatio: 2.0, compMakeup: 1.2, satDrive: 25, satMix: 12, limiterCeiling: 0.95, loudnessTarget: -16, roomMix: 5 },
  youtube: { deesser: true, eq: true, compressor: true, saturation: true, limiter: true, loudness: true, room: false, amount: 55, deesserFreq: 5200, deesserStrength: 55, eqMudDb: -1.8, eqClarityDb: 2.2, eqAirDb: 1.1, compThreshold: -20, compRatio: 3.0, compMakeup: 2.2, satDrive: 45, satMix: 28, limiterCeiling: 0.95, loudnessTarget: -16, roomMix: 10 },
  dense: { deesser: true, eq: true, compressor: true, saturation: true, limiter: true, loudness: true, room: false, amount: 70, deesserFreq: 5400, deesserStrength: 60, eqMudDb: -2.4, eqClarityDb: 2.8, eqAirDb: 1.0, compThreshold: -24, compRatio: 3.8, compMakeup: 3.0, satDrive: 65, satMix: 35, limiterCeiling: 0.94, loudnessTarget: -15, roomMix: 10 },
  soft: { deesser: true, eq: true, compressor: true, saturation: false, limiter: true, loudness: true, room: false, amount: 45, deesserFreq: 4800, deesserStrength: 65, eqMudDb: -1.4, eqClarityDb: 1.2, eqAirDb: -0.6, compThreshold: -18, compRatio: 2.2, compMakeup: 1.4, satDrive: 20, satMix: 10, limiterCeiling: 0.95, loudnessTarget: -17, roomMix: 8 },
  radio: { deesser: true, eq: true, compressor: true, saturation: true, limiter: true, loudness: true, room: false, amount: 80, deesserFreq: 5600, deesserStrength: 60, eqMudDb: -3.0, eqClarityDb: 3.2, eqAirDb: 0.8, compThreshold: -26, compRatio: 4.2, compMakeup: 3.8, satDrive: 70, satMix: 38, limiterCeiling: 0.93, loudnessTarget: -14.5, roomMix: 8 },
};

function setCleanEffect(name, value) {
  field(`cleanWav${name}`).checked = value;
}

function setCleanNumber(name, value) {
  field(`cleanWav${name}`).value = String(value);
}

function applyCleanPreset() {
  const preset = CLEAN_PRESETS[field("cleanWavPreset").value];
  if (!preset) return;
  setCleanEffect("Deesser", preset.deesser);
  setCleanEffect("Eq", preset.eq);
  setCleanEffect("Compressor", preset.compressor);
  setCleanEffect("Saturation", preset.saturation);
  setCleanEffect("Limiter", preset.limiter);
  setCleanEffect("Loudness", preset.loudness);
  setCleanEffect("Room", preset.room);
  field("cleanWavAmount").value = String(preset.amount);
  setCleanNumber("DeesserFreq", preset.deesserFreq);
  setCleanNumber("DeesserStrength", preset.deesserStrength);
  setCleanNumber("EqMudDb", preset.eqMudDb);
  setCleanNumber("EqClarityDb", preset.eqClarityDb);
  setCleanNumber("EqAirDb", preset.eqAirDb);
  setCleanNumber("CompThreshold", preset.compThreshold);
  setCleanNumber("CompRatio", preset.compRatio);
  setCleanNumber("CompMakeup", preset.compMakeup);
  setCleanNumber("SatDrive", preset.satDrive);
  setCleanNumber("SatMix", preset.satMix);
  setCleanNumber("LimiterCeiling", preset.limiterCeiling);
  setCleanNumber("LoudnessTarget", preset.loudnessTarget);
  setCleanNumber("RoomMix", preset.roomMix);
}

for (const tab of sectionTabs) {
  tab.addEventListener("click", () => showSection(tab.dataset.sectionTab));
}

themeToggle.addEventListener("click", toggleTheme);

musicVolumeDb.addEventListener("input", updateMusicVolumeLabel);
updateMusicVolumeLabel();
field("targetLanguage").addEventListener("change", syncTargetLanguageId);
syncTargetLanguageId();
field("sourceLanguage").addEventListener("change", syncWhisperLanguage);
syncWhisperLanguage();
field("whisperDevice").addEventListener("change", syncWhisperComputeType);
for (const button of document.querySelectorAll("[data-open-engines]")) {
  button.addEventListener("click", () => showSection("engines"));
}
field("translateTargetLanguage").addEventListener("change", syncTranslationTargetToVoiceLanguage);
field("translateVoiceSource").addEventListener("change", updateVoiceSourceMode);
field("cleanWavPreset").addEventListener("change", applyCleanPreset);
field("cleanWavInput").addEventListener("change", () => {
  updateCleanPreview(field("cleanWavInput").value, "original");
  updateCleanPreview("", "result");
});
syncTranslationTargetToVoiceLanguage();
updateVoiceSourceMode();

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await api("/api/run", { method: "POST", body: JSON.stringify(values()) });
    await refreshState();
    await waitForJobCompletion("translate", "Перевод не удался. Подробности в логе.");
    // Hand the fresh plan to the voice step so the user only presses "2. Озвучить".
    const data = await api("/api/latest-dub-jsonl");
    if (data.path) field("jsonl").value = data.path;
  } catch (error) {
    alert(error.message);
  }
});

stopBtn.addEventListener("click", async () => {
  await api("/api/stop", { method: "POST", body: "{}" });
  await refreshState();
});

refreshBtn.addEventListener("click", refreshAll);
musicRefreshBtn.addEventListener("click", refreshAll);
transcriptionRefreshBtn.addEventListener("click", refreshAll);
translationRefreshBtn.addEventListener("click", refreshAll);
scriptRefreshBtn.addEventListener("click", refreshAll);
youtubeRefreshBtn.addEventListener("click", refreshAll);
contentRefreshBtn.addEventListener("click", refreshAll);
cleanRefreshBtn.addEventListener("click", refreshAll);

async function fillLatestTranslationsJson(showMissingAlert = true) {
  const data = await api("/api/latest-translations");
  if (!data.path) {
    if (showMissingAlert) alert("Готовый .translations.json не найден.");
    return "";
  }
  field("translateVoiceJson").value = data.path;
  field("translateVoiceSource").value = "translation";
  updateVoiceSourceMode();
  return data.path;
}

useLatestTranslationsBtn.addEventListener("click", async () => {
  try {
    await fillLatestTranslationsJson(true);
  } catch (error) {
    alert(error.message);
  }
});

useLatestJsonlBtn.addEventListener("click", async () => {
  try {
    const data = await api("/api/latest-dub-jsonl");
    if (!data.path) {
      alert("План озвучки (.dub.jsonl) не найден. Сначала выполните шаг «1. Распознать и перевести».");
      return;
    }
    field("jsonl").value = data.path;
  } catch (error) {
    alert(error.message);
  }
});

useLatestCleanWavBtn.addEventListener("click", async () => {
  try {
    const data = await api("/api/latest-audio");
    if (!data.path) {
      alert("Готовый WAV в outputs не найден.");
      return;
    }
    field("cleanWavInput").value = data.path;
    updateCleanPreview(data.path, "original");
    updateCleanPreview("", "result");
  } catch (error) {
    alert(error.message);
  }
});

async function fillLatestScriptJsonl(showMissingAlert = true) {
  const data = await api("/api/latest-script-jsonl");
  if (!data.path) {
    if (showMissingAlert) alert("Готовый .script.dub.jsonl не найден.");
    return "";
  }
  field("scriptJsonl").value = data.path;
  return data.path;
}

async function fillLatestContentScript(showMissingAlert = true) {
  const data = await api("/api/latest-content-script");
  if (!data.path) {
    if (showMissingAlert) alert("Готовый .content.script.txt не найден.");
    return "";
  }
  field("contentScriptFile").value = data.path;
  return data.path;
}

async function fillLatestContentJsonl(showMissingAlert = true) {
  const data = await api("/api/latest-content-jsonl");
  if (!data.path) {
    if (showMissingAlert) alert("Готовый content .script.dub.jsonl не найден.");
    return "";
  }
  field("contentVoiceJsonl").value = data.path;
  return data.path;
}

useLatestScriptJsonlBtn.addEventListener("click", async () => {
  try {
    await fillLatestScriptJsonl(true);
  } catch (error) {
    alert(error.message);
  }
});

dubBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!payload.jsonl) {
      const data = await api("/api/latest-dub-jsonl");
      if (data.path) {
        field("jsonl").value = data.path;
        payload.jsonl = data.path;
      }
    }
    await api("/api/run-dubbing", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

useLatestAudioBtn.addEventListener("click", async () => {
  try {
    const data = await api("/api/latest-audio");
    if (!data.path) {
      alert("Готовый аудиофайл в outputs не найден.");
      return;
    }
    field("musicVoiceAudio").value = data.path;
  } catch (error) {
    alert(error.message);
  }
});

musicMixBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!payload.musicVoiceAudio) {
      const data = await api("/api/latest-audio");
      if (data.path) {
        field("musicVoiceAudio").value = data.path;
        payload.musicVoiceAudio = data.path;
      }
    }
    await api("/api/run-music-mix", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

transcriptionBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!payload.transcribeVideo && !payload.transcribeAudio) {
      alert("Выбери видео или аудио для транскрибации.");
      return;
    }
    await api("/api/run-transcription", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

translationBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!String(payload.translateText || "").trim()) {
      alert("Вставьте текст для перевода.");
      return;
    }
    if (!payload.translateTargetLanguage) {
      alert("Выбери язык перевода.");
      return;
    }
    syncTranslationTargetToVoiceLanguage();
    const originalText = translationBtn.textContent;
    translationBtn.textContent = "Перевожу...";
    try {
      await api("/api/run-text-translation", { method: "POST", body: JSON.stringify(payload) });
      await refreshAll();
      await waitForJobCompletion("text_translation", "Translation failed. Check the log.");
      await fillLatestTranslationsJson(false);
    } finally {
      translationBtn.textContent = originalText;
    }
  } catch (error) {
    alert(error.message);
  }
});

translationVoiceBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (payload.translateVoiceSource === "direct") {
      if (!String(payload.translateVoiceText || "").trim()) {
        alert("Вставь готовый текст для озвучки.");
        return;
      }
    } else if (!payload.translateVoiceJson) {
      const data = await api("/api/latest-translations");
      if (data.path) {
        field("translateVoiceJson").value = data.path;
        payload.translateVoiceJson = data.path;
      }
    }
    if (payload.translateVoiceSource !== "direct" && !payload.translateVoiceJson) {
      alert("Сначала сделай перевод или выбери .translations.json.");
      return;
    }
    await api("/api/run-text-voice", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

scriptPrepareBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!String(payload.scriptText || "").trim()) {
      alert("Вставь готовый сценарий.");
      return;
    }
    const originalText = scriptPrepareBtn.textContent;
    scriptPrepareBtn.textContent = "Готовлю...";
    try {
      await api("/api/run-script-prepare", { method: "POST", body: JSON.stringify(payload) });
      await refreshAll();
      await waitForJobCompletion("script_prepare", "Script preparation failed. Check the log.");
      await fillLatestScriptJsonl(false);
    } finally {
      scriptPrepareBtn.textContent = originalText;
    }
  } catch (error) {
    alert(error.message);
  }
});

scriptVoiceBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!payload.scriptJsonl) {
      const path = await fillLatestScriptJsonl(false);
      if (path) payload.scriptJsonl = path;
    }
    if (!payload.scriptJsonl) {
      alert("Сначала подготовьте сценарий или выберите .script.dub.jsonl.");
      return;
    }
    await api("/api/run-script-voice", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

const CONTENT_WORDS_PER_MINUTE = {
  russian: 130,
  english: 145,
  spanish: 150,
  portuguese: 140,
  bulgarian: 120,
};

function updateContentWordEstimate() {
  const estimateEl = document.getElementById("contentWordEstimate");
  if (!estimateEl) return;
  const minutes = Number(field("contentTargetDuration").value || 0);
  const language = String(field("contentTargetLanguage").value || "").toLowerCase();
  const wpm = CONTENT_WORDS_PER_MINUTE[language] || 130;
  if (minutes > 0) {
    estimateEl.textContent = `≈ ${Math.round(minutes * wpm)} слов (${wpm} сл/мин)`;
  } else {
    estimateEl.textContent = "";
  }
}

field("contentTargetDuration").addEventListener("input", updateContentWordEstimate);
field("contentTargetLanguage").addEventListener("change", updateContentWordEstimate);
updateContentWordEstimate();

useLatestSubtitlesBtn.addEventListener("click", async () => {
  try {
    const data = await api("/api/latest-youtube?kind=subtitle");
    if (!data.path) {
      alert("Скачанные субтитры в inputs\\youtube не найдены.");
      return;
    }
    field("contentSubtitles").value = data.path;
  } catch (error) {
    alert(error.message);
  }
});

useLatestContentScriptBtn.addEventListener("click", async () => {
  try {
    await fillLatestContentScript(true);
  } catch (error) {
    alert(error.message);
  }
});

contentGenerateBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!String(payload.contentSubtitles || "").trim()) {
      const data = await api("/api/latest-youtube?kind=subtitle");
      if (data.path) {
        field("contentSubtitles").value = data.path;
        payload.contentSubtitles = data.path;
      }
    }
    if (!String(payload.contentSubtitles || "").trim()) {
      alert("Выбери SRT/VTT субтитры для генерации контента.");
      return;
    }
    const originalText = contentGenerateBtn.textContent;
    contentGenerateBtn.textContent = "Генерирую...";
    try {
      await api("/api/run-content-generate", { method: "POST", body: JSON.stringify(payload) });
      await refreshAll();
      await waitForJobCompletion("content_generate", "Content generation failed. Check the log.");
      await fillLatestContentScript(false);
    } finally {
      contentGenerateBtn.textContent = originalText;
    }
  } catch (error) {
    alert(error.message);
  }
});

contentPrepareVoiceBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!String(payload.contentScriptFile || "").trim()) {
      const path = await fillLatestContentScript(false);
      if (path) payload.contentScriptFile = path;
    }
    if (!String(payload.contentScriptFile || "").trim()) {
      alert("Сначала сгенерируй контент или выбери .content.script.txt.");
      return;
    }
    const originalText = contentPrepareVoiceBtn.textContent;
    contentPrepareVoiceBtn.textContent = "Готовлю...";
    try {
      await api("/api/run-content-voice-prepare", { method: "POST", body: JSON.stringify(payload) });
      await refreshAll();
      await waitForJobCompletion("content_voice_prepare", "Content voice preparation failed. Check the log.");
      await fillLatestContentJsonl(false);
    } finally {
      contentPrepareVoiceBtn.textContent = originalText;
    }
  } catch (error) {
    alert(error.message);
  }
});

useLatestContentJsonlBtn.addEventListener("click", async () => {
  try {
    await fillLatestContentJsonl(true);
  } catch (error) {
    alert(error.message);
  }
});

contentVoiceBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!payload.contentVoiceJsonl) {
      const path = await fillLatestContentJsonl(false);
      if (path) payload.contentVoiceJsonl = path;
    }
    if (!payload.contentVoiceJsonl) {
      alert("Сначала подготовь паузы и эмоции или выбери content JSONL.");
      return;
    }
    await api("/api/run-content-voice", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

cleanWavBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!String(payload.cleanWavInput || "").trim()) {
      const data = await api("/api/latest-audio");
      if (data.path) {
        field("cleanWavInput").value = data.path;
        payload.cleanWavInput = data.path;
      }
    }
    if (!String(payload.cleanWavInput || "").trim()) {
      alert("Выбери WAV для очистки.");
      return;
    }
    updateCleanPreview(payload.cleanWavInput, "original");
    updateCleanPreview("", "result");
    await api("/api/run-clean-wav", {
      method: "POST",
      body: JSON.stringify({
        cleanWavInput: payload.cleanWavInput,
        cleanWavOutput: payload.cleanWavOutput,
        mode: payload.cleanWavMode,
        minPause: payload.cleanWavMinPause,
        fadeMs: payload.cleanWavFadeMs,
        preset: "custom",
        amount: payload.cleanWavAmount,
        deesser: payload.cleanWavDeesser,
        eq: payload.cleanWavEq,
        compressor: payload.cleanWavCompressor,
        saturation: payload.cleanWavSaturation,
        limiter: payload.cleanWavLimiter,
        loudness: payload.cleanWavLoudness,
        room: payload.cleanWavRoom,
        deesserFreq: payload.cleanWavDeesserFreq,
        deesserStrength: payload.cleanWavDeesserStrength,
        eqMudDb: payload.cleanWavEqMudDb,
        eqClarityDb: payload.cleanWavEqClarityDb,
        eqAirDb: payload.cleanWavEqAirDb,
        compThreshold: payload.cleanWavCompThreshold,
        compRatio: payload.cleanWavCompRatio,
        compMakeup: payload.cleanWavCompMakeup,
        satDrive: payload.cleanWavSatDrive,
        satMix: payload.cleanWavSatMix,
        limiterCeiling: payload.cleanWavLimiterCeiling,
        loudnessTarget: payload.cleanWavLoudnessTarget,
        roomMix: payload.cleanWavRoomMix,
        normalize: payload.cleanWavNormalize,
      }),
    });
    await refreshAll();
    const result = await waitForJobCompletion("wav_clean", "WAV cleanup failed. Check the log.");
    if (result.wav) {
      updateCleanPreview(result.wav, "result");
      field("cleanWavOutput").value = result.wav;
    }
    await refreshState();
  } catch (error) {
    alert(error.message);
  }
});

clearTranscriptionBtn.addEventListener("click", () => {
  field("transcribeVideo").value = "";
  field("transcribeAudio").value = "";
  field("transcribeOutputPrefix").value = "";
});

youtubeDownloadBtn.addEventListener("click", async () => {
  try {
    const payload = values();
    if (!String(payload.youtubeUrl || "").trim()) {
      alert("Вставь YouTube URL.");
      return;
    }
    if (payload.youtubeMode === "subtitles" && !String(payload.youtubeSubtitleLanguages || "").trim()) {
      alert("Выбери языки субтитров.");
      return;
    }
    await api("/api/run-youtube-download", { method: "POST", body: JSON.stringify(payload) });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

youtubeDetectSubsBtn.addEventListener("click", async () => {
  const originalText = youtubeDetectSubsBtn.textContent;
  youtubeDetectSubsBtn.disabled = true;
  youtubeDetectSubsBtn.textContent = "Ищу...";
  try {
    const payload = values();
    if (!String(payload.youtubeUrl || "").trim()) {
      alert("Вставь YouTube URL.");
      return;
    }
    const data = await api("/api/youtube-subtitles", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    setSelectedSubtitleCodes([]);
    renderSubtitleChips(data);
    if (!(data.manual || []).length && !(data.automatic || []).length) {
      alert("Для этого видео субтитры не найдены.");
    }
  } catch (error) {
    alert(error.message);
  } finally {
    youtubeDetectSubsBtn.disabled = false;
    youtubeDetectSubsBtn.textContent = originalText;
  }
});

field("youtubeSubtitleLanguages").addEventListener("input", () => {
  if (lastYoutubeSubtitles) renderSubtitleChips(lastYoutubeSubtitles);
});

youtubeCheckBtn.addEventListener("click", async () => {
  youtubeCheckBtn.disabled = true;
  try {
    const data = await api("/api/yt-dlp-status");
    alert(data.message || "yt-dlp OK");
  } catch (error) {
    alert(error.message);
  } finally {
    youtubeCheckBtn.disabled = false;
  }
});

youtubeUpdateBtn.addEventListener("click", async () => {
  try {
    await api("/api/run-ytdlp-update", { method: "POST", body: "{}" });
    await refreshAll();
  } catch (error) {
    alert(error.message);
  }
});

async function uploadAudioFile(file, kind, target) {
  const formData = new FormData();
  formData.append("kind", kind);
  formData.append("file", file);

  const response = await fetch("/api/upload-file", {
    method: "POST",
    body: formData,
  });
  const data = await response.json();
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || response.statusText);
  }
  field(target).value = data.path;
  if (target === "cleanWavInput") {
    updateCleanPreview(data.path, "original");
    updateCleanPreview("", "result");
  }
  await refreshState();
}

for (const button of document.querySelectorAll("[data-pick-file]")) {
  button.addEventListener("click", async () => {
    const kind = button.dataset.pickFile;
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
    if (!file) return;
    const kind = input.dataset.fileInput;
    const zone = document.querySelector(`[data-drop-kind="${kind}"]`);
    const target = zone?.dataset.target;
    if (!target) return;

    try {
      await uploadAudioFile(file, kind, target);
    } catch (error) {
      alert(error.message);
    }
  });
}

for (const zone of document.querySelectorAll("[data-drop-kind]")) {
  zone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragover", (event) => {
    event.preventDefault();
    zone.classList.add("drag-over");
  });
  zone.addEventListener("dragleave", (event) => {
    if (!zone.contains(event.relatedTarget)) {
      zone.classList.remove("drag-over");
    }
  });
  zone.addEventListener("drop", async (event) => {
    event.preventDefault();
    zone.classList.remove("drag-over");
    const file = event.dataTransfer?.files?.[0];
    if (!file) return;
    try {
      await uploadAudioFile(file, zone.dataset.dropKind, zone.dataset.target);
    } catch (error) {
      alert(error.message);
    }
  });
}

async function transcribeReference({ audioField, textField, button, missingMessage }) {
  const payload = values();
  const audio = String(payload[audioField] || "").trim();
  if (!audio) {
    alert(missingMessage);
    return;
  }

  payload.refAudio = audio;
  button.disabled = true;
  const originalText = button.textContent;
  button.textContent = "Whisper...";
  try {
    await api("/api/transcribe-reference", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    const result = await waitForReferenceTranscription();
    field(textField).value = result.text || "";
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

transcribeRefBtn.addEventListener("click", async () => {
  await transcribeReference({
    audioField: "refAudio",
    textField: "refText",
    button: transcribeRefBtn,
    missingMessage: "Сначала выбери Reference audio.",
  });
});

transcribeTranslateRefBtn.addEventListener("click", async () => {
  await transcribeReference({
    audioField: "translateRefAudio",
    textField: "translateRefText",
    button: transcribeTranslateRefBtn,
    missingMessage: "Сначала выбери Reference audio в разделе Перевод.",
  });
});

transcribeScriptRefBtn.addEventListener("click", async () => {
  await transcribeReference({
    audioField: "scriptRefAudio",
    textField: "scriptRefText",
    button: transcribeScriptRefBtn,
    missingMessage: "Сначала выбери Reference audio в разделе Сценарий.",
  });
});

transcribeContentRefBtn.addEventListener("click", async () => {
  await transcribeReference({
    audioField: "contentRefAudio",
    textField: "contentRefText",
    button: transcribeContentRefBtn,
    missingMessage: "Сначала выбери Reference audio в разделе Контент.",
  });
});

async function waitForReferenceTranscription() {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const state = await api("/api/state");
    lastState = state;
    renderJob(state.job);
    renderOutputs(state.outputs);
    renderYouTubeList(state.youtube || []);

    if (state.job.kind !== "reference_transcribe") {
      throw new Error("Reference transcription was replaced by another job.");
    }
    if (state.job.running) continue;
    if (state.job.exit_code === 0) return state.job.result || {};
    throw new Error("Reference transcription failed. Check the log.");
  }
}

async function waitForJobCompletion(kind, failureMessage) {
  while (true) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const state = await api("/api/state");
    lastState = state;
    renderJob(state.job);
    renderOutputs(state.outputs);
    renderYouTubeList(state.youtube || []);

    if (state.job.kind !== kind) {
      throw new Error("Job was replaced by another task.");
    }
    if (state.job.running) continue;
    if (state.job.exit_code === 0) return state.job.result || {};
    throw new Error(failureMessage);
  }
}

closePreview.addEventListener("click", () => previewDialog.close());

// ---------------------------------------------------------------- Engines tab
// Translation (any LLM provider) and voice (any TTS engine) settings shared by
// every tab. The server keeps them in config/settings.local.json.

const llmProviderEl = document.getElementById("llmProvider");
const llmProviderHintEl = document.getElementById("llmProviderHint");
const llmUrlEl = document.getElementById("llmUrl");
const llmKeyFieldEl = document.getElementById("llmKeyField");
const llmApiKeyEl = document.getElementById("llmApiKey");
const llmKeyStateEl = document.getElementById("llmKeyState");
const llmSaveKeyBtn = document.getElementById("llmSaveKeyBtn");
const llmModelEl = document.getElementById("llmModel");
const llmModelListEl = document.getElementById("llmModelList");
const llmModelsEl = document.getElementById("llmModels");
const llmRefreshModelsBtn = document.getElementById("llmRefreshModelsBtn");
const llmContextEl = document.getElementById("llmContext");
const llmAutoLoadEl = document.getElementById("llmAutoLoad");
const llmUnloadOthersEl = document.getElementById("llmUnloadOthers");
const llmUnloadWhenDoneEl = document.getElementById("llmUnloadWhenDone");
const llmTestBtn = document.getElementById("llmTestBtn");
const llmUnloadBtn = document.getElementById("llmUnloadBtn");
const llmTestResultEl = document.getElementById("llmTestResult");
const llmStatusEl = document.getElementById("llmStatus");
const ttsEngineEl = document.getElementById("ttsEngine");
const ttsEngineInfoEl = document.getElementById("ttsEngineInfo");
const ttsInstallEl = document.getElementById("ttsInstall");
const ttsDeviceEl = document.getElementById("ttsDevice");
const ttsSettingsEl = document.getElementById("ttsSettings");
const ttsTestTextEl = document.getElementById("ttsTestText");
const ttsTestLanguageEl = document.getElementById("ttsTestLanguage");
const ttsTestBtn = document.getElementById("ttsTestBtn");
const ttsTestPlayer = document.getElementById("ttsTestPlayer");
const ttsStatusEl = document.getElementById("ttsStatus");

let engineState = { settings: null, providers: [], keys: {}, engines: [] };
let settingsSaveTimer = null;

function providerById(id) {
  return engineState.providers.find((item) => item.id === id)
    || engineState.providers.find((item) => item.id === "custom")
    || { id, label: id };
}

function engineById(id) {
  return engineState.engines.find((item) => item.id === id);
}

async function loadEngineSettings() {
  const data = await api("/api/settings");
  engineState = { settings: data.settings, providers: data.providers, keys: data.keys, engines: data.engines };
  renderLlmForm();
  renderTtsForm();
  renderEngineSummaries();
}

// ---------------------------------------------------------------- LLM

function renderLlmForm() {
  const llm = engineState.settings.llm;
  if (llmProviderEl.options.length !== engineState.providers.length) {
    llmProviderEl.innerHTML = "";
    for (const provider of engineState.providers) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      llmProviderEl.appendChild(option);
    }
  }
  llmProviderEl.value = llm.provider;
  if (document.activeElement !== llmUrlEl) llmUrlEl.value = llm.url || "";
  if (document.activeElement !== llmModelEl) llmModelEl.value = llm.model || "";
  llmContextEl.value = llm.contextLength || 8192;
  llmAutoLoadEl.checked = llm.autoLoad !== false;
  llmUnloadOthersEl.checked = llm.unloadOthers !== false;
  llmUnloadWhenDoneEl.checked = llm.unloadWhenDone !== false;
  updateProviderFields();
}

function updateProviderFields() {
  const provider = providerById(llmProviderEl.value);
  llmProviderHintEl.textContent = provider.hint || "";
  llmUrlEl.placeholder = provider.base_url ? `По умолчанию: ${provider.base_url}` : "http://127.0.0.1:ПОРТ/v1";
  const masked = engineState.keys[provider.id];
  llmKeyFieldEl.hidden = !(provider.needs_key || masked || provider.id === "custom");
  llmKeyStateEl.textContent = masked ? `— сохранён ${masked}` : provider.needs_key ? "— нужен" : "— если сервер требует";
  const managed = provider.manage === "lmstudio";
  llmAutoLoadEl.closest("label").hidden = !managed;
  llmUnloadOthersEl.closest("label").hidden = !managed;
}

function llmFormValues() {
  return {
    provider: llmProviderEl.value,
    url: llmUrlEl.value.trim(),
    model: llmModelEl.value.trim(),
    contextLength: Number(llmContextEl.value || 8192),
    autoLoad: llmAutoLoadEl.checked,
    unloadOthers: llmUnloadOthersEl.checked,
    unloadWhenDone: llmUnloadWhenDoneEl.checked,
  };
}

function scheduleSettingsSave() {
  clearTimeout(settingsSaveTimer);
  settingsSaveTimer = setTimeout(() => saveEngineSettings().catch((error) => alert(error.message)), 400);
}

async function saveEngineSettings() {
  clearTimeout(settingsSaveTimer);
  const data = await api("/api/settings", {
    method: "POST",
    body: JSON.stringify({ llm: llmFormValues(), tts: ttsFormValues() }),
  });
  engineState.settings = data.settings;
  engineState.keys = data.keys;
  engineState.engines = data.engines;
  renderEngineSummaries();
}

async function refreshLlmModels() {
  const values = llmFormValues();
  const params = new URLSearchParams({ provider: values.provider, url: values.url });
  badge(llmStatusEl, "Проверяю...", "neutral");
  const data = await api(`/api/llm/models?${params}`);
  llmModelListEl.innerHTML = "";
  llmModelsEl.innerHTML = "";
  if (!data.ok) {
    badge(llmStatusEl, "Нет связи", "bad");
    const row = document.createElement("div");
    row.className = "meta";
    row.textContent = `${data.error} ${providerById(values.provider).hint || ""}`;
    llmModelsEl.appendChild(row);
    return;
  }
  badge(llmStatusEl, data.models.length ? `Доступно моделей: ${data.models.length}` : "Моделей нет", data.models.length ? "ok" : "warn");
  for (const model of data.models) {
    const option = document.createElement("option");
    option.value = model.id;
    llmModelListEl.appendChild(option);

    const row = document.createElement("button");
    row.type = "button";
    row.className = model.id === llmModelEl.value ? "model-row selected" : "model-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(model.name || model.id)}</strong>
        <div class="meta">${escapeHtml(model.id)}${model.details ? ` · ${escapeHtml(model.details)}` : ""}</div>
      </div>
      <div class="${model.loaded ? "loaded" : "meta"}">${model.loaded ? "Загружена" : ""}</div>
    `;
    row.addEventListener("click", () => {
      llmModelEl.value = model.id;
      for (const other of llmModelsEl.querySelectorAll(".model-row")) other.classList.remove("selected");
      row.classList.add("selected");
      scheduleSettingsSave();
    });
    llmModelsEl.appendChild(row);
  }
}

llmProviderEl.addEventListener("change", async () => {
  llmUrlEl.value = "";
  llmModelEl.value = "";
  llmTestResultEl.hidden = true;
  updateProviderFields();
  await saveEngineSettings();
  refreshLlmModels().catch(() => {});
});
for (const element of [llmUrlEl, llmModelEl, llmContextEl, llmAutoLoadEl, llmUnloadOthersEl, llmUnloadWhenDoneEl]) {
  element.addEventListener("change", scheduleSettingsSave);
}
llmUrlEl.addEventListener("change", () => refreshLlmModels().catch(() => {}));
llmRefreshModelsBtn.addEventListener("click", () => refreshLlmModels().catch((error) => alert(error.message)));

llmSaveKeyBtn.addEventListener("click", async () => {
  try {
    const data = await api("/api/llm/key", {
      method: "POST",
      body: JSON.stringify({ provider: llmProviderEl.value, apiKey: llmApiKeyEl.value.trim() }),
    });
    llmApiKeyEl.value = "";
    engineState.keys = data.keys;
    updateProviderFields();
    refreshLlmModels().catch(() => {});
  } catch (error) {
    alert(error.message);
  }
});

llmTestBtn.addEventListener("click", async () => {
  llmTestBtn.disabled = true;
  llmTestResultEl.hidden = false;
  llmTestResultEl.className = "test-result";
  llmTestResultEl.textContent = "Отправляю пробный запрос... (первая загрузка модели может занять минуту)";
  try {
    await saveEngineSettings();
    const data = await api("/api/llm/test", { method: "POST", body: JSON.stringify(llmFormValues()) });
    llmTestResultEl.className = "test-result ok";
    llmTestResultEl.textContent = `Работает: ${data.model} ответила за ${data.seconds} с:\n${data.answer}`;
    refreshLlmModels().catch(() => {});
  } catch (error) {
    llmTestResultEl.className = "test-result bad";
    llmTestResultEl.textContent = `Не получилось: ${error.message}`;
  } finally {
    llmTestBtn.disabled = false;
  }
});

llmUnloadBtn.addEventListener("click", async () => {
  try {
    await api("/api/llm/unload", { method: "POST", body: JSON.stringify(llmFormValues()) });
    await refreshLlmModels();
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------------- TTS

const CAPABILITY_LABELS = {
  voice_clone: "клонирует голос по образцу",
  voice_list: "готовые голоса сервера",
  duration: "точная длительность фразы",
  speed: "управление скоростью",
  instruct: "текстовые подсказки к голосу",
};

function renderTtsForm() {
  const tts = engineState.settings.tts;
  ttsEngineEl.innerHTML = "";
  for (const engine of engineState.engines) {
    const option = document.createElement("option");
    option.value = engine.id;
    const installed = engine.status?.installed;
    option.textContent = `${engine.name}${installed ? "" : " — не установлен"}`;
    ttsEngineEl.appendChild(option);
  }
  ttsEngineEl.value = tts.engine;
  ttsDeviceEl.value = tts.device || "auto";
  renderEngineDetails();
}

function licenseBadge(engine) {
  if (engine.commercial_use === true) return `<span class="badge ok">можно в коммерции</span>`;
  if (engine.commercial_use === false) return `<span class="badge warn">только некоммерческое</span>`;
  return `<span class="badge neutral">лицензия зависит от сервера</span>`;
}

function renderEngineDetails() {
  const engine = engineById(ttsEngineEl.value);
  ttsSettingsEl.innerHTML = "";
  ttsInstallEl.innerHTML = "";
  if (!engine) {
    ttsEngineInfoEl.textContent = "Движок не найден в папке engines/.";
    badge(ttsStatusEl, "Нет", "bad");
    return;
  }
  const languages = Array.isArray(engine.languages) ? engine.languages.join(", ") : (engine.languages || "");
  const capabilities = Object.entries(engine.supports || {})
    .filter(([, enabled]) => enabled)
    .map(([key]) => `<span class="chip">${escapeHtml(CAPABILITY_LABELS[key] || key)}</span>`)
    .join("");
  ttsEngineInfoEl.innerHTML = `
    <p>${escapeHtml(engine.description || "")}</p>
    <div class="chip-row">${capabilities}</div>
    <div class="meta">Лицензия: ${escapeHtml(engine.license || "?")} ${licenseBadge(engine)}</div>
    ${languages ? `<div class="meta">Языки: ${escapeHtml(languages)}</div>` : ""}
    ${engine.homepage ? `<div class="meta"><a href="${escapeHtml(engine.homepage)}" target="_blank" rel="noreferrer">${escapeHtml(engine.homepage)}</a></div>` : ""}
  `;

  const status = engine.status || {};
  badge(ttsStatusEl, status.installed ? "Готов" : "Не установлен", status.installed ? "ok" : "warn");
  const installBlock = document.createElement("div");
  installBlock.innerHTML = `
    <div class="section-title">Установка</div>
    <div class="meta">${escapeHtml(status.message || "")}${status.python ? `<br><code>${escapeHtml(status.python)}</code>` : ""}</div>
    ${engine.install_note ? `<div class="meta">${escapeHtml(engine.install_note)}</div>` : ""}
  `;
  if (engine.runtime !== "main") {
    if (!status.installed && status.installable !== false) {
      const installBtn = actionButton("Установить движок", () => installEngine(engine.id));
      installBtn.className = "primary";
      installBtn.dataset.jobButton = "1";
      installBlock.appendChild(installBtn);
    }
    const pythonRow = document.createElement("div");
    pythonRow.className = "input-action python-row";
    pythonRow.innerHTML = `<input placeholder="Или путь к python.exe уже готового окружения с этой моделью">`;
    const pythonInput = pythonRow.querySelector("input");
    pythonInput.value = status.source === "override" ? status.python : "";
    pythonRow.appendChild(actionButton(status.source === "override" ? "Сохранить путь" : "Указать", async () => {
      try {
        const data = await api("/api/engines/python", {
          method: "POST",
          body: JSON.stringify({ engine: engine.id, python: pythonInput.value.trim() }),
        });
        engineState.engines = data.engines;
        renderTtsForm();
        renderEngineSummaries();
      } catch (error) {
        alert(error.message);
      }
    }));
    installBlock.appendChild(pythonRow);
  }
  ttsInstallEl.appendChild(installBlock);

  const values = engine.values || {};
  const basic = document.createElement("div");
  const advanced = document.createElement("details");
  advanced.className = "accordion";
  advanced.innerHTML = "<summary>Дополнительные настройки движка</summary>";
  for (const item of engine.settings || []) {
    (item.advanced ? advanced : basic).appendChild(settingField(item, values[item.key]));
  }
  ttsSettingsEl.appendChild(basic);
  if (advanced.children.length > 1) ttsSettingsEl.appendChild(advanced);

  if (engine.supports?.voice_list) {
    const voicesRow = document.createElement("div");
    voicesRow.className = "voices-row";
    const list = document.createElement("div");
    list.className = "chip-row";
    voicesRow.appendChild(actionButton("Голоса сервера", async () => {
      try {
        await saveEngineSettings();
        const data = await api("/api/engines/voices", {
          method: "POST",
          body: JSON.stringify({ engine: engine.id, settings: ttsFormValues().engines[engine.id] }),
        });
        list.innerHTML = "";
        for (const voice of data.voices) {
          const chip = actionButton(voice, () => {
            const input = ttsSettingsEl.querySelector('[data-setting="voice"]');
            if (input) {
              input.value = voice;
              scheduleSettingsSave();
            }
          });
          list.appendChild(chip);
        }
      } catch (error) {
        alert(error.message);
      }
    }));
    voicesRow.appendChild(list);
    ttsSettingsEl.appendChild(voicesRow);
  }
}

function settingField(item, value) {
  const label = document.createElement("label");
  label.className = item.type === "bool" ? "inline-check" : "field wide";
  if (item.help) label.title = item.help;
  let input;
  if (item.type === "select") {
    input = document.createElement("select");
    for (const optionValue of item.options || []) {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = optionValue;
      input.appendChild(option);
    }
    input.value = value ?? item.default ?? "";
  } else if (item.type === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(value ?? item.default);
  } else {
    input = document.createElement("input");
    if (item.type === "int" || item.type === "float") {
      input.type = "number";
      if (item.min !== undefined) input.min = item.min;
      if (item.max !== undefined) input.max = item.max;
      input.step = item.step ?? (item.type === "int" ? 1 : 0.05);
    }
    input.value = value ?? item.default ?? "";
  }
  input.dataset.setting = item.key;
  input.dataset.type = item.type || "text";
  input.addEventListener("change", scheduleSettingsSave);

  const caption = document.createElement("span");
  caption.textContent = item.label || item.key;
  if (item.type === "bool") {
    label.append(input, caption);
  } else {
    label.append(caption, input);
    if (item.help) {
      const help = document.createElement("small");
      help.className = "muted";
      help.textContent = item.help;
      label.appendChild(help);
    }
  }
  return label;
}

function ttsFormValues() {
  const engineId = ttsEngineEl.value || engineState.settings?.tts?.engine || "omnivoice";
  const values = {};
  for (const input of ttsSettingsEl.querySelectorAll("[data-setting]")) {
    const type = input.dataset.type;
    if (type === "bool") values[input.dataset.setting] = input.checked;
    else if (type === "int") values[input.dataset.setting] = parseInt(input.value, 10);
    else if (type === "float") values[input.dataset.setting] = Number(input.value);
    else values[input.dataset.setting] = input.value;
  }
  return { engine: engineId, device: ttsDeviceEl.value, engines: { [engineId]: values } };
}

ttsEngineEl.addEventListener("change", async () => {
  ttsSettingsEl.innerHTML = "";  // do not save the previous engine's fields under the new id
  ttsTestPlayer.hidden = true;
  await saveEngineSettings();
  renderEngineDetails();
});
ttsDeviceEl.addEventListener("change", scheduleSettingsSave);

async function installEngine(engineId) {
  const engine = engineById(engineId);
  if (!confirm(`Установить «${engine?.name || engineId}»?\n\n${engine?.install_note || ""}\n\nПрогресс будет в логе справа.`)) return;
  try {
    await api("/api/engines/install", { method: "POST", body: JSON.stringify({ engine: engineId }) });
    await refreshState();
    await waitForJobCompletion("engine_install", "Установка не удалась. Подробности в логе.");
  } catch (error) {
    alert(error.message);
  } finally {
    await loadEngineSettings();
  }
}

ttsTestBtn.addEventListener("click", async () => {
  try {
    await saveEngineSettings();
    const payload = values();
    await api("/api/engines/test", {
      method: "POST",
      body: JSON.stringify({
        engine: ttsEngineEl.value,
        text: ttsTestTextEl.value,
        language: ttsTestLanguageEl.value,
        refAudio: payload.refAudio || "",
        refText: payload.refText || "",
      }),
    });
    await refreshState();
    const result = await waitForJobCompletion("engine_test", "Движок не смог озвучить фразу. Подробности в логе.");
    if (result.name) {
      ttsTestPlayer.src = outputFileUrl(result.name);
      ttsTestPlayer.hidden = false;
      ttsTestPlayer.play().catch(() => {});
    }
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------------- summaries in tabs and header

function renderEngineSummaries() {
  const settings = engineState.settings;
  if (!settings) return;
  const provider = providerById(settings.llm.provider);
  const model = settings.llm.model || (provider.manage === "lmstudio" ? "загруженная модель" : "модель не выбрана");
  const llmText = `${provider.label} · ${model}`;
  const engine = engineById(settings.tts.engine);
  const ttsText = engine ? `${engine.name}${engine.status?.installed ? "" : " (не установлен)"}` : settings.tts.engine;
  for (const element of document.querySelectorAll("[data-llm-summary]")) element.textContent = llmText;
  for (const element of document.querySelectorAll("[data-tts-summary]")) element.textContent = ttsText;
  badge(llmBadge, `Перевод: ${provider.label}`, settings.llm.model || provider.manage === "lmstudio" ? "ok" : "warn");
  llmBadge.title = llmText;
  badge(ttsBadge, `Озвучка: ${engine ? engine.name : settings.tts.engine}`, engine?.status?.installed ? "ok" : "bad");
  ttsBadge.title = ttsText;
}

setInterval(() => {
  refreshState().catch(() => {});
}, 2000);

refreshAll()
  .then(() => {
    const engine = engineById(engineState.settings.tts.engine);
    // First run: nothing configured yet, start on the Engines tab.
    if (!engine?.status?.installed) showSection("engines");
    const provider = providerById(engineState.settings.llm.provider);
    if (provider.local || engineState.keys[provider.id]) refreshLlmModels().catch(() => {});
  })
  .catch((error) => {
    logEl.textContent = error.message;
  });

