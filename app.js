// ============================================================
// MASRAINMAN
// FAST + SHARP + MODERATELY SMOOTH 6-HOURLY RAINFALL MAP
// VERSION 6
//
// FEATURES
// ------------------------------------------------------------
// • < 1 mm / 6h = transparent
// • 0.5° source grid with bilinear interpolation
// • Reduced smoothing
// • No blur
// • Fast canvas rendering
// • ECMWF / GEM / GFS / ICON model selection
// • 6-hour period selection
// • IST valid-time display
// • UTC model-run display
// ============================================================


// ============================================================
// RAINFALL LEVELS
// ============================================================

const levels = [
  0.1, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40,
  50, 60, 70, 80, 90, 100, 125, 150, 175, 200,
  250, 300, 400, 500, 600, 800
];


// ============================================================
// MASRAINMAN COLOUR SCALE
// ============================================================

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

const map = L.map(
  "map",
  {
    preferCanvas: true,
    zoomControl: true
  }
);


// ============================================================
// MAP EXTENT
// ============================================================

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
    attribution:
      "© OpenStreetMap contributors"
  }
).addTo(map);


// ============================================================
// GLOBAL DATA
// ============================================================

let data = null;

let rainLayer = null;

let lats = [];

let lons = [];

let gridLookup = new Map();


// ============================================================
// HEX → RGB
// ============================================================

function hexToRgb(
  hex
) {

  hex =
    hex.replace(
      "#",
      ""
    );


  return {

    r: parseInt(
      hex.substring(
        0,
        2
      ),
      16
    ),

    g: parseInt(
      hex.substring(
        2,
        4
      ),
      16
    ),

    b: parseInt(
      hex.substring(
        4,
        6
      ),
      16
    )
  };
}


const rgbColors =
  colors.map(
    hexToRgb
  );


// ============================================================
// SELECTED MODELS
// ============================================================

function getSelectedModels() {

  return [
    ...document.querySelectorAll(
      ".model:checked"
    )
  ].map(
    element =>
      element.value
  );
}


// ============================================================
// GRID POINT LOOKUP
// ============================================================

function getPoint(
  latIndex,
  lonIndex
) {

  if (
    latIndex < 0 ||
    lonIndex < 0 ||
    latIndex >= lats.length ||
    lonIndex >= lons.length
  ) {

    return null;
  }


  const key =
    lats[
      latIndex
    ].toFixed(4) +
    "," +
    lons[
      lonIndex
    ].toFixed(4);


  return (
    gridLookup.get(
      key
    ) ||
    null
  );
}


// ============================================================
// BINARY SEARCH
// ============================================================

function lowerIndex(
  array,
  value
) {

  if (
    value <= array[0]
  ) {

    return 0;
  }


  if (
    value >=
    array[
      array.length - 1
    ]
  ) {

    return (
      array.length - 2
    );
  }


  let low = 0;

  let high =
    array.length - 1;


  while (
    low <= high
  ) {

    const mid =
      (
        low +
        high
      ) >> 1;


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
// BUILD SELECTED MODEL FIELD
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
        getPoint(
          y,
          x
        );


      if (
        !point
      ) {

        row.push(
          null
        );

        continue;
      }


      const values = [];


      models.forEach(
        model => {

          const arr =
            point.models &&
            point.models[
              model
            ];


          if (
            arr &&
            Number.isFinite(
              Number(
                arr[period]
              )
            )
          ) {

            values.push(
              Number(
                arr[period]
              )
            );
          }
        }
      );


      if (
        !values.length
      ) {

        row.push(
          null
        );

      } else {

        row.push(
          values.reduce(
            (
              a,
              b
            ) =>
              a + b,
            0
          ) /
          values.length
        );
      }
    }


    field.push(
      row
    );
  }


  return field;
}


// ============================================================
// BILINEAR INTERPOLATION
// ============================================================
//
// No smooth-step.
// No Gaussian blur.
// Keeps rainfall cores sharper.
// ============================================================

