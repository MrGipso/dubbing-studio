// Settings view: translation (any LLM provider), voice (any TTS engine),
// vocal separation and Whisper. Saved on the server in config/settings.local.json.

const llmEl = {
  provider: document.getElementById("llmProvider"),
  hint: document.getElementById("llmProviderHint"),
  url: document.getElementById("llmUrl"),
  keyField: document.getElementById("llmKeyField"),
  apiKey: document.getElementById("llmApiKey"),
  keyState: document.getElementById("llmKeyState"),
  saveKey: document.getElementById("llmSaveKeyBtn"),
  model: document.getElementById("llmModel"),
  modelList: document.getElementById("llmModelList"),
  models: document.getElementById("llmModels"),
  refresh: document.getElementById("llmRefreshModelsBtn"),
  context: document.getElementById("llmContext"),
  autoLoad: document.getElementById("llmAutoLoad"),
  unloadOthers: document.getElementById("llmUnloadOthers"),
  unloadWhenDone: document.getElementById("llmUnloadWhenDone"),
  test: document.getElementById("llmTestBtn"),
  unload: document.getElementById("llmUnloadBtn"),
  testResult: document.getElementById("llmTestResult"),
  status: document.getElementById("llmStatus"),
};
const asrEl = {
  model: document.getElementById("asrModel"),
  device: document.getElementById("asrDevice"),
  compute: document.getElementById("asrCompute"),
};
const llmBadge = document.getElementById("llmBadge");
const ttsBadge = document.getElementById("ttsBadge");

let engineState = { settings: null, providers: [], keys: {}, engines: [], separators: [], resident: [] };
let settingsSaveTimer = null;
const settingsListeners = [];

function onSettings(listener) {
  settingsListeners.push(listener);
}

function providerById(id) {
  return engineState.providers.find((item) => item.id === id)
    || engineState.providers.find((item) => item.id === "custom")
    || { id, label: id };
}

function engineById(id, list = engineState.engines) {
  return list.find((item) => item.id === id);
}

async function loadEngineSettings() {
  const data = await api("/api/settings");
  applySettingsPayload(data);
  renderLlmForm();
  for (const panel of enginePanels) panel.render();
  renderAsrForm();
  renderEngineSummaries();
  return data;
}

function applySettingsPayload(data) {
  engineState = {
    settings: data.settings,
    providers: data.providers || engineState.providers,
    keys: data.keys || {},
    engines: data.engines || [],
    separators: data.separators || [],
    resident: data.resident || [],
  };
  for (const listener of settingsListeners) listener(engineState);
}

function scheduleSettingsSave() {
  clearTimeout(settingsSaveTimer);
  settingsSaveTimer = setTimeout(() => saveEngineSettings().catch((error) => alert(error.message)), 400);
}

async function saveEngineSettings() {
  clearTimeout(settingsSaveTimer);
  const body = { llm: llmFormValues(), asr: asrFormValues() };
  for (const panel of enginePanels) body[panel.section] = panel.values();
  const data = await postJson("/api/settings", body);
  applySettingsPayload(data);
  renderEngineSummaries();
}

// ---------------------------------------------------------------- LLM

function renderLlmForm() {
  const llm = engineState.settings.llm;
  if (llmEl.provider.options.length !== engineState.providers.length) {
    llmEl.provider.innerHTML = "";
    for (const provider of engineState.providers) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.label;
      llmEl.provider.appendChild(option);
    }
  }
  llmEl.provider.value = llm.provider;
  if (document.activeElement !== llmEl.url) llmEl.url.value = llm.url || "";
  if (document.activeElement !== llmEl.model) llmEl.model.value = llm.model || "";
  llmEl.context.value = llm.contextLength || 8192;
  llmEl.autoLoad.checked = llm.autoLoad !== false;
  llmEl.unloadOthers.checked = llm.unloadOthers !== false;
  llmEl.unloadWhenDone.checked = llm.unloadWhenDone !== false;
  updateProviderFields();
}

function updateProviderFields() {
  const provider = providerById(llmEl.provider.value);
  llmEl.hint.textContent = provider.hint || "";
  llmEl.url.placeholder = provider.base_url ? t("По умолчанию: {url}", { url: provider.base_url }) : t("http://127.0.0.1:ПОРТ/v1");
  const masked = engineState.keys[provider.id];
  llmEl.keyField.hidden = !(provider.needs_key || masked || provider.id === "custom");
  llmEl.keyState.textContent = masked ? t("— сохранён {key}", { key: masked }) : provider.needs_key ? t("— нужен") : t("— если сервер требует");
  const managed = provider.manage === "lmstudio";
  llmEl.autoLoad.closest("label").hidden = !managed;
  llmEl.unloadOthers.closest("label").hidden = !managed;
}

