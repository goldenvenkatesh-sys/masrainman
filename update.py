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
# Standard models + optional 850 hPa wind + separate AIFS Set
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
RETRIES = 8
MIN_REQUEST_GAP = 3.0
AIFS_COOLDOWN = 45.0

MODELS = {
    "ECMWF HRES": "https://api.open-meteo.com/v1/ecmwf",
    "GEM": "https://api.open-meteo.com/v1/gem",
    "GFS": "https://api.open-meteo.com/v1/gfs",
    "ICON": "https://api.open-meteo.com/v1/dwd-icon",
}

# Open-Meteo metadata domains. These expose the ACTUAL model
# initialization time, which is different from the first forecast-valid time.
MODEL_META_DOMAINS = {
    "ECMWF HRES": "ecmwf_ifs",
    "GEM": "cmc_gem_gdps",
    "GFS": "ncep_gfs013",
    "ICON": "dwd_icon",
}
MODEL_META_BASE = "https://api.open-meteo.com/data"

MODEL_HORIZONS = {
    "ECMWF HRES": 12,
    "GEM": 10,
    "GFS": 12,
    "ICON": 7,
}

# These are kept as a separate set and are NEVER blended with the
# four standard models. They are ensemble-mean products so all four
# can be compared on the same precipitation field.
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


def frange(start, stop, step):
    n = int(round((stop - start) / step))
    return [round(start + i * step, 6) for i in range(n + 1)]


