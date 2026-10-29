// Projects view: project list and the four-step dubbing wizard
// (source -> voice -> translation review -> result).

const projectsListEl = document.getElementById("projectsList");
const projectViewEl = document.getElementById("projectView");
const projectCardsEl = document.getElementById("projectCards");
const newProjectDrop = document.getElementById("newProjectDrop");
const newProjectFile = document.getElementById("newProjectFile");
const newProjectTarget = document.getElementById("newProjectTarget");
const newProjectProgress = document.getElementById("newProjectProgress");

const pv = {
  name: document.getElementById("projectName"),
  source: document.getElementById("projectSource"),
  error: document.getElementById("projectError"),
  sourcePlayer: document.getElementById("sourcePlayer"),
  sourceLanguage: document.getElementById("projectSourceLanguage"),
  target: document.getElementById("projectTarget"),
  separate: document.getElementById("projectSeparate"),
  separatorHint: document.getElementById("separatorHint"),
  prepareSummary: document.getElementById("prepareSummary"),
  voiceOriginalInfo: document.getElementById("voiceOriginalInfo"),
  voiceFileBtn: document.getElementById("voiceFileBtn"),
  voiceFileInput: document.getElementById("voiceFileInput"),
  voiceFileInfo: document.getElementById("voiceFileInfo"),
  voiceRefText: document.getElementById("voiceRefText"),
  voiceEngineId: document.getElementById("voiceEngineId"),
  timingSpeed: document.getElementById("timingSpeed"),
  timingTail: document.getElementById("timingTail"),
  timingMaxSpeed: document.getElementById("timingMaxSpeed"),
  timingClean: document.getElementById("timingClean"),
  voiceTrackPlayer: document.getElementById("voiceTrackPlayer"),
  segments: document.getElementById("segmentsTable"),
  resultPlayer: document.getElementById("resultPlayer"),
  mixVoice: document.getElementById("mixVoice"),
  mixBackground: document.getElementById("mixBackground"),
  mixMusic: document.getElementById("mixMusic"),
  mixLoudnorm: document.getElementById("mixLoudnorm"),
  backgroundHint: document.getElementById("backgroundHint"),
  musicFileBtn: document.getElementById("musicFileBtn"),
  musicFileInput: document.getElementById("musicFileInput"),
  musicFileInfo: document.getElementById("musicFileInfo"),
  downloads: document.getElementById("downloads"),
};

let currentProject = null;
let currentStep = "source";
let projectSaveTimer = null;
let wasRunningForProject = false;

const STATE_LABELS = [
  ["prepared", "переведено"],
  ["voiced", "озвучено"],
  ["exported", "собрано"],
];

function projectUrl(id, action = "") {
  return `/api/projects/${encodeURIComponent(id)}${action ? `/${action}` : ""}`;
}

// ---------------------------------------------------------------- list

async function loadProjects() {
  const data = await api("/api/projects");
  renderProjectCards(data.projects || []);
}

function renderProjectCards(projects) {
  projectCardsEl.innerHTML = "";
  if (!projects.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "Проектов пока нет. Перетащите видео в поле выше, чтобы начать первый дубляж.";
    projectCardsEl.appendChild(empty);
    return;
  }
  for (const project of projects) {
    const card = document.createElement("div");
    card.className = "project-card";
    const state = project.state || {};
    const chips = STATE_LABELS.map(([key, label]) => `<span class="chip ${state[key] ? "chip-on" : ""}">${label}</span>`).join("");
    card.innerHTML = `
      <button type="button" class="project-open">
        <strong>${escapeHtml(project.name || project.id)}</strong>
        <span class="meta">${escapeHtml(project.source_name || "")} · ${formatClock(project.duration)} · → ${escapeHtml(languageLabel(project.target))}</span>
        <span class="chip-row">${chips}${project.error ? '<span class="chip chip-bad">ошибка</span>' : ""}</span>
        <span class="meta">${project.updated ? new Date(project.updated * 1000).toLocaleString() : ""}</span>
      </button>
      <button type="button" class="icon-btn project-delete" title="Удалить в корзину">×</button>
    `;
    card.querySelector(".project-open").addEventListener("click", () => openProject(project.id));
    card.querySelector(".project-delete").addEventListener("click", async () => {
      if (!confirm(`Удалить проект «${project.name}»?\n\nОн переместится в папку projects/.trash, оттуда его можно вернуть.`)) return;
      try {
        const data = await postJson(projectUrl(project.id, "delete"));
        renderProjectCards(data.projects || []);
      } catch (error) {
        alert(error.message);
      }
    });
    projectCardsEl.appendChild(card);
  }
}

