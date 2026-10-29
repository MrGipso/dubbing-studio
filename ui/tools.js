// Tools view: standalone operations without a project (text/script voiceover,
// transcription, voice processing, music mix). Ported from the original UI.

const TRANSLATION_CODES = {
  English: "en", Russian: "ru", Ukrainian: "uk", Spanish: "es", Portuguese: "pt",
  German: "de", French: "fr", Italian: "it", Polish: "pl", Bulgarian: "bg",
  Turkish: "tr", Dutch: "nl", Swedish: "sv", Danish: "da", Norwegian: "no",
  Finnish: "fi", Greek: "el", Hebrew: "he", Arabic: "ar", Hindi: "hi",
  Japanese: "ja", Korean: "ko", Chinese: "zh", Malay: "ms", Swahili: "sw",
};

function numberOr(value, fallback) {
  const text = String(value ?? "").trim();
  if (!text) return fallback;
  const number = Number(text);
  return Number.isFinite(number) ? number : fallback;
}

function toolValues() {
  const payload = {};
  for (const input of document.querySelectorAll('[data-view="tools"] input[name], [data-view="tools"] select[name], [data-view="tools"] textarea[name]')) {
    if (input.type === "file") continue;
    payload[input.name] = input.type === "checkbox" ? input.checked : input.value;
  }
  payload.musicVolumeDb = Number(payload.musicVolumeDb || -18);
  payload.transcribeOutputDir = payload.transcribeOutputDir || "outputs";
  payload.translateTemperature = Number(payload.translateTemperature || 0.2);
  payload.translateOutputDir = payload.translateOutputDir || "outputs";
  payload.scriptTemperature = Number(payload.scriptTemperature || 0.4);
  payload.scriptOutputDir = payload.scriptOutputDir || "outputs";
  for (const prefix of ["translateVoice", "scriptVoice"]) {
    payload[`${prefix}Speed`] = numberOr(payload[`${prefix}Speed`], 1);
    payload[`${prefix}TailRoom`] = numberOr(payload[`${prefix}TailRoom`], 0.8);
    payload[`${prefix}MaxAutoSpeed`] = numberOr(payload[`${prefix}MaxAutoSpeed`], 1.18);
    payload[`${prefix}PostFadeMs`] = numberOr(payload[`${prefix}PostFadeMs`], 40);
    payload[`${prefix}PostMinPause`] = numberOr(payload[`${prefix}PostMinPause`], 0.15);
  }
  const cleanDefaults = {
    FadeMs: 40, MinPause: 0.15, Amount: 55, DeesserFreq: 5200, DeesserStrength: 55, EqMudDb: -1.8,
    EqClarityDb: 2.2, EqAirDb: 1.1, CompThreshold: -20, CompRatio: 3, CompMakeup: 2.2, SatDrive: 45,
    SatMix: 28, LimiterCeiling: 0.95, LoudnessTarget: -16, RoomMix: 10,
  };
  for (const [name, fallback] of Object.entries(cleanDefaults)) {
    payload[`cleanWav${name}`] = numberOr(payload[`cleanWav${name}`], fallback);
  }
  return payload;
}

function runToolJob(path, payload) {
  return postJson(path, payload).then(() => refreshState());
}

// ---------------------------------------------------------------- output lists

const toolOutputLists = [
  ["translationOutputs", "translationFileCount"],
  ["scriptOutputs", "scriptFileCount"],
  ["transcriptionOutputs", "transcriptionFileCount"],
  ["cleanOutputs", "cleanFileCount"],
  ["musicOutputs", "musicFileCount"],
];
let lastToolOutputsSignature = "";

function renderOutputList(container, countElement, files) {
  countElement.textContent = String(files.length);
  container.innerHTML = "";
  for (const file of files) {
    const audio = isAudioFile(file);
    const row = document.createElement("div");
    row.className = audio ? "file-row audio-row" : "file-row";
    row.innerHTML = `
      <div>
        <strong translate="no">${escapeHtml(file.name)}</strong>
        <div class="meta">${escapeHtml(file.kind.toUpperCase())} · ${formatBytes(file.size)} · ${new Date(file.mtime * 1000).toLocaleString()}</div>
        <div class="download-actions"></div>
      </div>
      ${audio ? `<audio controls preload="none" src="${outputFileUrl(file.name)}"></audio>` : `<button type="button">${t("Открыть")}</button>`}
    `;
    const button = row.querySelector(":scope > button");
    if (button) button.addEventListener("click", () => preview(file.name));
    const actions = row.querySelector(".download-actions");
    if (audio && file.name.toLowerCase().endsWith(".wav")) {
      actions.appendChild(actionButton(t("Настроить обработку"), () => {
        const path = `outputs\\${file.name}`;
        field("cleanWavInput").value = path;
        updateCleanPreview(path, "original");
        showSection("clean");
      }));
      actions.appendChild(actionButton(t("Быстро очистить"), () => cleanOutputWav(file.name)));
    }
    container.appendChild(row);
  }
}

