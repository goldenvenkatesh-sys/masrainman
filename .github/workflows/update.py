import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ============================================================
# MasRainman rainfall data updater
# ============================================================
# Creates 12 forecast days of 6-hour rainfall totals from
# Open-Meteo model-specific APIs, using a 1° source grid and
# bilinear interpolation to the 0.5° display grid.
#
# Models:
#   ECMWF HRES, GEM, GFS, ICON
#
# The browser groups the 6-hour periods into Day 1 ... Day 12.
# GEM has a 10-day forecast horizon at Open-Meteo; Day 11-12
# therefore remain unavailable for GEM and are represented as
# null values rather than invented data.
# ============================================================

BASE = Path(__file__).resolve().parent
OUT = BASE / "data.json"

LAT_MIN, LAT_MAX = 5.0, 38.0
LON_MIN, LON_MAX = 65.0, 100.0
SOURCE_STEP = 1.0
DISPLAY_STEP = 0.5
FORECAST_DAYS = 12
BATCH_SIZE = 500
REQUEST_TIMEOUT = 90
RETRIES = 5

MODELS = {
    "ECMWF HRES": "https://api.open-meteo.com/v1/ecmwf",
    "GEM": "https://api.open-meteo.com/v1/gem",
    "GFS": "https://api.open-meteo.com/v1/gfs",
    "ICON": "https://api.open-meteo.com/v1/dwd-icon",
}


def frange(start, stop, step):
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(n + 1)]


