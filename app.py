# -*- coding: utf-8 -*-
"""
MasRainman 6-Hourly Rainfall Model Blend
-----------------------------------------

A single-file Streamlit application that replaces the old placeholder local
GRIB paths with live model data retrieved from Open-Meteo's model-specific
forecast API.

Models:
    ECMWF HRES / IFS
    GFS Global
    ICON Global
    GEM Global

The map uses a common 0.25-degree sampling grid for the live API retrieval,
then interpolates the selected model fields to a smoother display grid.

IMPORTANT:
This version uses Open-Meteo as the data-access layer. It is NOT a raw-GRIB
implementation. Open-Meteo exposes model-specific ECMWF, GFS, ICON and GEM
data and supports model selection. The forecast API represents the latest
available model forecast rather than preserving a separately selected
initialisation run.

Install:
    python -m pip install streamlit streamlit-folium folium requests numpy pandas scipy pillow

Run:
    python -m streamlit run panel.py
"""

import io
import math
import base64
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests
import streamlit as st
import folium

from scipy.interpolate import RegularGridInterpolator
from streamlit_folium import st_folium


# ============================================================
# CONFIGURATION
# ============================================================

APP_TITLE = "MasRainman — 6-Hourly Rainfall"

# South India / Sri Lanka domain.
LAT_MIN = 7.5
LAT_MAX = 14.5
LON_MIN = 74.5
LON_MAX = 82.5

# API retrieval grid.
# 0.25° is intentionally used because it keeps the live API workload
# manageable while remaining suitable for a regional model comparison.
SOURCE_RESOLUTION = 0.25

# Display grid.
DISPLAY_RESOLUTION = 0.05

API_BATCH_SIZE = 250
FORECAST_DAYS = 3
API_RETRIES = 4
API_BACKOFF_SECONDS = 8

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo model identifiers.
# ECMWF IFS is the current ECMWF global deterministic model endpoint.
MODEL_CONFIG = {
    "ECMWF HRES": {
        "api_model": "ecmwf_ifs",
        "source": "ECMWF IFS HRES",
        "resolution": "9 km native",
    },
    "GFS": {
        "api_model": "gfs_global",
        "source": "NOAA GFS Global",
        "resolution": "0.11°/0.25°",
    },
    "ICON": {
        "api_model": "icon_global",
        "source": "DWD ICON Global",
        "resolution": "~11 km",
    },
    "GEM": {
        "api_model": "gem_global",
        "source": "Canadian GEM Global",
        "resolution": "~15 km",
    },
}

# UI groups.
MODEL_GROUPS = {
    "Deterministic": [
        "ECMWF HRES",
        "GEM",
        "GFS",
        "ICON",
    ],
    "Ensembles": [
        "EPS",
    ],
    "ML Models": [
        "AIFS (Deterministic)",
        "AIFS Ensembles",
        "WeatherNext 3",
    ],
}

# Rainfall thresholds in mm / 6 h.
RAIN_LEVELS = np.array([
    0.1, 1, 2, 3, 5, 7, 10, 15, 20, 25, 30, 40, 50, 60,
    70, 80, 90, 100, 125, 150, 175, 200, 250, 300, 400, 500,
    600, 800,
], dtype=float)

# MasRainman default precipitation colour scale.
RAIN_COLOURS = [
    "#f2f2f2", "#c7dcff", "#8ebfff", "#4aa3ff", "#007cff",
    "#004b99", "#1b5e20", "#00c853", "#64dd17", "#c6ff00",
    "#ffd600", "#ffab00", "#ff6d00", "#ff8f00", "#ff5c8a",
    "#ff1f5b", "#ff0033", "#d50000", "#7b1fa2", "#6a00ff",
    "#c000ff", "#d580ff", "#f0ccff", "#d9d9d9", "#a6a6a6",
    "#7a7a7a", "#4d4d4d", "#333333",
]


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🌧️",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
<style>
.block-container {
    padding-top: 0.4rem;
    padding-bottom: 0rem;
    max-width: 100%;
}

