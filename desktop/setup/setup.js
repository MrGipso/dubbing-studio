// First-run screen of the desktop app. On the first launch (and after an
// update that changes the Python packages) it lets the shell set up Python and
// ffmpeg, then starts the app and opens it in this window.

const TEXT = {
  ru: {
    title: "Готовлю программу к работе",
    lead: "Это нужно только при первом запуске: программа скачает всё необходимое и дальше будет открываться сразу.",
    python: "Python и распознавание речи",
    pythonHint: "Около 250 МБ пакетов, с видеокартой NVIDIA ещё около 500 МБ",
    ffmpeg: "ffmpeg",
    ffmpegHint: "Около 180 МБ: читает и собирает видео",
    start: "Запуск",
    startHint: "Движки озвучки выбираются в программе, в «Настройках»",
    details: "Подробности",
    openLogs: "Открыть журнал",
    retry: "Повторить",
    starting: "Запускаю…",
    checking: "Проверяю, что уже установлено…",
    failed: "Не получилось: {error}. Проверьте интернет и нажмите «Повторить».",
    folders: "Данные: {data} · Проекты: {library}",
  },
  en: {
    title: "Getting the app ready",
    lead: "This happens only on the first launch: the app downloads what it needs and opens right away next time.",
    python: "Python and speech recognition",
    pythonHint: "About 250 MB of packages, plus about 500 MB with an NVIDIA GPU",
    ffmpeg: "ffmpeg",
    ffmpegHint: "About 180 MB: reads and builds videos",
    start: "Start",
    startHint: "Voice engines are chosen in the app, under “Settings”",
    details: "Details",
    openLogs: "Open log",
    retry: "Retry",
    starting: "Starting…",
    checking: "Checking what is already installed…",
    failed: "That did not work: {error}. Check the internet connection and press “Retry”.",
    folders: "Data: {data} · Projects: {library}",
  },
};
const language = (navigator.language || "en").toLowerCase().match(/^(ru|uk|be|kk)/) ? "ru" : "en";
const words = TEXT[language];
const say = (key, params = {}) => words[key].replace(/\{(\w+)\}/g, (_, name) => params[name] ?? "");

document.documentElement.lang = language;
for (const element of document.querySelectorAll("[data-text]")) element.textContent = say(element.dataset.text);

const { invoke } = window.__TAURI__.core;
const { listen } = window.__TAURI__.event;
const fill = document.getElementById("fill");
const status = document.getElementById("status");
const log = document.getElementById("log");
const errorActions = document.getElementById("errorActions");

function setStep(name, state) {
  const item = document.querySelector(`[data-step="${name}"]`);
  item.classList.remove("active", "done", "failed");
  if (state) item.classList.add(state);
}

function setProgress(fraction) {
  fill.classList.toggle("indeterminate", fraction === null);
  if (fraction !== null) fill.style.width = `${Math.max(4, Math.min(100, fraction * 100))}%`;
}

let currentStep = "";
listen("setup-log", (event) => {
  const { step, line } = event.payload;
  if (step !== currentStep) {
    if (currentStep) setStep(currentStep, "done");
    currentStep = step;
    setStep(step, "active");
    setProgress(null);
  }
  const match = line.match(/^PROGRESS (\d+)\/(\d+)/);
  if (match) {
    setProgress(Number(match[1]) / Number(match[2]));
    return;
  }
  log.textContent += `${line}\n`;
  log.scrollTop = log.scrollHeight;
  if (line.trim()) status.textContent = line.trim();
});

async function launch() {
  errorActions.hidden = true;
  status.classList.remove("error");
  try {
    status.textContent = say("checking");
    setProgress(null);
    const state = await invoke("setup_status");
    document.getElementById("foot").textContent = say("folders", { data: state.data_dir, library: state.library_dir });
    if (!state.ready) {
      document.getElementById("card").classList.add("installing");
      setStep("python", "active");
      currentStep = "python";
      await invoke("run_setup");
    }
    for (const step of ["python", "ffmpeg"]) setStep(step, "done");
    setStep("start", "active");
    status.textContent = say("starting");
    setProgress(null);
    const url = await invoke("start_server");
    setStep("start", "done");
    window.location.replace(url);
  } catch (error) {
    if (currentStep) setStep(currentStep, "failed");
    setProgress(0);
    status.classList.add("error");
    status.textContent = say("failed", { error: String(error) });
    errorActions.hidden = false;
  }
}

document.getElementById("retry").addEventListener("click", launch);
document.getElementById("openLogs").addEventListener("click", () => invoke("open_logs"));
launch();