function languageLabel(name) {
  const option = Array.from(newProjectTarget.options).find((item) => item.value === name);
  return option ? option.textContent : name || "";
}

async function createProject(file) {
  const formData = new FormData();
  formData.append("target", newProjectTarget.value);
  formData.append("name", file.name.replace(/\.[^.]+$/, ""));
  formData.append("file", file);
  newProjectProgress.hidden = false;
  newProjectProgress.textContent = "Загрузка 0%";
  try {
    const data = await uploadForm("/api/projects", formData, (fraction) => {
      newProjectProgress.textContent = `Загрузка ${Math.round(fraction * 100)}%`;
    });
    try {
      localStorage.setItem("dub-last-target", newProjectTarget.value);
    } catch {
      // not important
    }
    showProject(data.project);
  } catch (error) {
    alert(error.message);
  } finally {
    newProjectProgress.hidden = true;
  }
}

newProjectFile.addEventListener("change", () => {
  const file = newProjectFile.files && newProjectFile.files[0];
  if (file) createProject(file);
  newProjectFile.value = "";
});
enableDrop(newProjectDrop, createProject);
newProjectDrop.addEventListener("click", (event) => {
  if (!event.target.closest(".new-project-target")) newProjectFile.click();
});
newProjectDrop.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") newProjectFile.click();
});
try {
  const lastTarget = localStorage.getItem("dub-last-target");
  if (lastTarget) newProjectTarget.value = lastTarget;
} catch {
  // ignore
}

// ---------------------------------------------------------------- project view

async function openProject(id) {
  const data = await api(projectUrl(id));
  showProject(data.project);
}

function showProject(project) {
  const firstOpen = !currentProject || currentProject.id !== project.id;
  currentProject = project;
  projectsListEl.hidden = true;
  projectViewEl.hidden = false;
  showView("projects");
  renderProject(firstOpen);
  if (firstOpen) showStep(suggestedStep(project));
}

function suggestedStep(project) {
  if (project.state?.exported || project.state?.voiced) return "result";
  if (project.state?.prepared) return "voice";
  return "source";
}

function showStep(step) {
  currentStep = step;
  for (const button of document.querySelectorAll(".step")) button.classList.toggle("active", button.dataset.step === step);
  for (const pane of document.querySelectorAll(".step-pane")) pane.classList.toggle("active", pane.dataset.pane === step);
}

for (const button of document.querySelectorAll(".step")) {
  button.addEventListener("click", () => showStep(button.dataset.step));
}

document.getElementById("backToProjects").addEventListener("click", async () => {
  await flushProjectSave();
  currentProject = null;
  projectViewEl.hidden = true;
  projectsListEl.hidden = false;
  loadProjects().catch(() => {});
});

document.getElementById("projectOpenFolder").addEventListener("click", () => {
  if (currentProject) postJson(projectUrl(currentProject.id, "open")).catch((error) => alert(error.message));
});

function mediaElement(file, kind) {
  if (!file) return "";
  return kind === "video"
    ? `<video controls preload="metadata" src="${file.url}"></video>`
    : `<audio controls preload="none" src="${file.url}"></audio>`;
}

function setIfIdle(element, value) {
  if (document.activeElement !== element) element.value = value;
}

