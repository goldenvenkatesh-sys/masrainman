/* MasRainman — 24-hour Day 1..Day 12 rainfall map */

const INDIA_BOUNDS = L.latLngBounds(
  [6.0, 68.0],
  [37.5, 97.5]
);

const levels = [
  0.1, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40,
  50, 60, 70, 80, 90, 100, 125, 150, 175, 200,
  250, 300, 400, 500, 600, 800
];

const colors = [
  "#f2f2f2", "#c7dcff", "#8ebfff", "#4aa3ff",
  "#007cff", "#004b99", "#1b5e20", "#00c853",
  "#64dd17", "#c6ff00", "#ffd600", "#ffab00",
  "#ff6d00", "#ff8f00", "#ff5c8a", "#ff1f5b",
  "#ff0033", "#d50000", "#7b1fa2", "#6a00ff",
  "#c000ff", "#d580ff", "#f0ccff", "#d9d9d9",
  "#a6a6a6", "#7a7a7a", "#4d4d4d", "#333333"
];

const state = {
  data: null,
  map: null,
  canvas: null,
  ctx: null,
  imageData: null,
  renderFrame: 0,
  opacity: 0.8,
  selectedDay: 0,
  selectedModels: new Set(["ECMWF HRES", "GEM", "GFS", "ICON"]),
};

function $(id) { return document.getElementById(id); }

function ensureUi() {
  const aside = document.querySelector("aside");
  if (!aside) return;

  const oldPeriod = $("period");
  if (oldPeriod) {
    oldPeriod.innerHTML = "";
    for (let i = 0; i < 12; i++) {
      const opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = `Day ${i + 1}`;
      oldPeriod.appendChild(opt);
    }
    oldPeriod.previousSibling && oldPeriod.previousSibling.nodeType === 3;
    const label = oldPeriod.parentElement;
    if (label) {
      label.childNodes.forEach(n => {
        if (n.nodeType === Node.TEXT_NODE && n.textContent.includes("6-hour")) {
          n.textContent = "Forecast day";
        }
      });
    }
  }

  const heading = document.querySelector("header h1");
  if (heading) heading.textContent = "24-Hour Rainfall Forecast";

  const note = aside.querySelector(".note");
  if (note) {
    note.innerHTML = "Rainfall is accumulated for each forecast day. ECMWF/GFS extend to Day 12; GEM to Day 10; ICON to Day 7.";
  }
}

function getSelectedModels() {
  const boxes = document.querySelectorAll("input.model");
  const selected = [];
  boxes.forEach(box => {
    if (box.checked) selected.push(box.value);
  });
  state.selectedModels = new Set(selected);
  return selected;
}

function setupControls() {
  document.querySelectorAll("input.model").forEach(box => {
    box.addEventListener("change", () => {
      getSelectedModels();
      drawRainfall();
    });
  });

  const day = $("period");
  if (day) {
    day.addEventListener("change", () => {
      state.selectedDay = Number(day.value) || 0;
      updateHeader();
      drawRainfall();
    });
  }

  const opacity = $("opacity");
  if (opacity) {
    state.opacity = Number(opacity.value) || 0.8;
    opacity.addEventListener("input", () => {
      state.opacity = Number(opacity.value) || 0.8;
      if (state.canvas) state.canvas.style.opacity = String(state.opacity);
    });
  }
}

function getRunTimestamp() {
  if (!state.data || !state.data.updated) return null;
  const updated = new Date(state.data.updated);
  if (Number.isNaN(updated.getTime())) return null;

  // The updater completes after a model run. Snap down to the latest
  // completed 6-hour cycle so the header identifies the model run.
  const run = new Date(updated.getTime());
  run.setUTCMinutes(0, 0, 0);
  run.setUTCHours(Math.floor(run.getUTCHours() / 6) * 6);
  return run;
}

function getValidDayRange(dayIndex) {
  if (state.data && state.data.valid_days && state.data.valid_days[dayIndex]) {
    return state.data.valid_days[dayIndex];
  }
  const run = getRunTimestamp();
  if (!run) return null;
  const start = new Date(run.getTime() + dayIndex * 86400000);
  const end = new Date(start.getTime() + 86400000);
  return { start: start.toISOString(), end: end.toISOString() };
}

function formatIST(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).replace(",", " / ");
}

