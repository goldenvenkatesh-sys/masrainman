/* MasRainman — Day 1..Day 12 rainfall + optional 850 hPa wind barbs */

// Default India-centered view. Keep the full Indian peninsula and Sri Lanka
// visible while retaining enough north/east context for the reference layout.
const INDIA_BOUNDS = L.latLngBounds(
  [5.5, 68.0],
  [36.5, 97.5]
);

const levels = [
  0.1, 1, 5, 10, 25, 50, 100, 200
];

// Target-map rainfall palette: stepped weather-radar bands with
// light/deep blue -> green -> yellow -> orange -> red.
const colors = [
  "#b9ddff", // >= 0.1 mm
  "#72b1f2", // >= 1 mm
  "#2d86e0", // >= 5 mm
  "#18a83a", // >= 10 mm
  "#39d43b", // >= 25 mm
  "#fff12f", // >= 50 mm
  "#ff9f24", // >= 100 mm
  "#e52d22"  // >= 200 mm
];

const state = {
  data: null,
  map: null,
  canvas: null,
  ctx: null,
  renderFrame: 0,
  opacity: 0.8,
  selectedDay: 0,
  selectedModels: new Set(["ECMWF HRES", "GEM", "GFS", "ICON"]),
  windEnabled: false,
  windSource: "blend",
  rainSet: "standard",
  aifsModel: "ECMWF AIFS",
  hoverRaf: 0,
  hoverPoint: null,
};

// Balanced rainfall rendering:
// - bilinear interpolation across the 0.5° display grid
// - stepped colour levels, so rainfall bands stay crisp
// - redraws at the current map viewport, so zooming does not scale a tiny raster
const RAIN_CELL_SAMPLING = "nearest";
const RAIN_RENDER_STEP = 0.25;
const WIND_GRID_SPACING_DEG = 2.0;
const WIND_MIN_KNOTS = 3;

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
  }

  const heading = document.querySelector("header h1");
  if (heading) heading.textContent = "24-Hour Rainfall Forecast";

  // Model run / valid-time information panel.
  if (!$("runDetails")) {
    const details = document.createElement("div");
    details.id = "runDetails";
    details.className = "run-details";
    details.innerHTML = `
      <div class="run-details-title">Model Run & Valid Time</div>
      <div id="runInfo">Loading…</div>
    `;
    const noteEl = aside.querySelector(".note");
    if (noteEl) aside.insertBefore(details, noteEl);
    else aside.appendChild(details);
  }

  const aifsModels = state.data?.aifs_models || [
    "ECMWF AIFS",
    "NOAA AIGFS",
    "ECMWF IFS",
    "Google WeatherNext 2"
  ];
  const aifsSelect = $("aifsModel");
  if (aifsSelect) {
    aifsSelect.innerHTML = "";
    aifsModels.forEach(model => {
      const opt = document.createElement("option");
      opt.value = model;
      opt.textContent = model;
      aifsSelect.appendChild(opt);
    });
    aifsSelect.value = state.aifsModel;
  }

  const note = aside.querySelector(".note");
  if (note) {
    note.innerHTML =
      "Rainfall is accumulated for each forecast day. " +
      "Rainfall display uses 0.5° grid bilinear sampling with stepped colours. " +
      "850 hPa wind barbs use 12 UTC representative wind.";
  }

  // Create optional wind controls if they are not already in HTML.
  if (!$('windControls')) {
    const box = document.createElement("div");
    box.id = "windControls";
    box.innerHTML = `
      <hr>
      <label class="wind-toggle">
        <input type="checkbox" id="wind850">
        850 hPa Wind Barbs
      </label>
      <label id="windSourceWrap" style="display:none">
        Wind source
        <select id="windSource">
          <option value="blend">Selected models — vector blend</option>
          <option value="ECMWF HRES">ECMWF HRES</option>
          <option value="GEM">GEM</option>
          <option value="GFS">GFS</option>
          <option value="ICON">ICON</option>
        </select>
      </label>
      <div class="wind-note">850 hPa ≈ 1.5 km. Barbs show wind from direction; speed in knots.</div>
    `;
    aside.appendChild(box);
  }
}

