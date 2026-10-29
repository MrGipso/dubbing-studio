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
  targets: document.getElementById("projectTargets"),
  addTarget: document.getElementById("projectAddTarget"),
  languageTabs: document.getElementById("languageTabs"),
  prepareBtn: document.getElementById("prepareBtn"),
  separate: document.getElementById("projectSeparate"),
  separatorHint: document.getElementById("separatorHint"),
  speakers: document.getElementById("projectSpeakers"),
  diarizerHint: document.getElementById("diarizerHint"),
  prepareSummary: document.getElementById("prepareSummary"),
  speakerVoices: document.getElementById("speakerVoices"),
  voiceFileInput: document.getElementById("voiceFileInput"),
  timingSpeed: document.getElementById("timingSpeed"),
  timingTail: document.getElementById("timingTail"),
  timingMaxSpeed: document.getElementById("timingMaxSpeed"),
  timingClean: document.getElementById("timingClean"),
  voiceTrackPlayer: document.getElementById("voiceTrackPlayer"),
  segments: document.getElementById("segmentsTable"),
  resultPlayer: document.getElementById("resultPlayer"),
  resultSwitch: document.getElementById("resultSwitch"),
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
    empty.textContent = t("Проектов пока нет. Перетащите видео в поле выше, чтобы начать первый дубляж.");
    projectCardsEl.appendChild(empty);
    return;
  }
  for (const project of projects) {
    const card = document.createElement("div");
    card.className = "project-card";
    const state = project.state || {};
    const done = STATE_LABELS.filter(([key]) => state[key]).length;
    const status = project.error ? `<span class="chip chip-bad">${t("ошибка")}</span>`
      : `<span class="project-status">${done ? t(STATE_LABELS[done - 1][1]) : t("не начат")}</span>`;
    const steps = STATE_LABELS.map(([key, label]) => `<span class="${state[key] ? "on" : ""}" title="${t(label)}"></span>`).join("");
    const thumb = project.thumb_url
      ? `<img src="${project.thumb_url}" alt="" loading="lazy">`
      : `<span class="project-thumb-empty">${icon("wave")}</span>`;
    const languages = (project.targets || []).map((name) => `<span class="chip">${escapeHtml(languageLabel(name))}</span>`).join("");
    card.innerHTML = `
      <button type="button" class="project-open">
        <span class="project-thumb">${thumb}<span class="project-duration">${formatClock(project.duration)}</span></span>
        <span class="project-body">
          <strong translate="no">${escapeHtml(project.name || project.id)}</strong>
          <span class="chip-row">${languages}${project.speakers > 1 ? `<span class="chip">${icon("users", "small")}${project.speakers}</span>` : ""}</span>
          <span class="project-progress"><span class="progress-steps">${steps}</span>${status}</span>
          <span class="meta">${project.updated ? new Date(project.updated * 1000).toLocaleString() : ""}</span>
        </span>
      </button>
      <button type="button" class="icon-btn project-delete" title="${t("Удалить в корзину")}" aria-label="${t("Удалить в корзину")}">${icon("trash")}</button>
    `;
    card.querySelector(".project-open").addEventListener("click", () => openProject(project.id));
    card.querySelector(".project-delete").addEventListener("click", async () => {
      const question = t("Удалить проект «{name}»?\n\nОн переместится в папку projects/.trash, оттуда его можно вернуть.", { name: project.name });
      if (!(await askConfirm(question, { ok: t("Удалить"), danger: true }))) return;
      try {
        const data = await postJson(projectUrl(project.id, "delete"));
        renderProjectCards(data.projects || []);
      } catch (error) {
        notifyError(error);
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
  formData.append("targets", newProjectTarget.value);
  formData.append("name", file.name.replace(/\.[^.]+$/, ""));
  formData.append("file", file);
  newProjectProgress.hidden = false;
  newProjectProgress.textContent = t("Загрузка {percent}%", { percent: 0 });
  try {
    const data = await uploadForm("/api/projects", formData, (fraction) => {
      newProjectProgress.textContent = t("Загрузка {percent}%", { percent: Math.round(fraction * 100) });
    });
    try {
      localStorage.setItem("dub-last-target", newProjectTarget.value);
    } catch {
      // not important
    }
    showProject(data.project);
  } catch (error) {
    notifyError(error);
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
  const active = project.active_track || {};
  if (active.exported || active.voiced) return "result";
  if (active.translated) return "voice";
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
  if (currentProject) postJson(projectUrl(currentProject.id, "open")).catch((error) => notifyError(error));
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
  pv.source.textContent = `${source.original_name || ""} · ${formatClock(source.duration)} · ${source.has_video ? t("видео") : t("аудио")}`;
  pv.error.hidden = !p.error;
  pv.error.textContent = p.error ? t("Ошибка: {message}", { message: p.error }) : "";

  const active = p.active_track || {};
  const steps = { source: state.prepared, voice: active.voiced, review: active.translated, result: active.exported };
  for (const button of document.querySelectorAll(".step")) button.classList.toggle("done", Boolean(steps[button.dataset.step]));

  // Step 1: source
  if (firstOpen || !pv.sourcePlayer.firstElementChild) {
    pv.sourcePlayer.innerHTML = mediaElement(files.source, source.has_video ? "video" : "audio");
  }
  setIfIdle(pv.sourceLanguage, p.source_language || "auto");
  renderLanguages(p);
  pv.separate.checked = p.separate !== false;
  const separator = p.separator || {};
  pv.separatorHint.hidden = separator.installed || !pv.separate.checked;
  pv.separatorHint.innerHTML = separator.installed ? "" :
    `${escapeHtml(t("Движок отделения фона ({name}) не установлен — без него фон оригинала в ролик не попадёт.", { name: separator.name || "Demucs" }))}
     <button type="button" class="text-btn" data-open-engines>${t("Установить в настройках")}</button>`;
  setIfIdle(pv.speakers, String(p.speaker_count ?? 1));
  const diarizer = p.diarizer || {};
  pv.diarizerHint.hidden = Number(p.speaker_count ?? 1) === 1 || diarizer.installed;
  pv.diarizerHint.innerHTML = diarizer.installed ? "" :
    `${escapeHtml(t("Движок разделения на говорящих ({name}) не установлен — все фразы озвучатся одним голосом.", { name: diarizer.name || "Sherpa-ONNX" }))}
     <button type="button" class="text-btn" data-open-engines>${t("Установить в настройках")}</button>`;
  const segmentsCount = (p.segments || []).length;
  const untranslated = (p.languages || []).filter((language) => !language.translated);
  if (p.transcribed && untranslated.length) {
    const languages = untranslated.map((language) => languageLabel(language.name)).join(", ");
    pv.prepareBtn.dataset.runStage = "translate";
    setLabel(pv.prepareBtn, "play", p.diarized ? t("Перевести: {languages}", { languages })
      : t("Найти говорящих и перевести: {languages}", { languages }));
  } else {
    pv.prepareBtn.dataset.runStage = "prepare";
    setLabel(pv.prepareBtn, "play", p.transcribed ? t("Распознать и перевести заново") : t("Распознать и перевести"));
  }
  const detected = { language: p.detected_language || "?", count: segmentsCount };
  const speakersCount = (p.speaker_list || []).length;
  pv.prepareSummary.textContent = !p.transcribed ? ""
    : (p.separated ? t("Распознано: язык оригинала {language}, голос отделён от фона. Фраз: {count}.", detected)
      : t("Распознано: язык оригинала {language}. Фраз: {count}.", detected))
      + (p.diarized && speakersCount > 1 ? ` ${t("Говорящих: {count}", { count: speakersCount })}.` : "");

  // Step 2: voice
  renderSpeakers(p);
  const timing = p.timing || {};
  setIfIdle(pv.timingSpeed, timing.speed ?? 1);
  setIfIdle(pv.timingTail, timing.tail_room ?? 0.6);
  setIfIdle(pv.timingMaxSpeed, timing.max_auto_speed ?? 1.18);
  setIfIdle(pv.timingClean, timing.post_clean || "soft");
  pv.voiceTrackPlayer.innerHTML = files.voice_track && active.voiced
    ? `<div class="section-title">${t("Озвучка перевода")}</div>${mediaElement(files.voice_track, "audio")}`
    : "";

  // Step 3: translation review
  renderSegments(p.segments || [], active.voiced);
  if (typeof renderTimeline === "function") renderTimeline(p);

  // Step 4: result
  const mix = p.mix || {};
  setIfIdle(pv.mixVoice, mix.voice ?? 1);
  setIfIdle(pv.mixBackground, mix.background ?? 0.8);
  setIfIdle(pv.mixMusic, mix.music ?? 0.25);
  pv.mixLoudnorm.checked = mix.loudnorm !== false;
  pv.mixBackground.disabled = !p.separated;
  pv.backgroundHint.hidden = Boolean(p.separated) || !p.transcribed;
  pv.musicFileInfo.textContent = files.music ? files.music.name : t("без музыки");
  updateMixOutputs();
  const resultFile = files.video || files.mix_track;
  const resultKey = resultFile ? `${resultFile.url}|${p.updated}` : "";
  pv.resultSwitch.hidden = !(active.exported && resultFile && files.source);
  if (pv.resultPlayer.dataset.key !== resultKey) {
    pv.resultPlayer.dataset.key = resultKey;
    for (const button of pv.resultSwitch.querySelectorAll("[data-source]")) button.classList.toggle("active", button.dataset.source === "dub");
    pv.resultPlayer.innerHTML = active.exported && resultFile
      ? mediaElement(resultFile, files.video ? "video" : "audio")
      : `<div class="media-placeholder">${active.voiced ? t("Нажмите «Собрать», чтобы получить готовый ролик.") : t("Сначала озвучьте перевод (шаг 2).")}</div>`;
  }
  renderDownloads(p.languages || []);
}

let segmentsSignature = "";
let segmentsPendingRender = false;

function isLongPhrase(segment) {
  return (segment.clipped || 0) > 0.05 || (segment.speedup || 1) >= 1.1;
}

function phraseStatus(segment, voiced) {
  if (segment.dirty) return `<span class="fit fit-warn">${t("изменено — переозвучьте")}</span>`;
  if (!voiced) return `<span class="fit">${t("не озвучено")}</span>`;
  if ((segment.clipped || 0) > 0.05) return `<span class="fit fit-bad">${t("обрезано {seconds} с", { seconds: segment.clipped.toFixed(1) })}</span>`;
  if ((segment.speedup || 1) > 1.02) return `<span class="fit fit-warn">${t("ускорено ×{ratio}", { ratio: segment.speedup.toFixed(2) })}</span>`;
  return `<span class="fit fit-ok">${t("влезает")}</span>`;
}

function renderSegments(segments, voiced) {
  const summary = document.getElementById("reviewSummary");
  const longCount = voiced ? segments.filter((segment) => !segment.dirty && isLongPhrase(segment)).length : 0;
  const dirtyCount = segments.filter((segment) => segment.dirty).length;
  const okCount = voiced ? segments.filter((segment) => !segment.dirty && !isLongPhrase(segment)).length : 0;
  summary.textContent = !segments.length ? "" : voiced
    ? t("Фраз: {count} · влезают: {ok} · длинных: {long} · изменено: {dirty}", { count: segments.length, ok: okCount, long: longCount, dirty: dirtyCount })
    : t("Фраз: {count}. Озвучьте перевод на шаге «Голос», чтобы увидеть, как фразы легли в тайминг.", { count: segments.length });
  const shortenBtn = document.getElementById("shortenLongBtn");
  const revoiceBtn = document.getElementById("revoiceDirtyBtn");
  setLabel(shortenBtn, "scissors", t("Сократить длинные ({count})", { count: longCount }));
  shortenBtn.dataset.count = String(longCount);
  setLabel(revoiceBtn, "refresh", t("Переозвучить изменённые ({count})", { count: dirtyCount }));
  revoiceBtn.dataset.count = String(dirtyCount);

  // Never rebuild the table under the user's cursor.
  const speakers = currentProject?.speaker_list || [];
  const signature = JSON.stringify([voiced, speakers.map((speaker) => [speaker.id, speaker.name]),
    segments.map((s) => [s.id, s.translation, s.dirty, s.speedup, s.clipped, s.voice_url, s.speaker])]);
  if (signature === segmentsSignature) return;
  if (pv.segments.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") {
    segmentsPendingRender = true;
    return;
  }
  segmentsSignature = signature;
  segmentsPendingRender = false;

  if (!segments.length) {
    pv.segments.innerHTML = `<p class="meta">${t("Перевод появится после шага 1.")}</p>`;
    return;
  }
  pv.segments.innerHTML = `<div class="segment-row segment-head"><span>${t("Время")}</span><span>${t("Оригинал")}</span><span>${t("Перевод")}</span><span>${t("Тайминг")}</span></div>`;
  for (const segment of segments) {
    const row = document.createElement("div");
    row.className = `segment-row${segment.dirty ? " segment-dirty" : ""}`;
    row.dataset.id = segment.id;
    if (speakers.length > 1) row.dataset.speakerIndex = String(Math.max(0, speakers.findIndex((item) => item.id === segment.speaker)) % 6);
    row.innerHTML = `
      <span class="meta">${formatClock(segment.start)} · ${t("{seconds} с", { seconds: Number(segment.duration || 0).toFixed(1) })}
        ${speakers.length > 1 ? speakerSelect(segment.speaker, speakers) : ""}</span>
      <span class="segment-source"><span translate="no">${escapeHtml(segment.source)}</span>
        <button type="button" class="text-btn" data-play="source">${icon("play", "small")}${t("оригинал")}</button></span>
      <textarea class="segment-text" rows="2" spellcheck="true">${escapeHtml(segment.translation)}</textarea>
      <span class="segment-actions">
        ${phraseStatus(segment, voiced)}
        <span class="segment-buttons">
          <button type="button" class="text-btn" data-play="voice" ${segment.voice_url ? "" : "hidden"}>${icon("play", "small")}${t("озвучка")}</button>
          <button type="button" class="text-btn" data-phrase-stage="shorten" data-job-button title="${t("Попросить модель сократить фразу под тайминг")}">${icon("scissors", "small")}${t("сократить")}</button>
          <button type="button" class="text-btn" data-phrase-stage="revoice" data-job-button title="${t("Переозвучить только эту фразу")}" ${voiced ? "" : "hidden"}>${icon("refresh", "small")}${t("переозвучить")}</button>
        </span>
      </span>
    `;
    pv.segments.appendChild(row);
  }
  if (lastState?.job) renderJob(lastState.job);  // apply disabled state to the new buttons
  if (typeof timelineState !== "undefined" && timelineState.selected) {
    pv.segments.querySelector(`.segment-row[data-id="${CSS.escape(timelineState.selected)}"]`)?.classList.add("segment-focus");
  }
}

let phraseSaveTimer = null;
const pendingPhrases = {};

async function flushPhraseEdits() {
  clearTimeout(phraseSaveTimer);
  if (!currentProject || !Object.keys(pendingPhrases).length) return;
  const translations = { ...pendingPhrases };
  for (const key of Object.keys(pendingPhrases)) delete pendingPhrases[key];
  const data = await postJson(projectUrl(currentProject.id, "phrases"), { translations, target: currentProject.active });
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
  phraseSaveTimer = setTimeout(() => flushPhraseEdits().catch((error) => notifyError(error)), 900);
});
pv.segments.addEventListener("focusout", (event) => {
  if (!event.target.closest(".segment-text")) return;
  flushPhraseEdits()
    .catch((error) => notifyError(error))
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
    toast(t("Длинных фраз нет: все укладываются в тайминг."), "warn");
    return;
  }
  await runProjectStage("shorten");
});
document.getElementById("revoiceDirtyBtn").addEventListener("click", async (event) => {
  if (event.currentTarget.dataset.count === "0") {
    toast(t("Изменённых фраз нет. Поправьте перевод в таблице или нажмите «сократить» у длинной фразы."), "warn");
    return;
  }
  await runProjectStage("revoice");
});

// Result: listen to the dub and the original at the same moment of the video.
pv.resultSwitch.addEventListener("click", (event) => {
  const button = event.target.closest("[data-source]");
  const media = pv.resultPlayer.querySelector("video, audio");
  if (!button || !media || !currentProject || button.classList.contains("active")) return;
  const files = currentProject.files || {};
  const url = button.dataset.source === "original" ? files.source?.url : (files.video || files.mix_track)?.url;
  if (!url) return;
  const time = media.currentTime;
  const playing = !media.paused;
  media.src = url;
  media.addEventListener("loadedmetadata", () => {
    media.currentTime = time;
    if (playing) media.play().catch(() => {});
  }, { once: true });
  for (const other of pv.resultSwitch.querySelectorAll("[data-source]")) other.classList.toggle("active", other === button);
});

function renderDownloads(languages) {
  const items = [
    ["video", "Видео с дубляжом"],
    ["mix_track", "Звук: голос + фон (WAV)"],
    ["voice_track", "Только голос (WAV)"],
    ["subtitles", "Субтитры перевода (SRT)"],
  ];
  const blocks = languages.map((language) => {
    const files = language.files || {};
    const links = items
      .filter(([key]) => files[key] && (key === "video" || key === "mix_track" ? language.exported : true))
      .map(([key, label]) => `<a class="download-link" href="${files[key].url}" download="${escapeHtml(files[key].name)}">${icon("download")}${t(label)}<span class="meta" translate="no">${escapeHtml(files[key].name)}</span></a>`)
      .join("");
    if (!links) return "";
    return `${languages.length > 1 ? `<div class="section-title">${escapeHtml(languageLabel(language.name))}</div>` : ""}${links}`;
  }).join("");
  pv.downloads.innerHTML = blocks ? `${languages.length > 1 ? "" : `<div class="section-title">${t("Файлы")}</div>`}${blocks}` : "";
}

// ---------------------------------------------------------------- languages

function renderLanguages(p) {
  const languages = p.languages || [];
  pv.targets.innerHTML = "";
  for (const language of languages) {
    const chip = document.createElement("span");
    chip.className = `language-chip${language.translated ? " chip-on" : ""}`;
    chip.innerHTML = `${escapeHtml(languageLabel(language.name))}<button type="button" class="chip-remove" title="${t("Убрать язык")}" data-remove-target="${escapeHtml(language.name)}" ${languages.length > 1 ? "" : "hidden"}>×</button>`;
    pv.targets.appendChild(chip);
  }
  for (const option of pv.addTarget.options) {
    option.hidden = Boolean(option.value) && (p.targets || []).includes(option.value);
  }
  pv.addTarget.value = "";

  pv.languageTabs.hidden = languages.length < 2;
  pv.languageTabs.innerHTML = "";
  for (const language of languages) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `language-tab${language.name === p.active ? " active" : ""}`;
    const status = language.exported ? t("готово") : language.voiced ? t("озвучено")
      : language.translated ? t("переведено") : t("не переведено");
    const dirty = language.dirty ? ` · ${t("изменено {count}", { count: language.dirty })}` : "";
    button.innerHTML = `${escapeHtml(languageLabel(language.name))} <span class="meta">${status}${dirty}</span>`;
    button.addEventListener("click", () => {
      if (language.name !== p.active) queueProjectChange({ active: language.name }, true);
    });
    pv.languageTabs.appendChild(button);
  }
}

pv.addTarget.addEventListener("change", () => {
  const value = pv.addTarget.value;
  if (!value || !currentProject) return;
  queueProjectChange({ targets: [...(currentProject.targets || []), value] }, true);
});

pv.targets.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-remove-target]");
  if (!button || !currentProject) return;
  const name = button.dataset.removeTarget;
  const language = (currentProject.languages || []).find((item) => item.name === name);
  if (language?.translated && !(await askConfirm(t("Убрать {language} из проекта? Готовые файлы этого языка останутся в папке проекта.",
    { language: languageLabel(name) }), { ok: t("Убрать") }))) return;
  queueProjectChange({ targets: (currentProject.targets || []).filter((item) => item !== name) }, true);
});