function renderProject(firstOpen = false) {
  const p = currentProject;
  const files = p.files || {};
  const state = p.state || {};
  const source = p.source || {};

  setIfIdle(pv.name, p.name || "");
  pv.source.textContent = `${source.original_name || ""} · ${formatClock(source.duration)} · ${source.has_video ? "видео" : "аудио"}`;
  pv.error.hidden = !p.error;
  pv.error.textContent = p.error ? `Ошибка: ${p.error}` : "";

  const steps = { source: state.prepared, voice: state.voiced, review: state.prepared, result: state.exported };
  for (const button of document.querySelectorAll(".step")) button.classList.toggle("done", Boolean(steps[button.dataset.step]));

  // Step 1: source
  if (firstOpen || !pv.sourcePlayer.firstElementChild) {
    pv.sourcePlayer.innerHTML = mediaElement(files.source, source.has_video ? "video" : "audio");
  }
  setIfIdle(pv.sourceLanguage, p.source_language || "auto");
  setIfIdle(pv.target, p.target || "English");
  pv.separate.checked = p.separate !== false;
  const separator = p.separator || {};
  pv.separatorHint.hidden = separator.installed || !pv.separate.checked;
  pv.separatorHint.innerHTML = separator.installed ? "" :
    `Движок отделения фона (${escapeHtml(separator.name || "Demucs")}) не установлен — без него фон оригинала в ролик не попадёт. <button type="button" class="text-btn" data-open-engines>Установить в настройках</button>`;
  const segmentsCount = (p.segments || []).length;
  pv.prepareSummary.textContent = state.prepared
    ? `Распознано и переведено фраз: ${segmentsCount}. Язык оригинала: ${p.detected_language || "?"}.${p.separated ? " Голос отделён от фона." : ""}`
    : "";

  // Step 2: voice
  const voice = p.voice || {};
  for (const radio of document.querySelectorAll('input[name="voiceMode"]')) radio.checked = radio.value === (voice.mode || "original");
  pv.voiceOriginalInfo.innerHTML = files.sample
    ? `${mediaElement(files.sample, "audio")}<div class="meta">${escapeHtml(voice.auto_text || "")}</div>`
    : '<div class="meta">Появится после шага 1.</div>';
  pv.voiceFileInfo.innerHTML = files.uploaded_sample ? mediaElement(files.uploaded_sample, "audio") : "";
  setIfIdle(pv.voiceRefText, voice.ref_text || "");
  setIfIdle(pv.voiceEngineId, voice.voice_id || "");
  const timing = p.timing || {};
  setIfIdle(pv.timingSpeed, timing.speed ?? 1);
  setIfIdle(pv.timingTail, timing.tail_room ?? 0.6);
  setIfIdle(pv.timingMaxSpeed, timing.max_auto_speed ?? 1.18);
  setIfIdle(pv.timingClean, timing.post_clean || "soft");
  pv.voiceTrackPlayer.innerHTML = files.voice_track && state.voiced
    ? `<div class="section-title">Озвучка перевода</div>${mediaElement(files.voice_track, "audio")}`
    : "";

  // Step 3: translation review
  renderSegments(p.segments || [], state.voiced);

  // Step 4: result
  const mix = p.mix || {};
  setIfIdle(pv.mixVoice, mix.voice ?? 1);
  setIfIdle(pv.mixBackground, mix.background ?? 0.8);
  setIfIdle(pv.mixMusic, mix.music ?? 0.25);
  pv.mixLoudnorm.checked = mix.loudnorm !== false;
  pv.mixBackground.disabled = !p.separated;
  pv.backgroundHint.hidden = Boolean(p.separated) || !state.prepared;
  pv.musicFileInfo.textContent = files.music ? files.music.name : "без музыки";
  updateMixOutputs();
  const resultFile = files.video || files.mix_track;
  const resultKey = resultFile ? `${resultFile.url}|${p.updated}` : "";
  if (pv.resultPlayer.dataset.key !== resultKey) {
    pv.resultPlayer.dataset.key = resultKey;
    pv.resultPlayer.innerHTML = state.exported && resultFile
      ? mediaElement(resultFile, files.video ? "video" : "audio")
      : `<div class="media-placeholder">${state.voiced ? "Нажмите «Собрать», чтобы получить готовый ролик." : "Сначала озвучьте перевод (шаг 2)."}</div>`;
  }
  renderDownloads(files, state);
}

let segmentsSignature = "";
let segmentsPendingRender = false;

function isLongPhrase(segment) {
  return (segment.clipped || 0) > 0.05 || (segment.speedup || 1) >= 1.1;
}

function phraseStatus(segment, voiced) {
  if (segment.dirty) return '<span class="fit fit-warn">изменено — переозвучьте</span>';
  if (!voiced) return '<span class="fit">не озвучено</span>';
  if ((segment.clipped || 0) > 0.05) return `<span class="fit fit-bad">обрезано ${segment.clipped.toFixed(1)} с</span>`;
  if ((segment.speedup || 1) > 1.02) return `<span class="fit fit-warn">ускорено ×${segment.speedup.toFixed(2)}</span>`;
  return '<span class="fit fit-ok">влезает</span>';
}