function updateWindControls() {
  const wind = $("wind850");
  const wrap = $("windSourceWrap");
  if (!wind || !wrap) return;
  if (state.rainSet === "aifs") {
    wind.checked = false;
    wind.disabled = true;
    state.windEnabled = false;
    wrap.style.display = "none";
  } else {
    wind.disabled = false;
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

  document.querySelectorAll("input[name=rainSet]").forEach(box => {
    box.addEventListener("change", () => {
      state.rainSet = box.value;
      const wrap = $("aifsModelWrap");
      if (wrap) wrap.style.display = state.rainSet === "aifs" ? "block" : "none";
      updateHeader();
      updateWindControls();
      drawRainfall();
    });
  });

  const aifsModel = $("aifsModel");
  if (aifsModel) {
    aifsModel.addEventListener("change", () => {
      state.aifsModel = aifsModel.value;
      updateHeader();
      drawRainfall();
    });
  }

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

  const wind = $("wind850");
  if (wind) {
    wind.addEventListener("change", () => {
      state.windEnabled = wind.checked;
      const wrap = $("windSourceWrap");
      if (wrap) wrap.style.display = wind.checked ? "block" : "none";
      drawRainfall();
    });
  }

  const windSource = $("windSource");
  if (windSource) {
    windSource.addEventListener("change", () => {
      state.windSource = windSource.value;
      drawRainfall();
    });
  }
}

function getRunTimestamp() {
  if (!state.data || !state.data.updated) return null;
  const updated = new Date(state.data.updated);
  if (Number.isNaN(updated.getTime())) return null;

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

  const sourceText = state.rainSet === "aifs"
    ? `AIFS Set — ${state.aifsModel}`
    : "Standard model blend";
  time.innerHTML = `<b>Model Run:</b> ${runText} &nbsp; | &nbsp; <b>Valid:</b> Day ${state.selectedDay + 1} &nbsp; ${validText} &nbsp; | &nbsp; <b>Source:</b> ${escapeHtml(sourceText)}`;

  const runInfo = $("runInfo");
  if (runInfo) {
    const horizons = state.data.model_horizons || {};
    const modelRows = (state.data.models || []).map(model => {
      const days = horizons[model];
      const available = Number.isFinite(days) ? `Day 1–${days}` : "Available";
      return `<div class="run-model-row"><span>${escapeHtml(model)}</span><b>${available}</b></div>`;
    }).join("");

    const aifsHorizons = state.data.aifs_model_horizons || {};
    const aifsRows = (state.data.aifs_models || []).map(model => {
      const days = aifsHorizons[model];
      const available = Number.isFinite(days) ? `Day 1–${days}` : "Available";
      return `<div class="run-model-row"><span>${escapeHtml(model)}</span><b>${available}</b></div>`;
    }).join("");

    runInfo.innerHTML =
      `<div class="run-main"><span>Run</span><b>${runText}</b></div>` +
      `<div class="run-main"><span>Valid period</span><b>Day ${state.selectedDay + 1}</b></div>` +
      `<div class="run-valid">${validText}</div>` +
      `<div class="run-subtitle">Standard model availability</div>` +
      modelRows +
      `<div class="run-subtitle">AIFS Set availability</div>` +
      aifsRows;
  }
}

function setupMap() {
  state.map = L.map("map", {
    zoomControl: true,
    preferCanvas: true,
    minZoom: 4,
    maxZoom: 10,
    worldCopyJump: false,
  });
  state.map.createPane("referenceBoundaries");
  state.map.getPane("referenceBoundaries").style.zIndex = 650;

  // Use the public OpenStreetMap raster tiles as the reliable basemap.
  // CARTO's raster endpoint can now return an API-key-required layer in
  // embedded pages, which was causing the visible "API KEY REQUIRED"
  // watermark. OSM keeps the India/Sri Lanka geography and place labels
  // while the rainfall canvas remains visually on top.
  const base = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "© OpenStreetMap contributors",
  }).addTo(state.map);

  // Keep India + Sri Lanka boundaries visible above the rainfall raster.
  // The state layer gives the reference-style internal India boundaries;
  // the Sri Lanka outline is deliberately drawn separately and strongly.
  addReferenceBoundaries();

  state.map.fitBounds(INDIA_BOUNDS, { padding: [8, 8] });
  state.map.on("zoomend moveend resize", scheduleRender);
  setupRainfallHover();

  const map = document.getElementById("map");
  if (map) {
    const ro = new ResizeObserver(() => {
      state.map.invalidateSize({ pan: false });
      scheduleRender();
    });
    ro.observe(map);
  }
}

