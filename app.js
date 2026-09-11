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

// India-focused view.
// Still includes Arabian Sea, Bay of Bengal and surrounding
// areas for weather-system context.

const mapBounds = [
  [6.0, 67.0],
  [36.0, 99.0]
];

const map = L.map("map", {
  minZoom: 4,
  maxZoom: 10
});

map.fitBounds(
  mapBounds,
  {
    padding: [10, 10]
  }
);


L.tileLayer(
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  {
    attribution:
      "© OpenStreetMap contributors"
  }
).addTo(map);


L.control.scale({
  imperial: false
}).addTo(map);


// ============================================================
// STATE
// ============================================================

let layer = null;
let data = null;


// ============================================================
// RAINFALL COLOUR
// ============================================================

function color(value) {

  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(value) ||
    value < levels[0]
  ) {
    return null;
  }

  let i = 0;

  while (
    i < levels.length - 1 &&
    value >= levels[i + 1]
  ) {
    i++;
  }

  return colors[
    Math.min(
      i,
      colors.length - 1
    )
  ];
}


// ============================================================
// MAP DRAW
// ============================================================

function draw() {

  if (!data) {
    return;
  }


  if (layer) {
    map.removeLayer(layer);
    layer = null;
  }


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


  const selected = [
    ...document.querySelectorAll(
      ".model:checked"
    )
  ].map(
    element => element.value
  );


  if (!selected.length) {
    return;
  }


  const features = [];


  const step =
    Number(
      data.step || 0.5
    );


  const half =
    step / 2;


  (data.grid || []).forEach(
    point => {

      const values =
        selected
          .map(
            model =>
              (
                point.models || {}
              )[model]?.[period]
          )
          .filter(
            Number.isFinite
          );


      if (!values.length) {
        return;
      }


      // Equal-weight mean of selected models
      const rainfall =
        values.reduce(
          (sum, value) =>
            sum + value,
          0
        ) / values.length;


      const fill =
        color(rainfall);


      if (!fill) {
        return;
      }


      const lat =
        Number(point.lat);

      const lon =
        Number(point.lon);


      features.push({

        type: "Feature",

        geometry: {

          type: "Polygon",

          coordinates: [[

            [
              lon - half,
              lat - half
            ],

            [
              lon + half,
              lat - half
            ],

            [
              lon + half,
              lat + half
            ],

            [
              lon - half,
              lat + half
            ],

            [
              lon - half,
              lat - half
            ]

          ]]

        },

        properties: {
          rainfall
        }

      });

    }
  );


  layer =
    L.geoJSON(
      {
        type:
          "FeatureCollection",

        features
      },
      {

        style: feature => {

          const fill =
            color(
              feature.properties.rainfall
            );

          return {

            fillColor: fill,

            fillOpacity:
              opacity,

            color: fill,

            weight: 0

          };

        },


        onEachFeature:
          (feature, polygon) => {

            const rainfall =
              feature.properties.rainfall;


            polygon.bindTooltip(
              `${rainfall.toFixed(1)} mm / 6h`,
              {
                sticky: true
              }
            );

          }

      }
    )
    .addTo(map);

}


// ============================================================
// LEGEND
// ============================================================

function legend() {

  const element =
    document.querySelector(
      "#legend"
    );


  element.innerHTML =
    "<b>Rainfall (mm / 6h)</b><br>" +

    levels
      .map(
        (value, index) => {

          const label =
            index <
            levels.length - 1

              ? `${value}–${levels[index + 1]}`

              : `≥${value}`;


          return `
            <span
              class="lg"
              style="background:${colors[index]}"
            ></span>${label}
          `;

        }
      )
      .join("<br>");

}


// ============================================================
// HEADER
// ============================================================

function updateHeader() {

  if (!data) {
    return;
  }


  const time =
    document.querySelector(
      "#time"
    );


  const updated =
    data.updated ||
    "Latest update";


  const models =
    (
      data.models || []
    ).join(
      " + "
    );


  time.textContent =
    `Updated: ${updated}` +
    (models
      ? ` • ${models}`
      : "");

}


// ============================================================
// PERIOD LABELS
// ============================================================

function updatePeriods() {

  const selector =
    document.querySelector(
      "#period"
    );


  if (
    !data ||
    !data.periods
  ) {
    return;
  }


  selector.innerHTML =
    "";


  data.periods.forEach(
    (label, index) => {

      const option =
        document.createElement(
          "option"
        );

      option.value =
        index;

      option.textContent =
        label;

      selector.appendChild(
        option
      );

    }
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
          "Unable to load rainfall data"
        );
      }

      return response.json();

    }
  )

  .then(
    json => {

      data = json;

      updateHeader();

      updatePeriods();

      draw();

      legend();

    }
  )

  .catch(
    error => {

      console.error(
        error
      );

      document.querySelector(
        "#time"
      ).textContent =
        "Data unavailable";

    }
  );


// ============================================================
// CONTROLS
// ============================================================

document
  .querySelectorAll(
    ".model"
  )
  .forEach(
    checkbox =>
      checkbox.addEventListener(
        "change",
        draw
      )
  );


document
  .querySelector(
    "#period"
  )
  .addEventListener(
    "change",
    draw
  );


document
  .querySelector(
    "#opacity"
  )
  .addEventListener(
    "input",
    draw
  );
