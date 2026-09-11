import json
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path


# ============================================================
# MASRAINMAN RAINFALL UPDATER
#
# Pan-India
# 1.0 degree API source grid
# 0.5 degree interpolated display grid
# 3 days
# 6-hour rainfall
# ECMWF / GEM / GFS / ICON
# ============================================================


MODELS = {
    "ECMWF HRES": "https://api.open-meteo.com/v1/ecmwf",
    "GEM": "https://api.open-meteo.com/v1/gem",
    "GFS": "https://api.open-meteo.com/v1/gfs",
    "ICON": "https://api.open-meteo.com/v1/dwd-icon",
}


# ============================================================
# DOMAIN
# ============================================================

LAT_MIN = 5.0
LAT_MAX = 38.0

LON_MIN = 65.0
LON_MAX = 100.0


# API source grid
SOURCE_STEP = 1.0

# Final display grid
DISPLAY_STEP = 0.5


# Keep requests comfortably sized
BATCH_SIZE = 300


FORECAST_DAYS = 3
PERIODS = FORECAST_DAYS * 4


MAX_RETRIES = 8

REQUEST_DELAY = 8.0

RATE_LIMIT_DELAY = 90.0

MAX_RETRY_DELAY = 300.0


OUTPUT = Path("data.json")


# ============================================================
# BUILD GRID
# ============================================================

def build_grid(step):

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

            lon += step

        lat += step

    return grid


# ============================================================
# HTTP FETCH
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
                timeout=120
            ) as response:

                raw = response.read()

                return json.loads(
                    raw.decode("utf-8")
                )


        except urllib.error.HTTPError as exc:

            last_error = exc

            if exc.code == 429:

                retry_after = exc.headers.get(
                    "Retry-After"
                )

                try:
                    wait = float(retry_after)
                except (TypeError, ValueError):
                    wait = RATE_LIMIT_DELAY

                wait = max(
                    wait,
                    RATE_LIMIT_DELAY
                )

                wait = min(
                    wait,
                    MAX_RETRY_DELAY
                )

                print(
                    f"HTTP 429 "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )

                print(
                    f"Rate limited. "
                    f"Waiting {int(wait)} seconds..."
                )

                time.sleep(wait)

                continue


            if exc.code in (
                500,
                502,
                503,
                504
            ):

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
                20 * (2 ** (attempt - 1)),
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
        f"{MAX_RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# BUILD API URL
# ============================================================

def build_url(endpoint, points):

    latitudes = ",".join(
        f"{p['lat']:.2f}"
        for p in points
    )

    longitudes = ",".join(
        f"{p['lon']:.2f}"
        for p in points
    )

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": "precipitation",
        "forecast_days": str(
            FORECAST_DAYS
        ),
        "timezone": "GMT",
        "cell_selection": "nearest",
    }

    return (
        endpoint
        + "?"
        + urllib.parse.urlencode(
            params,
            safe=","
        )
    )


# ============================================================
# CONVERT HOURLY TO 6-HOUR TOTALS
# ============================================================

def six_hour_totals(values):

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

            if value is None:
                continue

            try:
                total += float(value)
            except (TypeError, ValueError):
                pass

        result.append(
            round(total, 2)
        )

    return result


# ============================================================
# DOWNLOAD ONE MODEL
# ============================================================