.mas-title {
    font-size: 25px;
    font-weight: 800;
    color: #111;
    line-height: 1.1;
}

.mas-subtitle {
    color: #666;
    font-size: 13px;
    margin-bottom: 8px;
}

.info-card {
    border: 1px solid #dddddd;
    border-radius: 8px;
    padding: 10px;
    background: #ffffff;
    margin-bottom: 9px;
}

.model-card {
    border-radius: 8px;
    padding: 10px;
    margin-bottom: 8px;
    background: #fffde8;
    border: 1px solid #eee6a5;
}

.model-card-title {
    font-weight: 700;
    color: #735f00;
}

.legend {
    background: rgba(255,255,255,0.96);
    padding: 10px;
    border-radius: 7px;
    box-shadow: 0 1px 5px rgba(0,0,0,.25);
}

.legend-row {
    display: flex;
    align-items: center;
    height: 19px;
    font-size: 11px;
}

.legend-colour {
    width: 25px;
    height: 14px;
    margin-right: 6px;
}

.small-note {
    color: #777;
    font-size: 11px;
}
</style>
""",
    unsafe_allow_html=True,
)


# ============================================================
# HEADER
# ============================================================

st.markdown(
    """
<div class="mas-title">🌧️ MasRainman — 6-Hourly Rainfall</div>
<div class="mas-subtitle">
ECMWF • GFS • ICON • GEM • Model Blend
</div>
""",
    unsafe_allow_html=True,
)


# ============================================================
# GRID
# ============================================================

SOURCE_LAT = np.arange(
    LAT_MIN,
    LAT_MAX + SOURCE_RESOLUTION * 0.5,
    SOURCE_RESOLUTION,
)

SOURCE_LON = np.arange(
    LON_MIN,
    LON_MAX + SOURCE_RESOLUTION * 0.5,
    SOURCE_RESOLUTION,
)

DISPLAY_LAT = np.arange(
    LAT_MIN,
    LAT_MAX + DISPLAY_RESOLUTION * 0.5,
    DISPLAY_RESOLUTION,
)

DISPLAY_LON = np.arange(
    LON_MIN,
    LON_MAX + DISPLAY_RESOLUTION * 0.5,
    DISPLAY_RESOLUTION,
)


def make_points():
    """Return API query points in row-major order."""
    points = []
    for lat in SOURCE_LAT:
        for lon in SOURCE_LON:
            points.append((float(lat), float(lon)))
    return points


POINTS = make_points()


# ============================================================
# API HELPERS
# ============================================================

def chunks(values, size):
    for i in range(0, len(values), size):
        yield values[i:i + size]


def parse_iso_time(value):
    return pd.Timestamp(value).tz_localize("UTC") if pd.Timestamp(value).tzinfo is None else pd.Timestamp(value).tz_convert("UTC")


@st.cache_data(
    ttl=1800,
    show_spinner=False,
)
def fetch_model_grid(model_name):
    """
    Retrieve hourly precipitation for the full South India grid.

    Open-Meteo accepts comma-separated coordinate lists and returns one
    response object per coordinate. The grid is sent in larger batches to
    keep the number of HTTP requests low. Results are cached for 30 minutes
    so Streamlit reruns do not repeatedly hit the API.
    """

    if model_name not in MODEL_CONFIG:
        raise ValueError(
            f"Unsupported live model: {model_name}"
        )

    api_model = MODEL_CONFIG[model_name]["api_model"]

    all_records = []
    reference_times = None

    session = requests.Session()
    session.headers.update({
        "User-Agent": "MasRainman/1.0 rainfall-panel"
    })

    for batch in chunks(POINTS, API_BATCH_SIZE):

        latitudes = ",".join(
            f"{lat:.2f}" for lat, _ in batch
        )

        longitudes = ",".join(
            f"{lon:.2f}" for _, lon in batch
        )

        params = {
            "latitude": latitudes,
            "longitude": longitudes,
            "hourly": "precipitation",
            "models": api_model,
            "forecast_days": FORECAST_DAYS,
            "timezone": "GMT",
            "precipitation_unit": "mm",
            "cell_selection": "nearest",
        }

        # Open-Meteo is rate-limited on the free endpoint.  Retry 429s with
        # exponential backoff instead of immediately failing the whole model.
        response = None
        last_error = None

        for attempt in range(API_RETRIES):
            try:
                response = session.get(
                    OPEN_METEO_FORECAST_URL,
                    params=params,
                    timeout=90,
                )

                if response.status_code != 429:
                    response.raise_for_status()
                    break

                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait_seconds = max(
                            API_BACKOFF_SECONDS,
                            int(float(retry_after)),
                        )
                    except ValueError:
                        wait_seconds = API_BACKOFF_SECONDS * (2 ** attempt)
                else:
                    wait_seconds = API_BACKOFF_SECONDS * (2 ** attempt)

                import time
                time.sleep(wait_seconds)

            except requests.RequestException as exc:
                last_error = exc
                if attempt >= API_RETRIES - 1:
                    raise
                import time
                time.sleep(API_BACKOFF_SECONDS * (2 ** attempt))

        if response is None:
            raise RuntimeError(
                f"No response received for {model_name}: {last_error}"
            )

        if response.status_code == 429:
            raise RuntimeError(
                f"Open-Meteo rate limit remained active after {API_RETRIES} retries. "
                "Wait a few minutes and run the app again."
            )

        payload = response.json()

        # Multiple coordinates -> list.
        if isinstance(payload, dict):
            payload = [payload]

        for item in payload:

            if "error" in item and item["error"]:
                raise RuntimeError(
                    item.get(
                        "reason",
                        "Open-Meteo API returned an error.",
                    )
                )

            hourly = item.get("hourly")

            if hourly is None:
                raise RuntimeError(
                    f"No hourly data returned for {model_name}."
                )

            times = hourly.get("time", [])
            precipitation = hourly.get(
                "precipitation",
                [],
            )

            if reference_times is None:
                reference_times = [
                    pd.Timestamp(t).tz_localize("UTC")
                    if pd.Timestamp(t).tzinfo is None
                    else pd.Timestamp(t).tz_convert("UTC")
                    for t in times
                ]

            all_records.append({
                "lat": float(item["latitude"]),
                "lon": float(item["longitude"]),
                "precip": np.asarray(
                    precipitation,
                    dtype=float,
                ),
            })

    if not all_records:
        raise RuntimeError(
            f"No data returned for {model_name}."
        )

    n_time = len(reference_times)

    field = np.full(
        (
            len(SOURCE_LAT),
            len(SOURCE_LON),
            n_time,
        ),
        np.nan,
        dtype=float,
    )

    lat_index = {
        round(float(v), 4): i
        for i, v in enumerate(SOURCE_LAT)
    }

    lon_index = {
        round(float(v), 4): i
        for i, v in enumerate(SOURCE_LON)
    }

    for record in all_records:

        lat_key = round(
            record["lat"],
            4,
        )

        lon_key = round(
            record["lon"],
            4,
        )

        if lat_key not in lat_index:
            continue

        if lon_key not in lon_index:
            continue

        i = lat_index[lat_key]
        j = lon_index[lon_key]

        values = record["precip"]

        # API precipitation is hourly accumulation for the preceding hour.
        field[
            i,
            j,
            :min(n_time, len(values)),
        ] = values[
            :n_time
        ]

    return {
        "times": reference_times,
        "hourly_precip": field,
    }


def calculate_6hour_windows(model_data):
    """
    Convert hourly precipitation to rolling non-overlapping 6-hour totals.

    Window 0:
        hours 0..5

    Window 1:
        hours 6..11

    etc.
    """

    hourly = model_data["hourly_precip"]
    times = model_data["times"]

    n_hours = hourly.shape[-1]
    n_windows = n_hours // 6

    rainfall = np.full(
        (
            hourly.shape[0],
            hourly.shape[1],
            n_windows,
        ),
        np.nan,
        dtype=float,
    )

    window_times = []

    for k in range(n_windows):

        start = k * 6
        end = start + 6

        rainfall[:, :, k] = np.nansum(
            hourly[:, :, start:end],
            axis=2,
        )

        window_times.append(
            (
                times[start],
                times[end - 1] + pd.Timedelta(hours=1),
            )
        )

    return {
        "rainfall": rainfall,
        "windows": window_times,
    }


def interpolate_field(field):
    """
    Bilinear interpolation from the 0.25° retrieval grid to the 0.05°
    display grid.
    """

    interpolator = RegularGridInterpolator(
        (
            SOURCE_LAT,
            SOURCE_LON,
        ),
        field,
        method="linear",
        bounds_error=False,
        fill_value=np.nan,
    )

    lat_mesh, lon_mesh = np.meshgrid(
        DISPLAY_LAT,
        DISPLAY_LON,
        indexing="ij",
    )

    points = np.column_stack([
        lat_mesh.ravel(),
        lon_mesh.ravel(),
    ])

    result = interpolator(
        points
    ).reshape(
        lat_mesh.shape
    )

    return result


# ============================================================
# RAINFALL RENDERING
# ============================================================

def hex_to_rgb(hex_colour):
    value = hex_colour.lstrip("#")
    return tuple(
        int(value[i:i + 2], 16)
        for i in (0, 2, 4)
    )


def rainfall_to_rgba(
    rainfall,
    opacity,
):
    """
    Render rainfall using the MasRainman default precipitation scale.

    The thresholds and colours are intentionally kept identical to the
    standard contourf colour bar used by the MasRainman plotting scripts.
    """
    rain = np.asarray(
        rainfall,
        dtype=float,
    )

    rgba = np.zeros(
        rain.shape + (4,),
        dtype=np.uint8,
    )

    valid = (
        np.isfinite(rain) &
        (rain >= RAIN_LEVELS[0])
    )

    alpha = int(255 * opacity)

    # Each colour corresponds to rainfall from its threshold up to
    # (but not including) the next threshold.
    for i in range(len(RAIN_LEVELS) - 1):
        low = RAIN_LEVELS[i]
        high = RAIN_LEVELS[i + 1]

        mask = (
            valid &
            (rain >= low) &
            (rain < high)
        )

        r, g, b = hex_to_rgb(RAIN_COLOURS[i])

        rgba[mask, 0] = r
        rgba[mask, 1] = g
        rgba[mask, 2] = b
        rgba[mask, 3] = alpha

    # Final colour for >= 800 mm.
    mask = valid & (rain >= RAIN_LEVELS[-1])

    r, g, b = hex_to_rgb(RAIN_COLOURS[-1])

    rgba[mask, 0] = r
    rgba[mask, 1] = g
    rgba[mask, 2] = b
    rgba[mask, 3] = alpha

    return rgba


def rgba_to_data_url(rgba):
    from PIL import Image

    image = Image.fromarray(
        rgba,
        mode="RGBA",
    )

    buffer = io.BytesIO()

    image.save(
        buffer,
        format="PNG",
        optimize=True,
    )

    encoded = base64.b64encode(
        buffer.getvalue()
    ).decode("ascii")

    return (
        "data:image/png;base64,"
        + encoded
    )


def add_rainfall_overlay(
    fmap,
    rainfall,
    opacity,
):
    rgba = rainfall_to_rgba(
        rainfall,
        opacity,
    )

    data_url = rgba_to_data_url(
        rgba
    )

    folium.raster_layers.ImageOverlay(
        image=data_url,
        bounds=[
            [
                LAT_MIN,
                LON_MIN,
            ],
            [
                LAT_MAX,
                LON_MAX,
            ],
        ],
        opacity=1.0,
        interactive=False,
        cross_origin=False,
        zindex=10,
    ).add_to(fmap)


def add_legend(fmap):
    labels = [
        "0.1–1",
        "1–2",
        "2–3",
        "3–5",
        "5–7",
        "7–10",
        "10–15",
        "15–20",
        "20–25",
        "25–30",
        "30–40",
        "40–50",
        "50–60",
        "60–70",
        "70–80",
        "80–90",
        "90–100",
        "100–125",
        "125–150",
        "150–175",
        "175–200",
        "200–250",
        "250–300",
        "300–400",
        "400–500",
        "500–600",
        "600–800",
        ">800",
    ]

    colours = RAIN_COLOURS

    html = """
    <div class="legend">
        <b>6-Hour Rainfall (mm)</b>
    """

    for colour, label in zip(
        colours,
        labels,
    ):

        html += f"""
        <div class="legend-row">
            <div class="legend-colour"
                 style="background:{colour};"></div>
            {label}
        </div>
        """

    html += "</div>"

    fmap.get_root().html.add_child(
        folium.Element(html)
    )


# ============================================================
# MAP
# ============================================================

def create_map(
    rainfall,
    opacity,
    show_boundaries,
):
    fmap = folium.Map(
        location=[
            11.20,
            79.30,
        ],
        zoom_start=6,
        min_zoom=5,
        max_zoom=10,
        tiles="OpenStreetMap",
        control_scale=True,
    )

    if rainfall is not None:
        add_rainfall_overlay(
            fmap,
            rainfall,
            opacity,
        )

    if show_boundaries:
        # Natural Earth / GeoJSON state boundary layer.
        # If remote access is unavailable, the rainfall layer still works.
        try:
            folium.GeoJson(
                "https://raw.githubusercontent.com/"
                "datameet/maps/master/Country/"
                "India/india_state.geojson",
                name="State Boundaries",
                style_function=lambda feature: {
                    "fillOpacity": 0,
                    "weight": 1.5,
                    "color": "#333333",
                },
            ).add_to(fmap)
        except Exception:
            pass

    if rainfall is not None:
        add_legend(fmap)

    folium.LayerControl().add_to(fmap)

    return fmap


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.markdown(
        "## 6-Hourly Rainfall"
    )

    st.caption(
        "Select models within one category. "
        "Selected deterministic models are equally weighted."
    )

    st.markdown(
        "### Deterministic"
    )

    deterministic_models = []

    for model in MODEL_GROUPS["Deterministic"]:

        checked = st.checkbox(
            model,
            value=True,
            key=f"det_{model}",
        )

        if checked:
            deterministic_models.append(
                model
            )

    st.markdown(
        "### Ensembles"
    )

    st.checkbox(
        "EPS",
        value=False,
        disabled=True,
        help="Ensemble retrieval is reserved for the next ensemble module.",
    )

    st.markdown(
        "### ML Models"
    )

    st.checkbox(
        "AIFS (Deterministic)",
        value=False,
        disabled=True,
        help="AIFS can be enabled after adding its dedicated model endpoint.",
    )

    st.checkbox(
        "AIFS Ensembles",
        value=False,
        disabled=True,
    )

    st.checkbox(
        "WeatherNext 3",
        value=False,
        disabled=True,
    )

    st.markdown(
        "### Same Family (curated pairs)"
    )

    same_family = st.radio(
        "",
        [
            "None",
            "HRES + AIFS (Deterministic)",
            "HRES + EPS",
            "EPS + AIFS Ensembles",
            "AIFS (Deterministic) + AIFS Ensembles",
        ],
        index=0,
    )

    st.markdown(
        "### Reference Layers"
    )

    show_boundaries = st.checkbox(
        "State Boundaries",
        value=True,
    )

    st.checkbox(
        "District Boundaries",
        value=False,
        disabled=True,
        help="District GeoJSON can be added as a local layer later.",
    )

    st.checkbox(
        "State Capitals",
        value=False,
        disabled=True,
    )

    st.markdown(
        "### 6-Hour Period"
    )


# ============================================================
# MODEL SELECTION
# ============================================================

selected_models = list(
    deterministic_models
)

if same_family != "None":

    if same_family == "HRES + AIFS (Deterministic)":
        selected_models = [
            "ECMWF HRES",
        ]

    elif same_family == "HRES + EPS":
        selected_models = [
            "ECMWF HRES",
        ]

    else:
        selected_models = []

if not selected_models:
    st.warning(
        "Select at least one live deterministic model."
    )
    st.stop()


# ============================================================
# DOWNLOAD / PROCESS LIVE DATA
# ============================================================

model_windows = {}
model_status = {}

progress = st.progress(
    0,
    text="Preparing model data…",
)

for index, model in enumerate(
    selected_models
):

    try:

        progress.progress(
            int(
                100 *
                index /
                max(
                    len(selected_models),
                    1,
                )
            ),
            text=f"Loading {model}…",
        )

        raw = fetch_model_grid(
            model
        )

        processed = calculate_6hour_windows(
            raw
        )

        model_windows[model] = processed

        model_status[model] = (
            "Loaded"
        )

    except Exception as exc:

        model_status[model] = (
            f"Unavailable: {str(exc)[:140]}"
        )

progress.progress(
    100,
    text="Model processing complete.",
)


# ============================================================
# PERIOD OPTIONS
# ============================================================

available_models = list(
    model_windows.keys()
)

if not available_models:

    st.error(
        "No selected model could be loaded. "
        "Check the internet connection and API availability."
    )

    st.subheader(
        "Model Processing Status"
    )

    st.dataframe(
        pd.DataFrame(
            [
                [model, status]
                for model, status
                in model_status.items()
            ],
            columns=[
                "Model",
                "Status",
            ],
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.stop()


# Use the shortest common forecast horizon.
common_window_count = min(
    len(
        model_windows[m]["windows"]
    )
    for m in available_models
)

window_options = (
    model_windows[
        available_models[0]
    ]["windows"][:common_window_count]
)


# ============================================================
# PERIOD SLIDER
# ============================================================

with st.sidebar:

    max_period = max(
        0,
        len(window_options) - 1,
    )

    period_index = st.slider(
        "Forecast period",
        min_value=0,
        max_value=max_period,
        value=min(12, max_period),
        step=1,
        label_visibility="collapsed",
    )

    selected_start, selected_end = (
        window_options[period_index]
    )

    st.info(
        f"{selected_start.strftime('%d %b %Y %H:%M UTC')}"
        f" → "
        f"{selected_end.strftime('%d %b %Y %H:%M UTC')}"
    )

    st.markdown(
        "### Rainfall Transparency"
    )

    opacity = st.slider(
        "Opacity",
        min_value=0.10,
        max_value=1.00,
        value=0.70,
        step=0.05,
        label_visibility="collapsed",
    )

    st.divider()

    st.caption(
        "MasRainman • Weather for a Safer Tomorrow"
    )


# ============================================================
# MODEL REGRIDDING + BLENDING
# ============================================================

display_fields = []
model_display_fields = {}

for model in available_models:

    source_field = model_windows[
        model
    ]["rainfall"][
        :,
        :,
        period_index,
    ]

    display_field = interpolate_field(
        source_field
    )

    model_display_fields[
        model
    ] = display_field

    display_fields.append(
        display_field
    )

if display_fields:

    stack = np.stack(
        display_fields,
        axis=0,
    )

    blended_rain = np.nanmean(
        stack,
        axis=0,
    )

else:

    blended_rain = None


# ============================================================
# MAP + RIGHT PANEL
# ============================================================

map_col, panel_col = st.columns(
    [4.7, 1.3]
)

with map_col:

    fmap = create_map(
        blended_rain,
        opacity,
        show_boundaries,
    )

    st_folium(
        fmap,
        width=None,
        height=780,
        returned_objects=[],
    )


with panel_col:

    st.markdown(
        """
        <div class="info-card">
            <b>6-Hourly Rainfall</b><br>
            <span class="small-note">
            Forecast rainfall accumulation
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        f"""
        <div class="info-card">
            <b>Forecast Window</b><br>
            {selected_start.strftime('%d %b %Y')}<br>
            {selected_start.strftime('%I:%M %p')}
            –
            {selected_end.strftime('%I:%M %p')} UTC
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        "### Selected Models"
    )

    for model in selected_models:

        if model_status.get(model) == "Loaded":

            st.markdown(
                f"""
                <div class="model-card">
                    <div class="model-card-title">
                    ✓ {model}
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        else:

            st.warning(
                model
            )

    if blended_rain is not None:

        valid = blended_rain[
            np.isfinite(blended_rain)
        ]

        if len(valid):

            st.markdown(
                "### Rainfall Statistics"
            )

            st.metric(
                "Maximum",
                f"{np.nanmax(valid):.1f} mm",
            )

            st.metric(
                "Mean",
                f"{np.nanmean(valid):.1f} mm",
            )

            st.metric(
                "90th percentile",
                f"{np.nanpercentile(valid, 90):.1f} mm",
            )

            heavy_fraction = (
                np.sum(valid >= 25.0)
                / len(valid)
                * 100.0
            )

            st.metric(
                "≥25 mm / 6h",
                f"{heavy_fraction:.1f}% grid",
            )

    st.markdown(
        """
        <div class="info-card">
            <b>Product</b><br>
            6-hour accumulated rainfall
            <br><br>
            <b>Blend</b><br>
            Equal-weight selected model average
            <br><br>
            <b>Domain</b><br>
            South India / Sri Lanka
        </div>
        """,
        unsafe_allow_html=True,
    )


