// ============================================================
// MASRAINMAN — SMOOTH METEOROLOGICAL RAINFALL MAP
// Version 2 — fine interpolation + smoothing
// ============================================================

const levels = [
  0.1, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40,
  50, 60, 70, 80, 90, 100, 125, 150, 175, 200,
  250, 300, 400, 500, 600, 800
];

const colors = [
  "#f2f2f2",
  "#c7dcff",
  "#8ebfff",
  "#4aa3ff",
  "#007cff",
  "#004b99",
  "#1b5e20",
  "#00c853",
  "#64dd17",
  "#c6ff00",
  "#ffd600",
  "#ffab00",
  "#ff6d00",
  "#ff8f00",
  "#ff5c8a",
  "#ff1f5b",
  "#ff0033",
  "#d50000",
  "#7b1fa2",
  "#6a00ff",
  "#c000ff",
  "#d580ff",
  "#f0ccff",
  "#d9d9d9",
  "#a6a6a6",
  "#7a7a7a",
  "#4d4d4d",
  "#333333"
];


// ============================================================
// MAP
// ============================================================

const map = L.map("map", {
  preferCanvas: true,
  zoomControl: true
});

map.fitBounds(
  [
    [5, 65],
    [38, 100]
  ],
  {
    padding: [10, 10]
  }
);


// ============================================================
// BASE MAP
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

let lats = [];
let lons = [];

let gridLookup = new Map();


// ============================================================
// COLOR CONVERSION
// ============================================================

function hexToRgb(hex) {

  hex = hex.replace("#", "");

  return {
    r: parseInt(hex.substring(0, 2), 16),
    g: parseInt(hex.substring(2, 4), 16),
    b: parseInt(hex.substring(4, 6), 16)
  };
}

const rgbColors = colors.map(hexToRgb);


// ============================================================
// SMOOTH COLOR
// ============================================================

function rainfallColor(value) {

  if (
    !Number.isFinite(value) ||
    value < levels[0]
  ) {
    return null;
  }


  if (
    value >= levels[levels.length - 1]
  ) {

    const c =
      rgbColors[rgbColors.length - 1];

    return {
      r: c.r,
      g: c.g,
      b: c.b
    };
  }


  let i = 0;

  while (
    i < levels.length - 1 &&
    value >= levels[i + 1]
  ) {
    i++;
  }


  const low =
    levels[i];

  const high =
    levels[i + 1];


  let t =
    (value - low) /
    (high - low);


  t =
    Math.max(
      0,
      Math.min(1, t)
    );


  // Smooth-step interpolation.
  // This avoids harsh transitions.

  t =
    t * t * (3 - 2 * t);


  const a =
    rgbColors[i];

  const b =
    rgbColors[i + 1];


  return {
    r: Math.round(
      a.r + (b.r - a.r) * t
    ),

    g: Math.round(
      a.g + (b.g - a.g) * t
    ),

    b: Math.round(
      a.b + (b.b - a.b) * t
    )
  };
}


// ============================================================
// SELECTED MODELS
// ============================================================

function getSelectedModels() {

  return [
    ...document.querySelectorAll(
      ".model:checked"
    )
  ].map(
    el => el.value
  );
}


// ============================================================
// GET POINT VALUE
// ============================================================

function pointValue(point, period, models) {

  if (!point) {
    return null;
  }


  const values = [];


  models.forEach(model => {

    const arr =
      point.models &&
      point.models[model];


    if (
      arr &&
      Number.isFinite(
        Number(arr[period])
      )
    ) {

      values.push(
        Number(arr[period])
      );

    }

  });


  if (!values.length) {
    return null;
  }


  return (
    values.reduce(
      (a, b) => a + b,
      0
    ) / values.length
  );
}


// ============================================================
// FIND GRID INDEX
// ============================================================

function lowerIndex(array, value) {

  if (
    value <= array[0]
  ) {
    return 0;
  }


  if (
    value >= array[array.length - 1]
  ) {
    return array.length - 2;
  }


  let low = 0;
  let high = array.length - 1;


  while (
    low <= high
  ) {

    const mid =
      Math.floor(
        (low + high) / 2
      );


    if (
      array[mid] <= value
    ) {

      low =
        mid + 1;

    } else {

      high =
        mid - 1;

    }
  }


  return Math.max(
    0,
    Math.min(
      array.length - 2,
      high
    )
  );
}


// ============================================================
// GRID POINT LOOKUP
// ============================================================

function getPoint(latIndex, lonIndex) {

  if (
    latIndex < 0 ||
    lonIndex < 0 ||
    latIndex >= lats.length ||
    lonIndex >= lons.length
  ) {
    return null;
  }


  const key =
    lats[latIndex].toFixed(4) +
    "," +
    lons[lonIndex].toFixed(4);


  return gridLookup.get(key) || null;
}


// ============================================================
// BILINEAR INTERPOLATION
// ============================================================