function llmFormValues() {
  return {
    provider: llmEl.provider.value,
    url: llmEl.url.value.trim(),
    model: llmEl.model.value.trim(),
    contextLength: Number(llmEl.context.value || 8192),
    autoLoad: llmEl.autoLoad.checked,
    unloadOthers: llmEl.unloadOthers.checked,
    unloadWhenDone: llmEl.unloadWhenDone.checked,
  };
}

async function refreshLlmModels() {
  const values = llmFormValues();
  const params = new URLSearchParams({ provider: values.provider, url: values.url });
  badge(llmEl.status, t("Проверяю..."), "neutral");
  const data = await api(`/api/llm/models?${params}`);
  llmEl.modelList.innerHTML = "";
  llmEl.models.innerHTML = "";
  if (!data.ok) {
    badge(llmEl.status, t("Нет связи"), "bad");
    const row = document.createElement("div");
    row.className = "meta";
    row.textContent = `${data.error} ${providerById(values.provider).hint || ""}`;
    llmEl.models.appendChild(row);
    return;
  }
  badge(llmEl.status, data.models.length ? t("Доступно моделей: {count}", { count: data.models.length }) : t("Моделей нет"), data.models.length ? "ok" : "warn");
  for (const model of data.models) {
    const option = document.createElement("option");
    option.value = model.id;
    llmEl.modelList.appendChild(option);

    const row = document.createElement("button");
    row.type = "button";
    row.className = model.id === llmEl.model.value ? "model-row selected" : "model-row";
    row.innerHTML = `
      <div>
        <strong>${escapeHtml(model.name || model.id)}</strong>
        <div class="meta">${escapeHtml(model.id)}${model.details ? ` · ${escapeHtml(model.details)}` : ""}</div>
      </div>
      <div class="${model.loaded ? "loaded" : "meta"}">${model.loaded ? t("Загружена") : ""}</div>
    `;
    row.addEventListener("click", () => {
      llmEl.model.value = model.id;
      for (const other of llmEl.models.querySelectorAll(".model-row")) other.classList.remove("selected");
      row.classList.add("selected");
      scheduleSettingsSave();
    });
    llmEl.models.appendChild(row);
  }
}

llmEl.provider.addEventListener("change", async () => {
  llmEl.url.value = "";
  llmEl.model.value = "";
  llmEl.testResult.hidden = true;
  updateProviderFields();
  await saveEngineSettings();
  refreshLlmModels().catch(() => {});
});
for (const element of [llmEl.url, llmEl.model, llmEl.context, llmEl.autoLoad, llmEl.unloadOthers, llmEl.unloadWhenDone]) {
  element.addEventListener("change", scheduleSettingsSave);
}
llmEl.url.addEventListener("change", () => refreshLlmModels().catch(() => {}));
llmEl.refresh.addEventListener("click", () => refreshLlmModels().catch((error) => alert(error.message)));

llmEl.saveKey.addEventListener("click", async () => {
  try {
    const data = await postJson("/api/llm/key", { provider: llmEl.provider.value, apiKey: llmEl.apiKey.value.trim() });
    llmEl.apiKey.value = "";
    applySettingsPayload(data);
    updateProviderFields();
    refreshLlmModels().catch(() => {});
  } catch (error) {
    alert(error.message);
  }
});

llmEl.test.addEventListener("click", async () => {
  llmEl.test.disabled = true;
  llmEl.testResult.hidden = false;
  llmEl.testResult.className = "test-result";
  llmEl.testResult.textContent = t("Отправляю пробный запрос... Первая загрузка модели может занять минуту.");
  try {
    await saveEngineSettings();
    const data = await postJson("/api/llm/test", llmFormValues());
    llmEl.testResult.className = "test-result ok";
    llmEl.testResult.textContent = `${t("Работает: {model} ответила за {seconds} с:", { model: data.model, seconds: data.seconds })}\n${data.answer}`;
    refreshLlmModels().catch(() => {});
  } catch (error) {
    llmEl.testResult.className = "test-result bad";
    llmEl.testResult.textContent = t("Не получилось: {message}", { message: error.message });
  } finally {
    llmEl.test.disabled = false;
  }
});

llmEl.unload.addEventListener("click", async () => {
  try {
    await postJson("/api/llm/unload", llmFormValues());
    await refreshLlmModels();
  } catch (error) {
    alert(error.message);
  }
});

