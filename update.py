import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ============================================================
# MasRainman updater
#
# Standard models:
#   ECMWF HRES
#   GEM
#   GFS
#   ICON
#
# Separate AIFS Set:
#   ECMWF AIFS
#   NOAA AIGFS
#   ECMWF IFS
#   Google WeatherNext 2
#
# Also stores:
#   - 850 hPa wind
#   - individual model run times
#   - forecast valid periods
# ============================================================

BASE = Path(__file__).resolve().parent
OUT = BASE / "data.json"

# ------------------------------------------------------------
# DOMAIN
# ------------------------------------------------------------

LAT_MIN, LAT_MAX = 5.0, 38.0
LON_MIN, LON_MAX = 65.0, 100.0

SOURCE_STEP = 1.0
DISPLAY_STEP = 0.5

FORECAST_DAYS = 12

# ------------------------------------------------------------
# REQUEST CONTROL
# ------------------------------------------------------------

BATCH_SIZE = 500
REQUEST_TIMEOUT = 90

# More retries because GitHub Actions shared IPs can receive
# Open-Meteo 429 rate limits.
RETRIES = 8

# Minimum gap before each request.
MIN_REQUEST_GAP = 3.0

# Additional cooling period before Ensemble API.
AIFS_COOLDOWN = 45.0

# ------------------------------------------------------------
# STANDARD MODELS
# ------------------------------------------------------------

MODELS = {
    "ECMWF HRES": "https://api.open-meteo.com/v1/ecmwf",
    "GEM": "https://api.open-meteo.com/v1/gem",
    "GFS": "https://api.open-meteo.com/v1/gfs",
    "ICON": "https://api.open-meteo.com/v1/dwd-icon",
}

MODEL_HORIZONS = {
    "ECMWF HRES": 12,
    "GEM": 10,
    "GFS": 12,
    "ICON": 7,
}

# ------------------------------------------------------------
# SEPARATE AIFS SET
#
# These are NOT blended with the standard models.
# ------------------------------------------------------------

AIFS_MODELS = {
    "ECMWF AIFS": "ecmwf_aifs025_ensemble_mean",
    "NOAA AIGFS": "ncep_aigefs025_ensemble_mean",
    "ECMWF IFS": "ecmwf_ifs025_ensemble_mean",
    "Google WeatherNext 2": "google_weathernext2_ensemble_mean",
}

AIFS_MODEL_HORIZONS = {
    "ECMWF AIFS": 12,
    "NOAA AIGFS": 12,
    "ECMWF IFS": 12,
    "Google WeatherNext 2": 12,
}

AIFS_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"


# ============================================================
# HELPERS
# ============================================================

def frange(start, stop, step):
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(n + 1)]