# ============================================================
# MODEL STATUS
# ============================================================

st.divider()

st.subheader(
    "Model Processing Status"
)

status_rows = []

for model in selected_models:

    status_rows.append([
        model,
        MODEL_CONFIG.get(
            model,
            {},
        ).get(
            "source",
            model,
        ),
        MODEL_CONFIG.get(
            model,
            {},
        ).get(
            "resolution",
            "",
        ),
        model_status.get(
            model,
            "Not processed",
        ),
    ])

st.dataframe(
    pd.DataFrame(
        status_rows,
        columns=[
            "Model",
            "Source",
            "Native / API Resolution",
            "Status",
        ],
    ),
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# DATA PIPELINE
# ============================================================

with st.expander(
    "How this rainfall product is calculated"
):

    st.markdown(
        """
### Live model pipeline

**1. Live model retrieval**

The application requests model-specific precipitation from the
Open-Meteo forecast API.

The selected deterministic sources are:

- ECMWF IFS
- NOAA GFS Global
- DWD ICON Global
- Canadian GEM Global

**2. Common regional grid**

The application samples the South India / Sri Lanka domain on a
0.25° retrieval grid.

**3. Six-hour accumulation**

The API supplies hourly precipitation accumulation. Six consecutive
hourly values are summed:

```text
6-hour rainfall =
hour 1 + hour 2 + hour 3 + hour 4 + hour 5 + hour 6
```

**4. Regridding**

Each model field is interpolated to the common 0.05° display grid.

**5. Equal-weight model blend**

For N selected deterministic models:

```text
MME =
(Model 1 + Model 2 + ... + Model N) / N
```

NaN cells are ignored.

**6. Map rendering**

The blended rainfall field is rendered as a transparent raster over
the Leaflet base map.

### Important

The current live version uses model-specific Open-Meteo access rather
than downloading raw GRIB files directly. It therefore does not require
`data/ecmwf.grib`, `data/gfs.grib2`, `data/icon.grib`, or `data/gem.grib2`.

It also uses the latest available forecast for each selected model,
rather than allowing independent raw-model run selection.
"""
    )


# ============================================================
# FOOTER
# ============================================================

st.markdown(
    """
<hr>
<div style="text-align:center;color:#777;font-size:12px;">
🌧️ <b>MasRainman</b> — Weather for a Safer Tomorrow<br>
6-Hourly Rainfall Model Blend
</div>
""",
    unsafe_allow_html=True,
)
