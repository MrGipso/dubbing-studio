// Translation step: a timeline of the phrases over the waveform of the original
// speech. Blocks are coloured by speaker and outlined by how the phrase fits its
// slot; a click finds the phrase in the table and plays it. The timeline shares
// the phrase player with the table, so the playhead follows either of them.

const tl = {
  root: document.getElementById("timeline"),
  track: document.querySelector("[data-tl-track]"),
  scroll: document.querySelector("[data-tl-scroll]"),
  wave: document.querySelector("[data-tl-wave]"),
  blocks: document.querySelector("[data-tl-blocks]"),
  head: document.querySelector("[data-tl-head]"),
  time: document.querySelector("[data-tl-time]"),
  play: document.querySelector("[data-tl-play]"),
  legend: document.querySelector("[data-tl-legend]"),
};
const TL_ZOOMS = [1, 2, 4, 8];
const timelineState = { key: "", blocksKey: "", peaks: [], duration: 0, zoom: 1, source: "original", position: 0, selected: "" };

function timelineSourceUrl() {
  const files = currentProject?.files || {};
  if (timelineState.source === "dub") return files.voice_track?.url || "";
  return (files.vocals || files.source)?.url || "";
}

function playerOnTimeline() {
  const url = timelineSourceUrl();
  return Boolean(url) && phrasePlayer.dataset.src === url;
}

async function renderTimeline(p) {
  const segments = p.segments || [];
  const files = p.files || {};
  tl.root.hidden = !segments.length;
  if (tl.root.hidden) return;
  const key = `${p.id}|${(files.vocals || files.source)?.url}`;
  if (timelineState.key !== key) {
    timelineState.key = key;
    timelineState.peaks = [];
    timelineState.duration = Number(p.source?.duration) || 0;
    timelineState.position = 0;
    timelineState.selected = "";
    timelineState.blocksKey = "";
    try {
      const data = await api(projectUrl(p.id, "peaks"));
      if (timelineState.key !== key) return;  // another project was opened meanwhile
      timelineState.peaks = data.peaks || [];
      timelineState.duration = data.duration || timelineState.duration;
      timelineState.blocksKey = "";
    } catch (error) {
      console.error(error);
    }
    drawWave();
  }
  const dub = tl.root.querySelector('[data-source="dub"]');
  dub.disabled = !files.voice_track;
  dub.title = files.voice_track ? "" : t("Появится после озвучки");
  if (!files.voice_track && timelineState.source === "dub") setTimelineSource("original");
  renderTimelineBlocks(currentProject || p);
  renderTimelineLegend(currentProject || p);
  updateTimelineHead();
}

function drawWave() {
  const canvas = tl.wave;
  const width = tl.track.clientWidth;
  const height = canvas.clientHeight || 56;
  if (!width) return;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * ratio);
  canvas.height = Math.round(height * ratio);
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, width, height);
  const peaks = timelineState.peaks;
  if (!peaks.length) return;
  context.fillStyle = getComputedStyle(document.documentElement).getPropertyValue("--wave").trim() || "#8d96a8";
  const bar = 2;
  const step = 3;
  const columns = Math.max(1, Math.floor(width / step));
  for (let column = 0; column < columns; column++) {
    const from = Math.floor((column / columns) * peaks.length);
    const to = Math.max(from + 1, Math.floor(((column + 1) / columns) * peaks.length));
    let value = 0;
    for (let index = from; index < to; index++) value = Math.max(value, peaks[index] || 0);
    const size = Math.max(1, Math.sqrt(value) * (height - 4));  // square root keeps quiet speech visible
    context.fillRect(column * step, (height - size) / 2, bar, size);
  }
}

function timelineDuration(p) {
  const segments = p.segments || [];
  return timelineState.duration || Math.max(1, ...segments.map((segment) => Number(segment.end) || 0));
}

function renderTimelineBlocks(p) {
  const segments = p.segments || [];
  const speakers = p.speaker_list || [];
  const voiced = Boolean(p.active_track?.voiced);
  const key = JSON.stringify([timelineState.duration, voiced, speakers.map((speaker) => speaker.id),
    segments.map((s) => [s.id, s.start, s.end, s.speaker, s.dirty, s.speedup, s.clipped, s.translation])]);
  if (key === timelineState.blocksKey) return;
  timelineState.blocksKey = key;
  const duration = timelineDuration(p);
  tl.blocks.innerHTML = segments.map((segment) => {
    const start = Number(segment.start) || 0;
    const end = Number(segment.end ?? start + Number(segment.duration || 0)) || start;
    const speaker = speakers.length > 1
      ? ` data-speaker-index="${Math.max(0, speakers.findIndex((item) => item.id === segment.speaker)) % 6}"` : "";
    const fit = !voiced ? "" : segment.dirty ? "dirty" : (segment.clipped || 0) > 0.05 ? "bad" : (segment.speedup || 1) > 1.02 ? "warn" : "";
    const title = `${formatClock(start)} · ${segment.translation || segment.source || ""}`;
    return `<button type="button" class="tl-block${fit ? ` fit-${fit}` : ""}" data-tl-id="${segment.id}"${speaker}
      style="left:${((start / duration) * 100).toFixed(3)}%;width:${Math.max(0.15, ((end - start) / duration) * 100).toFixed(3)}%"
      title="${escapeHtml(title)}"></button>`;
  }).join("");
  markTimelineSelection();
}