let boundaryLayer = null;

async function addReferenceBoundaries() {
  try {
    const [indiaRes, sriRes] = await Promise.all([
      fetch("https://raw.githubusercontent.com/india-in-data/india-states-2019/master/india_states.geojson", { cache: "force-cache" }),
      fetch("https://raw.githubusercontent.com/glynnbird/countriesgeojson/master/sri%20lanka.geojson", { cache: "force-cache" })
    ]);
    if (!indiaRes.ok || !sriRes.ok) throw new Error("Boundary data unavailable");

    const india = await indiaRes.json();
    const sriLanka = await sriRes.json();

    boundaryLayer = L.layerGroup();

    L.geoJSON(india, {
      pane: "referenceBoundaries",
      interactive: false,
      style: {
        color: "#34383d",
        weight: 1.15,
        opacity: 0.92,
        fill: false
      }
    }).addTo(boundaryLayer);

    L.geoJSON(sriLanka, {
      pane: "referenceBoundaries",
      interactive: false,
      style: {
        color: "#20252a",
        weight: 1.8,
        opacity: 0.98,
        fill: false
      }
    }).addTo(boundaryLayer);

    boundaryLayer.addTo(state.map);
    boundaryLayer.bringToFront();
    scheduleRender();
  } catch (err) {
    console.warn("Reference boundary overlay unavailable:", err);
  }
}

function colorAt(value) {
  if (!Number.isFinite(value) || value < levels[0]) return null;

  // Hard bands: one colour per reference legend threshold.
  let idx = 0;
  while (idx < levels.length - 1 && value >= levels[idx + 1]) idx++;
  return colors[Math.min(idx, colors.length - 1)];
}

function buildSourceGrid() {
  const grid = state.data.grid || [];
  const cols = new Set();
  const map = new Map();
  grid.forEach(p => {
    cols.add(p.lon);
    map.set(`${p.lat}|${p.lon}`, p);
  });
  const lats = [...new Set(grid.map(p => p.lat))].sort((a,b) => a-b);
  const lons = [...cols].sort((a,b) => a-b);
  return { lats, lons, map, points: grid };
}

function nearestPoint(grid, lat, lon) {
  const { lats, lons, map } = grid;
  if (!lats.length || !lons.length) return null;

  // The generated display grid is regular 0.5°, so calculate the
  // nearest index directly instead of scanning the entire grid.
  const latStep = lats.length > 1 ? (lats[1] - lats[0]) : 0.5;
  const lonStep = lons.length > 1 ? (lons[1] - lons[0]) : 0.5;
  const latIndex = Math.max(0, Math.min(lats.length - 1, Math.round((lat - lats[0]) / latStep)));
  const lonIndex = Math.max(0, Math.min(lons.length - 1, Math.round((lon - lons[0]) / lonStep)));
  return map.get(`${lats[latIndex]}|${lons[lonIndex]}`) || null;
}