def request_json(url, params):
    query = urlencode(params)
    full_url = f"{url}?{query}"
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            # Small spacing between requests reduces burst-rate 429s from shared
            # GitHub Actions runner IPs.
            if attempt == 1:
                time.sleep(MIN_REQUEST_GAP)
            req = Request(
                full_url,
                headers={
                    "User-Agent": "MasRainman/1.1 (GitHub Actions rainfall updater; non-commercial)",
                    "Accept": "application/json",
                },
                method="GET",
            )
            with urlopen(req, timeout=REQUEST_TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    wait = float(retry_after) if retry_after else 0.0
                except (TypeError, ValueError):
                    wait = 0.0
                if wait <= 0:
                    # Longer exponential backoff is important for Open-Meteo's
                    # weighted request limits, especially on the Ensemble API.
                    wait = min(180, 10 * (2 ** (attempt - 1)))
                wait += 2.0
                print(f"429 rate limit; waiting {int(wait)}s (attempt {attempt}/{RETRIES})")
                time.sleep(wait)
                continue
            if exc.code in (500, 502, 503, 504):
                wait = min(60, 5 * attempt)
                print(f"HTTP {exc.code}; waiting {wait}s (attempt {attempt}/{RETRIES})")
                time.sleep(wait)
                continue
            raise RuntimeError(f"HTTP {exc.code}: {exc.reason}") from exc
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            wait = min(60, 5 * attempt)
            print(f"Request failed: {exc}; waiting {wait}s (attempt {attempt}/{RETRIES})")
            time.sleep(wait)
    raise RuntimeError(f"Request failed after {RETRIES} attempts: {last_error}")

def fetch_model_initialisation_time(model_name):
    """Read the actual UTC model initialization time from Open-Meteo metadata."""
    domain = MODEL_META_DOMAINS[model_name]
    url = f"{MODEL_META_BASE}/{domain}/static/meta.json"
    payload = request_json(url, {})
    timestamp = payload.get("last_run_initialisation_time")
    if timestamp is None:
        raise RuntimeError(
            f"{model_name}: Open-Meteo metadata did not provide last_run_initialisation_time"
        )
    run_time = datetime.fromtimestamp(float(timestamp), timezone.utc)
    print(f"{model_name} actual model initialization: {run_time.isoformat()}")
    return run_time


def parse_locations(payload):
    return payload if isinstance(payload, list) else [payload]


def six_hour_totals(loc, max_periods):
    hourly = loc.get("hourly", {})
    values = hourly.get("precipitation") or []
    times = hourly.get("time") or []
    periods = []
    for p in range(max_periods):
        start = 1 + p * 6
        block = values[start:start + 6]
        if len(block) < 6:
            periods.append(None)
        else:
            periods.append(round(sum(float(v or 0.0) for v in block), 2))
    return periods, times


def to_uv(speed_kmh, direction_deg):
    if speed_kmh is None or direction_deg is None:
        return None
    speed = float(speed_kmh) / 3.6
    direction = math.radians(float(direction_deg))
    # Meteorological direction is FROM; U/V are TOWARD.
    u = -speed * math.sin(direction)
    v = -speed * math.cos(direction)
    return u, v


def representative_wind(loc, model_days):
    hourly = loc.get("hourly", {})
    speeds = hourly.get("wind_speed_850hPa") or []
    directions = hourly.get("wind_direction_850hPa") or []
    winds = []
    for day in range(model_days):
        # 12 UTC of each forecast day.
        idx = 12 + day * 24
        if idx >= len(speeds) or idx >= len(directions):
            winds.append(None)
            continue
        winds.append(to_uv(speeds[idx], directions[idx]))
    return winds


def fetch_standard_model(model_name, url, source_points):
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
        locations = parse_locations(request_json(url, params))
        time.sleep(2.0)
        if len(locations) != len(batch):
            raise RuntimeError(f"{model_name}: API returned {len(locations)} locations for {len(batch)}")
        for point, loc in zip(batch, locations):
            rain, times = six_hour_totals(loc, model_days * 4)
            wind = representative_wind(loc, model_days)
            result[point] = {"rain": rain, "wind850": wind, "times": times}
    return result


def fetch_aifs_model(label, model_id, source_points):
    print(f"\n=== AIFS SET: {label} ({model_id}) ===")
    result = {}
    model_days = AIFS_MODEL_HORIZONS[label]
    for start in range(0, len(source_points), BATCH_SIZE):
        batch = source_points[start:start + BATCH_SIZE]
        lats = ",".join(str(p[0]) for p in batch)
        lons = ",".join(str(p[1]) for p in batch)
        params = {
            "latitude": lats,
            "longitude": lons,
            "models": model_id,
            "daily": "precipitation_sum",
            "forecast_days": model_days,
            "timezone": "UTC",
            "precipitation_unit": "mm",
        }
        print(f"Batch {start // BATCH_SIZE + 1}: {len(batch)} points")
        locations = parse_locations(request_json(AIFS_URL, params))
        time.sleep(4.0)
        if len(locations) != len(batch):
            raise RuntimeError(f"{label}: API returned {len(locations)} locations for {len(batch)}")
        for point, loc in zip(batch, locations):
            daily = loc.get("daily", {})
            values = daily.get("precipitation_sum") or []
            result[point] = [
                None if v is None else round(float(v), 2)
                for v in values[:model_days]
            ]
    return result


def bilinear(v00, v10, v01, v11, fx, fy):
    return (
        v00 * (1 - fx) * (1 - fy)
        + v10 * fx * (1 - fy)
        + v01 * (1 - fx) * fy
        + v11 * fx * fy
    )


def interpolate_array(src, lats, lons, lat, lon, index):
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
        if not arr or index >= len(arr):
            return None
        return arr[index]

    vals = [value(j0, i0), value(j0, i0 + 1), value(j0 + 1, i0), value(j0 + 1, i0 + 1)]
    available = [v for v in vals if v is not None]
    if not available:
        return None
    if len(available) < 4:
        return round(sum(available) / len(available), 2)
    return round(bilinear(vals[0], vals[1], vals[2], vals[3], fx, fy), 2)


def interpolate_uv(src, lats, lons, lat, lon, index):
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
        if not arr or index >= len(arr) or arr[index] is None:
            return None
        return arr[index]

    vals = [value(j0, i0), value(j0, i0 + 1), value(j0 + 1, i0), value(j0 + 1, i0 + 1)]
    if any(v is None for v in vals):
        available = [v for v in vals if v is not None]
        if not available:
            return None
        return [round(sum(v[0] for v in available) / len(available), 4), round(sum(v[1] for v in available) / len(available), 4)]
    u = bilinear(vals[0][0], vals[1][0], vals[2][0], vals[3][0], fx, fy)
    v = bilinear(vals[0][1], vals[1][1], vals[2][1], vals[3][1], fx, fy)
    return [round(u, 4), round(v, 4)]


def main():
    source_lats = frange(LAT_MIN, LAT_MAX, SOURCE_STEP)
    source_lons = frange(LON_MIN, LON_MAX, SOURCE_STEP)
    display_lats = frange(LAT_MIN, LAT_MAX, DISPLAY_STEP)
    display_lons = frange(LON_MIN, LON_MAX, DISPLAY_STEP)
    source_points = [(lat, lon) for lat in source_lats for lon in source_lons]

    print(f"Source grid: {len(source_points)} points")
    print(f"Display grid: {len(display_lats) * len(display_lons)} points")

    standard = {}
    model_times = {}
    for name, url in MODELS.items():
        fetched = fetch_standard_model(name, url, source_points)
        standard[name] = {k: v["rain"] for k, v in fetched.items()}
        standard[name + "::wind"] = {k: v["wind850"] for k, v in fetched.items()}
        first = next(iter(fetched.values()), None)
        model_times[name] = first["times"] if first else []

    # IMPORTANT: The first forecast timestamp is NOT the model run time.
    # Read the actual initialization time from Open-Meteo metadata.
    model_runs = {}
    for model_name in MODELS:
        run_time = fetch_model_initialisation_time(model_name)
        model_runs[model_name] = run_time.isoformat()

    # ECMWF HRES is the primary reference for the dashboard Day 1...Day 12
    # validity window. All individual model run times are also stored.
    primary_run_iso = model_runs["ECMWF HRES"]
    run_time = datetime.fromisoformat(primary_run_iso.replace("Z", "+00:00"))

    # Let the API rate-limit window settle after the four standard-model
    # downloads before starting the heavier Ensemble API requests.
    print(f"\nCooling down {int(AIFS_COOLDOWN)}s before AIFS Set requests...")
    time.sleep(AIFS_COOLDOWN)

    aifs_source = {}
    for label, model_id in AIFS_MODELS.items():
        aifs_source[label] = fetch_aifs_model(label, model_id, source_points)

    valid_days = []
    if run_time:
        for day in range(FORECAST_DAYS):
            start = run_time.timestamp() + day * 86400
            end = start + 86400
            valid_days.append({
                "day": day + 1,
                "start": datetime.fromtimestamp(start, timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end, timezone.utc).isoformat(),
            })

    grid = []
    for lat in display_lats:
        for lon in display_lons:
            models = {}
            for name in MODELS:
                rain_days = []
                wind_days = []
                for day in range(FORECAST_DAYS):
                    six_indices = range(day * 4, day * 4 + 4)
                    vals = [interpolate_array(standard[name], source_lats, source_lons, lat, lon, p) for p in six_indices]
                    rain_days.append(None if all(v is None for v in vals) else round(sum(v or 0 for v in vals), 2))
                    wind_days.append(interpolate_uv(standard[name + "::wind"], source_lats, source_lons, lat, lon, day))
                models[name] = {"rain": rain_days, "wind850": wind_days}

            aifs = {}
            for label in AIFS_MODELS:
                aifs[label] = [
                    interpolate_array(aifs_source[label], source_lats, source_lons, lat, lon, day)
                    for day in range(FORECAST_DAYS)
                ]

            grid.append({"lat": lat, "lon": lon, "models": models, "aifs": aifs})

    generated_at = datetime.now(timezone.utc).isoformat()

    out = {
        # Kept as the model run time because the existing browser uses this
        # field for the visible "Model Run" label. The real file-generation
        # time is preserved separately in generated_at.
        "updated": primary_run_iso,
        "generated_at": generated_at,
        "source": "Open-Meteo model-specific APIs + Open-Meteo Ensemble Mean API; 1° source grid interpolated to 0.5° display grid",
        "domain": {"lat_min": LAT_MIN, "lat_max": LAT_MAX, "lon_min": LON_MIN, "lon_max": LON_MAX},
        "step": DISPLAY_STEP,
        "source_step": SOURCE_STEP,
        "forecast_days": FORECAST_DAYS,
        "periods": [f"Day {d}" for d in range(1, FORECAST_DAYS + 1)],
        "valid_days": valid_days,
        "models": list(MODELS.keys()),
        "model_horizons": MODEL_HORIZONS,
        "model_runs": model_runs,
        "primary_model_run": primary_run_iso,
        "aifs_models": list(AIFS_MODELS.keys()),
        "aifs_model_ids": AIFS_MODELS,
        "aifs_model_horizons": AIFS_MODEL_HORIZONS,
        "aifs_set_note": "Independent precipitation products; not blended with the standard model set.",
        "wind850": {"enabled": True, "level_hpa": 850, "representative_utc": "12:00", "units": "m/s"},
        "grid": grid,
    }

    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    tmp.replace(OUT)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024 / 1024:.2f} MB)")
    print("AIFS Set stored separately; it is not blended with ECMWF HRES/GEM/GFS/ICON.")


if __name__ == "__main__":
    main()