// ---------------------------------------------------------------- speakers and their voices

function speakerLabel(speaker, index) {
  return speaker.name || t("Говорящий {number}", { number: index + 1 });
}

function speakerSelect(current, speakers) {
  const options = speakers.map((speaker, index) =>
    `<option value="${escapeHtml(speaker.id)}" ${speaker.id === current ? "selected" : ""}>${escapeHtml(speakerLabel(speaker, index))}</option>`).join("");
  return `<select class="speaker-select" data-speaker-select translate="no" title="${t("Кто говорит эту фразу")}">${options}</select>`;
}

const openSpeakers = new Set();  // speakers whose voice choice is expanded

function speakerCard(speaker, index, many) {
  const id = escapeHtml(speaker.id);
  return `
    <div class="speaker-card${many ? " many" : ""}" data-speaker="${id}" data-speaker-index="${index % 6}">
      ${many ? `<div class="speaker-head">
        <span class="speaker-avatar" data-speaker-avatar translate="no"></span>
        <span class="speaker-title">
          <input class="speaker-name" data-speaker-name maxlength="60" placeholder="${escapeHtml(t("Говорящий {number}", { number: index + 1 }))}">
          <span class="meta" data-speaker-stats></span>
        </span>
        <span class="speaker-voice" data-speaker-voice></span>
        <button type="button" class="icon-btn" data-speaker-play title="${t("Послушать образец")}" aria-label="${t("Послушать образец")}">${icon("play")}</button>
        <button type="button" class="secondary speaker-toggle" data-speaker-toggle>${icon("edit")}<span data-toggle-label></span></button>
      </div>` : ""}
      <div class="voice-modes">
        <label class="voice-mode">
          <input type="radio" name="voiceMode-${id}" value="original">
          <strong>${t("Голос оригинала")}</strong>
          <span>${t("Образец вырезается из ролика сам после шага 1.")}</span>
          <div class="voice-mode-body" data-sample-box></div>
        </label>
        <label class="voice-mode">
          <input type="radio" name="voiceMode-${id}" value="file">
          <strong>${t("Свой образец")}</strong>
          <span>${t("10–30 секунд чистой речи нужного голоса.")}</span>
          <div class="voice-mode-body">
            <div class="input-action"><button type="button" class="secondary" data-voice-upload>${t("Загрузить образец")}</button></div>
            <div data-uploaded-box></div>
            <textarea rows="2" data-ref-text placeholder="${escapeHtml(t("Что сказано в образце. Пусто — распознается само."))}"></textarea>
          </div>
        </label>
        <label class="voice-mode">
          <input type="radio" name="voiceMode-${id}" value="engine">
          <strong>${t("Голос движка")}</strong>
          <span>${t("Готовый голос выбранного движка, без клонирования.")}</span>
          <div class="voice-mode-body"><input data-voice-id placeholder="${escapeHtml(t("Имя голоса (пусто — по умолчанию)"))}"></div>
        </label>
      </div>
    </div>`;
}