function bracketPoint(grid, lat, lon) {
  const { lats, lons, map } = grid;
  if (lats.length < 2 || lons.length < 2) return null;

  const latStep = lats[1] - lats[0];
  const lonStep = lons[1] - lons[0];
  const latPos = (lat - lats[0]) / latStep;
  const lonPos = (lon - lons[0]) / lonStep;

  let i = Math.floor(latPos);
  let j = Math.floor(lonPos);
  i = Math.max(0, Math.min(lats.length - 2, i));
  j = Math.max(0, Math.min(lons.length - 2, j));

  const fy = Math.max(0, Math.min(1, latPos - i));
  const fx = Math.max(0, Math.min(1, lonPos - j));

  return {
    p00: map.get(`${lats[i]}|${lons[j]}`),
    p01: map.get(`${lats[i]}|${lons[j + 1]}`),
    p10: map.get(`${lats[i + 1]}|${lons[j]}`),
    p11: map.get(`${lats[i + 1]}|${lons[j + 1]}`),
    fx, fy,
  };
}

function bilinearRain(grid, lat, lon, model, dayIndex) {
  const b = bracketPoint(grid, lat, lon);
  if (!b) return null;

  const vals = [
    b.p00?.models?.[model]?.rain?.[dayIndex],
    b.p01?.models?.[model]?.rain?.[dayIndex],
    b.p10?.models?.[model]?.rain?.[dayIndex],
    b.p11?.models?.[model]?.rain?.[dayIndex],
  ];

  if (vals.every(Number.isFinite)) {
    const top = vals[0] * (1 - b.fx) + vals[1] * b.fx;
    const bottom = vals[2] * (1 - b.fx) + vals[3] * b.fx;
    return top * (1 - b.fy) + bottom * b.fy;
  }

  // Graceful fallback for model-horizon edges or missing cells.
  const nearest = nearestPoint(grid, lat, lon);
  const v = nearest?.models?.[model]?.rain?.[dayIndex];
  return Number.isFinite(v) ? v : null;
}

function blendedRain(grid, lat, lon, dayIndex, models) {
  const vals = models
    .map(model => bilinearRain(grid, lat, lon, model, dayIndex))
    .filter(v => Number.isFinite(v));
  if (!vals.length) return null;
  return vals.reduce((a,b) => a+b, 0) / vals.length;
}

function bilinearAifsRain(grid, lat, lon, model, dayIndex) {
  const b = bracketPoint(grid, lat, lon);
  if (!b) return null;
  const vals = [
    b.p00?.aifs?.[model]?.[dayIndex],
    b.p01?.aifs?.[model]?.[dayIndex],
    b.p10?.aifs?.[model]?.[dayIndex],
    b.p11?.aifs?.[model]?.[dayIndex],
  ];
  if (vals.every(Number.isFinite)) {
    const top = vals[0] * (1 - b.fx) + vals[1] * b.fx;
    const bottom = vals[2] * (1 - b.fx) + vals[3] * b.fx;
    return top * (1 - b.fy) + bottom * b.fy;
  }
  const nearest = nearestPoint(grid, lat, lon);
  const v = nearest?.aifs?.[model]?.[dayIndex];
  return Number.isFinite(v) ? v : null;
}

function nearestModelRain(grid, lat, lon, model, dayIndex) {
  const point = nearestPoint(grid, lat, lon);
  const value = point?.models?.[model]?.rain?.[dayIndex];
  return Number.isFinite(value) ? value : null;
}

function nearestBlendedRain(grid, lat, lon, dayIndex, models) {
  const vals = models
    .map(model => nearestModelRain(grid, lat, lon, model, dayIndex))
    .filter(v => Number.isFinite(v));
  if (!vals.length) return null;
  return vals.reduce((a,b) => a + b, 0) / vals.length;
}

function nearestAifsRain(grid, lat, lon, model, dayIndex) {
  const point = nearestPoint(grid, lat, lon);
  const value = point?.aifs?.[model]?.[dayIndex];
  return Number.isFinite(value) ? value : null;
}

function rainValueAt(grid, lat, lon, dayIndex) {
  if (state.rainSet === "aifs") {
    return bilinearAifsRain(grid, lat, lon, state.aifsModel, dayIndex);
  }
  return blendedRain(grid, lat, lon, dayIndex, [...state.selectedModels]);
}

