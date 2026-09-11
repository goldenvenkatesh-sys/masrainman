// ============================================================
// MASRAINMAN — SMOOTH 6-HOURLY RAINFALL MAP
// Display-only upgrade
// Data source remains data.json
// ============================================================

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


// ============================================================
// MAP
// ============================================================

const map = L.map("map", {
  preferCanvas: true,
  zoomControl: true
});

const indiaBounds = [
  [5, 65],
  [38, 100]
];

map.fitBounds(indiaBounds, {
  padding: [10, 10]
});


// ============================================================
// BASEMAP
// ============================================================

L.tileLayer(
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  {
    maxZoom: 12,
    attribution: "© OpenStreetMap contributors"
  }
).addTo(map);


// ============================================================
// VARIABLES
// ============================================================

let data = null;
let rainLayer = null;


// ============================================================
// HEX → RGB
// ============================================================

function hexToRgb(hex) {
  hex = hex.replace("#", "");

  return {
    r: parseInt(hex.substring(0, 2), 16),
    g: parseInt(hex.substring(2, 4), 16),
    b: parseInt(hex.substring(4, 6), 16)
  };
}


// ============================================================
// PRE-CONVERT COLORS
// ============================================================

const rgbColors = colors.map(hexToRgb);


// ============================================================
// SMOOTH COLOR INTERPOLATION
// ============================================================

function getRainColor(value) {

  if (!Number.isFinite(value) || value < levels[0]) {
    return null;
  }

  if (value >= levels[levels.length - 1]) {
    const c = rgbColors[rgbColors.length - 1];

    return {
      r: c.r,
      g: c.g,
      b: c.b,
      a: 255
    };
  }

  let i = 0;

  while (
    i < levels.length - 1 &&
    value >= levels[i + 1]
  ) {
    i++;
  }

  const low = levels[i];
  const high = levels[i + 1];

  let t = (value - low) / (high - low);

  t = Math.max(0, Math.min(1, t));

  const c1 = rgbColors[i];
  const c2 = rgbColors[i + 1];

  return {
    r: Math.round(c1.r + (c2.r - c1.r) * t),
    g: Math.round(c1.g + (c2.g - c1.g) * t),
    b: Math.round(c1.b + (c2.b - c1.b) * t),
    a: 255
  };
}


// ============================================================
// GRID PREPARATION
// ============================================================

let gridMap = null;
let minLat = 0;
let minLon = 0;
let step = 0.5;
let maxLat = 0;
let maxLon = 0;


// ============================================================
// SELECTED MODEL RAINFALL
// ============================================================

function selectedModels() {

  return [
    ...document.querySelectorAll(".model:checked")
  ].map(el => el.value);
}


// ============================================================
// GET RAINFALL VALUE FROM SOURCE GRID
// ============================================================

function sourceValue(latIndex, lonIndex, period) {

  if (
    latIndex < 0 ||
    lonIndex < 0
  ) {
    return null;
  }

  const lat = minLat + latIndex * step;
  const lon = minLon + lonIndex * step;

  if (
    lat > maxLat + step * 0.01 ||
    lon > maxLon + step * 0.01
  ) {
    return null;
  }

  const key =
    lat.toFixed(4) + "," +
    lon.toFixed(4);

  const point = gridMap.get(key);

  if (!point) {
    return null;
  }

  const models = selectedModels();

  if (!models.length) {
    return null;
  }

  const values = [];

  models.forEach(model => {

    const arr =
      point.models &&
      point.models[model];

    if (
      arr &&
      Number.isFinite(Number(arr[period]))
    ) {
      values.push(Number(arr[period]));
    }

  });

  if (!values.length) {
    return null;
  }

  return (
    values.reduce((a, b) => a + b, 0) /
    values.length
  );
}


// ============================================================
// BILINEAR INTERPOLATION
// ============================================================

