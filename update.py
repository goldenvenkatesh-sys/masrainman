import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


# ============================================================
# MASRAINMAN RAINFALL DATA UPDATER
# ============================================================
#
# Creates 12 forecast days of rainfall data.
#
# User interface:
#   Day 1
#   Day 2
#   ...
#   Day 12
#
# Internally:
#   Each day = four 6-hour rainfall periods
#   00–06 UTC
#   06–12 UTC
#   12–18 UTC
#   18–24 UTC
#
# The four periods are summed to produce the 24-hour
# rainfall total for each forecast day.
#
# Models:
#   ECMWF HRES
#   GEM
#   GFS
#   ICON
#
# Grid:
#   Source grid  = 1.0 degree
#   Display grid = 0.5 degree
#
# No third-party Python package is required.
# Uses Python standard library only.
# ============================================================


# ============================================================
# PATHS
# ============================================================

BASE = Path(__file__).resolve().parent
OUT = BASE / "data.json"


# ============================================================
# DOMAIN
# ============================================================

LAT_MIN = 5.0
LAT_MAX = 38.0

LON_MIN = 65.0
LON_MAX = 100.0


# ============================================================
# GRID
# ============================================================

SOURCE_STEP = 1.0
DISPLAY_STEP = 0.5


# ============================================================
# FORECAST
# ============================================================

FORECAST_DAYS = 12

PERIODS_PER_DAY = 4
TOTAL_PERIODS = FORECAST_DAYS * PERIODS_PER_DAY


# ============================================================
# DOWNLOAD SETTINGS
# ============================================================

BATCH_SIZE = 500

REQUEST_TIMEOUT = 90

RETRIES = 5


# ============================================================
# OPEN-METEO MODEL APIs
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
# MODEL FORECAST HORIZONS
# ============================================================
#
# We request only the amount of data each model can provide.
#
# ECMWF:
#   12 days requested
#
# GFS:
#   12 days requested
#
# GEM:
#   10 days
#
# ICON:
#   7 days
#
# The browser will show unavailable days as unavailable
# instead of inventing rainfall values.
# ============================================================

MODEL_HORIZONS = {
    "ECMWF HRES": 12,
    "GEM": 10,
    "GFS": 12,
    "ICON": 7,
}


# ============================================================
# GRID HELPER
# ============================================================

def frange(start, stop, step):
    """
    Generate a floating-point range including the end point.
    """

    n = int(round((stop - start) / step))

    return [
        round(start + i * step, 6)
        for i in range(n + 1)
    ]


# ============================================================
# HTTP / JSON DOWNLOAD
# ============================================================