function sampleBox(speaker) {
  const voice = speaker.voice || {};
  if (!speaker.sample) return `<div class="meta">${t("Появится после шага 1.")}</div>`;
  const candidates = voice.candidates || [];
  const index = Number(voice.auto_index || 0);
  const candidate = candidates[index] || {};
  const where = candidate.parts ? t("склеено из коротких фраз") : `${formatClock(candidate.start)}–${formatClock(candidate.end)}`;
  return `${mediaElement(speaker.sample, "audio")}
    <div class="meta" translate="no">${escapeHtml(voice.auto_text || "")}</div>
    ${candidates.length > 1 ? `<div class="sample-switch">
      <button type="button" class="icon-btn mini" data-sample-step="-1" data-job-button aria-label="${t("Предыдущий отрывок")}">${icon("left")}</button>
      <span class="meta">${t("Отрывок {index} из {count}", { index: index + 1, count: candidates.length })} · ${where}</span>
      <button type="button" class="icon-btn mini" data-sample-step="1" data-job-button aria-label="${t("Следующий отрывок")}">${icon("right")}</button>
    </div>` : ""}`;
}

function renderSpeakers(p) {
  const speakers = p.speaker_list || [];
  const layout = JSON.stringify(speakers.map((speaker) => speaker.id));
  if (pv.speakerVoices.dataset.layout !== layout) {
    pv.speakerVoices.dataset.layout = layout;
    pv.speakerVoices.innerHTML = speakers.map((speaker, index) => speakerCard(speaker, index, speakers.length > 1)).join("");
  }
  for (const speaker of speakers) {
    const card = pv.speakerVoices.querySelector(`[data-speaker="${CSS.escape(speaker.id)}"]`);
    if (!card) continue;
    const voice = speaker.voice || {};
    for (const radio of card.querySelectorAll('input[type="radio"]')) radio.checked = radio.value === (voice.mode || "original");
    const name = card.querySelector("[data-speaker-name]");
    if (name) setIfIdle(name, speaker.name || "");
    const stats = card.querySelector("[data-speaker-stats]");
    if (stats) stats.textContent = speaker.phrases ? t("Фраз: {count} · речь {time}", { count: speaker.phrases, time: formatClock(speaker.seconds) }) : "";
    if (card.classList.contains("many")) {
      const index = speakers.indexOf(speaker);
      card.querySelector("[data-speaker-avatar]").textContent = (speaker.name || "").trim().charAt(0).toUpperCase() || String(index + 1);
      card.querySelector("[data-speaker-voice]").textContent = voiceSummary(speaker);
      card.querySelector("[data-speaker-play]").hidden = !speakerSampleUrl(speaker);
      const open = openSpeakers.has(speaker.id);
      card.classList.toggle("open", open);
      card.querySelector("[data-toggle-label]").textContent = open ? t("Свернуть") : t("Изменить");
    }
    const box = card.querySelector("[data-sample-box]");
    const key = `${speaker.sample?.url}|${voice.auto_index}|${p.updated}`;
    if (box.dataset.key !== key) {
      box.dataset.key = key;
      box.innerHTML = sampleBox(speaker);
    }
    const uploaded = card.querySelector("[data-uploaded-box]");
    const uploadedKey = speaker.uploaded?.url || "";
    if (uploaded.dataset.key !== uploadedKey) {
      uploaded.dataset.key = uploadedKey;
      uploaded.innerHTML = speaker.uploaded ? mediaElement(speaker.uploaded, "audio") : "";
    }
    setIfIdle(card.querySelector("[data-ref-text]"), voice.ref_text || "");
    setIfIdle(card.querySelector("[data-voice-id]"), voice.voice_id || "");
  }
  if (lastState?.job) renderJob(lastState.job);  // disabled state of the new sample buttons
}