function interpolateRainfall(
  lat,
  lon,
  period,
  models
) {

  if (
    lat < lats[0] ||
    lat > lats[lats.length - 1] ||
    lon < lons[0] ||
    lon > lons[lons.length - 1]
  ) {
    return null;
  }


  const yi =
    lowerIndex(
      lats,
      lat
    );

  const xi =
    lowerIndex(
      lons,
      lon
    );


  const y0 =
    lats[yi];

  const y1 =
    lats[yi + 1];

  const x0 =
    lons[xi];

  const x1 =
    lons[xi + 1];


  const fy =
    y1 === y0
      ? 0
      : (lat - y0) /
        (y1 - y0);


  const fx =
    x1 === x0
      ? 0
      : (lon - x0) /
        (x1 - x0);


  const q11 =
    pointValue(
      getPoint(yi, xi),
      period,
      models
    );

  const q21 =
    pointValue(
      getPoint(yi, xi + 1),
      period,
      models
    );

  const q12 =
    pointValue(
      getPoint(yi + 1, xi),
      period,
      models
    );

  const q22 =
    pointValue(
      getPoint(yi + 1, xi + 1),
      period,
      models
    );


  const available = [
    q11,
    q21,
    q12,
    q22
  ].filter(
    v => v !== null
  );


  if (!available.length) {
    return null;
  }


  // If one or more neighbours are missing,
  // use the available average.

  if (
    q11 === null ||
    q21 === null ||
    q12 === null ||
    q22 === null
  ) {

    return (
      available.reduce(
        (a, b) => a + b,
        0
      ) /
      available.length
    );
  }


  // Smooth-step geographic interpolation

  let sx =
    fx * fx * (3 - 2 * fx);

  let sy =
    fy * fy * (3 - 2 * fy);


  const top =
    q11 * (1 - sx) +
    q21 * sx;


  const bottom =
    q12 * (1 - sx) +
    q22 * sx;


  return (
    top * (1 - sy) +
    bottom * sy
  );
}


// ============================================================
// SMOOTH RAIN CANVAS
// ============================================================