onState((state) => {
  for (const element of document.querySelectorAll('[data-view="tools"] [id$="Runtime"]')) {
    element.textContent = formatDuration(state.job.started_at, state.job.finished_at);
  }
  // Rebuilding the lists destroys audio players, so skip identical lists.
  const files = state.outputs || [];
  const signature = JSON.stringify(files.map((file) => [file.name, file.size, file.mtime]));
  if (signature === lastToolOutputsSignature) return;
  lastToolOutputsSignature = signature;
  for (const [listId, countId] of toolOutputLists) {
    renderOutputList(document.getElementById(listId), document.getElementById(countId), files);
  }
});

for (const id of ["translationRefreshBtn", "scriptRefreshBtn", "transcriptionRefreshBtn", "cleanRefreshBtn", "musicRefreshBtn"]) {
  document.getElementById(id)?.addEventListener("click", () => {
    lastToolOutputsSignature = "";
    refreshState().catch((error) => alert(error.message));
  });
}

// ---------------------------------------------------------------- reference transcription

async function transcribeReference({ audioField, textField, button, missingMessage }) {
  const payload = toolValues();
  const audio = String(payload[audioField] || "").trim();
  if (!audio) {
    alert(missingMessage);
    return;
  }
  payload.refAudio = audio;
  const originalText = button.textContent;
  button.textContent = "Whisper...";
  try {
    await postJson("/api/transcribe-reference", payload);
    await refreshState();
    const result = await waitForJobCompletion("reference_transcribe", t("Не удалось распознать образец. Подробности в логе."));
    field(textField).value = result.text || "";
  } catch (error) {
    alert(error.message);
  } finally {
    button.textContent = originalText;
  }
}

document.getElementById("transcribeTranslateRefBtn").addEventListener("click", (event) => transcribeReference({
  audioField: "translateRefAudio", textField: "translateRefText", button: event.currentTarget,
  missingMessage: t("Сначала выберите образец голоса."),
}));
document.getElementById("transcribeScriptRefBtn").addEventListener("click", (event) => transcribeReference({
  audioField: "scriptRefAudio", textField: "scriptRefText", button: event.currentTarget,
  missingMessage: t("Сначала выберите образец голоса."),
}));

// ---------------------------------------------------------------- translate text + voice

function syncTranslationTargetToVoiceLanguage() {
  const code = TRANSLATION_CODES[field("translateTargetLanguage").value];
  if (code) field("translateVoiceLanguage").value = code;
}

function updateVoiceSourceMode() {
  const source = field("translateVoiceSource").value;
  for (const element of document.querySelectorAll("[data-voice-source-field]")) {
    element.hidden = element.dataset.voiceSourceField !== source;
  }
}

async function fillLatestTranslationsJson(showMissingAlert = true) {
  const data = await api("/api/latest-translations");
  if (!data.path) {
    if (showMissingAlert) alert(t("Готовый перевод (.translations.json) не найден."));
    return "";
  }
  field("translateVoiceJson").value = data.path;
  field("translateVoiceSource").value = "translation";
  updateVoiceSourceMode();
  return data.path;
}

field("translateTargetLanguage").addEventListener("change", syncTranslationTargetToVoiceLanguage);
field("translateVoiceSource").addEventListener("change", updateVoiceSourceMode);
syncTranslationTargetToVoiceLanguage();
updateVoiceSourceMode();

document.getElementById("useLatestTranslationsBtn").addEventListener("click", () => {
  fillLatestTranslationsJson(true).catch((error) => alert(error.message));
});