function interpolate(lat, lon, period) {

  const y =
    (lat - minLat) / step;

  const x =
    (lon - minLon) / step;

  const y0 = Math.floor(y);
  const x0 = Math.floor(x);

  const y1 = y0 + 1;
  const x1 = x0 + 1;

  const fy = y - y0;
  const fx = x - x0;

  const q11 =
    sourceValue(y0, x0, period);

  const q21 =
    sourceValue(y0, x1, period);

  const q12 =
    sourceValue(y1, x0, period);

  const q22 =
    sourceValue(y1, x1, period);


  // If all surrounding points are missing
  if (
    q11 === null &&
    q21 === null &&
    q12 === null &&
    q22 === null
  ) {
    return null;
  }


  // Replace missing neighbours with available values

  const values = [
    q11,
    q21,
    q12,
    q22
  ].filter(v => v !== null);


  if (q11 === null) {
    return values[0];
  }

  if (q21 === null) {
    return values[0];
  }

  if (q12 === null) {
    return values[0];
  }

  if (q22 === null) {
    return values[0];
  }


  // Bilinear interpolation

  const top =
    q11 * (1 - fx) +
    q21 * fx;

  const bottom =
    q12 * (1 - fx) +
    q22 * fx;

  return (
    top * (1 - fy) +
    bottom * fy
  );
}


// ============================================================
// CANVAS RAINFALL LAYER
// ============================================================

const SmoothRainLayer = L.Layer.extend({

  onAdd: function(map) {

    this._map = map;

    this._canvas =
      document.createElement("canvas");

    this._canvas.className =
      "masrainman-rain-canvas";

    this._canvas.style.position =
      "absolute";

    this._canvas.style.pointerEvents =
      "none";

    this._canvas.style.zIndex =
      "350";

    map.getPanes().overlayPane.appendChild(
      this._canvas
    );

    this._resizeHandler =
      () => this.redraw();

    map.on(
      "moveend zoomend resize",
      this._resizeHandler
    );

    this.redraw();
  },


  onRemove: function(map) {

    map.off(
      "moveend zoomend resize",
      this._resizeHandler
    );

    if (this._canvas) {
      this._canvas.remove();
    }
  },


  redraw: function() {

    if (!data || !gridMap) {
      return;
    }

    const size =
      this._map.getSize();

    const bounds =
      this._map.getBounds();

    const canvas =
      this._canvas;

    // Render at moderate resolution.
    // This keeps the browser fast while
    // producing a smooth meteorological field.

    const scale = 1;

    canvas.width =
      Math.max(1, Math.floor(size.x * scale));

    canvas.height =
      Math.max(1, Math.floor(size.y * scale));

    canvas.style.width =
      size.x + "px";

    canvas.style.height =
      size.y + "px";

    canvas.style.left = "0px";
    canvas.style.top = "0px";


    const ctx =
      canvas.getContext("2d");

    const image =
      ctx.createImageData(
        canvas.width,
        canvas.height
      );

    const pixels =
      image.data;

    const period =
      Number(
        document.querySelector("#period").value
      );

    const opacity =
      Number(
        document.querySelector("#opacity").value
      );


    // Geographic bounds

    const west =
      bounds.getWest();

    const east =
      bounds.getEast();

    const north =
      bounds.getNorth();

    const south =
      bounds.getSouth();


    // Pixel sampling step.
    // 2 means every second pixel is sampled
    // and then the canvas naturally displays
    // a very smooth field.

    const sampleStep = 2;


    for (
      let py = 0;
      py < canvas.height;
      py += sampleStep
    ) {

      const screenY =
        py / scale;

      const lat =
        this._map.containerPointToLatLng(
          L.point(0, screenY)
        ).lat;


      if (
        lat < south - 1 ||
        lat > north + 1
      ) {
        continue;
      }


      for (
        let px = 0;
        px < canvas.width;
        px += sampleStep
      ) {

        const screenX =
          px / scale;

        const latlng =
          this._map.containerPointToLatLng(
            L.point(screenX, screenY)
          );

        const lon =
          latlng.lng;


        if (
          lon < west - 1 ||
          lon > east + 1
        ) {
          continue;
        }


        const value =
          interpolate(
            lat,
            lon,
            period
          );


        if (
          value === null ||
          value < levels[0]
        ) {
          continue;
        }


        const c =
          getRainColor(value);

        if (!c) {
          continue;
        }


        const alpha =
          Math.round(255 * opacity);


        // Fill a small block.
        // The canvas interpolation plus neighbouring
        // sampling produces a smooth visual field.

        for (
          let yy = py;
          yy < Math.min(
            py + sampleStep,
            canvas.height
          );
          yy++
        ) {

          for (
            let xx = px;
            xx < Math.min(
              px + sampleStep,
              canvas.width
            );
            xx++
          ) {

            const index =
              (yy * canvas.width + xx) * 4;

            pixels[index] =
              c.r;

            pixels[index + 1] =
              c.g;

            pixels[index + 2] =
              c.b;

            pixels[index + 3] =
              alpha;
          }
        }
      }
    }


    ctx.putImageData(
      image,
      0,
      0
    );
  }
});


