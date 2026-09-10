const levels = [
  0.1, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60,
  70, 80, 90, 100, 125, 150, 175, 200, 250, 300, 400,
  500, 600, 800
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

/* MasRainman rainfall domain */
const bounds = [
  [7.5, 74.5],
  [14.5, 82.5]
];

/* Create map and automatically fit to the rainfall domain */
const map = L.map("map").fitBounds(bounds, {
  padding: [10, 10]
});

/* OpenStreetMap base layer */
L.tileLayer(
  "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  {
    attribution: "© OpenStreetMap contributors"
  }
).addTo(map);

/* Map scale */
L.control.scale({
  imperial: false
}).addTo(map);

let layer = null;
let data = null;

/* Convert rainfall value to MasRainman colour */
function color(value) {
  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(value) ||
    value < levels[0]
  ) {
    return null;
  }

  let index = 0;

  while (
    index < levels.length - 1 &&
    value >= levels[index + 1]
  ) {
    index++;
  }

  return colors[Math.min(index, colors.length - 1)];
}

/* Update period labels if data.json contains them */
function updatePeriods() {
  const select = document.querySelector("#period");

  if (!select || !data) {
    return;
  }

  if (Array.isArray(data.periods) && data.periods.length) {
    select.innerHTML = "";

    data.periods.forEach((label, index) => {
      const option = document.createElement("option");
      option.value = index;
      option.textContent = label;
      select.appendChild(option);
    });
  }
}

/* Draw rainfall layer */
function draw() {
  if (!data) {
    return;
  }

  if (layer) {
    map.removeLayer(layer);
    layer = null;
  }

  const periodElement = document.querySelector("#period");
  const opacityElement = document.querySelector("#opacity");

  const period = periodElement
    ? Number(periodElement.value)
    : 0;

  const opacity = opacityElement
    ? Number(opacityElement.value)
    : 0.8;

  const selectedModels = [
    ...document.querySelectorAll(".model:checked")
  ].map(element => element.value);

  const features = [];

  (data.grid || []).forEach(gridPoint => {

    const values = selectedModels
      .map(model => {
        const series = (gridPoint.models || {})[model];
        return Array.isArray(series)
          ? series[period]
          : null;
      })
      .filter(Number.isFinite);

    if (!values.length) {
      return;
    }

    /* Equal-weight multi-model mean */
    const rainfall =
      values.reduce((sum, value) => sum + value, 0) /
      values.length;

    const fill = color(rainfall);

    if (!fill) {
      return;
    }

    const step = Number(data.step) || 0.25;

    const lat = Number(gridPoint.lat);
    const lon = Number(gridPoint.lon);

    features.push({
      type: "Feature",

      geometry: {
        type: "Polygon",

        coordinates: [[
          [lon - step / 2, lat - step / 2],
          [lon + step / 2, lat - step / 2],
          [lon + step / 2, lat + step / 2],
          [lon - step / 2, lat + step / 2],
          [lon - step / 2, lat - step / 2]
        ]]
      },

      properties: {
        rainfall: rainfall
      }
    });
  });

  layer = L.geoJSON(
    {
      type: "FeatureCollection",
      features: features
    },
    {
      style: feature => ({
        fillColor: color(feature.properties.rainfall),
        fillOpacity: opacity,
        color: color(feature.properties.rainfall),
        weight: 0
      }),

      onEachFeature: (feature, polygon) => {
        const rainfall = feature.properties.rainfall;

        polygon.bindTooltip(
          `${rainfall.toFixed(1)} mm / 6h`,
          {
            sticky: true,
            direction: "top"
          }
        );
      }
    }
  ).addTo(map);
}

/* Build rainfall legend */
function legend() {
  const legendElement = document.querySelector("#legend");

  if (!legendElement) {
    return;
  }

  let html = "<b>Rainfall (mm / 6h)</b><br>";

  levels.forEach((value, index) => {

    const next =
      index < levels.length - 1
        ? levels[index + 1]
        : null;

    const label =
      next !== null
        ? `${value}–${next}`
        : `≥${value}`;

    html +=
      `<span class="lg"
        style="
          display:inline-block;
          width:14px;
          height:14px;
          margin-right:4px;
          vertical-align:middle;
          background:${colors[index]};
        ">
      </span>${label}<br>`;
  });

  legendElement.innerHTML = html;
}

/* Show current data timestamp */
function updateTime() {
  const timeElement = document.querySelector("#time");

  if (!timeElement || !data) {
    return;
  }

  if (!data.updated) {
    timeElement.textContent = "Latest update unavailable";
    return;
  }

  const date = new Date(data.updated);

  if (Number.isNaN(date.getTime())) {
    timeElement.textContent = data.updated;
    return;
  }

  timeElement.textContent =
    "Updated: " +
    date.toLocaleString("en-IN", {
      dateStyle: "medium",
      timeStyle: "short"
    });
}

/* Show selected model information */
function updateModelInfo() {
  const selected = [
    ...document.querySelectorAll(".model:checked")
  ].map(element => element.value);

  const timeElement = document.querySelector("#time");

  if (!timeElement || !data) {
    return;
  }

  let updateText = "";

  if (data.updated) {
    const date = new Date(data.updated);

    if (!Number.isNaN(date.getTime())) {
      updateText =
        "Updated: " +
        date.toLocaleString("en-IN", {
          dateStyle: "medium",
          timeStyle: "short"
        });
    }
  }

  if (selected.length) {
    timeElement.textContent =
      updateText +
      "  •  " +
      selected.join(" + ");
  } else {
    timeElement.textContent =
      updateText +
      "  •  No model selected";
  }
}

/* Load rainfall data */
fetch("data.json?" + Date.now())
  .then(response => {

    if (!response.ok) {
      throw new Error(
        `HTTP ${response.status}`
      );
    }

    return response.json();
  })

  .then(json => {

    data = json;

    updatePeriods();
    updateModelInfo();
    draw();
    legend();

    /* Keep map inside the MasRainman domain */
    map.fitBounds(bounds, {
      padding: [10, 10]
    });
  })

  .catch(error => {

    const timeElement =
      document.querySelector("#time");

    if (timeElement) {
      timeElement.textContent =
        "Rainfall data unavailable";
    }

    console.error(
      "MasRainman rainfall data error:",
      error
    );
  });

/* Redraw when controls change */
document
  .querySelectorAll(".model, #period, #opacity")
  .forEach(element => {

    element.addEventListener("change", () => {
      updateModelInfo();
      draw();
    });
  });