function renderSegments(segments, voiced) {
  const summary = document.getElementById("reviewSummary");
  const longCount = voiced ? segments.filter((segment) => !segment.dirty && isLongPhrase(segment)).length : 0;
  const dirtyCount = segments.filter((segment) => segment.dirty).length;
  const okCount = voiced ? segments.filter((segment) => !segment.dirty && !isLongPhrase(segment)).length : 0;
  summary.textContent = !segments.length ? "" : voiced
    ? `Фраз: ${segments.length} · влезают: ${okCount} · длинных: ${longCount} · изменено: ${dirtyCount}`
    : `Фраз: ${segments.length}. Озвучьте перевод на шаге «Голос», чтобы увидеть, как фразы легли в тайминг.`;
  const shortenBtn = document.getElementById("shortenLongBtn");
  const revoiceBtn = document.getElementById("revoiceDirtyBtn");
  shortenBtn.textContent = `✂ Сократить длинные (${longCount})`;
  shortenBtn.dataset.count = String(longCount);
  revoiceBtn.textContent = `▶ Переозвучить изменённые (${dirtyCount})`;
  revoiceBtn.dataset.count = String(dirtyCount);

  // Never rebuild the table under the user's cursor.
  const signature = JSON.stringify([voiced, segments.map((s) => [s.id, s.translation, s.dirty, s.speedup, s.clipped, s.voice_url])]);
  if (signature === segmentsSignature) return;
  if (pv.segments.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") {
    segmentsPendingRender = true;
    return;
  }
  segmentsSignature = signature;
  segmentsPendingRender = false;

  if (!segments.length) {
    pv.segments.innerHTML = '<p class="meta">Перевод появится после шага 1.</p>';
    return;
  }
  pv.segments.innerHTML = '<div class="segment-row segment-head"><span>Время</span><span>Оригинал</span><span>Перевод</span><span>Тайминг</span></div>';
  for (const segment of segments) {
    const row = document.createElement("div");
    row.className = `segment-row${segment.dirty ? " segment-dirty" : ""}`;
    row.dataset.id = segment.id;
    row.innerHTML = `
      <span class="meta">${formatClock(segment.start)} · ${Number(segment.duration || 0).toFixed(1)} с</span>
      <span class="segment-source">${escapeHtml(segment.source)}
        <button type="button" class="text-btn" data-play="source">▶ оригинал</button></span>
      <textarea class="segment-text" rows="2" spellcheck="true">${escapeHtml(segment.translation)}</textarea>
      <span class="segment-actions">
        ${phraseStatus(segment, voiced)}
        <span class="segment-buttons">
          <button type="button" class="text-btn" data-play="voice" ${segment.voice_url ? "" : "hidden"}>▶ озвучка</button>
          <button type="button" class="text-btn" data-phrase-stage="shorten" data-job-button title="Попросить модель сократить фразу под тайминг">✂ сократить</button>
          <button type="button" class="text-btn" data-phrase-stage="revoice" data-job-button title="Переозвучить только эту фразу" ${voiced ? "" : "hidden"}>↻ переозвучить</button>
        </span>
      </span>
    `;
    pv.segments.appendChild(row);
  }
  if (lastState?.job) renderJob(lastState.job);  // apply disabled state to the new buttons
}

let phraseSaveTimer = null;
const pendingPhrases = {};

async function flushPhraseEdits() {
  clearTimeout(phraseSaveTimer);
  if (!currentProject || !Object.keys(pendingPhrases).length) return;
  const translations = { ...pendingPhrases };
  for (const key of Object.keys(pendingPhrases)) delete pendingPhrases[key];
  const data = await postJson(projectUrl(currentProject.id, "phrases"), { translations });
  currentProject = data.project;
  renderProject();
}

pv.segments.addEventListener("input", (event) => {
  const textarea = event.target.closest(".segment-text");
  if (!textarea) return;
  const id = textarea.closest(".segment-row").dataset.id;
  const value = textarea.value.trim();
  if (!value) return;
  pendingPhrases[id] = value;
  clearTimeout(phraseSaveTimer);
  phraseSaveTimer = setTimeout(() => flushPhraseEdits().catch((error) => alert(error.message)), 900);
});
pv.segments.addEventListener("focusout", (event) => {
  if (!event.target.closest(".segment-text")) return;
  flushPhraseEdits()
    .catch((error) => alert(error.message))
    .finally(() => {
      if (segmentsPendingRender) renderSegments(currentProject.segments || [], currentProject.state?.voiced);
    });
});

// One shared player for phrase previews: original fragment or the phrase voice.
const phrasePlayer = document.getElementById("phrasePlayer");
let phraseStopAt = null;
phrasePlayer.addEventListener("timeupdate", () => {
  if (phraseStopAt !== null && phrasePlayer.currentTime >= phraseStopAt) {
    phrasePlayer.pause();
    phraseStopAt = null;
  }
});

