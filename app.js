// ============================================================
// MASRAINMAN — FAST + MODERATELY SMOOTH 6-HOURLY RAINFALL
// V4
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

let lats = [];
let lons = [];

let gridLookup = new Map();


// ============================================================
// RGB
// ============================================================

function hexToRgb(hex) {

  hex = hex.replace("#", "");

  return {
    r: parseInt(hex.substring(0, 2), 16),
    g: parseInt(hex.substring(2, 4), 16),
    b: parseInt(hex.substring(4, 6), 16)
  };
}

const rgbColors =
  colors.map(hexToRgb);


// ============================================================
// MODEL SELECTION
// ============================================================

function getSelectedModels() {

  return [
    ...document.querySelectorAll(".model:checked")
  ].map(
    el => el.value
  );
}


// ============================================================
// GRID LOOKUP
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
// LOWER GRID INDEX
// ============================================================

function lowerIndex(array, value) {

  if (value <= array[0]) {
    return 0;
  }

  if (
    value >=
    array[array.length - 1]
  ) {
    return array.length - 2;
  }

  let low = 0;
  let high = array.length - 1;

  while (low <= high) {

    const mid =
      (low + high) >> 1;

    if (
      array[mid] <= value
    ) {
      low = mid + 1;
    } else {
      high = mid - 1;
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
// PREPARE SELECTED MODEL FIELD
// ============================================================

function buildField(
  period,
  models
) {

  const field = [];

  for (
    let y = 0;
    y < lats.length;
    y++
  ) {

    const row = [];

    for (
      let x = 0;
      x < lons.length;
      x++
    ) {

      const point =
        getPoint(y, x);

      if (!point) {

        row.push(null);

        continue;
      }


      const values = [];


      models.forEach(
        model => {

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
        }
      );


      if (!values.length) {

        row.push(null);

      } else {

        row.push(
          values.reduce(
            (a, b) => a + b,
            0
          ) / values.length
        );
      }
    }

    field.push(row);
  }

  return field;
}


// ============================================================
// FAST BILINEAR INTERPOLATION
// ============================================================

function interpolate(
  field,
  lat,
  lon
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


  let fx =
    (lon - x0) /
    (x1 - x0);

  let fy =
    (lat - y0) /
    (y1 - y0);


  fx =
    Math.max(
      0,
      Math.min(1, fx)
    );

  fy =
    Math.max(
      0,
      Math.min(1, fy)
    );


  const q11 =
    field[yi][xi];

  const q21 =
    field[yi][xi + 1];

  const q12 =
    field[yi + 1][xi];

  const q22 =
    field[yi + 1][xi + 1];


  if (
    q11 === null ||
    q21 === null ||
    q12 === null ||
    q22 === null
  ) {

    const values = [
      q11,
      q21,
      q12,
      q22
    ].filter(
      v => v !== null
    );


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


  // Moderate smoothing.
  // Much less than the previous version.

  fx =
    fx * fx * (3 - 2 * fx);

  fy =
    fy * fy * (3 - 2 * fy);


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
// COLOUR
// ============================================================

function rainfallColor(value) {

  if (
    !Number.isFinite(value) ||
    value < levels[0]
  ) {
    return null;
  }


  if (
    value >=
    levels[levels.length - 1]
  ) {

    return rgbColors[
      rgbColors.length - 1
    ];
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


  // Very mild colour smoothing

  t =
    t * t * (3 - 2 * t);


  const a =
    rgbColors[i];

  const b =
    rgbColors[i + 1];


  return {
    r: Math.round(
      a.r +
      (b.r - a.r) * t
    ),

    g: Math.round(
      a.g +
      (b.g - a.g) * t
    ),

    b: Math.round(
      a.b +
      (b.b - a.b) * t
    )
  };
}


// ============================================================
// RAINFALL CANVAS
// ============================================================

const RainLayer =
  L.Layer.extend({

    onAdd: function(map) {

      this._map =
        map;


      this._canvas =
        document.createElement(
          "canvas"
        );


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
          size.x
        );

      const height =
        Math.max(
          1,
          size.y
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


      ctx.clearRect(
        0,
        0,
        width,
        height
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
        return;
      }


      // ------------------------------------------------------
      // BUILD SOURCE FIELD ONCE
      // ------------------------------------------------------

      const field =
        buildField(
          period,
          models
        );


      // ------------------------------------------------------
      // SMALL DISPLAY FIELD
      // ------------------------------------------------------

      // Much smaller than the previous version.
      // This is deliberately designed for speed.

      const FW = 220;
      const FH = 132;


      const small =
        document.createElement(
          "canvas"
        );


      small.width =
        FW;

      small.height =
        FH;


      const smallCtx =
        small.getContext(
          "2d"
        );


      const image =
        smallCtx.createImageData(
          FW,
          FH
        );


      const pixels =
        image.data;


      const bounds =
        this._map.getBounds();


      const west =
        bounds.getWest();

      const east =
        bounds.getEast();


      // ------------------------------------------------------
      // RENDER SMALL FIELD
      // ------------------------------------------------------

      for (
        let y = 0;
        y < FH;
        y++
      ) {

        const screenY =
          (
            y /
            (FH - 1)
          ) *
          height;


        // Only one geographic conversion
        // per row.

        const rowLat =
          this._map
            .containerPointToLatLng(
              L.point(
                0,
                screenY
              )
            ).lat;


        for (
          let x = 0;
          x < FW;
          x++
        ) {

          const fraction =
            x /
            (FW - 1);


          const lon =
            west +
            (
              east - west
            ) *
            fraction;


          const value =
            interpolate(
              field,
              rowLat,
              lon
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


          const index =
            (
              y * FW +
              x
            ) * 4;


          pixels[index] =
            c.r;

          pixels[index + 1] =
            c.g;

          pixels[index + 2] =
            c.b;

          pixels[index + 3] =
            Math.round(
              255 * opacity
            );
        }
      }


      smallCtx.putImageData(
        image,
        0,
        0
      );


      // ------------------------------------------------------
      // UPSCALE — NO HEAVY BLUR
      // ------------------------------------------------------

      ctx.save();


      ctx.imageSmoothingEnabled =
        true;


      ctx.imageSmoothingQuality =
        "medium";


      // No Gaussian blur.
      // This keeps rainfall cores sharper.

      ctx.filter =
        "none";


      ctx.drawImage(
        small,
        0,
        0,
        width,
        height
      );


      ctx.restore();
    }
  });


// ============================================================
// DRAW
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
    new RainLayer();


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
// MODEL RUN DISPLAY
// ============================================================

function getRunHour(
  timestamp,
  intervalHours
) {

  if (!timestamp) {
    return "--Z";
  }


  const d =
    new Date(timestamp);


  if (
    Number.isNaN(
      d.getTime()
    )
  ) {
    return "--Z";
  }


  const hour =
    d.getUTCHours();


  const cycle =
    Math.floor(
      hour / intervalHours
    ) * intervalHours;


  return (
    String(cycle)
      .padStart(2, "0") +
    "Z"
  );
}


function buildRunText() {

  if (!data || !data.updated) {
    return "";
  }


  const ecmwf =
    getRunHour(
      data.updated,
      6
    );


  const gfs =
    getRunHour(
      data.updated,
      6
    );


  const icon =
    getRunHour(
      data.updated,
      6
    );


  const gem =
    getRunHour(
      data.updated,
      12
    );


  return (
    "Runs: " +
    "ECMWF " + ecmwf +
    " • GEM " + gem +
    " • GFS " + gfs +
    " • ICON " + icon +
    " UTC"
  );
}


// ============================================================
// TOP INFORMATION
// ============================================================

function updateHeader() {

  const time =
    document.querySelector(
      "#time"
    );


  if (!time || !data) {
    return;
  }


  const runText =
    buildRunText();


  time.textContent =
    "Updated: " +
    (
      data.updated ||
      "Latest update"
    ) +
    (
      runText
        ? "  •  " + runText
        : ""
    );
}


// ============================================================
// PREPARE GRID
// ============================================================

function prepareGrid() {

  gridLookup =
    new Map();

  lats = [];
  lons = [];


  const grid =
    data.grid || [];


  if (!grid.length) {
    return;
  }


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
        !Number.isFinite(lat) ||
        !Number.isFinite(lon)
      ) {
        return;
      }


      latSet.add(lat);
      lonSet.add(lon);


      const key =
        lat.toFixed(4) +
        "," +
        lon.toFixed(4);


      gridLookup.set(
        key,
        point
      );
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
// OPACITY
// ============================================================

const opacityControl =
  document.querySelector(
    "#opacity"
  );


if (opacityControl) {

  // Moderate transparency

  opacityControl.value =
    "0.62";


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
          "Unable to load data.json"
        );
      }

      return response.json();
    }
  )


  .then(
    json => {

      data =
        json;


      prepareGrid();

      buildLegend();

      updateHeader();

      drawRainfall();


      setTimeout(
        () => {

          map.invalidateSize();

          drawRainfall();

        },
        250
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
// RESIZE
// ============================================================

window.addEventListener(
  "resize",
  () => {

    if (rainLayer) {
      rainLayer.redraw();
    }

  }
);