const SmoothRainLayer =
  L.Layer.extend({

    onAdd: function(map) {

      this._map = map;


      this._canvas =
        document.createElement(
          "canvas"
        );


      this._canvas.className =
        "masrainman-smooth-rain";


      this._canvas.style.position =
        "absolute";


      this._canvas.style.left =
        "0px";


      this._canvas.style.top =
        "0px";


      this._canvas.style.pointerEvents =
        "none";


      this._canvas.style.zIndex =
        "350";


      map.getPanes()
        .overlayPane
        .appendChild(
          this._canvas
        );


      this._redraw =
        () => this.redraw();


      map.on(
        "moveend zoomend resize",
        this._redraw
      );


      this.redraw();
    },


    onRemove: function(map) {

      map.off(
        "moveend zoomend resize",
        this._redraw
      );


      if (this._canvas) {
        this._canvas.remove();
      }
    },


    redraw: function() {

      if (
        !data ||
        !lats.length ||
        !lons.length
      ) {
        return;
      }


      const size =
        this._map.getSize();


      const width =
        Math.max(
          1,
          Math.floor(size.x)
        );


      const height =
        Math.max(
          1,
          Math.floor(size.y)
        );


      const canvas =
        this._canvas;


      canvas.width =
        width;

      canvas.height =
        height;


      canvas.style.width =
        width + "px";

      canvas.style.height =
        height + "px";


      const ctx =
        canvas.getContext(
          "2d"
        );


      const period =
        Number(
          document.querySelector(
            "#period"
          ).value
        );


      const opacity =
        Number(
          document.querySelector(
            "#opacity"
          ).value
        );


      const models =
        getSelectedModels();


      if (!models.length) {

        ctx.clearRect(
          0,
          0,
          width,
          height
        );

        return;
      }


      // ------------------------------------------------------
      // FIRST PASS
      // ------------------------------------------------------

      // Render into an off-screen canvas.
      // This is then blurred gently before being placed
      // on the map.

      const raw =
        document.createElement(
          "canvas"
        );


      raw.width =
        width;

      raw.height =
        height;


      const rawCtx =
        raw.getContext(
          "2d"
        );


      const image =
        rawCtx.createImageData(
          width,
          height
        );


      const pixels =
        image.data;


      // Sampling every 2 pixels gives good performance
      // while still producing a very fine rainfall field.

      const sample = 2;


      for (
        let y = 0;
        y < height;
        y += sample
      ) {


        for (
          let x = 0;
          x < width;
          x += sample
        ) {


          const latlng =
            this._map.containerPointToLatLng(
              L.point(x, y)
            );


          const value =
            interpolateRainfall(
              latlng.lat,
              latlng.lng,
              period,
              models
            );


          if (
            value === null ||
            value < levels[0]
          ) {
            continue;
          }


          const c =
            rainfallColor(
              value
            );


          if (!c) {
            continue;
          }


          const alpha =
            Math.round(
              255 * opacity
            );


          // Write a tiny 2x2 area

          for (
            let yy = y;
            yy < Math.min(
              y + sample,
              height
            );
            yy++
          ) {

            for (
              let xx = x;
              xx < Math.min(
                x + sample,
                width
              );
              xx++
            ) {

              const index =
                (
                  yy * width +
                  xx
                ) * 4;


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


      rawCtx.putImageData(
        image,
        0,
        0
      );


      // ------------------------------------------------------
      // SECOND PASS — METEOROLOGICAL SMOOTHING
      // ------------------------------------------------------

      ctx.clearRect(
        0,
        0,
        width,
        height
      );


      ctx.save();


      // Gentle Gaussian-style canvas blur.
      // This removes the remaining grid impression.

      ctx.filter =
        "blur(7px)";


      ctx.globalAlpha =
        0.96;


      ctx.drawImage(
        raw,
        0,
        0
      );


      ctx.restore();


      // ------------------------------------------------------
      // THIRD PASS — VERY LIGHT CORE DETAIL
      // ------------------------------------------------------

      // Add a slightly sharper low-alpha layer so that
      // intense rainfall cores remain clearly visible.

      ctx.save();


      ctx.globalAlpha =
        0.20;


      ctx.filter =
        "blur(2px)";


      ctx.drawImage(
        raw,
        0,
        0
      );


      ctx.restore();
    }
  });


// ============================================================
// DRAW MAP
// ============================================================

function drawRainfall() {

  if (
    !data ||
    !gridLookup.size
  ) {
    return;
  }


  if (rainLayer) {

    map.removeLayer(
      rainLayer
    );
  }


  rainLayer =
    new SmoothRainLayer();


  rainLayer.addTo(
    map
  );
}


// ============================================================
// LEGEND
// ============================================================

function buildLegend() {

  const el =
    document.querySelector(
      "#legend"
    );


  if (!el) {
    return;
  }


  let html =
    "<b>Rainfall (mm / 6h)</b><br>";


  levels.forEach(
    (value, i) => {

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
    }
  );


  el.innerHTML =
    html;
}


// ============================================================
// PREPARE GRID
// ============================================================

function prepareGrid() {

  gridLookup =
    new Map();


  const grid =
    data.grid || [];


  if (!grid.length) {
    return;
  }


  // Build exact unique coordinate arrays

  const latSet =
    new Set();


  const lonSet =
    new Set();


  grid.forEach(
    point => {

      const lat =
        Number(point.lat);

      const lon =
        Number(point.lon);


      if (
        Number.isFinite(lat) &&
        Number.isFinite(lon)
      ) {

        latSet.add(
          lat
        );

        lonSet.add(
          lon
        );


        const key =
          lat.toFixed(4) +
          "," +
          lon.toFixed(4);


        gridLookup.set(
          key,
          point
        );
      }
    }
  );


  lats =
    [...latSet].sort(
      (a, b) => a - b
    );


  lons =
    [...lonSet].sort(
      (a, b) => a - b
    );
}


// ============================================================
// PERIOD CONTROL
// ============================================================

const periodControl =
  document.querySelector(
    "#period"
  );


if (periodControl) {

  periodControl.addEventListener(
    "change",
    drawRainfall
  );
}


// ============================================================
// MODEL CONTROLS
// ============================================================

document
  .querySelectorAll(
    ".model"
  )
  .forEach(
    checkbox => {

      checkbox.addEventListener(
        "change",
        drawRainfall
      );

    }
  );


// ============================================================
// OPACITY CONTROL
// ============================================================

const opacityControl =
  document.querySelector(
    "#opacity"
  );


if (opacityControl) {

  // Label-friendly default

  opacityControl.value =
    "0.60";


  opacityControl.addEventListener(
    "input",
    drawRainfall
  );
}


// ============================================================
// LOAD DATA
// ============================================================

fetch(
  "data.json?" +
  Date.now()
)

  .then(
    response => {

      if (!response.ok) {

        throw new Error(
          "Rainfall data could not be loaded"
        );

      }

      return response.json();
    }
  )


  .then(
    json => {

      data =
        json;


      const time =
        document.querySelector(
          "#time"
        );


      if (time) {

        time.textContent =
          data.updated ||
          "Latest update";
      }


      prepareGrid();


      buildLegend();


      drawRainfall();


      setTimeout(
        () => {

          map.invalidateSize();

          drawRainfall();

        },
        500
      );
    }
  )


  .catch(
    error => {

      console.error(
        error
      );


      const time =
        document.querySelector(
          "#time"
        );


      if (time) {

        time.textContent =
          "Rainfall data unavailable";
      }
    }
  );


// ============================================================
// WINDOW RESIZE
// ============================================================

window.addEventListener(
  "resize",
  () => {

    if (rainLayer) {
      rainLayer.redraw();
    }

  }
);
