// ============================================================
// MASRAINMAN — FAST SMOOTH 6-HOURLY RAINFALL MAP
// Optimized smooth rendering
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
// DATA VARIABLES
// ============================================================

let data = null;
let rainLayer = null;

let lats = [];
let lons = [];

let gridLookup = new Map();


// ============================================================
// RGB COLORS
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
// SELECTED MODELS
// ============================================================

function getSelectedModels() {

  return [
    ...document.querySelectorAll(".model:checked")
  ].map(
    el => el.value
  );
}


// ============================================================
// MODEL MEAN
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

  return (
    gridLookup.get(key) ||
    null
  );
}


// ============================================================
// FIND LOWER GRID INDEX
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

  while (low <= high) {

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
// FAST BILINEAR INTERPOLATION
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

  let fy =
    (lat - y0) /
    (y1 - y0);

  let fx =
    (lon - x0) /
    (x1 - x0);

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


  // Missing corner fallback

  if (
    q11 === null ||
    q21 === null ||
    q12 === null ||
    q22 === null
  ) {

    return (
      values.reduce(
        (a, b) => a + b,
        0
      ) /
      values.length
    );
  }


  // Smooth-step interpolation

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
// CONTINUOUS RAINFALL COLOUR
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


  // Smooth colour transition

  t =
    t * t * (3 - 2 * t);


  const c1 =
    rgbColors[i];

  const c2 =
    rgbColors[i + 1];


  return {
    r: Math.round(
      c1.r +
      (c2.r - c1.r) * t
    ),

    g: Math.round(
      c1.g +
      (c2.g - c1.g) * t
    ),

    b: Math.round(
      c1.b +
      (c2.b - c1.b) * t
    )
  };
}


// ============================================================
// SMOOTH RAINFALL CANVAS
// ============================================================

const SmoothRainLayer =
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

      this._canvas.style.width =
        "100%";

      this._canvas.style.height =
        "100%";

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


      const displayWidth =
        Math.max(
          1,
          size.x
        );

      const displayHeight =
        Math.max(
          1,
          size.y
        );


      const canvas =
        this._canvas;


      canvas.width =
        displayWidth;

      canvas.height =
        displayHeight;


      const ctx =
        canvas.getContext(
          "2d"
        );


      ctx.clearRect(
        0,
        0,
        displayWidth,
        displayHeight
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
      // LOW RESOLUTION METEOROLOGICAL FIELD
      // ------------------------------------------------------

      // This is the key performance improvement.
      //
      // We calculate only 360 × 220 values rather than
      // calculating hundreds of thousands of screen pixels.

      const FIELD_WIDTH = 360;

      const FIELD_HEIGHT = 220;


      const field =
        document.createElement(
          "canvas"
        );


      field.width =
        FIELD_WIDTH;

      field.height =
        FIELD_HEIGHT;


      const fieldCtx =
        field.getContext(
          "2d"
        );


      const image =
        fieldCtx.createImageData(
          FIELD_WIDTH,
          FIELD_HEIGHT
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
      // CALCULATE EACH FIELD ROW
      // ------------------------------------------------------

      for (
        let y = 0;
        y < FIELD_HEIGHT;
        y++
      ) {

        const screenY =
          (
            y /
            (FIELD_HEIGHT - 1)
          ) *
          displayHeight;


        // Only one Leaflet coordinate conversion
        // per row instead of one per pixel.

        const rowLat =
          this._map
            .containerPointToLatLng(
              L.point(
                0,
                screenY
              )
            )
            .lat;


        for (
          let x = 0;
          x < FIELD_WIDTH;
          x++
        ) {

          const fraction =
            x /
            (FIELD_WIDTH - 1);


          const lon =
            west +
            (
              east - west
            ) *
            fraction;


          const value =
            interpolateRainfall(
              rowLat,
              lon,
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


          const index =
            (
              y *
              FIELD_WIDTH +
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


      fieldCtx.putImageData(
        image,
        0,
        0
      );


      // ------------------------------------------------------
      // SMOOTH UPSCALE
      // ------------------------------------------------------

      ctx.save();


      // Browser interpolation makes the rainfall field
      // continuous rather than showing source-grid squares.

      ctx.imageSmoothingEnabled =
        true;


      ctx.imageSmoothingQuality =
        "high";


      // Very gentle blur removes remaining pixel edges.

      ctx.filter =
        "blur(2.5px)";


      ctx.drawImage(
        field,
        0,
        0,
        displayWidth,
        displayHeight
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
// PERIOD
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
// TRANSPARENCY
// ============================================================

const opacityControl =
  document.querySelector(
    "#opacity"
  );


if (opacityControl) {

  // Default opacity.
  // Lower value keeps labels and boundaries visible.

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


      // Give Leaflet time to finish layout

      setTimeout(
        () => {

          map.invalidateSize();

          drawRainfall();

        },
        400
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