function voiceSummary(speaker) {
  const voice = speaker.voice || {};
  if (voice.mode === "file") return speaker.uploaded ? t("Свой образец") : t("Свой образец · не загружен");
  if (voice.mode === "engine") return voice.voice_id ? t("Голос движка: {name}", { name: voice.voice_id }) : t("Голос движка по умолчанию");
  if (!speaker.sample) return t("Голос оригинала · образца пока нет");
  const candidates = voice.candidates || [];
  return candidates.length > 1
    ? t("Голос оригинала · отрывок {index} из {count}", { index: Number(voice.auto_index || 0) + 1, count: candidates.length })
    : t("Голос оригинала");
}

function speakerSampleUrl(speaker) {
  const mode = speaker.voice?.mode || "original";
  if (mode === "file") return speaker.uploaded?.url || "";
  if (mode === "original") return speaker.sample?.url || "";
  return "";
}

function speakerOf(element) {
  return element.closest("[data-speaker]")?.dataset.speaker;
}

pv.speakerVoices.addEventListener("change", (event) => {
  const speaker = speakerOf(event.target);
  if (!speaker) return;
  const field = event.target;
  const voice = (change) => queueProjectChange({ speakers: { [speaker]: { voice: change } } });
  if (field.type === "radio") voice({ mode: field.value });
  else if (field.matches("[data-ref-text]")) voice({ ref_text: field.value.trim() });
  else if (field.matches("[data-voice-id]")) voice({ voice_id: field.value.trim() });
  else if (field.matches("[data-speaker-name]")) queueProjectChange({ speakers: { [speaker]: { name: field.value.trim() } } });
});

