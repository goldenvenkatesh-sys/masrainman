/* MasRainman — Day 1..Day 12 rainfall + optional 850 hPa wind barbs */

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
  renderFrame: 0,
  opacity: 0.8,
  selectedDay: 0,
  selectedModels: new Set(["ECMWF HRES", "GEM", "GFS", "ICON"]),
  windEnabled: false,
  windSource: "blend",
};

// Balanced rainfall rendering:
// - bilinear interpolation across the 0.5° display grid
// - stepped colour levels, so rainfall bands stay crisp
// - redraws at the current map viewport, so zooming does not scale a tiny raster
const RAIN_CELL_SAMPLING = "bilinear";
const RAIN_RENDER_STEP = 1;
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
  let i = 0;
  while (i < levels.length - 1 && value >= levels[i + 1]) i++;
  return colors[i];
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
  return { lats, lons, map };
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
  const step = RAIN_RENDER_STEP;
  const image = ctx.createImageData(w, h);

  for (let y = 0; y < h; y += step) {
    for (let x = 0; x < w; x += step) {
      const geo = state.map.containerPointToLatLng([x, y]);
      const lat = geo.lat;
      const lng = geo.lng;
      if (
        lat < state.data.domain.lat_min ||
        lat > state.data.domain.lat_max ||
        lng < state.data.domain.lon_min ||
        lng > state.data.domain.lon_max
      ) continue;

      const value = blendedRain(source, lat, lng, state.selectedDay, models);
      const color = colorAt(value);
      if (!color) continue;

      const [r,g,b] = hexToRgb(color);
      for (let yy = y; yy < Math.min(y + step, h); yy++) {
        for (let xx = x; xx < Math.min(x + step, w); xx++) {
          const idx = (yy * w + xx) * 4;
          image.data[idx] = r;
          image.data[idx+1] = g;
          image.data[idx+2] = b;
          image.data[idx+3] = 235;
        }
      }
    }
  }

  // Put the rainfall field directly onto the current-size canvas.
  // The field is interpolated from the 0.5° model grid, but colours remain
  // stepped. This avoids large square pixels while keeping a crisp model-grid look.
  ctx.imageSmoothingEnabled = false;
  ctx.putImageData(image, 0, 0);

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
  legend.innerHTML =
    `<div class="legend-title">24-hour rainfall (mm) — stepped display</div>` +
    levels.map((v,i) =>
      `<span class="legend-item"><i style="background:${colors[i]}"></i>${v}</span>`
    ).join("");
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