function interpolate(
  field,
  lat,
  lon
) {

  if (
    lat < lats[0] ||
    lat > lats[
      lats.length - 1
    ] ||
    lon < lons[0] ||
    lon > lons[
      lons.length - 1
    ]
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


  const fx =
    Math.max(
      0,
      Math.min(
        1,
        (
          lon - x0
        ) /
        (
          x1 - x0
        )
      )
    );


  const fy =
    Math.max(
      0,
      Math.min(
        1,
        (
          lat - y0
        ) /
        (
          y1 - y0
        )
      )
    );


  const q11 =
    field[yi][xi];


  const q21 =
    field[yi][
      xi + 1
    ];


  const q12 =
    field[
      yi + 1
    ][xi];


  const q22 =
    field[
      yi + 1
    ][
      xi + 1
    ];


  // ----------------------------------------------------------
  // Missing-data fallback
  // ----------------------------------------------------------

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
      value =>
        value !== null
    );


    if (
      !values.length
    ) {

      return null;
    }


    return (
      values.reduce(
        (
          a,
          b
        ) =>
          a + b,
        0
      ) /
      values.length
    );
  }


  // ----------------------------------------------------------
  // LINEAR BILINEAR INTERPOLATION
  // ----------------------------------------------------------

  const top =
    q11 *
      (1 - fx) +
    q21 *
      fx;


  const bottom =
    q12 *
      (1 - fx) +
    q22 *
      fx;


  return (
    top *
      (1 - fy) +
    bottom *
      fy
  );
}


// ============================================================
// RAINFALL COLOUR
// ============================================================
//
// IMPORTANT:
//
// < 1 mm / 6h = TRANSPARENT
// ============================================================

function rainfallColor(
  value
) {

  if (
    !Number.isFinite(
      value
    ) ||
    value < 1
  ) {

    return null;
  }


  if (
    value >=
    levels[
      levels.length - 1
    ]
  ) {

    return rgbColors[
      rgbColors.length - 1
    ];
  }


  let i = 0;


  while (
    i <
      levels.length - 1 &&
    value >=
      levels[i + 1]
  ) {

    i++;
  }


  const low =
    levels[i];


  const high =
    levels[
      i + 1
    ];


  let t =
    (
      value - low
    ) /
    (
      high - low
    );


  t =
    Math.max(
      0,
      Math.min(
        1,
        t
      )
    );


  // Linear colour interpolation.
  // No extra smoothing.

  const c1 =
    rgbColors[i];


  const c2 =
    rgbColors[
      i + 1
    ];


  return {

    r: Math.round(
      c1.r +
      (
        c2.r -
        c1.r
      ) *
      t
    ),

    g: Math.round(
      c1.g +
      (
        c2.g -
        c1.g
      ) *
      t
    ),

    b: Math.round(
      c1.b +
      (
        c2.b -
        c1.b
      ) *
      t
    )
  };
}


// ============================================================
// RAINFALL CANVAS LAYER
// ============================================================