function windAtPoint(grid, lat, lon, dayIndex, source) {
  const point = nearestPoint(grid, lat, lon);
  if (!point || !point.models) return null;

  const models = source === "blend"
    ? [...state.selectedModels]
    : [source];

  let sumU = 0;
  let sumV = 0;
  let count = 0;

  models.forEach(model => {
    const entry = point.models[model];
    if (!entry || !Array.isArray(entry.wind850)) return;
    const uv = entry.wind850[dayIndex];
    if (!uv || !Number.isFinite(uv[0]) || !Number.isFinite(uv[1])) return;
    sumU += uv[0];
    sumV += uv[1];
    count++;
  });

  if (!count) return null;
  return {
    u: sumU / count,
    v: sumV / count,
  };
}


function setupRainfallHover() {
  const mapEl = $("map");
  if (!mapEl || state.map._rainHoverReady) return;
  state.map._rainHoverReady = true;

  let tooltip = $("rainTooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.id = "rainTooltip";
    tooltip.className = "rain-tooltip";
    tooltip.style.display = "none";
    mapEl.appendChild(tooltip);
  }

  mapEl.addEventListener("mousemove", (e) => {
    state.hoverPoint = { x: e.offsetX, y: e.offsetY };
    if (state.hoverRaf) return;
    state.hoverRaf = requestAnimationFrame(() => {
      state.hoverRaf = 0;
      updateRainfallHover();
    });
  });

  mapEl.addEventListener("mouseleave", () => {
    state.hoverPoint = null;
    tooltip.style.display = "none";
  });
}

function updateRainfallHover() {
  const tooltip = $("rainTooltip");
  if (!tooltip || !state.hoverPoint || !state.data || !state.map) return;

  const { x, y } = state.hoverPoint;
  const geo = state.map.containerPointToLatLng([x, y]);
  const lat = geo.lat;
  const lon = geo.lng;

  if (
    lat < state.data.domain.lat_min || lat > state.data.domain.lat_max ||
    lon < state.data.domain.lon_min || lon > state.data.domain.lon_max
  ) {
    tooltip.style.display = "none";
    return;
  }

  const grid = buildSourceGrid();
  if (state.rainSet === "aifs") {
    const value = bilinearAifsRain(grid, lat, lon, state.aifsModel, state.selectedDay);
    if (!Number.isFinite(value)) { tooltip.style.display = "none"; return; }
    tooltip.innerHTML =
      `<div class="rain-tooltip-title">${escapeHtml(state.aifsModel)} — Day ${state.selectedDay + 1}</div>` +
      `<div class="rain-tooltip-main"><b>${value.toFixed(1)} mm</b><span>AIFS Set</span></div>` +
      `<small>${lat.toFixed(2)}°N, ${lon.toFixed(2)}°E</small>`;
  } else {
    const models = [...state.selectedModels];
    if (!models.length) { tooltip.style.display = "none"; return; }
    const values = models
      .map(model => ({ model, value: bilinearRain(grid, lat, lon, model, state.selectedDay) }))
      .filter(item => Number.isFinite(item.value));
    if (!values.length) { tooltip.style.display = "none"; return; }
    const blend = values.reduce((sum, item) => sum + item.value, 0) / values.length;
    const modelLines = values.map(item =>
      `<div><span>${escapeHtml(item.model)}</span><b>${item.value.toFixed(1)} mm</b></div>`
    ).join("");
    tooltip.innerHTML =
      `<div class="rain-tooltip-title">Rainfall — Day ${state.selectedDay + 1}</div>` +
      `<div class="rain-tooltip-main"><b>${blend.toFixed(1)} mm</b><span>selected-model blend</span></div>` +
      modelLines +
      `<small>${lat.toFixed(2)}°N, ${lon.toFixed(2)}°E</small>`;
  }

  const mapWidth = mapElWidth();
  const mapHeight = mapElHeight();
  const tw = tooltip.offsetWidth || 190;
  const th = tooltip.offsetHeight || 110;
  const gap = 14;
  let left = x + gap;
  let top = y + gap;
  if (left + tw > mapWidth - 6) left = x - tw - gap;
  if (top + th > mapHeight - 6) top = y - th - gap;
  left = Math.max(6, Math.min(left, mapWidth - tw - 6));
  top = Math.max(6, Math.min(top, mapHeight - th - 6));

  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
  tooltip.style.display = "block";
}