function updateHeader() {
  const time = $("time");
  if (!time || !state.data) return;

  const run = getRunTimestamp();
  const range = getValidDayRange(state.selectedDay);
  const runText = run
    ? `${run.toLocaleDateString("en-GB", {day:"2-digit", month:"short", year:"numeric", timeZone:"UTC"})} / ${String(run.getUTCHours()).padStart(2,"0")}Z`
    : "—";

  let validText = "—";
  if (range) {
    validText = `${formatIST(range.start)} IST – ${formatIST(range.end)} IST`;
  }

  time.innerHTML = `<b>Run:</b> ${runText} &nbsp; | &nbsp; <b>Valid:</b> Day ${state.selectedDay + 1} &nbsp; ${validText}`;
}

function setupMap() {
  state.map = L.map("map", {
    zoomControl: true,
    preferCanvas: true,
    minZoom: 4,
    maxZoom: 10,
    worldCopyJump: false,
  });

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap contributors",
  }).addTo(state.map);

  state.map.fitBounds(INDIA_BOUNDS, { padding: [18, 18] });

  state.map.on("zoomend moveend resize", scheduleRender);

  const map = document.getElementById("map");
  if (map) {
    const ro = new ResizeObserver(() => {
      state.map.invalidateSize({ pan: false });
      scheduleRender();
    });
    ro.observe(map);
  }
}

function colorAt(value) {
  if (!Number.isFinite(value) || value < 1) return null;
  if (value >= levels[levels.length - 1]) return colors[colors.length - 1];

  let i = 0;
  while (i < levels.length - 1 && value > levels[i + 1]) i++;
  const a = levels[i];
  const b = levels[i + 1];
  const t = Math.max(0, Math.min(1, (value - a) / (b - a)));
  return mixHex(colors[i], colors[i + 1], t);
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0,2),16), parseInt(h.slice(2,4),16), parseInt(h.slice(4,6),16)];
}

function mixHex(a, b, t) {
  const ar = hexToRgb(a), br = hexToRgb(b);
  return `rgb(${Math.round(ar[0] + (br[0]-ar[0])*t)},${Math.round(ar[1] + (br[1]-ar[1])*t)},${Math.round(ar[2] + (br[2]-ar[2])*t)})`;
}

function buildSourceGrid() {
  const grid = state.data.grid || [];
  const rows = new Map();
  const cols = new Set();
  grid.forEach(p => {
    cols.add(p.lon);
    if (!rows.has(p.lat)) rows.set(p.lat, []);
    rows.get(p.lat).push(p);
  });
  const lats = [...rows.keys()].sort((a,b) => a-b);
  const lons = [...cols].sort((a,b) => a-b);
  const map = new Map();
  grid.forEach(p => map.set(`${p.lat}|${p.lon}`, p));
  return { lats, lons, map };
}

function interpolate(grid, lat, lon, model, dayIndex) {
  const { lats, lons, map } = grid;
  if (!lats.length || !lons.length) return null;

  lat = Math.max(lats[0], Math.min(lats[lats.length - 1], lat));
  lon = Math.max(lons[0], Math.min(lons[lons.length - 1], lon));

  let j = Math.floor((lat - lats[0]) / (lats[1] - lats[0]));
  let i = Math.floor((lon - lons[0]) / (lons[1] - lons[0]));
  j = Math.max(0, Math.min(lats.length - 2, j));
  i = Math.max(0, Math.min(lons.length - 2, i));

  const lat0 = lats[j], lat1 = lats[j+1];
  const lon0 = lons[i], lon1 = lons[i+1];
  const fy = (lat - lat0) / (lat1 - lat0);
  const fx = (lon - lon0) / (lon1 - lon0);

  const pts = [
    map.get(`${lat0}|${lon0}`), map.get(`${lat0}|${lon1}`),
    map.get(`${lat1}|${lon0}`), map.get(`${lat1}|${lon1}`),
  ];
  const vals = pts.map(p => {
    const v = p && p.models && p.models[model] ? p.models[model][dayIndex] : null;
    return Number.isFinite(v) ? v : null;
  });
  const available = vals.filter(v => v !== null);
  if (!available.length) return null;
  if (available.length < 4) return available.reduce((a,b) => a+b,0) / available.length;

  return vals[0]*(1-fx)*(1-fy) + vals[1]*fx*(1-fy) + vals[2]*(1-fx)*fy + vals[3]*fx*fy;
}