const RainLayer =
  L.Layer.extend({

    onAdd:
      function(
        map
      ) {

        this._map =
          map;


        this._canvas =
          document.createElement(
            "canvas"
          );


        this._canvas.className =
          "masrainman-rainfall";


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
          () =>
            this.redraw();


        map.on(
          "moveend zoomend resize",
          this._redraw
        );


        this.redraw();
      },


    onRemove:
      function(
        map
      ) {

        map.off(
          "moveend zoomend resize",
          this._redraw
        );


        if (
          this._canvas
        ) {

          this._canvas.remove();
        }
      },


    redraw:
      function() {

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
            Math.floor(
              size.x
            )
          );


        const height =
          Math.max(
            1,
            Math.floor(
              size.y
            )
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


        // ------------------------------------------------------
        // CURRENT PERIOD
        // ------------------------------------------------------

        const period =
          Number(
            document.querySelector(
              "#period"
            ).value
          );


        // ------------------------------------------------------
        // OPACITY
        // ------------------------------------------------------

        const opacity =
          Number(
            document.querySelector(
              "#opacity"
            ).value
          );


        // ------------------------------------------------------
        // SELECTED MODELS
        // ------------------------------------------------------

        const models =
          getSelectedModels();


        if (
          !models.length
        ) {

          return;
        }


        // ------------------------------------------------------
        // MODEL MEAN FIELD
        // ------------------------------------------------------

        const field =
          buildField(
            period,
            models
          );


        // ------------------------------------------------------
        // FAST DISPLAY GRID
        // ------------------------------------------------------

        const FW = 330;

        const FH = 198;


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


        // ------------------------------------------------------
        // CURRENT MAP BOUNDS
        // ------------------------------------------------------

        const bounds =
          this._map.getBounds();


        const west =
          bounds.getWest();


        const east =
          bounds.getEast();


        // ------------------------------------------------------
        // RENDER
        // ------------------------------------------------------

        for (
          let y = 0;
          y < FH;
          y++
        ) {

          const screenY =
            (
              y /
              (
                FH - 1
              )
            ) *
            height;


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
            x < FW;
            x++
          ) {

            const fraction =
              x /
              (
                FW - 1
              );


            const lon =
              west +
              (
                east -
                west
              ) *
              fraction;


            const value =
              interpolate(
                field,
                rowLat,
                lon
              );


            // --------------------------------------------------
            // BELOW 1 MM = NO COLOUR
            // --------------------------------------------------

            if (
              value === null ||
              value < 1
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


            pixels[
              index + 1
            ] =
              c.g;


            pixels[
              index + 2
            ] =
              c.b;


            pixels[
              index + 3
            ] =
              Math.round(
                255 *
                opacity
              );
          }
        }


        smallCtx.putImageData(
          image,
          0,
          0
        );


        // ------------------------------------------------------
        // UPSCALE
        // ------------------------------------------------------
        //
        // No blur.
        // No filter.
        // Medium interpolation gives a balance between
        // smoothness and rainfall-core definition.
        // ------------------------------------------------------

        ctx.save();


        ctx.imageSmoothingEnabled =
          true;


        ctx.imageSmoothingQuality =
          "medium";


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
// DRAW RAINFALL
// ============================================================

function drawRainfall() {

  if (
    !data ||
    !gridLookup.size
  ) {

    return;
  }


  if (
    rainLayer
  ) {

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
    (
      value,
      i
    ) => {

      const next =
        levels[
          i + 1
        ];


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
// MODEL RUN HOUR
// ============================================================
//
// Model run cycles:
// 00Z
// 06Z
// 12Z
// 18Z
//
// Current data.json contains the update timestamp.
// We derive the nearest completed 6-hour cycle.
// ============================================================

function getRunHour(
  timestamp
) {

  if (
    !timestamp
  ) {

    return null;
  }


  const date =
    new Date(
      timestamp
    );


  if (
    Number.isNaN(
      date.getTime()
    )
  ) {

    return null;
  }


  const utcHour =
    date.getUTCHours();


  const runHour =
    Math.floor(
      utcHour / 6
    ) * 6;


  return (
    String(
      runHour
    ).padStart(
      2,
      "0"
    ) +
    "Z"
  );
}


// ============================================================
// RUN DATE
// ============================================================

function formatRunDate(
  timestamp
) {

  if (
    !timestamp
  ) {

    return null;
  }


  const date =
    new Date(
      timestamp
    );


  if (
    Number.isNaN(
      date.getTime()
    )
  ) {

    return null;
  }


  const day =
    String(
      date.getUTCDate()
    ).padStart(
      2,
      "0"
    );


  const month =
    date.toLocaleString(
      "en-US",
      {
        month: "short",
        timeZone: "UTC"
      }
    );


  const year =
    date.getUTCFullYear();


  return (
    day +
    " " +
    month +
    " " +
    year
  );
}


// ============================================================
// GET VALID TIME
// ============================================================
//
// This function supports several possible data formats:
//
// 1. ISO timestamps in data.periods
// 2. Date/time strings
// 3. Hour offsets
//
// If periods contain timestamps, those are used directly.
//
// Otherwise the selected period is treated as a 6-hour
// forecast offset from the model run.
// ============================================================

function getValidTimestamp(
  period
) {

  // ----------------------------------------------------------
  // OPTION 1 — PERIOD IS ALREADY AN ISO DATE
  // ----------------------------------------------------------

  if (
    data &&
    Array.isArray(
      data.periods
    ) &&
    data.periods[
      period
    ]
  ) {

    const raw =
      data.periods[
        period
      ];


    if (
      typeof raw === "string"
    ) {

      const parsed =
        new Date(
          raw
        );


      if (
        !Number.isNaN(
          parsed.getTime()
        )
      ) {

        return parsed;
      }
    }
  }


  // ----------------------------------------------------------
  // OPTION 2 — USE MODEL RUN + FORECAST INDEX
  // ----------------------------------------------------------

  if (
    data &&
    data.updated
  ) {

    const base =
      new Date(
        data.updated
      );


    if (
      Number.isNaN(
        base.getTime()
      )
    ) {

      return null;
    }


    // Determine completed 6-hour run

    const runHour =
      Math.floor(
        base.getUTCHours() /
        6
      ) * 6;


    const run =
      new Date(
        base
      );


    run.setUTCHours(
      runHour,
      0,
      0,
      0
    );


    // Forecast period index × 6 hours

    run.setUTCHours(
      run.getUTCHours() +
      (
        Number(period) *
        6
      )
    );


    return run;
  }


  return null;
}


// ============================================================
// FORMAT VALID TIME IN IST
// ============================================================

function formatValidIST(
  period
) {

  const valid =
    getValidTimestamp(
      period
    );


  if (
    !valid
  ) {

    return "--";
  }


  // Convert UTC → IST (+5:30)

  const ist =
    new Date(
      valid.getTime() +
      (
        5.5 *
        60 *
        60 *
        1000
      )
    );


  const day =
    String(
      ist.getUTCDate()
    ).padStart(
      2,
      "0"
    );


  const month =
    ist.toLocaleString(
      "en-US",
      {
        month: "short",
        timeZone: "UTC"
      }
    );


  const year =
    ist.getUTCFullYear();


  const hours =
    String(
      ist.getUTCHours()
    ).padStart(
      2,
      "0"
    );


  const minutes =
    String(
      ist.getUTCMinutes()
    ).padStart(
      2,
      "0"
    );


  return (
    day +
    " " +
    month +
    " " +
    year +
    " / " +
    hours +
    ":" +
    minutes +
    " IST"
  );
}


// ============================================================
// TOP-RIGHT RUN + VALID DISPLAY
// ============================================================

function updateHeader() {

  const time =
    document.querySelector(
      "#time"
    );


  if (
    !time ||
    !data ||
    !data.updated
  ) {

    return;
  }


  const periodControl =
    document.querySelector(
      "#period"
    );


  const period =
    periodControl
      ? Number(
          periodControl.value
        )
      : 0;


  const runDate =
    formatRunDate(
      data.updated
    );


  const runHour =
    getRunHour(
      data.updated
    );


  const validIST =
    formatValidIST(
      period
    );


  if (
    !runDate ||
    !runHour
  ) {

    time.textContent =
      "Run : -- | Valid : " +
      validIST;


    return;
  }


  time.textContent =
    "Run : " +
    runDate +
    " / " +
    runHour +
    "  |  Valid : " +
    validIST;


  // ----------------------------------------------------------
  // HEADER STYLE
  // ----------------------------------------------------------

  time.style.fontWeight =
    "600";


  time.style.fontSize =
    "14px";


  time.style.color =
    "#444";


  time.style.whiteSpace =
    "nowrap";
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


  if (
    !grid.length
  ) {

    return;
  }


  const latSet =
    new Set();


  const lonSet =
    new Set();


  grid.forEach(
    point => {

      const lat =
        Number(
          point.lat
        );


      const lon =
        Number(
          point.lon
        );


      if (
        !Number.isFinite(
          lat
        ) ||
        !Number.isFinite(
          lon
        )
      ) {

        return;
      }


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
  );


  lats =
    [
      ...latSet
    ].sort(
      (
        a,
        b
      ) =>
        a - b
    );


  lons =
    [
      ...lonSet
    ].sort(
      (
        a,
        b
      ) =>
        a - b
    );
}


// ============================================================
// MODEL CHECKBOXES
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


if (
  periodControl
) {

  periodControl.addEventListener(
    "change",
    () => {

      // Update Valid time immediately

      updateHeader();


      // Redraw rainfall

      drawRainfall();
    }
  );
}


// ============================================================
// OPACITY CONTROL
// ============================================================

const opacityControl =
  document.querySelector(
    "#opacity"
  );


if (
  opacityControl
) {

  // Default transparency

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

      if (
        !response.ok
      ) {

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


      // Prepare rainfall grid

      prepareGrid();


      // Build legend

      buildLegend();


      // Display Run + Valid

      updateHeader();


      // Draw rainfall

      drawRainfall();


      // Second render after Leaflet layout

      setTimeout(
        () => {

          map.invalidateSize();

          updateHeader();

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


      if (
        time
      ) {

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

    if (
      rainLayer
    ) {

      rainLayer.redraw();
    }
  }
);