pv.speakerVoices.addEventListener("click", async (event) => {
  const speaker = speakerOf(event.target);
  if (!speaker || !currentProject) return;
  if (event.target.closest("[data-voice-upload]")) {
    event.preventDefault();
    pv.voiceFileInput.dataset.speaker = speaker;
    pv.voiceFileInput.click();
    return;
  }
  const info = (currentProject.speaker_list || []).find((item) => item.id === speaker);
  if (event.target.closest("[data-speaker-toggle]")) {
    if (openSpeakers.has(speaker)) openSpeakers.delete(speaker);
    else openSpeakers.add(speaker);
    renderSpeakers(currentProject);
    return;
  }
  if (event.target.closest("[data-speaker-play]")) {
    const url = info && speakerSampleUrl(info);
    if (url) playPhrase(url);
    return;
  }
  const step = event.target.closest("[data-sample-step]");
  if (!step) return;
  event.preventDefault();
  const voice = (currentProject.speaker_list || []).find((item) => item.id === speaker)?.voice || {};
  const candidates = voice.candidates || [];
  if (!candidates.length) return;
  const index = (Number(voice.auto_index || 0) + Number(step.dataset.sampleStep) + candidates.length) % candidates.length;
  try {
    const data = await postJson(projectUrl(currentProject.id, "sample"), { index, speaker });
    currentProject = data.project;
    renderProject();
  } catch (error) {
    notifyError(error);
  }
});