function playPhrase(url, start = null, end = null) {
  phrasePlayer.pause();
  phraseStopAt = end;
  const begin = () => {
    if (start !== null) phrasePlayer.currentTime = start;
    phrasePlayer.play().catch(() => {});
  };
  if (phrasePlayer.dataset.src === url && phrasePlayer.readyState >= 1) {
    begin();
    return;
  }
  phrasePlayer.dataset.src = url;
  phrasePlayer.src = url;
  phrasePlayer.addEventListener("loadedmetadata", begin, { once: true });
  phrasePlayer.load();
}

pv.segments.addEventListener("click", async (event) => {
  const row = event.target.closest(".segment-row[data-id]");
  if (!row || !currentProject) return;
  const segment = (currentProject.segments || []).find((item) => String(item.id) === row.dataset.id);
  if (!segment) return;
  const play = event.target.closest("[data-play]");
  if (play) {
    const files = currentProject.files || {};
    if (play.dataset.play === "voice" && segment.voice_url) playPhrase(segment.voice_url);
    if (play.dataset.play === "source") {
      const audio = files.vocals || files.source;
      if (audio) playPhrase(audio.url, Number(segment.start), Number(segment.end ?? segment.start + segment.duration));
    }
    return;
  }
  const stageButton = event.target.closest("[data-phrase-stage]");
  if (stageButton) await runProjectStage(stageButton.dataset.phraseStage, [Number(segment.id)]);
});

document.getElementById("shortenLongBtn").addEventListener("click", async (event) => {
  if (event.currentTarget.dataset.count === "0") {
    alert("Длинных фраз нет: все укладываются в тайминг.");
    return;
  }
  await runProjectStage("shorten");
});
document.getElementById("revoiceDirtyBtn").addEventListener("click", async (event) => {
  if (event.currentTarget.dataset.count === "0") {
    alert("Изменённых фраз нет. Поправьте перевод в таблице или нажмите «✂ сократить» у длинной фразы.");
    return;
  }
  await runProjectStage("revoice");
});

function renderDownloads(files, state) {
  const items = [
    ["video", "Видео с дубляжом"],
    ["mix_track", "Звук: голос + фон (WAV)"],
    ["voice_track", "Только голос (WAV)"],
    ["subtitles", "Субтитры перевода (SRT)"],
  ];
  const links = items
    .filter(([key]) => files[key] && (key !== "video" && key !== "mix_track" ? true : state.exported))
    .map(([key, label]) => `<a class="download-link" href="${files[key].url}" download="${escapeHtml(files[key].name)}">${label}<span class="meta">${escapeHtml(files[key].name)}</span></a>`)
    .join("");
  pv.downloads.innerHTML = links ? `<div class="section-title">Файлы</div>${links}` : "";
}

function updateMixOutputs() {
  const percent = (value) => `${Math.round(Number(value) * 100)}%`;
  document.getElementById("mixVoiceOut").textContent = percent(pv.mixVoice.value);
  document.getElementById("mixBackgroundOut").textContent = pv.mixBackground.disabled ? "нет" : percent(pv.mixBackground.value);
  document.getElementById("mixMusicOut").textContent = currentProject?.files?.music ? percent(pv.mixMusic.value) : "нет";
}

// ---------------------------------------------------------------- edits

let pendingChanges = {};

function queueProjectChange(changes) {
  for (const [key, value] of Object.entries(changes)) {
    pendingChanges[key] = typeof value === "object" && !Array.isArray(value)
      ? { ...(pendingChanges[key] || {}), ...value }
      : value;
  }
  clearTimeout(projectSaveTimer);
  projectSaveTimer = setTimeout(() => flushProjectSave().catch((error) => alert(error.message)), 450);
}

async function flushProjectSave() {
  clearTimeout(projectSaveTimer);
  if (!currentProject || !Object.keys(pendingChanges).length) return;
  const changes = pendingChanges;
  pendingChanges = {};
  const data = await postJson(projectUrl(currentProject.id, "update"), changes);
  currentProject = data.project;
  renderProject();
}