// ============================================================
// DRAW RAINFALL
// ============================================================

function draw() {

  if (!data || !gridMap) {
    return;
  }

  if (rainLayer) {
    map.removeLayer(rainLayer);
  }

  rainLayer =
    new SmoothRainLayer();

  rainLayer.addTo(map);
}


// ============================================================
// LEGEND
// ============================================================

function legend() {

  const el =
    document.querySelector("#legend");

  if (!el) {
    return;
  }


  let html =
    "<b>Rainfall (mm / 6h)</b><br>";


  levels.forEach((value, i) => {

    const next =
      levels[i + 1];

    const label =
      next !== undefined
        ? `${value}–${next}`
        : `≥${value}`;


    html +=
      `<span class="lg" ` +
      `style="background:${colors[i]}"></span>` +
      `${label}<br>`;
  });


  el.innerHTML = html;
}


// ============================================================
// PERIOD LABEL
// ============================================================

function updatePeriodLabel() {

  const select =
    document.querySelector("#period");

  if (!select || !data) {
    return;
  }

  const p =
    Number(select.value);

  if (
    data.periods &&
    data.periods[p]
  ) {
    select.options[p].text =
      data.periods[p];
  }
}


// ============================================================
// INITIALIZE GRID
// ============================================================

function prepareGrid() {

  gridMap = new Map();


  const grid =
    data.grid || [];


  if (!grid.length) {
    return;
  }


  // Use data domain when available

  if (data.domain) {

    minLat =
      Number(data.domain.lat_min);

    maxLat =
      Number(data.domain.lat_max);

    minLon =
      Number(data.domain.lon_min);

    maxLon =
      Number(data.domain.lon_max);

  } else {

    minLat =
      Math.min(...grid.map(g => Number(g.lat)));

    maxLat =
      Math.max(...grid.map(g => Number(g.lat)));

    minLon =
      Math.min(...grid.map(g => Number(g.lon)));

    maxLon =
      Math.max(...grid.map(g => Number(g.lon)));
  }


  step =
    Number(data.step || 0.5);


  grid.forEach(g => {

    const lat =
      Number(g.lat);

    const lon =
      Number(g.lon);


    if (
      !Number.isFinite(lat) ||
      !Number.isFinite(lon)
    ) {
      return;
    }


    const key =
      lat.toFixed(4) +
      "," +
      lon.toFixed(4);


    gridMap.set(key, g);
  });
}


// ============================================================
// CONTROL EVENTS
// ============================================================

document
  .querySelectorAll(".model")
  .forEach(el => {

    el.addEventListener(
      "change",
      draw
    );

  });


const periodControl =
  document.querySelector("#period");

if (periodControl) {

  periodControl.addEventListener(
    "change",
    draw
  );

}


const opacityControl =
  document.querySelector("#opacity");

if (opacityControl) {

  // Better default for map labels

  opacityControl.value = "0.65";


  opacityControl.addEventListener(
    "input",
    draw
  );
}


// ============================================================
// LOAD DATA
// ============================================================

fetch(
  "data.json?" +
  Date.now()
)

  .then(response => {

    if (!response.ok) {
      throw new Error(
        "Unable to load rainfall data"
      );
    }

    return response.json();
  })


  .then(json => {

    data = json;


    const time =
      document.querySelector("#time");


    if (time) {

      time.textContent =
        data.updated ||
        "Latest update";
    }


    prepareGrid();

    updatePeriodLabel();

    legend();

    draw();


    // First render after layout settles

    setTimeout(
      () => {

        map.invalidateSize();

        draw();

      },
      300
    );

  })


  .catch(error => {

    console.error(error);


    const time =
      document.querySelector("#time");


    if (time) {

      time.textContent =
        "Rainfall data unavailable";
    }

  });


// ============================================================
// REDRAW WHEN BROWSER WINDOW CHANGES
// ============================================================

window.addEventListener(
  "resize",
  () => {

    if (rainLayer) {
      rainLayer.redraw();
    }

  }
);