def request_json(url, params):
    query = urlencode(params)
    full_url = f"{url}?{query}"

    last_error = None

    for attempt in range(1, RETRIES + 1):

        try:
            if attempt == 1:
                time.sleep(MIN_REQUEST_GAP)

            req = Request(
                full_url,
                headers={
                    "User-Agent": (
                        "MasRainman/1.2 "
                        "(GitHub Actions rainfall updater; non-commercial)"
                    ),
                    "Accept": "application/json",
                },
                method="GET",
            )

            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                raw = response.read().decode("utf-8")

                return json.loads(raw)

        except HTTPError as exc:

            last_error = exc

            # ------------------------------------------------
            # RATE LIMIT
            # ------------------------------------------------
            if exc.code == 429:

                retry_after = (
                    exc.headers.get("Retry-After")
                    if exc.headers
                    else None
                )

                try:
                    wait = float(retry_after) if retry_after else 0.0
                except (TypeError, ValueError):
                    wait = 0.0

                if wait <= 0:
                    wait = min(
                        180,
                        10 * (2 ** (attempt - 1))
                    )

                wait += 2.0

                print(
                    f"429 rate limit; waiting "
                    f"{int(wait)}s "
                    f"(attempt {attempt}/{RETRIES})"
                )

                time.sleep(wait)
                continue

            # ------------------------------------------------
            # TEMPORARY SERVER ERRORS
            # ------------------------------------------------
            if exc.code in (500, 502, 503, 504):

                wait = min(60, 5 * attempt)

                print(
                    f"HTTP {exc.code}; waiting "
                    f"{wait}s "
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

            wait = min(60, 5 * attempt)

            print(
                f"Request failed: {exc}; "
                f"waiting {wait}s "
                f"(attempt {attempt}/{RETRIES})"
            )

            time.sleep(wait)

    raise RuntimeError(
        f"Request failed after {RETRIES} attempts: "
        f"{last_error}"
    )


def parse_locations(payload):
    if isinstance(payload, list):
        return payload

    return [payload]


# ============================================================
# RAINFALL
# ============================================================

def six_hour_totals(loc, max_periods):

    hourly = loc.get("hourly", {})

    values = hourly.get("precipitation") or []
    times = hourly.get("time") or []

    periods = []

    for p in range(max_periods):

        # Index 0 is the model run / initial forecast time.
        start = 1 + p * 6

        block = values[start:start + 6]

        if len(block) < 6:
            periods.append(None)

        else:
            total = sum(
                float(v or 0.0)
                for v in block
            )

            periods.append(round(total, 2))

    return periods, times


# ============================================================
# 850 hPa WIND
# ============================================================

def to_uv(speed_kmh, direction_deg):

    if speed_kmh is None or direction_deg is None:
        return None

    speed = float(speed_kmh) / 3.6

    direction = math.radians(
        float(direction_deg)
    )

    # Meteorological direction = direction FROM which
    # wind is blowing.
    #
    # U/V below represent the direction TOWARD which
    # the wind is moving.

    u = -speed * math.sin(direction)
    v = -speed * math.cos(direction)

    return u, v


def representative_wind(loc, model_days):

    hourly = loc.get("hourly", {})

    speeds = (
        hourly.get("wind_speed_850hPa")
        or []
    )

    directions = (
        hourly.get("wind_direction_850hPa")
        or []
    )

    winds = []

    for day in range(model_days):

        # 12 UTC representative wind for each forecast day.
        idx = 12 + day * 24

        if (
            idx >= len(speeds)
            or idx >= len(directions)
        ):
            winds.append(None)
            continue

        winds.append(
            to_uv(
                speeds[idx],
                directions[idx]
            )
        )

    return winds


# ============================================================
# STANDARD MODEL DOWNLOAD
# ============================================================

def fetch_standard_model(
    model_name,
    url,
    source_points
):

    print(f"\n=== {model_name} ===")

    result = {}

    model_days = MODEL_HORIZONS[model_name]

    for start in range(
        0,
        len(source_points),
        BATCH_SIZE
    ):

        batch = source_points[
            start:start + BATCH_SIZE
        ]

        lats = ",".join(
            str(p[0])
            for p in batch
        )

        lons = ",".join(
            str(p[1])
            for p in batch
        )

        params = {
            "latitude": lats,
            "longitude": lons,

            "hourly": (
                "precipitation,"
                "wind_speed_850hPa,"
                "wind_direction_850hPa"
            ),

            "forecast_days": model_days,

            "timezone": "UTC",

            "precipitation_unit": "mm",

            "wind_speed_unit": "kmh",
        }

        batch_number = (
            start // BATCH_SIZE
        ) + 1

        print(
            f"Batch {batch_number}: "
            f"{len(batch)} points"
        )

        locations = request_json(
            url,
            params
        )

        # Small spacing between batches.
        time.sleep(2.0)

        if len(locations) != len(batch):

            raise RuntimeError(
                f"{model_name}: API returned "
                f"{len(locations)} locations "
                f"for {len(batch)}"
            )

        for point, loc in zip(
            batch,
            locations
        ):

            rain, times = six_hour_totals(
                loc,
                model_days * 4
            )

            wind = representative_wind(
                loc,
                model_days
            )

            result[point] = {
                "rain": rain,
                "wind850": wind,
                "times": times,
            }

    return result


# ============================================================
# AIFS SET DOWNLOAD
# ============================================================

def fetch_aifs_model(
    label,
    model_id,
    source_points
):

    print(
        f"\n=== AIFS SET: "
        f"{label} ({model_id}) ==="
    )

    result = {}

    model_days = (
        AIFS_MODEL_HORIZONS[label]
    )

    for start in range(
        0,
        len(source_points),
        BATCH_SIZE
    ):

        batch = source_points[
            start:start + BATCH_SIZE
        ]

        lats = ",".join(
            str(p[0])
            for p in batch
        )

        lons = ",".join(
            str(p[1])
            for p in batch
        )

        params = {
            "latitude": lats,
            "longitude": lons,

            "models": model_id,

            "daily": "precipitation_sum",

            "forecast_days": model_days,

            "timezone": "UTC",

            "precipitation_unit": "mm",
        }

        batch_number = (
            start // BATCH_SIZE
        ) + 1

        print(
            f"Batch {batch_number}: "
            f"{len(batch)} points"
        )

        locations = request_json(
            AIFS_URL,
            params
        )

        # Longer gap between Ensemble requests.
        time.sleep(4.0)

        if len(locations) != len(batch):

            raise RuntimeError(
                f"{label}: API returned "
                f"{len(locations)} locations "
                f"for {len(batch)}"
            )

        for point, loc in zip(
            batch,
            locations
        ):

            daily = loc.get(
                "daily",
                {}
            )

            values = (
                daily.get(
                    "precipitation_sum"
                )
                or []
            )

            result[point] = [
                (
                    None
                    if v is None
                    else round(float(v), 2)
                )
                for v in values[:model_days]
            ]

    return result


# ============================================================
# INTERPOLATION
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


def interpolate_array(
    src,
    lats,
    lons,
    lat,
    lon,
    index
):

    lat = min(
        max(lat, lats[0]),
        lats[-1]
    )

    lon = min(
        max(lon, lons[0]),
        lons[-1]
    )

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

    def value(j, i):

        arr = src.get(
            (lats[j], lons[i])
        )

        if (
            not arr
            or index >= len(arr)
        ):
            return None

        return arr[index]

    vals = [
        value(j0, i0),
        value(j0, i0 + 1),
        value(j0 + 1, i0),
        value(j0 + 1, i0 + 1),
    ]

    available = [
        v for v in vals
        if v is not None
    ]

    if not available:
        return None

    # Missing corners:
    # use available surrounding values.
    if len(available) < 4:

        return round(
            sum(available)
            / len(available),
            2
        )

    return round(
        bilinear(
            vals[0],
            vals[1],
            vals[2],
            vals[3],
            fx,
            fy
        ),
        2
    )


def interpolate_uv(
    src,
    lats,
    lons,
    lat,
    lon,
    index
):

    lat = min(
        max(lat, lats[0]),
        lats[-1]
    )

    lon = min(
        max(lon, lons[0]),
        lons[-1]
    )

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

    def value(j, i):

        arr = src.get(
            (lats[j], lons[i])
        )

        if (
            not arr
            or index >= len(arr)
            or arr[index] is None
        ):
            return None

        return arr[index]

    vals = [
        value(j0, i0),
        value(j0, i0 + 1),
        value(j0 + 1, i0),
        value(j0 + 1, i0 + 1),
    ]

    if any(
        v is None
        for v in vals
    ):

        available = [
            v for v in vals
            if v is not None
        ]

        if not available:
            return None

        return [
            round(
                sum(
                    v[0]
                    for v in available
                )
                / len(available),
                4
            ),

            round(
                sum(
                    v[1]
                    for v in available
                )
                / len(available),
                4
            ),
        ]

    u = bilinear(
        vals[0][0],
        vals[1][0],
        vals[2][0],
        vals[3][0],
        fx,
        fy
    )

    v = bilinear(
        vals[0][1],
        vals[1][1],
        vals[2][1],
        vals[3][1],
        fx,
        fy
    )

    return [
        round(u, 4),
        round(v, 4),
    ]


# ============================================================
# RUN TIME HELPERS
# ============================================================

def parse_run_time(times):

    if not times:
        return None

    first = times[0]

    try:

        # Handle:
        # 2026-09-12T06:00
        # 2026-09-12T06:00:00
        # 2026-09-12T06:00:00Z

        text = str(first)

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        dt = datetime.fromisoformat(text)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt.astimezone(
            timezone.utc
        )

    except Exception:
        return None


def iso_or_none(value):

    if value is None:
        return None

    return value.isoformat()


def make_valid_days(
    run_time,
    days
):

    valid = []

    if run_time is None:
        return valid

    for day in range(days):

        start = (
            run_time
            + __import__("datetime").timedelta(
                days=day
            )
        )

        end = (
            start
            + __import__("datetime").timedelta(
                days=1
            )
        )

        valid.append({
            "day": day + 1,
            "start": start.isoformat(),
            "end": end.isoformat(),
        })

    return valid


# ============================================================
# MAIN
# ============================================================

def main():

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

    # --------------------------------------------------------
    # STANDARD MODELS
    # --------------------------------------------------------

    standard = {}

    model_runs = {}

    model_times = {}

    for name, url in MODELS.items():

        fetched = fetch_standard_model(
            name,
            url,
            source_points
        )

        standard[name] = {
            point: item["rain"]
            for point, item
            in fetched.items()
        }

        standard[
            name + "::wind"
        ] = {
            point: item["wind850"]
            for point, item
            in fetched.items()
        }

        first = next(
            iter(fetched.values()),
            None
        )

        times = (
            first["times"]
            if first
            else []
        )

        model_times[name] = times

        run_time = parse_run_time(
            times
        )

        model_runs[name] = (
            iso_or_none(run_time)
        )

        print(
            f"{name} model run: "
            f"{model_runs[name]}"
        )

    # --------------------------------------------------------
    # COOL DOWN BEFORE AIFS
    # --------------------------------------------------------

    print(
        f"\nCooling down "
        f"{int(AIFS_COOLDOWN)}s "
        f"before AIFS Set requests..."
    )

    time.sleep(
        AIFS_COOLDOWN
    )

    # --------------------------------------------------------
    # AIFS SET
    # --------------------------------------------------------

    aifs_source = {}

    aifs_runs = {}

    for label, model_id in AIFS_MODELS.items():

        aifs_source[label] = (
            fetch_aifs_model(
                label,
                model_id,
                source_points
            )
        )

        # The Ensemble API's daily output does not expose
        # the same hourly initialization timestamp as the
        # standard model endpoints.
        #
        # Therefore we record the update timestamp as the
        # retrieval timestamp and DO NOT pretend it is the
        # model initialization time.
        aifs_runs[label] = None

    # --------------------------------------------------------
    # PRIMARY STANDARD RUN
    #
    # Kept for compatibility with the existing dashboard.
    # ECMWF HRES is preferred when available.
    # --------------------------------------------------------

    primary_run = parse_run_time(
        model_times.get(
            "ECMWF HRES",
            []
        )
    )

    if primary_run is None:

        for times in model_times.values():

            candidate = parse_run_time(
                times
            )

            if candidate is not None:
                primary_run = candidate
                break

    # --------------------------------------------------------
    # VALID DAYS
    # --------------------------------------------------------

    valid_days = make_valid_days(
        primary_run,
        FORECAST_DAYS
    )

    # --------------------------------------------------------
    # BUILD DISPLAY GRID
    # --------------------------------------------------------

    grid = []

    for lat in display_lats:

        for lon in display_lons:

            models = {}

            # -----------------------------------------------
            # STANDARD MODEL DATA
            # -----------------------------------------------

            for name in MODELS:

                rain_days = []

                wind_days = []

                for day in range(
                    FORECAST_DAYS
                ):

                    six_indices = range(
                        day * 4,
                        day * 4 + 4
                    )

                    vals = [
                        interpolate_array(
                            standard[name],
                            source_lats,
                            source_lons,
                            lat,
                            lon,
                            p
                        )
                        for p in six_indices
                    ]

                    if all(
                        v is None
                        for v in vals
                    ):
                        rain_days.append(None)

                    else:
                        rain_days.append(
                            round(
                                sum(
                                    v or 0
                                    for v in vals
                                ),
                                2
                            )
                        )

                    wind_days.append(
                        interpolate_uv(
                            standard[
                                name + "::wind"
                            ],
                            source_lats,
                            source_lons,
                            lat,
                            lon,
                            day
                        )
                    )

                models[name] = {
                    "rain": rain_days,
                    "wind850": wind_days,
                }

            # -----------------------------------------------
            # AIFS SET
            # -----------------------------------------------

            aifs = {}

            for label in AIFS_MODELS:

                aifs[label] = [

                    interpolate_array(
                        aifs_source[label],
                        source_lats,
                        source_lons,
                        lat,
                        lon,
                        day
                    )

                    for day
                    in range(
                        FORECAST_DAYS
                    )
                ]

            grid.append({
                "lat": lat,
                "lon": lon,
                "models": models,
                "aifs": aifs,
            })

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    now_utc = datetime.now(
        timezone.utc
    )

    out = {

        "updated": now_utc.isoformat(),

        "source": (
            "Open-Meteo model-specific APIs "
            "+ Open-Meteo Ensemble Mean API; "
            "1° source grid interpolated to "
            "0.5° display grid"
        ),

        "domain": {
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
        },

        "step": DISPLAY_STEP,

        "source_step": SOURCE_STEP,

        "forecast_days": FORECAST_DAYS,

        "periods": [
            f"Day {d}"
            for d in range(
                1,
                FORECAST_DAYS + 1
            )
        ],

        # ----------------------------------------------------
        # VALID PERIODS
        # ----------------------------------------------------

        "valid_days": valid_days,

        # ----------------------------------------------------
        # STANDARD MODELS
        # ----------------------------------------------------

        "models": list(
            MODELS.keys()
        ),

        "model_horizons": (
            MODEL_HORIZONS
        ),

        # ACTUAL run timestamp for each
        # standard model.
        "model_runs": model_runs,

        # ----------------------------------------------------
        # AIFS SET
        # ----------------------------------------------------

        "aifs_models": list(
            AIFS_MODELS.keys()
        ),

        "aifs_model_ids": (
            AIFS_MODELS
        ),

        "aifs_model_horizons": (
            AIFS_MODEL_HORIZONS
        ),

        "aifs_model_runs": (
            aifs_runs
        ),

        "aifs_set_note": (
            "Independent precipitation "
            "products; not blended with "
            "the standard model set."
        ),

        # ----------------------------------------------------
        # PRIMARY RUN
        # ----------------------------------------------------

        "primary_model_run": (
            iso_or_none(primary_run)
        ),

        # ----------------------------------------------------
        # 850 hPa WIND
        # ----------------------------------------------------

        "wind850": {
            "enabled": True,
            "level_hpa": 850,
            "representative_utc": "12:00",
            "units": "m/s",
        },

        # ----------------------------------------------------
        # GRID
        # ----------------------------------------------------

        "grid": grid,
    }

    # --------------------------------------------------------
    # ATOMIC WRITE
    # --------------------------------------------------------

    tmp = OUT.with_suffix(
        ".json.tmp"
    )

    tmp.write_text(
        json.dumps(
            out,
            separators=(",", ":"),
            ensure_ascii=False
        ),
        encoding="utf-8"
    )

    tmp.replace(OUT)

    print(
        f"\nWrote {OUT} "
        f"({OUT.stat().st_size / 1024 / 1024:.2f} MB)"
    )

    print(
        "\nStandard model runs:"
    )

    for name, run in model_runs.items():
        print(
            f"  {name}: {run}"
        )

    print(
        "\nAIFS Set:"
    )

    for name in AIFS_MODELS:
        print(
            f"  {name}: "
            f"stored separately"
        )

    print(
        "\nAIFS Set is NOT blended "
        "with ECMWF HRES/GEM/GFS/ICON."
    )


if __name__ == "__main__":
    main()