function blendedValue(grid, lat, lon, dayIndex, models) {
  const vals = models.map(model => interpolate(grid, lat, lon, model, dayIndex)).filter(v => Number.isFinite(v));
  if (!vals.length) return null;
  return vals.reduce((a,b) => a+b,0) / vals.length;
}

function makeCanvas() {
  const mapEl = $("map");
  if (!mapEl) return;
  if (!state.canvas) {
    state.canvas = document.createElement("canvas");
    state.canvas.className = "rainfall-overlay";
    state.canvas.style.position = "absolute";
    state.canvas.style.left = "0";
    state.canvas.style.top = "0";
    state.canvas.style.pointerEvents = "none";
    state.canvas.style.zIndex = "450";
    state.canvas.style.opacity = String(state.opacity);
    mapEl.appendChild(state.canvas);
  }

  const w = Math.max(1, mapEl.clientWidth);
  const h = Math.max(1, mapEl.clientHeight);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  state.canvas.width = Math.round(w * dpr);
  state.canvas.height = Math.round(h * dpr);
  state.canvas.style.width = `${w}px`;
  state.canvas.style.height = `${h}px`;
  state.ctx = state.canvas.getContext("2d", { alpha: true });
  state.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function drawRainfall() {
  if (!state.data || !state.map) return;
  makeCanvas();
  const ctx = state.ctx;
  const canvas = state.canvas;
  if (!ctx || !canvas) return;

  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  const models = [...state.selectedModels];
  ctx.clearRect(0, 0, w, h);
  if (!models.length) return;

  const source = buildSourceGrid();
  // Draw at the actual viewport resolution. Each screen pixel gets a
  // bilinearly interpolated value from the 0.5° display grid. This means
  // zooming never scales a tiny fixed raster and therefore avoids the
  // old pixelated appearance.
  const step = 2;
  const image = ctx.createImageData(Math.ceil(w / step), Math.ceil(h / step));
  const iw = image.width;
  const ih = image.height;

  for (let py = 0; py < ih; py++) {
    const y = py * step;
    for (let px = 0; px < iw; px++) {
      const x = px * step;
      const geo = state.map.containerPointToLatLng([x, y]);
      const lat = geo.lat;
      const lng = geo.lng;
      if (lat < state.data.domain.lat_min || lat > state.data.domain.lat_max || lng < state.data.domain.lon_min || lng > state.data.domain.lon_max) continue;

      const value = blendedValue(source, lat, lng, state.selectedDay, models);
      const color = colorAt(value);
      if (!color) continue;
      const [r,g,b] = hexToRgb(color.match(/#/) ? color : rgbToHex(color));
      const alpha = Math.round(235);
      const idx = (py * iw + px) * 4;
      image.data[idx] = r;
      image.data[idx+1] = g;
      image.data[idx+2] = b;
      image.data[idx+3] = alpha;
    }
  }

  const temp = document.createElement("canvas");
  temp.width = iw;
  temp.height = ih;
  temp.getContext("2d").putImageData(image, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(temp, 0, 0, w, h);
}

function rgbToHex(rgb) {
  const m = rgb.match(/\d+/g);
  if (!m) return "#000000";
  return "#" + m.slice(0,3).map(v => Number(v).toString(16).padStart(2,"0")).join("");
}

function scheduleRender() {
  cancelAnimationFrame(state.renderFrame);
  state.renderFrame = requestAnimationFrame(drawRainfall);
}

function buildLegend() {
  const legend = $("legend");
  if (!legend) return;
  legend.innerHTML = `<div class="legend-title">24-hour rainfall (mm)</div>` +
    levels.map((v,i) => `<span class="legend-item"><i style="background:${colors[i]}"></i>${v}</span>`).join("");
}

async function loadData() {
  const res = await fetch(`data.json?v=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`data.json HTTP ${res.status}`);
  state.data = await res.json();
  ensureUi();
  setupControls();
  getSelectedModels();
  updateHeader();
  buildLegend();
  drawRainfall();
}

setupMap();
loadData().catch(err => {
  console.error(err);
  const time = $("time");
  if (time) time.textContent = `Unable to load rainfall data: ${err.message}`;
});
