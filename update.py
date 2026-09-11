import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ============================================================
# MASRAINMAN — 12-DAY RAINFALL + 850 hPa WIND UPDATER
# ============================================================
#
# Rainfall:
#   1° source grid -> 0.5° display grid
#   4 x 6-hour periods are summed into Day 1 ... Day 12
#
# Wind:
#   850 hPa wind speed + direction
#   Stored as U/V components for each forecast day.
#   Representative time = 12 UTC for each forecast day.
#
# Models:
#   ECMWF HRES, GEM, GFS, ICON
#
# Python standard library only. No requests package required.
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

MODEL_HORIZONS = {
    "ECMWF HRES": 12,
    "GEM": 10,
    "GFS": 12,
    "ICON": 7,
}


def frange(start, stop, step):
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(n + 1)]


def request_json(url, params):
    query = urlencode(params)
    full_url = f"{url}?{query}"
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            req = Request(
                full_url,
                headers={
                    "User-Agent": "MasRainman/1.0 (GitHub Actions rainfall updater)"
                },
                method="GET",
            )
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))

        except HTTPError as exc:
            last_error = exc
            if exc.code in (429, 500, 502, 503, 504):
                wait = min(60, 5 * attempt)
                print(f"HTTP {exc.code}; waiting {wait}s (attempt {attempt}/{RETRIES})")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc

        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            wait = min(30, 3 * attempt)
            print(f"Request failed: {exc}; waiting {wait}s (attempt {attempt}/{RETRIES})")
            time.sleep(wait)

    raise RuntimeError(f"Request failed after {RETRIES} attempts: {last_error}")


def parse_locations(payload):
    return payload if isinstance(payload, list) else [payload]


def to_uv(speed_kmh, direction_deg):
    """Meteorological direction -> eastward U / northward V."""
    if speed_kmh is None or direction_deg is None:
        return None, None
    try:
        speed = float(speed_kmh) / 3.6
        direction = math.radians(float(direction_deg))
        u = -speed * math.sin(direction)
        v = -speed * math.cos(direction)
        return u, v
    except (TypeError, ValueError):
        return None, None