// ---------------------------------------------------------------- engine panels (TTS and separation)

const CAPABILITY_LABELS = {
  voice_clone: "клонирует голос по образцу",
  voice_list: "готовые голоса сервера",
  duration: "точная длительность фразы",
  speed: "управление скоростью",
  instruct: "текстовые подсказки к голосу",
};

function licenseBadge(engine) {
  if (engine.commercial_use === true) return `<span class="badge ok">${t("можно в коммерции")}</span>`;
  if (engine.commercial_use === false) return `<span class="badge warn">${t("только некоммерческое")}</span>`;
  return `<span class="badge neutral">${t("лицензия зависит от сервера")}</span>`;
}

function settingField(item, value, onChange) {
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
  input.addEventListener("change", onChange);

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

function createEnginePanel(root, section, listKey) {
  const statusEl = root.querySelector("[data-engine-status]");
  const body = document.createElement("div");
  root.appendChild(body);
  body.innerHTML = `
    <label class="field wide"><span>Движок</span><select data-engine-select></select></label>
    <div class="engine-info" data-engine-info></div>
    <div class="form-section" data-engine-install></div>
    <label class="field"><span>Устройство</span>
      <select data-engine-device>
        <option value="auto">Автоматически</option>
        <option value="cuda">Видеокарта (CUDA)</option>
        <option value="cpu">Процессор</option>
      </select>
    </label>
    <div data-engine-settings></div>
  `;
  const select = body.querySelector("[data-engine-select]");
  const info = body.querySelector("[data-engine-info]");
  const install = body.querySelector("[data-engine-install]");
  const device = body.querySelector("[data-engine-device]");
  const settingsBox = body.querySelector("[data-engine-settings]");

  const panel = {
    section,
    list: () => engineState[listKey] || [],
    selected: () => select.value || engineState.settings?.[section]?.engine || "",
    values() {
      const engineId = panel.selected();
      const values = {};
      for (const input of settingsBox.querySelectorAll("[data-setting]")) {
        const type = input.dataset.type;
        if (type === "bool") values[input.dataset.setting] = input.checked;
        else if (type === "int") values[input.dataset.setting] = parseInt(input.value, 10);
        else if (type === "float") values[input.dataset.setting] = Number(input.value);
        else values[input.dataset.setting] = input.value;
      }
      const result = { engine: engineId, device: device.value, engines: engineId ? { [engineId]: values } : {} };
      if (keepWarm) result.keepWarmMinutes = Math.max(0, Number(keepWarm.value || 0));
      return result;
    },
    render() {
      const current = engineState.settings[section];
      select.innerHTML = "";
      for (const engine of panel.list()) {
        const option = document.createElement("option");
        option.value = engine.id;
        option.textContent = engine.status?.installed ? engine.name : t("{name} — не установлен", { name: engine.name });
        select.appendChild(option);
      }
      select.value = current.engine;
      device.value = current.device || "auto";
      if (keepWarm) {
        if (document.activeElement !== keepWarm) keepWarm.value = current.keepWarmMinutes ?? 10;
        renderResident();
      }
      panel.renderDetails();
    },
    renderDetails() {
      const engine = engineById(select.value, panel.list());
      settingsBox.innerHTML = "";
      install.innerHTML = "";
      if (!engine) {
        info.textContent = t("Движок не найден в папке engines/.");
        badge(statusEl, t("Нет"), "bad");
        return;
      }
      const languages = Array.isArray(engine.languages) ? engine.languages.join(", ") : (engine.languages || "");
      const capabilities = Object.entries(engine.supports || {})
        .filter(([, enabled]) => enabled)
        .map(([key]) => `<span class="chip">${escapeHtml(t(CAPABILITY_LABELS[key] || key))}</span>`)
        .join("");
      info.innerHTML = `
        <p>${escapeHtml(engine.description || "")}</p>
        ${capabilities ? `<div class="chip-row">${capabilities}</div>` : ""}
        <div class="meta">${escapeHtml(t("Лицензия: {license}", { license: engine.license || "?" }))} ${licenseBadge(engine)}</div>
        ${languages ? `<div class="meta">${escapeHtml(t("Языки: {languages}", { languages }))}</div>` : ""}
        ${engine.homepage ? `<div class="meta"><a href="${escapeHtml(engine.homepage)}" target="_blank" rel="noreferrer">${escapeHtml(engine.homepage)}</a></div>` : ""}
      `;
      const status = engine.status || {};
      badge(statusEl, status.installed ? t("Готов") : t("Не установлен"), status.installed ? "ok" : "warn");
      install.innerHTML = `
        <div class="section-title">Установка</div>
        <div class="meta">${escapeHtml(status.message || "")}${status.python ? `<br><code>${escapeHtml(status.python)}</code>` : ""}</div>
        ${engine.install_note ? `<div class="meta">${escapeHtml(engine.install_note)}</div>` : ""}
      `;
      if (engine.runtime !== "main") {
        if (!status.installed && status.installable !== false) {
          const installBtn = actionButton(t("Установить движок"), () => installEngine(engine.id, engine));
          installBtn.className = "primary";
          installBtn.dataset.jobButton = "1";
          install.appendChild(installBtn);
        }
        const pythonRow = document.createElement("div");
        pythonRow.className = "input-action python-row";
        pythonRow.innerHTML = `<input placeholder="${t("Или путь к python.exe уже готового окружения с этой моделью")}">`;
        const pythonInput = pythonRow.querySelector("input");
        pythonInput.value = status.source === "override" ? status.python : "";
        pythonRow.appendChild(actionButton(status.source === "override" ? t("Сохранить путь") : t("Указать"), async () => {
          try {
            const data = await postJson("/api/engines/python", { engine: engine.id, python: pythonInput.value.trim() });
            applySettingsPayload(data);
            panel.render();
            renderEngineSummaries();
          } catch (error) {
            alert(error.message);
          }
        }));
        install.appendChild(pythonRow);
      }

      const values = engine.values || {};
      const basic = document.createElement("div");
      const advanced = document.createElement("details");
      advanced.className = "accordion";
      advanced.innerHTML = `<summary>${t("Дополнительные настройки движка")}</summary>`;
      for (const item of engine.settings || []) {
        (item.advanced ? advanced : basic).appendChild(settingField(item, values[item.key], scheduleSettingsSave));
      }
      settingsBox.appendChild(basic);
      if (advanced.children.length > 1) settingsBox.appendChild(advanced);

      if (engine.supports?.voice_list) {
        const voicesRow = document.createElement("div");
        voicesRow.className = "voices-row";
        const list = document.createElement("div");
        list.className = "chip-row";
        voicesRow.appendChild(actionButton(t("Голоса сервера"), async () => {
          try {
            await saveEngineSettings();
            const data = await postJson("/api/engines/voices", { engine: engine.id, settings: panel.values().engines[engine.id] });
            list.innerHTML = "";
            for (const voice of data.voices) {
              list.appendChild(actionButton(voice, () => {
                const input = settingsBox.querySelector('[data-setting="voice"]');
                if (input) {
                  input.value = voice;
                  scheduleSettingsSave();
                }
              }));
            }
          } catch (error) {
            alert(error.message);
          }
        }));
        voicesRow.appendChild(list);
        settingsBox.appendChild(voicesRow);
      }
    },
  };

  let keepWarm = null;
  let residentStatus = null;
  function renderResident() {
    const loaded = (engineState.resident || []).filter((item) => item.loaded);
    residentStatus.textContent = loaded.length
      ? loaded.map((item) => (item.idle_left != null
        ? t("{name}: загружена, выгрузится через {minutes} мин без работы", { name: item.name, minutes: Math.ceil(item.idle_left / 60) })
        : t("{name}: загружена", { name: item.name }))).join("; ")
      : t("Сейчас ни одна модель озвучки не загружена.");
  }
  if (section === "tts") {
    const warm = document.createElement("div");
    warm.className = "form-section";
    warm.innerHTML = `
      <div class="section-title">Держать модель загруженной</div>
      <label class="field"><span>Минут после работы (0 — выгружать сразу)</span>
        <input type="number" min="0" max="240" step="1" data-keep-warm></label>
      <p class="meta" data-resident-status></p>
      <div class="actions"><button type="button" class="secondary" data-release>Выгрузить модель сейчас</button></div>
      <p class="hint">Пока модель загружена, переозвучка фразы и пробная фраза занимают секунды. Перед распознаванием и переводом на локальной модели она выгружается сама, чтобы освободить видеопамять.</p>
    `;
    body.insertBefore(warm, settingsBox);
    keepWarm = warm.querySelector("[data-keep-warm]");
    residentStatus = warm.querySelector("[data-resident-status]");
    keepWarm.addEventListener("change", scheduleSettingsSave);
    warm.querySelector("[data-release]").addEventListener("click", async () => {
      try {
        const data = await postJson("/api/engines/release", {});
        applySettingsPayload(data);
        renderResident();
      } catch (error) {
        alert(error.message);
      }
    });
  }
  if (section === "tts") {
    const test = document.createElement("div");
    test.className = "form-section";
    test.innerHTML = `
      <div class="section-title">Пробная фраза</div>
      <div class="row">
        <label class="field wide"><span>Текст</span><input data-test-text></label>
        <label class="field"><span>Язык</span><input data-test-language placeholder="ru, en, de, ..."></label>
      </div>
      <div class="actions"><button type="button" class="primary" data-test-run data-job-button>▶ Озвучить пробную фразу</button></div>
      <audio data-test-player controls preload="none" hidden></audio>
    `;
    body.appendChild(test);
    test.querySelector("[data-test-text]").value = t("Привет! Это проверка голоса.");
    test.querySelector("[data-test-language]").value = UI_LANGUAGE;
    const player = test.querySelector("[data-test-player]");
    test.querySelector("[data-test-run]").addEventListener("click", async () => {
      try {
        await saveEngineSettings();
        await postJson("/api/engines/test", {
          engine: panel.selected(),
          text: test.querySelector("[data-test-text]").value,
          language: test.querySelector("[data-test-language]").value.trim(),
        });
        await refreshState();
        const result = await waitForJobCompletion("engine_test", t("Движок не смог озвучить фразу. Подробности в логе."));
        if (result.name) {
          player.src = outputFileUrl(result.name);
          player.hidden = false;
          player.play().catch(() => {});
        }
        await loadEngineSettings();  // the engine is warm now
      } catch (error) {
        alert(error.message);
      }
    });
  }

  select.addEventListener("change", async () => {
    settingsBox.innerHTML = "";  // do not save the previous engine's fields under the new id
    await saveEngineSettings();
    panel.renderDetails();
  });
  device.addEventListener("change", scheduleSettingsSave);
  return panel;
}

const enginePanels = [
  createEnginePanel(document.getElementById("ttsPanel"), "tts", "engines"),
  createEnginePanel(document.getElementById("separationPanel"), "separation", "separators"),
];

async function installEngine(engineId, engine) {
  const question = t("Установить «{name}»?", { name: engine?.name || engineId });
  if (!confirm(`${question}\n\n${engine?.install_note || ""}\n\n${t("Прогресс будет в логе внизу страницы.")}`)) return;
  try {
    await postJson("/api/engines/install", { engine: engineId });
    await refreshState();
    await waitForJobCompletion("engine_install", t("Установка не удалась. Подробности в логе."));
  } catch (error) {
    alert(error.message);
  } finally {
    await loadEngineSettings();
  }
}

// ---------------------------------------------------------------- Whisper

function renderAsrForm() {
  const asr = engineState.settings.asr || {};
  asrEl.model.value = asr.model || "medium";
  asrEl.device.value = asr.device || "cuda";
  asrEl.compute.value = asr.computeType || "float16";
}

function asrFormValues() {
  return { model: asrEl.model.value, device: asrEl.device.value, computeType: asrEl.compute.value };
}

asrEl.device.addEventListener("change", () => {
  if (asrEl.device.value === "cpu" && asrEl.compute.value.includes("float16")) asrEl.compute.value = "int8";
});
for (const element of Object.values(asrEl)) element.addEventListener("change", scheduleSettingsSave);

// ---------------------------------------------------------------- summaries

function renderEngineSummaries() {
  const settings = engineState.settings;
  if (!settings) return;
  const provider = providerById(settings.llm.provider);
  const model = settings.llm.model || (provider.manage === "lmstudio" ? t("загруженная модель") : t("модель не выбрана"));
  const llmText = `${provider.label} · ${model}`;
  const engine = engineById(settings.tts.engine);
  const ttsText = !engine ? settings.tts.engine
    : engine.status?.installed ? engine.name : t("{name} (не установлен)", { name: engine.name });
  for (const element of document.querySelectorAll("[data-llm-summary]")) element.textContent = llmText;
  for (const element of document.querySelectorAll("[data-tts-summary]")) element.textContent = ttsText;
  badge(llmBadge, t("Перевод: {name}", { name: provider.label }), settings.llm.model || provider.manage === "lmstudio" ? "ok" : "warn");
  llmBadge.title = llmText;
  badge(ttsBadge, t("Озвучка: {name}", { name: engine ? engine.name : settings.tts.engine }), engine?.status?.installed ? "ok" : "bad");
  ttsBadge.title = ttsText;
}

// Fresh engine status (installed, loaded models) whenever Settings opens.
document.querySelector('.rail-item[data-view="settings"]').addEventListener("click", () => {
  loadEngineSettings().catch(() => {});
});