pv.segments.addEventListener("change", async (event) => {
  const select = event.target.closest("[data-speaker-select]");
  if (!select || !currentProject) return;
  const id = select.closest(".segment-row").dataset.id;
  try {
    await flushPhraseEdits();
    const data = await postJson(projectUrl(currentProject.id, "phrases"), { speakers: { [id]: select.value } });
    currentProject = data.project;
    renderProject();
  } catch (error) {
    notifyError(error);
  }
});

function updateMixOutputs() {
  const percent = (value) => `${Math.round(Number(value) * 100)}%`;
  document.getElementById("mixVoiceOut").textContent = percent(pv.mixVoice.value);
  document.getElementById("mixBackgroundOut").textContent = pv.mixBackground.disabled ? t("нет") : percent(pv.mixBackground.value);
  document.getElementById("mixMusicOut").textContent = currentProject?.files?.music ? percent(pv.mixMusic.value) : t("нет");
}

// ---------------------------------------------------------------- edits

let pendingChanges = {};

function mergeChange(target, value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const result = target && typeof target === "object" && !Array.isArray(target) ? { ...target } : {};
  for (const [key, inner] of Object.entries(value)) result[key] = mergeChange(result[key], inner);
  return result;
}

function queueProjectChange(changes, immediate = false) {
  pendingChanges = mergeChange(pendingChanges, changes);
  clearTimeout(projectSaveTimer);
  projectSaveTimer = setTimeout(() => flushProjectSave().catch((error) => notifyError(error)), immediate ? 0 : 450);
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
pv.separate.addEventListener("change", () => queueProjectChange({ separate: pv.separate.checked }));
pv.speakers.addEventListener("change", async () => {
  const translated = (currentProject?.languages || []).some((language) => language.translated);
  if (translated && !(await askConfirm(t("Фразы будут заново разделены между говорящими, перевод и озвучка будут сделаны заново. Продолжить?")))) {
    pv.speakers.value = String(currentProject.speaker_count ?? 1);
    return;
  }
  queueProjectChange({ speaker_count: Number(pv.speakers.value) }, true);
});
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

async function uploadProjectFile(kind, file, speaker = "") {
  const formData = new FormData();
  formData.append("file", file);
  const query = `kind=${kind}${speaker ? `&speaker=${encodeURIComponent(speaker)}` : ""}`;
  const data = await uploadForm(`${projectUrl(currentProject.id, "upload")}?${query}`, formData);
  currentProject = data.project;
  renderProject();
}

pv.voiceFileInput.addEventListener("change", () => {
  const file = pv.voiceFileInput.files?.[0];
  if (file) uploadProjectFile("voice", file, pv.voiceFileInput.dataset.speaker || "S1").catch((error) => notifyError(error));
  pv.voiceFileInput.value = "";
});
pv.musicFileBtn.addEventListener("click", () => pv.musicFileInput.click());
pv.musicFileInput.addEventListener("change", () => {
  const file = pv.musicFileInput.files?.[0];
  if (file) uploadProjectFile("music", file).catch((error) => notifyError(error));
  pv.musicFileInput.value = "";
});

// ---------------------------------------------------------------- running stages

async function runProjectStage(stage, ids = null) {
  if (!currentProject) return;
  try {
    await flushProjectSave();
    await flushPhraseEdits();
    const editedAnywhere = Object.values(currentProject.tracks || {}).some((track) => (track.edited || []).length);
    if (stage === "prepare" && editedAnywhere) {
      if (!(await askConfirm(t("Перевод будет сделан заново, и ваши правки фраз пропадут. Продолжить?")))) return;
    }
    const data = await postJson(projectUrl(currentProject.id, "run"), ids ? { stage, ids } : { stage });
    currentProject = data.project;
    renderProject();
    await refreshState();
  } catch (error) {
    notifyError(error);
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
      showStep({ prepare: "voice", translate: "voice", voice: "result", export: "result", all: "result" }[stage] || currentStep);
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
