"""
MasRainman Pan-India 6-hour rainfall updater.

Free GitHub Actions + Open-Meteo version.

Creates:
    data.json

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
# MODELS
# ============================================================

MODELS = {
    "ECMWF HRES":
        "https://api.open-meteo.com/v1/ecmwf",

    "GEM":
        "https://api.open-meteo.com/v1/gem",

    "GFS":
        "https://api.open-meteo.com/v1/gfs",

    "ICON":
        "https://api.open-meteo.com/v1/dwd-icon",
}


# ============================================================
# PAN-INDIA DOMAIN
# ============================================================

LAT_MIN = 5.0
LAT_MAX = 38.0

LON_MIN = 65.0
LON_MAX = 100.0


# ============================================================
# GRID
# ============================================================

# 0.50 degree grid.
#
# This is intentionally coarser than the previous 0.25 degree
# grid so that the free API remains reliable.

GRID_STEP = 0.50


# ============================================================
# REQUEST SETTINGS
# ============================================================

BATCH_SIZE = 200

FORECAST_DAYS = 3

PERIODS = FORECAST_DAYS * 4

MAX_RETRIES = 8

BASE_RETRY_DELAY = 10

REQUEST_DELAY = 3.0


# ============================================================
# OUTPUT
# ============================================================

OUTPUT = Path("data.json")


# ============================================================
# BUILD GRID
# ============================================================

def build_grid():

    grid = []

    lat = LAT_MIN

    while lat <= LAT_MAX + 0.00001:

        lon = LON_MIN

        while lon <= LON_MAX + 0.00001:

            grid.append(
                {
                    "lat": round(lat, 2),
                    "lon": round(lon, 2),
                    "models": {}
                }
            )

            lon += GRID_STEP

        lat += GRID_STEP

    return grid


# ============================================================
# FETCH JSON
# ============================================================

def fetch_json(url, params):

    query = urlencode(params)

    full_url = url + "?" + query

    headers = {
        "User-Agent":
            "MasRainman/1.0 rainfall updater"
    }

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            request = Request(
                full_url,
                headers=headers
            )

            with urlopen(
                request,
                timeout=180
            ) as response:

                raw = response.read()

            return json.loads(raw)

        except HTTPError as error:

            status = error.code

            print(
                f"HTTP {status} "
                f"(attempt "
                f"{attempt}/{MAX_RETRIES})",
                flush=True
            )

            if status not in (
                429,
                500,
                502,
                503,
                504
            ):
                raise

        except URLError as error:

            print(
                f"Network error: {error} "
                f"(attempt "
                f"{attempt}/{MAX_RETRIES})",
                flush=True
            )

        except TimeoutError:

            print(
                f"Timeout "
                f"(attempt "
                f"{attempt}/{MAX_RETRIES})",
                flush=True
            )

        if attempt < MAX_RETRIES:

            # Exponential backoff.
            #
            # 10, 20, 40, 80...
            #
            # This gives Open-Meteo time to recover
            # from a temporary rate limit.

            delay = min(
                BASE_RETRY_DELAY * (2 ** (attempt - 1)),
                120
            )

            print(
                f"Waiting {delay} seconds...",
                flush=True
            )

            time.sleep(delay)

    raise RuntimeError(
        "Failed to fetch API after "
        f"{MAX_RETRIES} attempts"
    )


# ============================================================
# SIX-HOUR TOTALS
# ============================================================

def six_hour_totals(hourly_precip):

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
                round(
                    sum(valid),
                    2
                )
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
# UPDATE ONE MODEL
# ============================================================

def update_model(
    model_name,
    endpoint,
    grid
):

    print()

    print(
        "=" * 60,
        flush=True
    )

    print(
        f"Downloading {model_name}",
        flush=True
    )

    print(
        "=" * 60,
        flush=True
    )

    total = len(grid)

    batches = [

        grid[
            i:i + BATCH_SIZE
        ]

        for i in range(
            0,
            total,
            BATCH_SIZE
        )
    ]

    print(
        f"Grid points: {total}",
        flush=True
    )

    print(
        f"Requests: {len(batches)}",
        flush=True
    )

    for batch_number, batch in enumerate(
        batches,
        start=1
    ):

        print(
            f"{model_name}: "
            f"batch "
            f"{batch_number}/"
            f"{len(batches)}",
            flush=True
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

            "forecast_days":
                FORECAST_DAYS,

            "timezone": "UTC",

            "precipitation_unit":
                "mm"
        }

        result = fetch_json(
            endpoint,
            params
        )

        if isinstance(
            result,
            list
        ):

            results = result

        else:

            results = [result]

        if len(results) != len(batch):

            raise RuntimeError(

                f"{model_name}: API returned "

                f"{len(results)} locations "

                f"for "

                f"{len(batch)} requested"
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

        # Deliberate pause between requests.
        #
        # This is important for the free API.

        if (
            batch_number <
            len(batches)
        ):

            time.sleep(
                REQUEST_DELAY
            )

    print(
        f"{model_name} completed.",
        flush=True
    )


# ============================================================
# MAIN
# ============================================================

def main():

    started = datetime.now(
        timezone.utc
    )

    print()

    print(
        "=" * 60
    )

    print(
        "MASRAINMAN RAINFALL UPDATER"
    )

    print(
        "=" * 60
    )

    print(
        f"Started: "
        f"{started.isoformat()}"
    )

    print(
        f"Domain: "
        f"{LAT_MIN}–{LAT_MAX} N, "
        f"{LON_MIN}–{LON_MAX} E"
    )

    print(
        f"Grid step: "
        f"{GRID_STEP}°"
    )

    print(
        f"Forecast: "
        f"{FORECAST_DAYS} days"
    )

    grid = build_grid()

    print(
        f"Grid points: "
        f"{len(grid)}"
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

        "updated":
            updated.isoformat(),

        "source":
            "Open-Meteo model-specific APIs",

        "domain": {

            "lat_min":
                LAT_MIN,

            "lat_max":
                LAT_MAX,

            "lon_min":
                LON_MIN,

            "lon_max":
                LON_MAX
        },

        "step":
            GRID_STEP,

        "forecast_days":
            FORECAST_DAYS,

        "periods":
            make_period_labels(),

        "period_hours":
            6,

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

    print(
        "=" * 60
    )

    print(
        "UPDATE COMPLETE"
    )

    print(
        "=" * 60
    )

    print(
        f"Updated: "
        f"{updated.isoformat()}"
    )

    print(
        f"Grid points: "
        f"{len(grid)}"
    )

    print(
        f"Output: "
        f"{OUTPUT}"
    )


if __name__ == "__main__":

    main()