function renderTimelineLegend(p) {
  const speakers = p.speaker_list || [];
  tl.legend.innerHTML = speakers.length > 1
    ? speakers.map((speaker, index) =>
      `<span data-speaker-index="${index % 6}" translate="no">${escapeHtml(speakerLabel(speaker, index))}</span>`).join("")
    : "";
}

function markTimelineSelection() {
  for (const block of tl.blocks.querySelectorAll(".tl-block.selected")) block.classList.remove("selected");
  const block = timelineState.selected && tl.blocks.querySelector(`[data-tl-id="${CSS.escape(timelineState.selected)}"]`);
  if (block) block.classList.add("selected");
}

function updateTimelineHead() {
  const duration = timelineDuration(currentProject || {});
  const position = Math.max(0, Math.min(duration, timelineState.position));
  tl.head.style.left = `${(position / duration) * 100}%`;
  tl.time.textContent = `${formatClock(position)} / ${formatClock(duration)}`;
  const current = (currentProject?.segments || []).find((segment) =>
    position >= Number(segment.start) && position < Number(segment.end ?? segment.start));
  const id = current ? String(current.id) : "";
  for (const block of tl.blocks.querySelectorAll(".tl-block.current")) {
    if (block.dataset.tlId !== id) block.classList.remove("current");
  }
  if (id && !phrasePlayer.paused) tl.blocks.querySelector(`[data-tl-id="${CSS.escape(id)}"]`)?.classList.add("current");
}

function keepHeadVisible() {
  const scroll = tl.scroll;
  const x = (timelineState.position / timelineDuration(currentProject || {})) * tl.track.clientWidth;
  if (x < scroll.scrollLeft + 24 || x > scroll.scrollLeft + scroll.clientWidth - 24) {
    scroll.scrollLeft = Math.max(0, x - scroll.clientWidth / 3);
  }
}

function selectPhrase(id, { play = true, scroll = true } = {}) {
  timelineState.selected = String(id);
  markTimelineSelection();
  for (const row of pv.segments.querySelectorAll(".segment-focus")) row.classList.remove("segment-focus");
  const row = pv.segments.querySelector(`.segment-row[data-id="${CSS.escape(String(id))}"]`);
  if (row) {
    row.classList.add("segment-focus");
    if (scroll) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
  const segment = (currentProject?.segments || []).find((item) => String(item.id) === String(id));
  if (!segment) return;
  const start = Number(segment.start);
  const end = Number(segment.end ?? start + Number(segment.duration || 0));
  timelineState.position = start;
  updateTimelineHead();
  keepHeadVisible();
  const url = timelineSourceUrl();
  if (play && url) playPhrase(url, start, timelineState.source === "dub" ? end + 0.6 : end);
}

function setTimelineSource(source) {
  if (!phrasePlayer.paused) phrasePlayer.pause();
  timelineState.source = source;
  for (const button of tl.root.querySelectorAll("[data-source]")) button.classList.toggle("active", button.dataset.source === source);
}

let timelineFrame = null;
function followPlayer() {
  if (playerOnTimeline()) {
    timelineState.position = phrasePlayer.currentTime;
    updateTimelineHead();
    keepHeadVisible();
  }
  timelineFrame = phrasePlayer.paused ? null : requestAnimationFrame(followPlayer);
}

phrasePlayer.addEventListener("play", () => {
  tl.root.classList.toggle("playing", playerOnTimeline());
  if (!timelineFrame) timelineFrame = requestAnimationFrame(followPlayer);
});
for (const name of ["pause", "ended"]) {
  phrasePlayer.addEventListener(name, () => {
    tl.root.classList.remove("playing");
    updateTimelineHead();
  });
}

tl.play.addEventListener("click", () => {
  if (!phrasePlayer.paused && playerOnTimeline()) {
    phrasePlayer.pause();
    return;
  }
  const url = timelineSourceUrl();
  if (!url) return;
  const duration = timelineDuration(currentProject || {});
  playPhrase(url, timelineState.position >= duration - 0.2 ? 0 : timelineState.position, null);
});

tl.track.addEventListener("click", (event) => {
  const block = event.target.closest("[data-tl-id]");
  if (block) {
    selectPhrase(block.dataset.tlId);
    return;
  }
  const rect = tl.track.getBoundingClientRect();
  timelineState.position = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * timelineDuration(currentProject || {});
  updateTimelineHead();
  if (!phrasePlayer.paused && playerOnTimeline()) phrasePlayer.currentTime = timelineState.position;
});

tl.root.querySelector("[data-tl-source]").addEventListener("click", (event) => {
  const button = event.target.closest("[data-source]");
  if (button && !button.disabled) setTimelineSource(button.dataset.source);
});

tl.root.addEventListener("click", (event) => {
  const button = event.target.closest("[data-tl-zoom]");
  if (!button) return;
  const index = Math.max(0, Math.min(TL_ZOOMS.length - 1, TL_ZOOMS.indexOf(timelineState.zoom) + Number(button.dataset.tlZoom)));
  timelineState.zoom = TL_ZOOMS[index];
  tl.track.style.width = `${timelineState.zoom * 100}%`;
  drawWave();
  keepHeadVisible();
});

// The table drives the timeline too: editing a phrase selects its block.
pv.segments.addEventListener("focusin", (event) => {
  const row = event.target.closest(".segment-row[data-id]");
  if (row && event.target.matches(".segment-text")) selectPhrase(row.dataset.id, { play: false, scroll: false });
});

new ResizeObserver(() => drawWave()).observe(tl.scroll);
document.getElementById("themeToggle").addEventListener("click", () => setTimeout(drawWave));