def six_hour_totals(payload, max_periods):
    hourly = payload.get("hourly", {})
    precipitation = hourly.get("precipitation") or []
    times = hourly.get("time") or []
    wind_speed = hourly.get("wind_speed_850hPa") or []
    wind_direction = hourly.get("wind_direction_850hPa") or []

    periods = []
    for p in range(max_periods):
        start = 1 + p * 6
        end = start + 6
        block = precipitation[start:end]
        if len(block) < 6:
            periods.append(None)
        else:
            periods.append(round(sum(float(v or 0.0) for v in block), 2))

    # One representative 850 hPa wind for each forecast day: 12 UTC.
    # Day 1 -> hour 12, Day 2 -> hour 36, etc.
    wind_u = []
    wind_v = []
    for day in range(FORECAST_DAYS):
        index = 12 + day * 24
        if index >= len(wind_speed) or index >= len(wind_direction):
            wind_u.append(None)
            wind_v.append(None)
            continue
        u, v = to_uv(wind_speed[index], wind_direction[index])
        wind_u.append(round(u, 3) if u is not None else None)
        wind_v.append(round(v, 3) if v is not None else None)

    return periods, wind_u, wind_v, times


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
            "hourly": "precipitation,wind_speed_850hPa,wind_direction_850hPa",
            "forecast_days": model_days,
            "timezone": "UTC",
            "precipitation_unit": "mm",
            "wind_speed_unit": "kmh",
        }

        print(f"Batch {start // BATCH_SIZE + 1}: {len(batch)} points")
        payload = request_json(url, params)
        locations = parse_locations(payload)

        if len(locations) != len(batch):
            raise RuntimeError(
                f"{model_name}: API returned {len(locations)} locations for {len(batch)} requested"
            )

        for point, loc in zip(batch, locations):
            periods, wind_u, wind_v, times = six_hour_totals(
                loc,
                max_periods=model_days * 4,
            )
            result[(point[0], point[1])] = {
                "rain": periods,
                "wind_u": wind_u,
                "wind_v": wind_v,
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
        value = arr[period]
        return None if value is None else float(value)

    vals = [
        value(j0, i0),
        value(j0, i0 + 1),
        value(j0 + 1, i0),
        value(j0 + 1, i0 + 1),
    ]

    available = [v for v in vals if v is not None]
    if not available:
        return None
    if len(available) < 4:
        return round(sum(available) / len(available), 3)

    return round(bilinear(vals[0], vals[1], vals[2], vals[3], fx, fy), 3)


def nearest_value(src, lats, lons, lat, lon, period):
    """Nearest-neighbour sampling for the deliberately sharper rainfall map."""
    lat = min(max(lat, lats[0]), lats[-1])
    lon = min(max(lon, lons[0]), lons[-1])
    lat_idx = min(range(len(lats)), key=lambda i: abs(lats[i] - lat))
    lon_idx = min(range(len(lons)), key=lambda i: abs(lons[i] - lon))
    arr = src.get((lats[lat_idx], lons[lon_idx]))
    if not arr or period >= len(arr) or arr[period] is None:
        return None
    return round(float(arr[period]), 3)


def interpolate_vector(src_u, src_v, lats, lons, lat, lon, day):
    u = interpolate_value(src_u, lats, lons, lat, lon, day)
    v = interpolate_value(src_v, lats, lons, lat, lon, day)
    if u is None or v is None:
        return None, None
    return round(u, 3), round(v, 3)


def main():
    source_lats = frange(LAT_MIN, LAT_MAX, SOURCE_STEP)
    source_lons = frange(LON_MIN, LON_MAX, SOURCE_STEP)
    display_lats = frange(LAT_MIN, LAT_MAX, DISPLAY_STEP)
    display_lons = frange(LON_MIN, LON_MAX, DISPLAY_STEP)
    source_points = [(lat, lon) for lat in source_lats for lon in source_lons]

    print(f"Source grid: {len(source_points)} points")
    print(f"Display grid: {len(display_lats) * len(display_lons)} points")
    print(f"Forecast horizon: {FORECAST_DAYS} days")
    print("Rainfall display interpolation: nearest-neighbour")
    print("850 hPa wind: representative 12 UTC each day")

    model_source = {}
    model_times = {}

    for model_name, url in MODELS.items():
        fetched = fetch_model(model_name, url, source_points)
        model_source[model_name] = {
            "rain": {k: v["rain"] for k, v in fetched.items()},
            "wind_u": {k: v["wind_u"] for k, v in fetched.items()},
            "wind_v": {k: v["wind_v"] for k, v in fetched.items()},
        }
        first = next(iter(fetched.values()), None)
        model_times[model_name] = first["times"] if first else []

    primary_times = model_times.get("ECMWF HRES") or next(
        (v for v in model_times.values() if v), []
    )

    valid_days = []
    if primary_times:
        try:
            run_time = datetime.fromisoformat(primary_times[0].replace("Z", "+00:00"))
            for day in range(FORECAST_DAYS):
                start = run_time.timestamp() + day * 86400
                end = start + 86400
                valid_days.append({
                    "day": day + 1,
                    "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                    "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
                })
        except Exception:
            valid_days = []

    grid = []
    total = len(display_lats) * len(display_lons)
    processed = 0

    for lat in display_lats:
        for lon in display_lons:
            models = {}
            for model_name in MODELS:
                rain_values = []
                wind_values = []

                for day in range(FORECAST_DAYS):
                    six_hour_indices = range(day * 4, day * 4 + 4)
                    day_values = [
                        nearest_value(
                            model_source[model_name]["rain"],
                            source_lats,
                            source_lons,
                            lat,
                            lon,
                            p,
                        )
                        for p in six_hour_indices
                    ]
                    if all(v is None for v in day_values):
                        rain_values.append(None)
                    else:
                        rain_values.append(round(sum(v or 0.0 for v in day_values), 2))

                    u, v = interpolate_vector(
                        model_source[model_name]["wind_u"],
                        model_source[model_name]["wind_v"],
                        source_lats,
                        source_lons,
                        lat,
                        lon,
                        day,
                    )
                    wind_values.append(
                        [u, v] if u is not None and v is not None else None
                    )

                models[model_name] = {
                    "rain": rain_values,
                    "wind850": wind_values,
                }

            grid.append({"lat": lat, "lon": lon, "models": models})
            processed += 1
            if processed % 500 == 0 or processed == total:
                print(f"Building display grid: {processed}/{total}")

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
        "periods": [f"Day {d}" for d in range(1, FORECAST_DAYS + 1)],
        "valid_days": valid_days,
        "models": list(MODELS.keys()),
        "model_horizons": MODEL_HORIZONS,
        "wind850": {
            "enabled": True,
            "level_hpa": 850,
            "representative_utc": "12:00",
            "units": "m/s",
        },
        "grid": grid,
    }

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(out, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    tmp.replace(OUT)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024 / 1024:.2f} MB)")


if __name__ == "__main__":
    main()