def request_json(url, params):
    """
    Download JSON using Python standard library.

    No requests package is required.

    Includes retry handling for:
      - HTTP 429
      - HTTP 500
      - HTTP 502
      - HTTP 503
      - HTTP 504
      - network errors
      - timeouts
      - invalid JSON
    """

    query = urlencode(params)

    full_url = f"{url}?{query}"

    last_error = None

    for attempt in range(1, RETRIES + 1):

        try:

            req = Request(
                full_url,
                headers={
                    "User-Agent":
                        "MasRainman/1.0 "
                        "(GitHub Actions rainfall updater)"
                },
                method="GET",
            )

            with urlopen(
                req,
                timeout=REQUEST_TIMEOUT
            ) as response:

                raw = response.read()

                text = raw.decode("utf-8")

                return json.loads(text)

        except HTTPError as exc:

            last_error = exc

            # ------------------------------------------------
            # Rate limit
            # ------------------------------------------------

            if exc.code == 429:

                wait = min(
                    60,
                    5 * attempt
                )

                print(
                    f"429 rate limit; "
                    f"waiting {wait}s "
                    f"(attempt {attempt}/{RETRIES})"
                )

                time.sleep(wait)

                continue

            # ------------------------------------------------
            # Temporary server errors
            # ------------------------------------------------

            if exc.code in (
                500,
                502,
                503,
                504,
            ):

                wait = min(
                    30,
                    3 * attempt
                )

                print(
                    f"HTTP {exc.code}; "
                    f"waiting {wait}s "
                    f"(attempt {attempt}/{RETRIES})"
                )

                time.sleep(wait)

                continue

            raise RuntimeError(
                f"HTTP {exc.code}: {exc.reason}"
            ) from exc

        except (
            URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:

            last_error = exc

            wait = min(
                30,
                3 * attempt
            )

            print(
                f"Request failed: {exc}; "
                f"waiting {wait}s "
                f"(attempt {attempt}/{RETRIES})"
            )

            time.sleep(wait)

    raise RuntimeError(
        f"Request failed after "
        f"{RETRIES} attempts: "
        f"{last_error}"
    )


# ============================================================
# OPEN-METEO RESPONSE NORMALIZATION
# ============================================================

def parse_locations(payload):
    """
    Multiple-coordinate Open-Meteo requests normally return
    a list of location objects.

    A single location is also accepted.
    """

    if isinstance(payload, list):
        return payload

    return [payload]


# ============================================================
# SIX-HOUR RAINFALL CALCULATION
# ============================================================

def six_hour_totals(
    payload,
    max_periods
):
    """
    Convert hourly precipitation into 6-hour totals.

    Each 6-hour period contains six hourly values.

    We use:

        01–06
        07–12
        13–18
        19–24

    for the first UTC day.

    This corresponds to the precipitation accumulated
    through the end of each hourly timestamp.
    """

    hourly = payload.get(
        "hourly",
        {}
    )

    values = hourly.get(
        "precipitation"
    ) or []

    times = hourly.get(
        "time"
    ) or []

    periods = []

    for p in range(max_periods):

        start = 1 + p * 6

        end = start + 6

        block = values[start:end]

        # ----------------------------------------------------
        # Not enough forecast data
        # ----------------------------------------------------

        if len(block) < 6:

            periods.append(None)

            continue

        total = 0.0

        for value in block:

            if value is None:
                value = 0.0

            total += float(value)

        periods.append(
            round(total, 2)
        )

    return periods, times


# ============================================================
# FETCH ONE MODEL
# ============================================================

def fetch_model(
    model_name,
    url,
    source_points
):
    """
    Download one model for all source-grid points.
    """

    print()
    print("=" * 60)
    print(f"MODEL: {model_name}")
    print("=" * 60)

    result = {}

    model_days = MODEL_HORIZONS[
        model_name
    ]

    max_periods = (
        model_days *
        PERIODS_PER_DAY
    )

    total_batches = math.ceil(
        len(source_points) /
        BATCH_SIZE
    )

    for start in range(
        0,
        len(source_points),
        BATCH_SIZE
    ):

        batch = source_points[
            start:start + BATCH_SIZE
        ]

        batch_number = (
            start // BATCH_SIZE
        ) + 1

        print(
            f"{model_name}: "
            f"batch {batch_number}/"
            f"{total_batches} "
            f"({len(batch)} points)"
        )

        lats = ",".join(
            str(point[0])
            for point in batch
        )

        lons = ",".join(
            str(point[1])
            for point in batch
        )

        params = {
            "latitude": lats,
            "longitude": lons,
            "hourly": "precipitation",
            "forecast_days": model_days,
            "timezone": "UTC",
            "precipitation_unit": "mm",
        }

        payload = request_json(
            url,
            params
        )

        locations = parse_locations(
            payload
        )

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

            periods, times = six_hour_totals(
                location,
                max_periods
            )

            key = (
                point[0],
                point[1]
            )

            result[key] = {
                "values": periods,
                "times": times,
            }

    print(
        f"{model_name}: download complete"
    )

    return result


# ============================================================
# BILINEAR INTERPOLATION
# ============================================================

def bilinear(
    v00,
    v10,
    v01,
    v11,
    fx,
    fy
):

    return (
        v00 * (1 - fx) * (1 - fy)
        + v10 * fx * (1 - fy)
        + v01 * (1 - fx) * fy
        + v11 * fx * fy
    )


# ============================================================
# INTERPOLATE ONE VALUE
# ============================================================

def interpolate_value(
    src,
    lats,
    lons,
    lat,
    lon,
    period
):
    """
    Interpolate a source-grid rainfall value
    onto the 0.5-degree display grid.
    """

    # --------------------------------------------------------
    # Clamp coordinates
    # --------------------------------------------------------

    lat = min(
        max(lat, lats[0]),
        lats[-1]
    )

    lon = min(
        max(lon, lons[0]),
        lons[-1]
    )

    # --------------------------------------------------------
    # Position inside source grid
    # --------------------------------------------------------

    lat_pos = (
        lat - lats[0]
    ) / SOURCE_STEP

    lon_pos = (
        lon - lons[0]
    ) / SOURCE_STEP

    j0 = min(
        int(math.floor(lat_pos)),
        len(lats) - 2
    )

    i0 = min(
        int(math.floor(lon_pos)),
        len(lons) - 2
    )

    fy = lat_pos - j0

    fx = lon_pos - i0

    # --------------------------------------------------------
    # Get source value
    # --------------------------------------------------------

    def value(j, i):

        arr = src.get(
            (
                lats[j],
                lons[i]
            )
        )

        if not arr:
            return None

        if period >= len(arr):
            return None

        v = arr[period]

        if v is None:
            return None

        return float(v)

    v00 = value(j0, i0)

    v10 = value(j0, i0 + 1)

    v01 = value(j0 + 1, i0)

    v11 = value(j0 + 1, i0 + 1)

    vals = [
        v00,
        v10,
        v01,
        v11,
    ]

    # --------------------------------------------------------
    # No forecast available
    # --------------------------------------------------------

    available = [
        v
        for v in vals
        if v is not None
    ]

    if not available:

        return None

    # --------------------------------------------------------
    # Missing corner(s)
    #
    # Example:
    # GEM after its forecast horizon.
    # --------------------------------------------------------

    if len(available) < 4:

        return round(
            sum(available) /
            len(available),
            2
        )

    # --------------------------------------------------------
    # Normal bilinear interpolation
    # --------------------------------------------------------

    return round(
        bilinear(
            v00,
            v10,
            v01,
            v11,
            fx,
            fy,
        ),
        2
    )


# ============================================================
# BUILD DAY TOTAL
# ============================================================

def build_day_total(
    model_source,
    source_lats,
    source_lons,
    lat,
    lon,
    day
):
    """
    Sum four 6-hour periods into one 24-hour day.

    Day 1:
        periods 0,1,2,3

    Day 2:
        periods 4,5,6,7

    ...

    Day 12:
        periods 44,45,46,47
    """

    first_period = (
        day *
        PERIODS_PER_DAY
    )

    periods = range(
        first_period,
        first_period +
        PERIODS_PER_DAY
    )

    day_values = []

    for period in periods:

        value = interpolate_value(
            model_source,
            source_lats,
            source_lons,
            lat,
            lon,
            period
        )

        day_values.append(
            value
        )

    # --------------------------------------------------------
    # Entire day unavailable
    # --------------------------------------------------------

    if all(
        value is None
        for value in day_values
    ):

        return None

    # --------------------------------------------------------
    # Sum available periods
    # --------------------------------------------------------

    total = sum(
        value or 0.0
        for value in day_values
    )

    return round(
        total,
        2
    )


# ============================================================
# BUILD VALID DAY TIMESTAMPS
# ============================================================

def build_valid_days(
    primary_times
):
    """
    Build Day 1–Day 12 UTC date ranges.
    """

    valid_days = []

    if not primary_times:

        return valid_days

    try:

        run_time = datetime.fromisoformat(
            primary_times[0].replace(
                "Z",
                "+00:00"
            )
        )

    except Exception:

        return valid_days

    for day in range(
        FORECAST_DAYS
    ):

        start_seconds = (
            day *
            86400
        )

        end_seconds = (
            (day + 1) *
            86400
        )

        start = datetime.fromtimestamp(
            run_time.timestamp() +
            start_seconds,
            timezone.utc
        )

        end = datetime.fromtimestamp(
            run_time.timestamp() +
            end_seconds,
            timezone.utc
        )

        valid_days.append(
            {
                "day": day + 1,
                "start":
                    start.isoformat(),
                "end":
                    end.isoformat(),
            }
        )

    return valid_days


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print("MASRAINMAN 12-DAY RAINFALL UPDATE")
    print("=" * 70)

    # --------------------------------------------------------
    # Build source grid
    # --------------------------------------------------------

    source_lats = frange(
        LAT_MIN,
        LAT_MAX,
        SOURCE_STEP
    )

    source_lons = frange(
        LON_MIN,
        LON_MAX,
        SOURCE_STEP
    )

    # --------------------------------------------------------
    # Build display grid
    # --------------------------------------------------------

    display_lats = frange(
        LAT_MIN,
        LAT_MAX,
        DISPLAY_STEP
    )

    display_lons = frange(
        LON_MIN,
        LON_MAX,
        DISPLAY_STEP
    )

    # --------------------------------------------------------
    # Source points
    # --------------------------------------------------------

    source_points = [
        (lat, lon)
        for lat in source_lats
        for lon in source_lons
    ]

    print(
        f"Source grid: "
        f"{len(source_points)} points"
    )

    print(
        f"Display grid: "
        f"{len(display_lats) * len(display_lons)} points"
    )

    print(
        f"Forecast: "
        f"{FORECAST_DAYS} days"
    )

    print(
        f"Internal periods: "
        f"{TOTAL_PERIODS} x 6 hours"
    )

    print(
        "Final user view: "
        "Day 1 → Day 12"
    )

    # --------------------------------------------------------
    # Download all models
    # --------------------------------------------------------

    model_source = {}

    model_times = {}

    for model_name, url in MODELS.items():

        fetched = fetch_model(
            model_name,
            url,
            source_points
        )

        model_source[
            model_name
        ] = {
            key: item["values"]
            for key, item
            in fetched.items()
        }

        # ----------------------------------------------------
        # Use first location's time axis
        # ----------------------------------------------------

        first = next(
            iter(fetched.values()),
            None
        )

        if first:

            model_times[
                model_name
            ] = first["times"]

        else:

            model_times[
                model_name
            ] = []

    # --------------------------------------------------------
    # Primary time reference
    # --------------------------------------------------------

    primary_times = (
        model_times.get(
            "ECMWF HRES"
        )
        or next(
            (
                times
                for times
                in model_times.values()
                if times
            ),
            []
        )
    )

    # --------------------------------------------------------
    # Day labels
    # --------------------------------------------------------

    periods = [
        f"Day {day}"
        for day in range(
            1,
            FORECAST_DAYS + 1
        )
    ]

    # --------------------------------------------------------
    # Valid day information
    # --------------------------------------------------------

    valid_days = build_valid_days(
        primary_times
    )

    # --------------------------------------------------------
    # Build display grid
    # --------------------------------------------------------

    grid = []

    total_display_points = (
        len(display_lats) *
        len(display_lons)
    )

    processed = 0

    for lat in display_lats:

        for lon in display_lons:

            models = {}

            # ----------------------------------------------
            # Every model
            # ----------------------------------------------

            for model_name in MODELS:

                values = []

                # ------------------------------------------
                # Day 1 → Day 12
                # ------------------------------------------

                for day in range(
                    FORECAST_DAYS
                ):

                    total = build_day_total(
                        model_source[
                            model_name
                        ],
                        source_lats,
                        source_lons,
                        lat,
                        lon,
                        day
                    )

                    values.append(
                        total
                    )

                models[
                    model_name
                ] = values

            grid.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "models": models,
                }
            )

            processed += 1

            # ----------------------------------------------
            # Progress every 500 points
            # ----------------------------------------------

            if (
                processed % 500 == 0
                or
                processed == total_display_points
            ):

                print(
                    f"Building display grid: "
                    f"{processed}/"
                    f"{total_display_points}"
                )

    # ========================================================
    # FINAL JSON
    # ========================================================

    output = {

        "updated":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "source":
            "Open-Meteo model-specific APIs; "
            "1° source grid interpolated "
            "to 0.5° display grid",

        "domain": {

            "lat_min":
                LAT_MIN,

            "lat_max":
                LAT_MAX,

            "lon_min":
                LON_MIN,

            "lon_max":
                LON_MAX,
        },

        "step":
            DISPLAY_STEP,

        "source_step":
            SOURCE_STEP,

        "forecast_days":
            FORECAST_DAYS,

        "periods":
            periods,

        "valid_days":
            valid_days,

        "models":
            list(MODELS.keys()),

        "model_horizons":
            MODEL_HORIZONS,

        "grid":
            grid,
    }

    # ========================================================
    # WRITE ATOMICALLY
    # ========================================================

    temp_file = OUT.with_suffix(
        ".json.tmp"
    )

    temp_file.write_text(
        json.dumps(
            output,
            separators=(
                ",",
                ":"
            ),
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    temp_file.replace(
        OUT
    )

    # ========================================================
    # FINISHED
    # ========================================================

    size_mb = (
        OUT.stat().st_size /
        1024 /
        1024
    )

    print()
    print("=" * 70)
    print("UPDATE COMPLETE")
    print("=" * 70)

    print(
        f"Output: {OUT}"
    )

    print(
        f"Size: {size_mb:.2f} MB"
    )

    print(
        f"Forecast days: "
        f"{FORECAST_DAYS}"
    )

    print(
        "Days: Day 1 → Day 12"
    )

    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