function mapElWidth() {
  const el = $("map");
  return el ? el.clientWidth : 0;
}

function mapElHeight() {
  const el = $("map");
  return el ? el.clientHeight : 0;
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[ch]));
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
  if (state.rainSet === "standard" && !models.length) return;

  const source = buildSourceGrid();
  const latStep = Number(source.latStep) || 0.5;
  const lonStep = Number(source.lonStep) || 0.5;
  const halfLat = latStep / 2;
  const halfLon = lonStep / 2;

  // Keep the original 0.5° data grid, but subdivide each source cell into
  // 0.25° display cells. Values at the smaller-cell centres are bilinearly
  // interpolated from the model grid, then converted to hard colour bands.
  // This gives much smaller rainfall pixels without blurring the map.
  const displayStep = Math.min(RAIN_RENDER_STEP, latStep, lonStep);
  const subRows = Math.max(1, Math.round(latStep / displayStep));
  const subCols = Math.max(1, Math.round(lonStep / displayStep));
  const cellLat = latStep / subRows;
  const cellLon = lonStep / subCols;
  const subHalfLat = cellLat / 2;
  const subHalfLon = cellLon / 2;

  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(1, state.opacity));
  ctx.imageSmoothingEnabled = false;

  source.points.forEach(point => {
    const lat = Number(point.lat);
    const lon = Number(point.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

    for (let r = 0; r < subRows; r++) {
      const centerLat = lat + halfLat - (r + 0.5) * cellLat;
      const northLat = centerLat + subHalfLat;
      const southLat = centerLat - subHalfLat;

      for (let c = 0; c < subCols; c++) {
        const centerLon = lon - halfLon + (c + 0.5) * cellLon;
        const westLon = centerLon - subHalfLon;
        const eastLon = centerLon + subHalfLon;

        const value = state.rainSet === "aifs"
          ? bilinearAifsRain(source, centerLat, centerLon, state.aifsModel, state.selectedDay)
          : blendedRain(source, centerLat, centerLon, state.selectedDay, models);
        const color = colorAt(value);
        if (!color) continue;

        const nw = state.map.latLngToContainerPoint([northLat, westLon]);
        const ne = state.map.latLngToContainerPoint([northLat, eastLon]);
        const se = state.map.latLngToContainerPoint([southLat, eastLon]);
        const sw = state.map.latLngToContainerPoint([southLat, westLon]);

        if (Math.max(nw.x, ne.x, se.x, sw.x) < -2 || Math.min(nw.x, ne.x, se.x, sw.x) > w + 2 ||
            Math.max(nw.y, ne.y, se.y, sw.y) < -2 || Math.min(nw.y, ne.y, se.y, sw.y) > h + 2) continue;

        ctx.fillStyle = color;
        ctx.fillRect(
          Math.floor(Math.min(nw.x, sw.x)),
          Math.floor(Math.min(nw.y, ne.y)),
          Math.ceil(Math.abs(ne.x - nw.x)),
          Math.ceil(Math.abs(sw.y - nw.y))
        );
      }
    }
  });

  ctx.restore();

  if (state.windEnabled) {
    drawWindBarbs(ctx, source, w, h);
  }
}

function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [
    parseInt(h.slice(0,2),16),
    parseInt(h.slice(2,4),16),
    parseInt(h.slice(4,6),16)
  ];
}

