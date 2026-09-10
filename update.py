"""
MasRainman 6-hour rainfall updater.

Runs in GitHub Actions and writes data.json for the GitHub Pages viewer.
Uses Open-Meteo's model-specific forecast endpoints with batched coordinates.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# MasRainman display domain
LAT_MIN, LAT_MAX = 7.5, 14.5
LON_MIN, LON_MAX = 74.5, 82.5
GRID_STEP = 0.25
BATCH_SIZE = 200
FORECAST_DAYS = 3
PERIODS = 12  # 3 days x 4 six-hour periods

# Dedicated Open-Meteo endpoints.
# These correspond to the model-specific APIs:
# ECMWF IFS HRES, NOAA GFS, DWD ICON, and Canadian GEM Global.
MODELS = {
    "ECMWF HRES": "https://api.open-meteo.com/v1/ecmwf",
    "GFS": "https://api.open-meteo.com/v1/gfs",
    "ICON": "https://api.open-meteo.com/v1/dwd-icon",
    "GEM": "https://api.open-meteo.com/v1/gem",
}

OUT = Path("data.json")


def make_grid():
    lats = []
    lat = LAT_MIN
    while lat <= LAT_MAX + 1e-9:
        lats.append(round(lat, 2))
        lat += GRID_STEP

    lons = []
    lon = LON_MIN
    while lon <= LON_MAX + 1e-9:
        lons.append(round(lon, 2))
        lon += GRID_STEP

    return [(lat, lon) for lat in lats for lon in lons]


def fetch_json(url, params, retries=5):
    full = url + "?" + urlencode(params, doseq=True)

    for attempt in range(retries):
        try:
            req = Request(
                full,
                headers={
                    "User-Agent": "MasRainman/1.0 (non-commercial weather project)"
                },
            )
            with urlopen(req, timeout=90) as response:
                return json.loads(response.read().decode("utf-8"))

        except HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                if attempt == retries - 1:
                    raise
                wait = min(60, 5 * (2 ** attempt))
                print(f"HTTP {exc.code}; retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise

        except (URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise
            wait = min(60, 5 * (2 ** attempt))
            print(f"Network error {exc}; retrying in {wait}s...")
            time.sleep(wait)

    raise RuntimeError("Request failed")


def six_hour_totals(hourly_values):
    """
    Convert hourly precipitation into 12 consecutive 6-hour totals.

    The model APIs return hourly precipitation amounts in mm.
    """
    values = [
        float(v) if v is not None else 0.0
        for v in hourly_values[:FORECAST_DAYS * 24]
    ]

    # Pad if an endpoint returns fewer than expected hours.
    values += [0.0] * (FORECAST_DAYS * 24 - len(values))

    return [
        round(sum(values[i:i + 6]), 2)
        for i in range(0, FORECAST_DAYS * 24, 6)
    ]


def fetch_model(model_name, endpoint, points):
    result = {}

    for start in range(0, len(points), BATCH_SIZE):
        batch = points[start:start + BATCH_SIZE]
        lats = ",".join(str(p[0]) for p in batch)
        lons = ",".join(str(p[1]) for p in batch)

        params = {
            "latitude": lats,
            "longitude": lons,
            "hourly": "precipitation",
            "forecast_days": str(FORECAST_DAYS),
            "timezone": "UTC",
            "precipitation_unit": "mm",
        }

        print(
            f"{model_name}: request {start + 1}-"
            f"{min(start + BATCH_SIZE, len(points))} of {len(points)}"
        )

        payload = fetch_json(endpoint, params)

        # Multiple coordinates return a JSON list. A single point can return
        # one object, so handle both forms.
        responses = payload if isinstance(payload, list) else [payload]

        if len(responses) != len(batch):
            raise RuntimeError(
                f"{model_name}: expected {len(batch)} locations, "
                f"received {len(responses)}"
            )

        for point, item in zip(batch, responses):
            hourly = item.get("hourly", {}).get("precipitation")
            if hourly is None:
                raise RuntimeError(
                    f"{model_name}: precipitation missing from API response"
                )

            result[point] = six_hour_totals(hourly)

    return result


def main():
    points = make_grid()
    print(f"MasRainman grid: {len(points)} points at {GRID_STEP}°")

    all_models = {}

    for name, endpoint in MODELS.items():
        print(f"\nFetching {name}...")
        all_models[name] = fetch_model(name, endpoint, points)

    grid = []
    for lat, lon in points:
        grid.append(
            {
                "lat": lat,
                "lon": lon,
                "models": {
                    model: all_models[model][(lat, lon)]
                    for model in MODELS
                },
            }
        )

    now = datetime.now(timezone.utc)

    payload = {
        "updated": now.isoformat(),
        "source": "Open-Meteo model-specific APIs",
        "domain": {
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
        },
        "step": GRID_STEP,
        "periods": PERIODS,
        "period_labels": [
            f"{h:02d}–{h + 6:02d} h"
            for h in range(0, 72, 6)
        ],
        "models": list(MODELS.keys()),
        "grid": grid,
    }

    OUT.write_text(
        json.dumps(payload, separators=(",", ":")),
        encoding="utf-8",
    )

    print(f"\nWrote {OUT} ({OUT.stat().st_size / 1024:.1f} KB)")
    print(f"Grid points: {len(grid)}")
    print(f"Models: {', '.join(MODELS)}")


if __name__ == "__main__":
    main()
