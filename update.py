import json
import math
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# MASRAINMAN RAINFALL UPDATER
# Pan-India / 0.50 degree / 3 days / 6-hour rainfall
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

# 0.50 degree source grid
GRID_STEP = 0.50

# Larger coordinate batches = fewer HTTP requests
BATCH_SIZE = 500

# 3 days
FORECAST_DAYS = 3

# 4 x 6-hour periods per day
PERIODS = FORECAST_DAYS * 4

# Retry configuration
MAX_RETRIES = 8

# Delay between successful API requests
REQUEST_DELAY = 5.0

# Minimum wait after HTTP 429
RATE_LIMIT_DELAY = 60.0

# Maximum retry wait
MAX_RETRY_DELAY = 300.0

OUTPUT = Path("data.json")


# ============================================================
# GRID
# ============================================================

def build_grid():
    grid = []

    lat = LAT_MIN
    while lat <= LAT_MAX + 1e-9:
        lon = LON_MIN

        while lon <= LON_MAX + 1e-9:
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

def fetch_json(url):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "MasRainman/1.0"
                }
            )

            with urllib.request.urlopen(
                request,
                timeout=90
            ) as response:

                raw = response.read()
                return json.loads(raw.decode("utf-8"))

        except urllib.error.HTTPError as exc:

            last_error = exc

            if exc.code == 429:

                # Respect Retry-After when supplied
                retry_after = exc.headers.get("Retry-After")

                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = RATE_LIMIT_DELAY

                wait = max(wait, RATE_LIMIT_DELAY)
                wait = min(wait, MAX_RETRY_DELAY)

                print(
                    f"HTTP 429 (attempt {attempt}/{MAX_RETRIES})"
                )
                print(
                    f"Rate limited. Waiting {int(wait)} seconds..."
                )

                time.sleep(wait)

                continue

            if exc.code in (500, 502, 503, 504):

                wait = min(
                    30 * (2 ** (attempt - 1)),
                    MAX_RETRY_DELAY
                )

                print(
                    f"HTTP {exc.code} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                print(
                    f"Waiting {int(wait)} seconds..."
                )

                time.sleep(wait)

                continue

            raise

        except Exception as exc:

            last_error = exc

            wait = min(
                15 * (2 ** (attempt - 1)),
                MAX_RETRY_DELAY
            )

            print(
                f"Network error: {exc} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            print(
                f"Waiting {int(wait)} seconds..."
            )

            time.sleep(wait)

    raise RuntimeError(
        f"Failed to fetch API after "
        f"{MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# URL
# ============================================================

def build_url(endpoint, points):

    latitudes = ",".join(
        f"{p['lat']:.2f}" for p in points
    )

    longitudes = ",".join(
        f"{p['lon']:.2f}" for p in points
    )

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": "precipitation",
        "forecast_days": str(FORECAST_DAYS),
        "timezone": "GMT",
        "cell_selection": "nearest",
    }

    return endpoint + "?" + urllib.parse.urlencode(
        params,
        safe=","
    )


# ============================================================
# RAINFALL CONVERSION
# ============================================================

def six_hour_totals(values):
    """
    Convert hourly precipitation into 12 x 6-hour totals.
    """

    result = []

    for period in range(PERIODS):

        start = period * 6
        end = start + 6

        chunk = values[start:end]

        if len(chunk) < 6:
            result.append(None)
            continue

        total = 0.0

        for value in chunk:
            if value is not None:
                try:
                    total += float(value)
                except (TypeError, ValueError):
                    pass

        result.append(round(total, 2))

    return result


# ============================================================
# MODEL UPDATE
# ============================================================

def update_model(model_name, endpoint, grid):

    print()
    print("=" * 60)
    print(f"Downloading {model_name}")
    print("=" * 60)

    batches = [
        grid[i:i + BATCH_SIZE]
        for i in range(0, len(grid), BATCH_SIZE)
    ]

    print(f"Grid points: {len(grid)}")
    print(f"Batch size: {BATCH_SIZE}")
    print(f"Requests: {len(batches)}")

    for batch_number, batch in enumerate(batches, start=1):

        print(
            f"{model_name}: "
            f"batch {batch_number}/{len(batches)}"
        )

        url = build_url(endpoint, batch)

        result = fetch_json(url)

        # Multiple coordinates return a list.
        # A single coordinate returns one dictionary.
        if isinstance(result, list):
            locations = result
        else:
            locations = [result]

        if len(locations) != len(batch):

            raise RuntimeError(
                f"{model_name}: API returned "
                f"{len(locations)} locations for "
                f"{len(batch)} requested points"
            )

        for point, location in zip(batch, locations):

            hourly = location.get("hourly", {})
            precipitation = hourly.get(
                "precipitation",
                []
            )

            point["models"][model_name] = (
                six_hour_totals(precipitation)
            )

        # Slow down slightly between requests
        if batch_number < len(batches):
            time.sleep(REQUEST_DELAY)

    print(f"{model_name} completed.")


# ============================================================
# MAIN
# ============================================================

def main():

    started = datetime.now(
        timezone.utc
    ).isoformat()

    print("=" * 60)
    print("MASRAINMAN RAINFALL UPDATER")
    print("=" * 60)

    print(f"Started: {started}")

    print(
        f"Domain: "
        f"{LAT_MIN}–{LAT_MAX} N, "
        f"{LON_MIN}–{LON_MAX} E"
    )

    print(
        f"Grid step: {GRID_STEP}°"
    )

    print(
        f"Batch size: {BATCH_SIZE}"
    )

    print(
        f"Forecast: {FORECAST_DAYS} days"
    )

    grid = build_grid()

    print(
        f"Grid points: {len(grid)}"
    )

    print("=" * 60)

    # Download each model
    for model_name, endpoint in MODELS.items():

        update_model(
            model_name,
            endpoint,
            grid
        )

    # ========================================================
    # OUTPUT
    # ========================================================

    updated = datetime.now(
        timezone.utc
    ).isoformat()

    periods = []

    for i in range(PERIODS):

        start = i * 6
        end = start + 6

        periods.append(
            f"{start}–{end} h"
        )

    output = {
        "updated": updated,

        "source": (
            "Open-Meteo model-specific APIs"
        ),

        "domain": {
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
        },

        "step": GRID_STEP,

        "periods": periods,

        "models": list(MODELS.keys()),

        "grid": grid,
    }

    print()
    print("=" * 60)
    print("Writing data.json")
    print("=" * 60)

    with OUTPUT.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            separators=(",", ":")
        )

    size_kb = OUTPUT.stat().st_size / 1024

    print(
        f"data.json written successfully "
        f"({size_kb:.1f} KB)"
    )

    print()
    print("=" * 60)
    print("MASRAINMAN UPDATE COMPLETE")
    print("=" * 60)

    print(
        f"Updated: {updated}"
    )


if __name__ == "__main__":
    main()