function drawWindBarbs(ctx, grid, w, h) {
  const bounds = state.data.domain;
  const spacing = WIND_GRID_SPACING_DEG;

  // Start on clean degree multiples so the barb field is stable while zooming.
  const latStart = Math.ceil(bounds.lat_min / spacing) * spacing;
  const lonStart = Math.ceil(bounds.lon_min / spacing) * spacing;

  ctx.save();
  ctx.strokeStyle = "#111";
  ctx.fillStyle = "#111";
  ctx.lineWidth = 1.3;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.font = "10px Arial";

  for (let lat = latStart; lat <= bounds.lat_max; lat += spacing) {
    for (let lon = lonStart; lon <= bounds.lon_max; lon += spacing) {
      const point = state.map.latLngToContainerPoint([lat, lon]);
      if (point.x < -30 || point.x > w + 30 || point.y < -30 || point.y > h + 30) continue;

      const wind = windAtPoint(grid, lat, lon, state.selectedDay, state.windSource);
      if (!wind) continue;

      const knots = Math.sqrt(wind.u * wind.u + wind.v * wind.v) * 1.943844;
      if (!Number.isFinite(knots) || knots < WIND_MIN_KNOTS) continue;

      drawOneBarb(ctx, point.x, point.y, wind.u, wind.v, knots);
    }
  }

  ctx.restore();
}

function drawOneBarb(ctx, x, y, u, v, knots) {
  // Meteorological wind direction is FROM. The U/V vector is TOWARD.
  // Screen coordinates: east = +x, north = -y.
  const mag = Math.sqrt(u*u + v*v) || 1;
  const fromX = -u / mag;
  const fromY = v / mag;

  const length = 23;
  const ex = x + fromX * length;
  const ey = y + fromY * length;

  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(ex, ey);
  ctx.stroke();

  let remaining = Math.round(knots / 5) * 5;
  let pos = 0;
  const barbSpacing = 5.5;
  const feather = 9;
  const angle = Math.atan2(fromY, fromX);
  const backX = -Math.cos(angle);
  const backY = -Math.sin(angle);
  const sideX = -Math.sin(angle);
  const sideY = Math.cos(angle);

  // Draw 50 kt pennants first, then 10 kt, then 5 kt.
  const fifties = Math.floor(remaining / 50);
  remaining -= fifties * 50;
  const tens = Math.floor(remaining / 10);
  remaining -= tens * 10;
  const fives = Math.floor(remaining / 5);

  for (let i = 0; i < fifties; i++) {
    const bx = ex + backX * pos;
    const by = ey + backY * pos;
    const tipX = bx + backX * 10;
    const tipY = by + backY * 10;
    const outerX = bx + sideX * 7;
    const outerY = by + sideY * 7;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.lineTo(tipX, tipY);
    ctx.lineTo(outerX, outerY);
    ctx.closePath();
    ctx.fill();
    pos += barbSpacing;
  }

  for (let i = 0; i < tens; i++) {
    const bx = ex + backX * pos;
    const by = ey + backY * pos;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.lineTo(bx + sideX * feather + backX * 7, by + sideY * feather + backY * 7);
    ctx.stroke();
    pos += barbSpacing;
  }

  if (fives) {
    const bx = ex + backX * pos;
    const by = ey + backY * pos;
    ctx.beginPath();
    ctx.moveTo(bx, by);
    ctx.lineTo(bx + sideX * feather * 0.65 + backX * 7, by + sideY * feather * 0.65 + backY * 7);
    ctx.stroke();
  }
}

function scheduleRender() {
  cancelAnimationFrame(state.renderFrame);
  state.renderFrame = requestAnimationFrame(drawRainfall);
}

function buildLegend() {
  const legend = $("legend");
  if (!legend) return;
  const sourceText = state.rainSet === "aifs"
    ? `AIFS Set — ${escapeHtml(state.aifsModel)}`
    : "Standard model blend";
  legend.innerHTML =
    `<div class="legend-title">6-hour rainfall (mm) — ${sourceText}</div>` +
    `<div class="legend-scale">` +
    levels.map((v,i) =>
      `<span class="legend-item"><i style="background:${colors[Math.min(i, colors.length - 1)]}"></i>${v}</span>`
    ).join("") +
    `</div>`;
}

async function loadData() {
  const res = await fetch(`data.json?v=${Date.now()}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`data.json HTTP ${res.status}`);
  state.data = await res.json();
  ensureUi();
  setupControls();
  getSelectedModels();
  updateWindControls();
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