def request_json(url, params):
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if r.status_code == 429:
                wait = min(30, 3 * attempt)
                print(f"429 rate limit; waiting {wait}s (attempt {attempt}/{RETRIES})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            wait = min(20, 2 * attempt)
            print(f"Request failed: {exc}; waiting {wait}s (attempt {attempt}/{RETRIES})")
            time.sleep(wait)
    raise RuntimeError(f"Request failed after {RETRIES} attempts: {last_error}")


def parse_hourly(payload):
    # Multiple-coordinate requests return a list of location objects.
    if isinstance(payload, list):
        return payload
    return [payload]


def six_hour_totals(payload, max_periods=FORECAST_DAYS * 4):
    hourly = payload.get("hourly", {})
    values = hourly.get("precipitation") or []
    times = hourly.get("time") or []

    # Need one 6-hour block for every forecast period. Open-Meteo's
    # hourly precipitation value at HH is the precipitation during
    # the preceding hour, so indices 1..6 form the 00-06 UTC block.
    periods = []
    for p in range(max_periods):
        start = 1 + p * 6
        end = start + 6
        block = values[start:end]
        if len(block) < 6:
            periods.append(None)
            continue
        periods.append(round(sum(float(v or 0.0) for v in block), 2))
    return periods, times


MODEL_HORIZONS = {
    "ECMWF HRES": 12,
    "GEM": 10,
    "GFS": 12,
    "ICON": 7,
}


def fetch_model(model_name, url, source_points):
    print(f"\n=== {model_name} ===")
    result = {}

    model_days = MODEL_HORIZONS[model_name]
    for start in range(0, len(source_points), BATCH_SIZE):
        batch = source_points[start:start + BATCH_SIZE]
        lats = ",".join(str(p[0]) for p in batch)
        lons = ",".join(str(p[1]) for p in batch)

        params = {
            "latitude": lats,
            "longitude": lons,
            "hourly": "precipitation",
            "forecast_days": model_days,
            "timezone": "UTC",
            "precipitation_unit": "mm",
        }

        print(f"Batch {start // BATCH_SIZE + 1}: {len(batch)} points")
        payload = request_json(url, params)
        locations = parse_hourly(payload)

        if len(locations) != len(batch):
            raise RuntimeError(
                f"{model_name}: API returned {len(locations)} locations for {len(batch)} requested"
            )

        for point, loc in zip(batch, locations):
            periods, times = six_hour_totals(loc, max_periods=model_days * 4)
            key = (point[0], point[1])
            result[key] = {
                "values": periods,
                "times": times,
            }

    return result


def bilinear(v00, v10, v01, v11, fx, fy):
    return (
        v00 * (1 - fx) * (1 - fy)
        + v10 * fx * (1 - fy)
        + v01 * (1 - fx) * fy
        + v11 * fx * fy
    )


def interpolate_value(src, lats, lons, lat, lon, period):
    # Clamp to source domain.
    lat = min(max(lat, lats[0]), lats[-1])
    lon = min(max(lon, lons[0]), lons[-1])

    lat_pos = (lat - lats[0]) / SOURCE_STEP
    lon_pos = (lon - lons[0]) / SOURCE_STEP
    j0 = min(int(math.floor(lat_pos)), len(lats) - 2)
    i0 = min(int(math.floor(lon_pos)), len(lons) - 2)
    fy = lat_pos - j0
    fx = lon_pos - i0

    def value(j, i):
        arr = src.get((lats[j], lons[i]))
        if not arr or period >= len(arr):
            return None
        v = arr[period]
        return None if v is None else float(v)

    vals = [
        value(j0, i0),
        value(j0, i0 + 1),
        value(j0 + 1, i0),
        value(j0 + 1, i0 + 1),
    ]

    # If a model has no forecast at a corner (for example GEM after
    # its 10-day horizon), interpolate only across available values.
    available = [v for v in vals if v is not None]
    if not available:
        return None
    if len(available) < 4:
        return round(sum(available) / len(available), 2)

    return round(bilinear(vals[0], vals[1], vals[2], vals[3], fx, fy), 2)


def main():
    source_lats = frange(LAT_MIN, LAT_MAX, SOURCE_STEP)
    source_lons = frange(LON_MIN, LON_MAX, SOURCE_STEP)
    display_lats = frange(LAT_MIN, LAT_MAX, DISPLAY_STEP)
    display_lons = frange(LON_MIN, LON_MAX, DISPLAY_STEP)

    source_points = [(lat, lon) for lat in source_lats for lon in source_lons]
    print(f"Source grid: {len(source_points)} points")
    print(f"Display grid: {len(display_lats) * len(display_lons)} points")
    print(f"Forecast horizon: {FORECAST_DAYS} days / {FORECAST_DAYS * 4} six-hour periods")

    model_source = {}
    model_times = {}
    for model_name, url in MODELS.items():
        fetched = fetch_model(model_name, url, source_points)
        model_source[model_name] = {k: v["values"] for k, v in fetched.items()}
        # Use the first available point as the common model time axis.
        first = next(iter(fetched.values()), None)
        model_times[model_name] = first["times"] if first else []

    # Use ECMWF's hourly timestamps as the primary time reference when available.
    primary_times = model_times.get("ECMWF HRES") or next(
        (v for v in model_times.values() if v), []
    )

    periods = []
    for day in range(1, FORECAST_DAYS + 1):
        periods.append(f"Day {day}")

    grid = []
    for lat in display_lats:
        for lon in display_lons:
            models = {}
            for model_name in MODELS:
                vals = []
                for day in range(FORECAST_DAYS):
                    six_hour_indices = range(day * 4, day * 4 + 4)
                    day_values = [
                        interpolate_value(
                            model_source[model_name],
                            source_lats,
                            source_lons,
                            lat,
                            lon,
                            p,
                        )
                        for p in six_hour_indices
                    ]
                    if all(v is None for v in day_values):
                        vals.append(None)
                    else:
                        vals.append(round(sum(v or 0.0 for v in day_values), 2))
                models[model_name] = vals
            grid.append({"lat": lat, "lon": lon, "models": models})

    # Forecast day validity is based on the model's first 00 UTC timestamp.
    valid_days = []
    if primary_times:
        # First timestamp is normally 00:00 UTC. Day 1 ends 24h later.
        # Store the start/end UTC timestamps so the browser can display them.
        try:
            run_time = datetime.fromisoformat(primary_times[0].replace("Z", "+00:00"))
            for day in range(FORECAST_DAYS):
                start = run_time.timestamp() + day * 86400
                end = start + 86400
                valid_days.append({
                    "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
                })
        except Exception:
            valid_days = []

    out = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "source": "Open-Meteo model-specific APIs; 1° source grid interpolated to 0.5° display grid",
        "domain": {
            "lat_min": LAT_MIN,
            "lat_max": LAT_MAX,
            "lon_min": LON_MIN,
            "lon_max": LON_MAX,
        },
        "step": DISPLAY_STEP,
        "source_step": SOURCE_STEP,
        "forecast_days": FORECAST_DAYS,
        "periods": periods,
        "valid_days": valid_days,
        "models": list(MODELS.keys()),
        "model_horizons": MODEL_HORIZONS,
        "grid": grid,
    }

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