pv.name.addEventListener("change", () => queueProjectChange({ name: pv.name.value.trim() }));
pv.sourceLanguage.addEventListener("change", () => queueProjectChange({ source_language: pv.sourceLanguage.value }));
pv.target.addEventListener("change", () => queueProjectChange({ target: pv.target.value }));
pv.separate.addEventListener("change", () => queueProjectChange({ separate: pv.separate.checked }));
for (const radio of document.querySelectorAll('input[name="voiceMode"]')) {
  radio.addEventListener("change", () => queueProjectChange({ voice: { mode: radio.value } }));
}
pv.voiceRefText.addEventListener("change", () => queueProjectChange({ voice: { ref_text: pv.voiceRefText.value.trim() } }));
pv.voiceEngineId.addEventListener("change", () => queueProjectChange({ voice: { voice_id: pv.voiceEngineId.value.trim() } }));
const timingFields = [
  [pv.timingSpeed, "speed", Number], [pv.timingTail, "tail_room", Number],
  [pv.timingMaxSpeed, "max_auto_speed", Number], [pv.timingClean, "post_clean", String],
];
for (const [element, key, cast] of timingFields) {
  element.addEventListener("change", () => queueProjectChange({ timing: { [key]: cast(element.value) } }));
}
for (const [element, key] of [[pv.mixVoice, "voice"], [pv.mixBackground, "background"], [pv.mixMusic, "music"]]) {
  element.addEventListener("input", updateMixOutputs);
  element.addEventListener("change", () => queueProjectChange({ mix: { [key]: Number(element.value) } }));
}
pv.mixLoudnorm.addEventListener("change", () => queueProjectChange({ mix: { loudnorm: pv.mixLoudnorm.checked } }));

async function uploadProjectFile(kind, file) {
  const formData = new FormData();
  formData.append("file", file);
  const data = await uploadForm(`${projectUrl(currentProject.id, "upload")}?kind=${kind}`, formData);
  currentProject = data.project;
  renderProject();
}

pv.voiceFileBtn.addEventListener("click", (event) => {
  event.preventDefault();
  pv.voiceFileInput.click();
});
pv.voiceFileInput.addEventListener("change", () => {
  const file = pv.voiceFileInput.files?.[0];
  if (file) uploadProjectFile("voice", file).catch((error) => alert(error.message));
  pv.voiceFileInput.value = "";
});
pv.musicFileBtn.addEventListener("click", () => pv.musicFileInput.click());
pv.musicFileInput.addEventListener("change", () => {
  const file = pv.musicFileInput.files?.[0];
  if (file) uploadProjectFile("music", file).catch((error) => alert(error.message));
  pv.musicFileInput.value = "";
});

// ---------------------------------------------------------------- running stages

async function runProjectStage(stage, ids = null) {
  if (!currentProject) return;
  try {
    await flushProjectSave();
    await flushPhraseEdits();
    if ((stage === "prepare" || stage === "all") && (currentProject.edited || []).length) {
      if (!confirm("Перевод будет сделан заново, и ваши правки фраз пропадут. Продолжить?")) return;
    }
    const data = await postJson(projectUrl(currentProject.id, "run"), ids ? { stage, ids } : { stage });
    currentProject = data.project;
    renderProject();
    await refreshState();
  } catch (error) {
    alert(error.message);
  }
}

for (const button of document.querySelectorAll("[data-run-stage]")) {
  button.addEventListener("click", () => runProjectStage(button.dataset.runStage));
}

// Reload the project when its job finishes, then move the user to the next step.
onState(async (state) => {
  const job = state.job;
  const ours = currentProject && job.kind === "project" && job.meta?.project === currentProject.id;
  if (ours && job.running) {
    wasRunningForProject = true;
    return;
  }
  if (ours && wasRunningForProject && !job.running) {
    wasRunningForProject = false;
    const stage = job.meta?.stage;
    await openProject(currentProject.id);
    if (job.exit_code === 0) {
      showStep({ prepare: "voice", voice: "result", export: "result", all: "result" }[stage] || currentStep);
    }
    if (stage === "revoice" && job.exit_code === 0) {
      const ids = job.meta?.ids || [];
      const segment = ids.length === 1 && (currentProject.segments || []).find((item) => item.id === ids[0]);
      if (segment?.voice_url) playPhrase(`${segment.voice_url}&v=${Date.now()}`);  // let the user hear the new take
    }
  }
});

// ---------------------------------------------------------------- start

(async () => {
  try {
    await loadEngineSettings();
    await loadProjects();
    await refreshState();
    const engine = engineById(engineState.settings.tts.engine);
    if (!engine?.status?.installed) showView("settings");  // first run: nothing configured yet
    const provider = providerById(engineState.settings.llm.provider);
    if (provider.local || engineState.keys[provider.id]) refreshLlmModels().catch(() => {});
  } catch (error) {
    console.error(error);
  }
})();
