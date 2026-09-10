"""
MasRainman Pan-India 6-hour rainfall updater.

Runs in GitHub Actions and creates data.json
for the GitHub Pages rainfall viewer.

Source:
Open-Meteo model-specific APIs

Models:
ECMWF HRES
GEM
GFS
ICON
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


# ============================================================
# CONFIGURATION
# ============================================================

MODELS = {
    "ECMWF HRES": "https://api.open-meteo.com/v1/ecmwf",
    "GEM": "https://api.open-meteo.com/v1/gem",
    "GFS": "https://api.open-meteo.com/v1/gfs",
    "ICON": "https://api.open-meteo.com/v1/dwd-icon",
}


# Pan-India + surrounding seas
LAT_MIN = 5.0
LAT_MAX = 38.0
LON_MIN = 65.0
LON_MAX = 100.0


# Source grid resolution
GRID_STEP = 0.25


# Open-Meteo supports multiple coordinates.
BATCH_SIZE = 200


# Forecast length
FORECAST_DAYS = 3


# Six-hour periods
PERIODS = FORECAST_DAYS * 4


# Retry configuration
MAX_RETRIES = 5
RETRY_DELAY = 5


# Output
OUTPUT = Path("data.json")


# ============================================================
# GRID
# ============================================================

def build_grid():
    """
    Create the Pan-India rainfall grid.
    """

    grid = []

    lat = LAT_MIN

    while lat <= LAT_MAX + 0.00001:

        lon = LON_MIN

        while lon <= LON_MAX + 0.00001:

            grid.append({
                "lat": round(lat, 2),
                "lon": round(lon, 2),
                "models": {}
            })

            lon += GRID_STEP

        lat += GRID_STEP

    return grid


# ============================================================
# HTTP
# ============================================================

def fetch_json(url, params):
    """
    Fetch JSON with retries for temporary API errors.
    """

    query = urlencode(params)

    full_url = url + "?" + query

    headers = {
        "User-Agent": "MasRainman/1.0 rainfall updater"
    }

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            request = Request(
                full_url,
                headers=headers
            )

            with urlopen(
                request,
                timeout=120
            ) as response:

                raw = response.read()

            return json.loads(raw)

        except HTTPError as error:

            status = error.code

            print(
                f"HTTP {status} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

            if status not in (429, 500, 502, 503, 504):
                raise

        except URLError as error:

            print(
                f"Network error: {error} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        except TimeoutError:

            print(
                f"Timeout "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )

        if attempt < MAX_RETRIES:

            delay = RETRY_DELAY * attempt

            print(
                f"Waiting {delay} seconds..."
            )

            time.sleep(delay)

    raise RuntimeError(
        f"Failed to fetch API after "
        f"{MAX_RETRIES} attempts"
    )


# ============================================================
# SIX-HOUR RAINFALL
# ============================================================

def six_hour_totals(hourly_precip):

    """
    Convert hourly precipitation into
    six-hour accumulated rainfall.

    0-6h
    6-12h
    12-18h
    ...
    """

    totals = []

    for period in range(PERIODS):

        start = period * 6
        end = start + 6

        values = hourly_precip[start:end]

        if not values:

            totals.append(None)
            continue

        valid = [
            float(value)
            for value in values
            if value is not None
        ]

        if not valid:

            totals.append(None)

        else:

            totals.append(
                round(sum(valid), 2)
            )

    return totals


# ============================================================
# PERIOD LABELS
# ============================================================

def make_period_labels():

    labels = []

    for period in range(PERIODS):

        start = period * 6
        end = start + 6

        labels.append(
            f"{start}–{end} h"
        )

    return labels


# ============================================================
# MODEL DOWNLOAD
# ============================================================

def update_model(model_name, endpoint, grid):

    print()
    print("=" * 60)
    print(f"Downloading {model_name}")
    print("=" * 60)

    total = len(grid)

    batches = [
        grid[i:i + BATCH_SIZE]
        for i in range(0, total, BATCH_SIZE)
    ]

    print(
        f"Grid points: {total}"
    )

    print(
        f"Requests: {len(batches)}"
    )

    for batch_number, batch in enumerate(
        batches,
        start=1
    ):

        print(
            f"{model_name}: "
            f"batch {batch_number}/{len(batches)}"
        )

        lats = ",".join(
            str(point["lat"])
            for point in batch
        )

        lons = ",".join(
            str(point["lon"])
            for point in batch
        )

        params = {

            "latitude": lats,

            "longitude": lons,

            "hourly": "precipitation",

            "forecast_days": FORECAST_DAYS,

            "timezone": "UTC",

            "temperature_unit": "celsius",

            "wind_speed_unit": "kmh",

            "precipitation_unit": "mm"
        }

        result = fetch_json(
            endpoint,
            params
        )

        # Open-Meteo returns one object for
        # one coordinate and a list for
        # multiple coordinates.
        results = result

        if not isinstance(
            results,
            list
        ):

            results = [results]

        if len(results) != len(batch):

            raise RuntimeError(
                f"{model_name}: API returned "
                f"{len(results)} locations "
                f"for {len(batch)} requested"
            )

        for point, location in zip(
            batch,
            results
        ):

            hourly = location.get(
                "hourly",
                {}
            )

            precipitation = hourly.get(
                "precipitation",
                []
            )

            point["models"][model_name] = (
                six_hour_totals(
                    precipitation
                )
            )

        # Small delay between requests.
        time.sleep(0.2)

    print(
        f"{model_name} completed."
    )


# ============================================================
# MAIN
# ============================================================

def main():

    started = datetime.now(
        timezone.utc
    )

    print()
    print("=" * 60)
    print("MASRAINMAN RAINFALL UPDATER")
    print("=" * 60)

    print(
        f"Started: {started.isoformat()}"
    )

    print(
        f"Domain: "
        f"{LAT_MIN}–{LAT_MAX} N, "
        f"{LON_MIN}–{LON_MAX} E"
    )

    print(
        f"Grid step: {GRID_STEP}°"
    )

    print(
        f"Forecast: {FORECAST_DAYS} days"
    )

    grid = build_grid()

    print(
        f"Grid points: {len(grid)}"
    )

    for model_name, endpoint in MODELS.items():

        update_model(
            model_name,
            endpoint,
            grid
        )

    updated = datetime.now(
        timezone.utc
    )

    output = {

        "updated": updated.isoformat(),

        "source":
            "Open-Meteo model-specific APIs",

        "domain": {

            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lon_min": LON_MIN,
            "lon_max": LON_MAX
        },

        "step": GRID_STEP,

        "forecast_days":
            FORECAST_DAYS,

        "periods":
            make_period_labels(),

        "period_hours": 6,

        "models":
            list(MODELS.keys()),

        "grid":
            grid
    }

    with OUTPUT.open(
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            output,
            file,
            separators=(",", ":")
        )

    print()
    print("=" * 60)
    print("UPDATE COMPLETE")
    print("=" * 60)

    print(
        f"Updated: {updated.isoformat()}"
    )

    print(
        f"Grid points: {len(grid)}"
    )

    print(
        f"Output: {OUTPUT}"
    )


if __name__ == "__main__":
    main()