document.getElementById("translationBtn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    const payload = toolValues();
    if (!String(payload.translateText || "").trim()) {
      alert(t("Вставьте текст для перевода."));
      return;
    }
    syncTranslationTargetToVoiceLanguage();
    const originalText = button.textContent;
    button.textContent = t("Перевожу...");
    try {
      await runToolJob("/api/run-text-translation", payload);
      await waitForJobCompletion("text_translation", t("Перевод не удался. Подробности в логе."));
      await fillLatestTranslationsJson(false);
    } finally {
      button.textContent = originalText;
    }
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("translationVoiceBtn").addEventListener("click", async () => {
  try {
    const payload = toolValues();
    if (payload.translateVoiceSource === "direct") {
      if (!String(payload.translateVoiceText || "").trim()) {
        alert(t("Вставьте готовый текст для озвучки."));
        return;
      }
    } else if (!payload.translateVoiceJson) {
      payload.translateVoiceJson = await fillLatestTranslationsJson(false);
      if (!payload.translateVoiceJson) {
        alert(t("Сначала переведите текст или выберите .translations.json."));
        return;
      }
    }
    await runToolJob("/api/run-text-voice", payload);
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------------- script

async function fillLatestScriptJsonl(showMissingAlert = true) {
  const data = await api("/api/latest-script-jsonl");
  if (!data.path) {
    if (showMissingAlert) alert(t("Готовый план озвучки сценария (.script.dub.jsonl) не найден."));
    return "";
  }
  field("scriptJsonl").value = data.path;
  return data.path;
}

document.getElementById("useLatestScriptJsonlBtn").addEventListener("click", () => {
  fillLatestScriptJsonl(true).catch((error) => alert(error.message));
});

document.getElementById("scriptPrepareBtn").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  try {
    const payload = toolValues();
    if (!String(payload.scriptText || "").trim()) {
      alert(t("Вставьте готовый сценарий."));
      return;
    }
    const originalText = button.textContent;
    button.textContent = t("Готовлю...");
    try {
      await runToolJob("/api/run-script-prepare", payload);
      await waitForJobCompletion("script_prepare", t("Подготовка сценария не удалась. Подробности в логе."));
      await fillLatestScriptJsonl(false);
    } finally {
      button.textContent = originalText;
    }
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("scriptVoiceBtn").addEventListener("click", async () => {
  try {
    const payload = toolValues();
    if (!payload.scriptJsonl) payload.scriptJsonl = await fillLatestScriptJsonl(false);
    if (!payload.scriptJsonl) {
      alert(t("Сначала подготовьте сценарий или выберите .script.dub.jsonl."));
      return;
    }
    await runToolJob("/api/run-script-voice", payload);
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------------- transcription

document.getElementById("transcriptionBtn").addEventListener("click", async () => {
  try {
    const payload = toolValues();
    if (!payload.transcribeVideo && !payload.transcribeAudio) {
      alert(t("Выберите видео или аудио для транскрибации."));
      return;
    }
    await runToolJob("/api/run-transcription", payload);
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("clearTranscriptionBtn").addEventListener("click", () => {
  field("transcribeVideo").value = "";
  field("transcribeAudio").value = "";
  field("transcribeOutputPrefix").value = "";
});

// ---------------------------------------------------------------- voice processing

const cleanOriginalPlayer = document.getElementById("cleanOriginalPlayer");
const cleanResultPlayer = document.getElementById("cleanResultPlayer");

const CLEAN_PRESETS = {
  clean: { deesser: false, eq: false, compressor: false, saturation: false, limiter: true, loudness: true, room: false, amount: 35, deesserFreq: 5200, deesserStrength: 45, eqMudDb: -1.0, eqClarityDb: 1.2, eqAirDb: 0.5, compThreshold: -18, compRatio: 2.0, compMakeup: 1.2, satDrive: 25, satMix: 12, limiterCeiling: 0.95, loudnessTarget: -16, roomMix: 5 },
  youtube: { deesser: true, eq: true, compressor: true, saturation: true, limiter: true, loudness: true, room: false, amount: 55, deesserFreq: 5200, deesserStrength: 55, eqMudDb: -1.8, eqClarityDb: 2.2, eqAirDb: 1.1, compThreshold: -20, compRatio: 3.0, compMakeup: 2.2, satDrive: 45, satMix: 28, limiterCeiling: 0.95, loudnessTarget: -16, roomMix: 10 },
  dense: { deesser: true, eq: true, compressor: true, saturation: true, limiter: true, loudness: true, room: false, amount: 70, deesserFreq: 5400, deesserStrength: 60, eqMudDb: -2.4, eqClarityDb: 2.8, eqAirDb: 1.0, compThreshold: -24, compRatio: 3.8, compMakeup: 3.0, satDrive: 65, satMix: 35, limiterCeiling: 0.94, loudnessTarget: -15, roomMix: 10 },
  soft: { deesser: true, eq: true, compressor: true, saturation: false, limiter: true, loudness: true, room: false, amount: 45, deesserFreq: 4800, deesserStrength: 65, eqMudDb: -1.4, eqClarityDb: 1.2, eqAirDb: -0.6, compThreshold: -18, compRatio: 2.2, compMakeup: 1.4, satDrive: 20, satMix: 10, limiterCeiling: 0.95, loudnessTarget: -17, roomMix: 8 },
  radio: { deesser: true, eq: true, compressor: true, saturation: true, limiter: true, loudness: true, room: false, amount: 80, deesserFreq: 5600, deesserStrength: 60, eqMudDb: -3.0, eqClarityDb: 3.2, eqAirDb: 0.8, compThreshold: -26, compRatio: 4.2, compMakeup: 3.8, satDrive: 70, satMix: 38, limiterCeiling: 0.93, loudnessTarget: -14.5, roomMix: 8 },
};

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

function applyCleanPreset() {
  const preset = CLEAN_PRESETS[field("cleanWavPreset").value];
  if (!preset) return;
  for (const name of ["Deesser", "Eq", "Compressor", "Saturation", "Limiter", "Loudness", "Room"]) {
    field(`cleanWav${name}`).checked = preset[name.charAt(0).toLowerCase() + name.slice(1)];
  }
  const numbers = {
    Amount: "amount", DeesserFreq: "deesserFreq", DeesserStrength: "deesserStrength", EqMudDb: "eqMudDb",
    EqClarityDb: "eqClarityDb", EqAirDb: "eqAirDb", CompThreshold: "compThreshold", CompRatio: "compRatio",
    CompMakeup: "compMakeup", SatDrive: "satDrive", SatMix: "satMix", LimiterCeiling: "limiterCeiling",
    LoudnessTarget: "loudnessTarget", RoomMix: "roomMix",
  };
  for (const [name, key] of Object.entries(numbers)) field(`cleanWav${name}`).value = String(preset[key]);
}

async function cleanOutputWav(fileName) {
  try {
    await runToolJob("/api/run-clean-wav", { fileName, mode: "soft", minPause: 0.15, fadeMs: 40, normalize: true });
  } catch (error) {
    alert(error.message);
  }
}

field("cleanWavPreset").addEventListener("change", applyCleanPreset);
field("cleanWavInput").addEventListener("change", () => {
  updateCleanPreview(field("cleanWavInput").value, "original");
  updateCleanPreview("", "result");
});
onToolUpload((target, path) => {
  if (target === "cleanWavInput") {
    updateCleanPreview(path, "original");
    updateCleanPreview("", "result");
  }
});

document.getElementById("useLatestCleanWavBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/latest-audio");
    if (!data.path) {
      alert(t("Готовый WAV не найден."));
      return;
    }
    field("cleanWavInput").value = data.path;
    updateCleanPreview(data.path, "original");
    updateCleanPreview("", "result");
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("cleanWavBtn").addEventListener("click", async () => {
  try {
    const payload = toolValues();
    if (!String(payload.cleanWavInput || "").trim()) {
      const data = await api("/api/latest-audio");
      if (data.path) field("cleanWavInput").value = payload.cleanWavInput = data.path;
    }
    if (!String(payload.cleanWavInput || "").trim()) {
      alert(t("Выберите WAV для обработки."));
      return;
    }
    updateCleanPreview(payload.cleanWavInput, "original");
    updateCleanPreview("", "result");
    await runToolJob("/api/run-clean-wav", {
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
    });
    const result = await waitForJobCompletion("wav_clean", t("Обработка не удалась. Подробности в логе."));
    if (result.wav) {
      updateCleanPreview(result.wav, "result");
      field("cleanWavOutput").value = result.wav;
    }
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------------- music mix

const musicVolumeDb = field("musicVolumeDb");
const musicVolumeDbValue = document.getElementById("musicVolumeDbValue");
function updateMusicVolumeLabel() {
  musicVolumeDbValue.textContent = `${musicVolumeDb.value} dB`;
}
musicVolumeDb.addEventListener("input", updateMusicVolumeLabel);
updateMusicVolumeLabel();

document.getElementById("useLatestAudioBtn").addEventListener("click", async () => {
  try {
    const data = await api("/api/latest-audio");
    if (!data.path) {
      alert(t("Готовый аудиофайл не найден."));
      return;
    }
    field("musicVoiceAudio").value = data.path;
  } catch (error) {
    alert(error.message);
  }
});

document.getElementById("musicMixBtn").addEventListener("click", async () => {
  try {
    const payload = toolValues();
    if (!payload.musicVoiceAudio) {
      const data = await api("/api/latest-audio");
      if (data.path) field("musicVoiceAudio").value = payload.musicVoiceAudio = data.path;
    }
    await runToolJob("/api/run-music-mix", payload);
  } catch (error) {
    alert(error.message);
  }
});