def update_model(
    model_name,
    endpoint,
    grid
):

    print()
    print("=" * 60)
    print(f"Downloading {model_name}")
    print("=" * 60)

    batches = [
        grid[i:i + BATCH_SIZE]
        for i in range(
            0,
            len(grid),
            BATCH_SIZE
        )
    ]

    print(
        f"Grid points: {len(grid)}"
    )

    print(
        f"Batch size: {BATCH_SIZE}"
    )

    print(
        f"Requests: {len(batches)}"
    )

    for number, batch in enumerate(
        batches,
        start=1
    ):

        print(
            f"{model_name}: "
            f"batch {number}/{len(batches)}"
        )

        url = build_url(
            endpoint,
            batch
        )

        result = fetch_json(url)

        if isinstance(result, list):
            locations = result
        else:
            locations = [result]

        if len(locations) != len(batch):

            raise RuntimeError(
                f"{model_name}: API returned "
                f"{len(locations)} locations "
                f"for {len(batch)} requested"
            )

        for point, location in zip(
            batch,
            locations
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

        if number < len(batches):

            time.sleep(
                REQUEST_DELAY
            )

    print(
        f"{model_name} completed."
    )


# ============================================================
# CREATE LOOKUP TABLE
# ============================================================

def make_lookup(grid):

    lookup = {}

    for point in grid:

        key = (
            round(point["lat"], 2),
            round(point["lon"], 2)
        )

        lookup[key] = point

    return lookup


# ============================================================
# BILINEAR INTERPOLATION
# ============================================================

def interpolate_value(
    lookup,
    lat,
    lon,
    model,
    period
):

    # Source-grid lower-left coordinate

    lat0 = int(
        (lat - LAT_MIN)
        // SOURCE_STEP
    ) * SOURCE_STEP + LAT_MIN

    lon0 = int(
        (lon - LON_MIN)
        // SOURCE_STEP
    ) * SOURCE_STEP + LON_MIN

    lat0 = round(
        max(
            LAT_MIN,
            min(
                LAT_MAX - SOURCE_STEP,
                lat0
            )
        ),
        2
    )

    lon0 = round(
        max(
            LON_MIN,
            min(
                LON_MAX - SOURCE_STEP,
                lon0
            )
        ),
        2
    )

    lat1 = round(
        lat0 + SOURCE_STEP,
        2
    )

    lon1 = round(
        lon0 + SOURCE_STEP,
        2
    )


    points = []

    for la, lo in [
        (lat0, lon0),
        (lat0, lon1),
        (lat1, lon0),
        (lat1, lon1),
    ]:

        point = lookup.get(
            (round(la, 2), round(lo, 2))
        )

        if point is None:
            return None

        values = (
            point
            .get("models", {})
            .get(model)
        )

        if (
            values is None
            or period >= len(values)
        ):
            return None

        value = values[period]

        if value is None:
            return None

        points.append(
            float(value)
        )


    q11, q12, q21, q22 = points


    # Fractional position

    if SOURCE_STEP == 0:
        return q11

    x = (
        lon - lon0
    ) / SOURCE_STEP

    y = (
        lat - lat0
    ) / SOURCE_STEP


    value = (
        q11 * (1 - x) * (1 - y)
        + q12 * x * (1 - y)
        + q21 * (1 - x) * y
        + q22 * x * y
    )


    return round(
        max(0.0, value),
        2
    )


# ============================================================
# CREATE FINAL 0.5 DEGREE GRID
# ============================================================

def interpolate_grid(
    source_grid
):

    print()
    print("=" * 60)
    print("Creating 0.5° display grid")
    print("=" * 60)

    lookup = make_lookup(
        source_grid
    )

    display_grid = build_grid(
        DISPLAY_STEP
    )

    models = list(
        MODELS.keys()
    )

    for point in display_grid:

        for model in models:

            values = []

            for period in range(
                PERIODS
            ):

                value = interpolate_value(
                    lookup,
                    point["lat"],
                    point["lon"],
                    model,
                    period
                )

                values.append(
                    value
                )

            point["models"][model] = (
                values
            )

    print(
        f"Display points: "
        f"{len(display_grid)}"
    )

    return display_grid


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

    print(
        f"Started: {started}"
    )

    print(
        f"Domain: "
        f"{LAT_MIN}–{LAT_MAX} N, "
        f"{LON_MIN}–{LON_MAX} E"
    )

    print(
        f"API grid: "
        f"{SOURCE_STEP}°"
    )

    print(
        f"Display grid: "
        f"{DISPLAY_STEP}°"
    )

    print(
        f"Batch size: "
        f"{BATCH_SIZE}"
    )

    print(
        f"Forecast: "
        f"{FORECAST_DAYS} days"
    )

    source_grid = build_grid(
        SOURCE_STEP
    )

    print(
        f"API grid points: "
        f"{len(source_grid)}"
    )

    print("=" * 60)


    # --------------------------------------------------------
    # Download four models
    # --------------------------------------------------------

    for model_name, endpoint in (
        MODELS.items()
    ):

        update_model(
            model_name,
            endpoint,
            source_grid
        )


    # --------------------------------------------------------
    # Interpolate
    # --------------------------------------------------------

    display_grid = interpolate_grid(
        source_grid
    )


    # --------------------------------------------------------
    # Period labels
    # --------------------------------------------------------

    periods = []

    for i in range(PERIODS):

        start = i * 6
        end = start + 6

        periods.append(
            f"{start}–{end} h"
        )


    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    updated = datetime.now(
        timezone.utc
    ).isoformat()

    output = {

        "updated": updated,

        "source": (
            "Open-Meteo model-specific APIs; "
            "1° source grid interpolated "
            "to 0.5° display grid"
        ),

        "domain": {

            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,

            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
        },

        "step": DISPLAY_STEP,

        "source_step": SOURCE_STEP,

        "periods": periods,

        "models": list(
            MODELS.keys()
        ),

        "grid": display_grid,
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


    size_kb = (
        OUTPUT.stat().st_size
        / 1024
    )


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
