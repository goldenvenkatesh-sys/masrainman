#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MASRAINMAN TALK ENGINE - RENDER MEMORY-SAFE SERVICE
===================================================

Purpose
-------
Dedicated Render service for the MasRainMan Blogger Talk Engine.

IMPORTANT ARCHITECTURE
----------------------
This service does NOT import the full NEM dashboard.

It only:
    1. resolves the requested town/city
    2. creates a Talk job
    3. runs ONE existing app.py Talk worker at a time
    4. stores progressive model results
    5. exposes /talk/ping, /talk/status, /talk/query and /talk/progress

The existing app.py already contains the validated model-specific Talk
worker and supports:

    python app.py --talk-worker MODEL PLACE_JSON RESULT_JSON

The old dashboard parent launched 7 of those workers simultaneously.
On Render Free (512 MB RAM), that can cause the service to exceed memory.

THIS VERSION:
    - never launches 7 workers simultaneously
    - only ONE model subprocess is active at a time
    - the parent service is lightweight
    - results still appear progressively: 1/7, 2/7, ... 7/7
    - the Blogger CORS origin remains masrainman.blogspot.com
    - /talk/progress remains compatible with the existing Blogger UI

Render Start Command
--------------------
    python talk_service.py

Environment variables
---------------------
    PORT
    MASRAINMAN_TALK_CORS_ORIGIN
    MASRAINMAN_TALK_PUBLIC_URL

Expected files in the same repository
--------------------------------------
    talk_service.py
    app.py
    requirements.txt
    runtime.txt
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone
from typing import Any


# ============================================================================
# CONFIG
# ============================================================================

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "10000"))

APP_NAME = "MASRAINMAN TALK ENGINE"
APP_VERSION = (
    "V196 RENDER MEMORY SAFE • TALK + VOICE + "
    "SEQUENTIAL 7-MODEL API"
)

TALK_CORS_ORIGIN = os.environ.get(
    "MASRAINMAN_TALK_CORS_ORIGIN",
    "https://masrainman.blogspot.com",
).rstrip("/")

TALK_PUBLIC_URL = os.environ.get(
    "MASRAINMAN_TALK_PUBLIC_URL",
    "https://masrainman.onrender.com",
).rstrip("/")

BASE_DIR = Path(__file__).resolve().parent
APP_PY = BASE_DIR / "app.py"

# The seven validated Talk models.
TALK_CANDIDATE_MODELS = [
    "ECMWF",
    "GFS",
    "ICON",
    "AIFS",
    "UKMET",
    "GEM",
    "AIGFS",
]

# Memory safety:
# Only ONE app.py worker is allowed to exist at a time.
#
# This is deliberately conservative for Render Free 512 MB.
MAX_ACTIVE_WORKERS = 1

# Normal model timeouts.
# AIFS and UKMET can require longer.
MODEL_TIMEOUTS = {
    "ECMWF": 180,
    "GFS": 180,
    "ICON": 180,
    "AIFS": 360,
    "UKMET": 360,
    "GEM": 180,
    "AIGFS": 240,
}

# Job state is kept long enough for the Blogger browser to finish polling.
TALK_JOB_TTL = 1800

# Maximum number of completed jobs retained in memory.
MAX_RETAINED_JOBS = 20

# How frequently the sequential runner checks its queue.
QUEUE_SLEEP = 0.20

# A small directory is used for worker JSON handoff.
# Render's filesystem is ephemeral, but this is only transient job state.
RUNTIME_ROOT = Path(
    os.environ.get(
        "MASRAINMAN_TALK_RUNTIME",
        str(Path(tempfile.gettempdir()) / "masrainman_talk_service"),
    )
)
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)


# ============================================================================
# PLACE DATABASE
# ============================================================================
# We intentionally keep this lightweight. The actual app.py contains the full
# MasRainman gazetteer and its live geocoder. For the parent service, the
# common locations are resolved locally so a query does not require importing
# app.py just to resolve a city.
#
# Unknown locations are handled with a small online geocoder fallback.
# ============================================================================

TALK_PLACE_INDEX = {
    # Tamil Nadu / Puducherry
    "chennai": (13.0827, 80.2707, "Chennai"),
    "madras": (13.0827, 80.2707, "Chennai"),
    "chengalpattu": (12.6819, 79.9888, "Chengalpattu"),
    "kanchipuram": (12.8342, 79.7036, "Kanchipuram"),
    "tiruvallur": (13.1430, 79.9070, "Tiruvallur"),
    "thiruvallur": (13.1430, 79.9070, "Tiruvallur"),
    "vellore": (12.9165, 79.1325, "Vellore"),
    "ranipet": (12.9249, 79.3329, "Ranipet"),
    "tirupattur": (12.4950, 78.5677, "Tirupattur"),
    "tiruvannamalai": (12.2253, 79.0747, "Tiruvannamalai"),
    "thiruvannamalai": (12.2253, 79.0747, "Tiruvannamalai"),
    "villupuram": (11.9401, 79.4861, "Villupuram"),
    "viluppuram": (11.9401, 79.4861, "Villupuram"),
    "cuddalore": (11.7480, 79.7714, "Cuddalore"),
    "chidambaram": (11.3990, 79.6910, "Chidambaram"),
    "salem": (11.6643, 78.1460, "Salem"),
    "namakkal": (11.2194, 78.1670, "Namakkal"),
    "erode": (11.3410, 77.7172, "Erode"),
    "tiruppur": (11.1085, 77.3411, "Tiruppur"),
    "coimbatore": (11.0168, 76.9558, "Coimbatore"),
    "ooty": (11.4102, 76.6950, "Udhagamandalam"),
    "udhagamandalam": (11.4102, 76.6950, "Udhagamandalam"),
    "nilgiris": (11.4064, 76.6932, "The Nilgiris"),
    "karur": (10.9601, 78.0766, "Karur"),
    "trichy": (10.7905, 78.7047, "Tiruchirappalli"),
    "tiruchirappalli": (10.7905, 78.7047, "Tiruchirappalli"),
    "perambalur": (11.2320, 78.8801, "Perambalur"),
    "ariyalur": (11.1401, 79.0786, "Ariyalur"),
    "thanjavur": (10.7870, 79.1378, "Thanjavur"),
    "tanjore": (10.7870, 79.1378, "Thanjavur"),
    "nagapattinam": (10.7672, 79.8449, "Nagapattinam"),
    "mayiladuthurai": (11.1035, 79.6550, "Mayiladuthurai"),
    "karaikal": (10.9254, 79.8380, "Karaikal"),
    "puducherry": (11.9416, 79.8083, "Puducherry"),
    "pondicherry": (11.9416, 79.8083, "Puducherry"),
    "tiruvarur": (10.7724, 79.6368, "Tiruvarur"),
    "pudukottai": (10.3797, 78.8208, "Pudukkottai"),
    "sivaganga": (9.8433, 78.4809, "Sivaganga"),
    "karaikudi": (10.0731, 78.7808, "Karaikudi"),
    "madurai": (9.9252, 78.1198, "Madurai"),
    "dindigul": (10.3624, 77.9695, "Dindigul"),
    "theni": (10.0104, 77.4768, "Theni"),
    "virudhunagar": (9.5851, 77.9579, "Virudhunagar"),
    "sivakasi": (9.4490, 77.7979, "Sivakasi"),
    "ramanathapuram": (9.3639, 78.8395, "Ramanathapuram"),
    "rameswaram": (9.2885, 79.3129, "Rameswaram"),
    "thoothukudi": (8.7642, 78.1348, "Thoothukudi"),
    "tuticorin": (8.7642, 78.1348, "Thoothukudi"),
    "tirunelveli": (8.7139, 77.7567, "Tirunelveli"),
    "tenkasi": (8.9590, 77.3152, "Tenkasi"),
    "nagercoil": (8.1833, 77.4119, "Nagercoil"),
    "kanyakumari": (8.0883, 77.5385, "Kanyakumari"),
    "pollachi": (10.6581, 77.0081, "Pollachi"),
    "udumalpet": (10.5881, 77.2477, "Udumalpet"),
    "mettur": (11.7875, 77.8000, "Mettur"),
    "krishnagiri": (12.5186, 78.2137, "Krishnagiri"),
    "dharmapuri": (12.1211, 78.1582, "Dharmapuri"),
    "hosur": (12.7409, 77.8253, "Hosur"),

    # Kerala
    "thiruvananthapuram": (8.5241, 76.9366, "Thiruvananthapuram"),
    "trivandrum": (8.5241, 76.9366, "Thiruvananthapuram"),
    "kollam": (8.8932, 76.6141, "Kollam"),
    "alappuzha": (9.4981, 76.3388, "Alappuzha"),
    "alleppey": (9.4981, 76.3388, "Alappuzha"),
    "kottayam": (9.5916, 76.5222, "Kottayam"),
    "kochi": (9.9312, 76.2673, "Kochi"),
    "cochin": (9.9312, 76.2673, "Kochi"),
    "thrissur": (10.5276, 76.2144, "Thrissur"),
    "palakkad": (10.7867, 76.6548, "Palakkad"),
    "malappuram": (11.0510, 76.0711, "Malappuram"),
    "kozhikode": (11.2588, 75.7804, "Kozhikode"),
    "calicut": (11.2588, 75.7804, "Kozhikode"),
    "wayanad": (11.6854, 76.1320, "Wayanad"),
    "kannur": (11.8745, 75.3704, "Kannur"),
    "kasaragod": (12.4996, 74.9869, "Kasaragod"),

    # Karnataka
    "bengaluru": (12.9716, 77.5946, "Bengaluru"),
    "bangalore": (12.9716, 77.5946, "Bengaluru"),
    "mysuru": (12.2958, 76.6394, "Mysuru"),
    "mysore": (12.2958, 76.6394, "Mysuru"),
    "mangaluru": (12.9141, 74.8560, "Mangaluru"),
    "mangalore": (12.9141, 74.8560, "Mangaluru"),
    "hubballi": (15.3647, 75.1240, "Hubballi"),
    "dharwad": (15.4589, 75.0078, "Dharwad"),
    "belagavi": (15.8497, 74.4977, "Belagavi"),
    "shimoga": (13.9299, 75.5681, "Shivamogga"),
    "shivamogga": (13.9299, 75.5681, "Shivamogga"),

    # Andhra Pradesh
    "visakhapatnam": (17.6868, 83.2185, "Visakhapatnam"),
    "vizag": (17.6868, 83.2185, "Visakhapatnam"),
    "vijayawada": (16.5062, 80.6480, "Vijayawada"),
    "tirupati": (13.6288, 79.4192, "Tirupati"),
    "nellore": (14.4426, 79.9865, "Nellore"),
    "kakinada": (16.9891, 82.2475, "Kakinada"),
    "rajahmundry": (17.0005, 81.8040, "Rajahmundry"),

    # Telangana
    "hyderabad": (17.3850, 78.4867, "Hyderabad"),
    "warangal": (17.9689, 79.5941, "Warangal"),
}


# ============================================================================
# GLOBAL STATE
# ============================================================================

TALK_JOBS: dict[str, dict[str, Any]] = {}
TALK_JOBS_LOCK = threading.RLock()

# Only one native app.py worker is allowed to run globally.
TALK_WORKER_LOCK = threading.Lock()

# Job execution queue.
TALK_QUEUE: list[str] = []
TALK_QUEUE_CONDITION = threading.Condition(TALK_JOBS_LOCK)

SHUTDOWN = False


# ============================================================================
# UTILS
# ============================================================================

def now_ts() -> float:
    return time.time()


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_place(value: str) -> str:
    value = (value or "").strip().lower()
    value = " ".join(value.split())
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)


def safe_json_load(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ============================================================================
# PLACE RESOLUTION
# ============================================================================

def place_suggestions(query: str, limit: int = 3) -> list[dict[str, Any]]:
    q = normalize_place(query)
    if not q:
        return []

    scored: list[tuple[int, str]] = []

    for key in TALK_PLACE_INDEX:
        score = 0

        if key == q:
            score = 100
        elif key.startswith(q):
            score = 80
        elif q in key:
            score = 60
        else:
            # Very lightweight token overlap.
            qt = set(q.split())
            kt = set(key.split())
            overlap = len(qt & kt)
            if overlap:
                score = 30 + overlap

        if score:
            scored.append((score, key))

    scored.sort(key=lambda x: (-x[0], x[1]))

    out = []
    seen = set()

    for _, key in scored:
        if key in seen:
            continue
        seen.add(key)
        lat, lon, name = TALK_PLACE_INDEX[key]
        out.append(
            {
                "name": name,
                "lat": lat,
                "lon": lon,
                "key": key,
            }
        )
        if len(out) >= limit:
            break

    return out


def resolve_place(query: str) -> dict[str, Any] | None:
    q = normalize_place(query)

    if not q:
        return None

    if q in TALK_PLACE_INDEX:
        lat, lon, name = TALK_PLACE_INDEX[q]
        return {
            "query": query,
            "name": name,
            "lat": lat,
            "lon": lon,
            "exact": True,
            "resolver": "built-in",
            "geocode_source": "MasRainman Talk lightweight gazetteer",
        }

    # Try a small online fallback without importing app.py.
    # If unavailable, return None and let the UI show suggestions.
    try:
        import urllib.parse
        import urllib.request

        url = (
            "https://nominatim.openstreetmap.org/search?"
            + urllib.parse.urlencode(
                {
                    "q": query,
                    "format": "json",
                    "limit": 1,
                }
            )
        )

        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MasRainman-Talk/196",
                "Accept": "application/json",
            },
        )

        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode("utf-8"))

        if data:
            item = data[0]
            lat = float(item["lat"])
            lon = float(item["lon"])

            display = str(item.get("display_name", query))
            name = display.split(",")[0].strip() or query

            return {
                "query": query,
                "name": name,
                "lat": lat,
                "lon": lon,
                "exact": True,
                "resolver": "online",
                "geocode_source": "OpenStreetMap Nominatim",
            }

    except Exception as exc:
        print(
            f"[TALK PLACE] online geocoder unavailable: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    return None


# ============================================================================
# TALK AGGREGATION
# ============================================================================

def build_tamil_rain_brief(
    usable: dict[str, dict[str, Any]],
    peak_day: int,
    agreement: str,
) -> str:
    totals = []

    for value in usable.values():
        try:
            totals.append(float(value["total72h"]))
        except Exception:
            pass

    mean_total = sum(totals) / len(totals) if totals else 0.0

    if mean_total < 1:
        rain_text = "அடுத்த 2–3 நாட்களில் மழை அளவு மிகவும் குறைவாகவே தெரிகிறது."
    elif mean_total < 10:
        rain_text = "அடுத்த 2–3 நாட்களில் லேசான மழைக்கான சிக்னல் உள்ளது."
    elif mean_total < 25:
        rain_text = "அடுத்த 2–3 நாட்களில் மிதமான மழைக்கான சிக்னல் உள்ளது."
    else:
        rain_text = "அடுத்த 2–3 நாட்களில் குறிப்பிடத்தக்க மழைக்கான சிக்னல் உள்ளது."

    peak_text = (
        f"மாடல்களில் அதிக மழை Day {peak_day} காலப்பகுதியில் தெரிகிறது."
    )

    if agreement == "7/7 models available":
        source_text = "அனைத்து ஏழு மாடல்களும் கிடைக்கின்றன."
    elif agreement == "6/7 models available":
        source_text = "ஏழு மாடல்களில் ஆறு கிடைக்கின்றன."
    else:
        source_text = (
            f"{agreement.replace('models available', 'மாடல்கள் கிடைக்கின்றன')}; "
            "அதனால் இது கிடைத்த மாடல்களின் அடிப்படையிலான சுருக்கம்."
        )

    return f"{rain_text} {peak_text} {source_text}"


def aggregate_job(place: dict[str, Any], models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    usable = {
        key: value
        for key, value in models.items()
        if isinstance(value, dict)
        and "error" not in value
        and "total72h" in value
    }

    totals = []

    for value in usable.values():
        try:
            totals.append(float(value["total72h"]))
        except Exception:
            pass

    count = len(usable)
    total_models = len(TALK_CANDIDATE_MODELS)

    agreement = f"{count}/{total_models} models available"

    if not usable:
        return {
            "mean72h_mm": 0.0,
            "spread72h_mm": None,
            "peak_day": 1,
            "peak_day_mm": 0.0,
            "low_day": 1,
            "low_day_mm": 0.0,
            "tamil_brief": (
                f"{agreement}; தற்போது கிடைத்த மாடல் தரவு இல்லை."
            ),
            "usable_model_count": 0,
            "agreement": agreement,
        }

    mean_total = sum(totals) / len(totals)

    spread = (
        max(totals) - min(totals)
        if len(totals) > 1
        else None
    )

    day_means = []

    for day in (1, 2, 3):
        values = []

        for value in usable.values():
            try:
                values.append(float(value[f"day{day}"]))
            except Exception:
                pass

        day_means.append(
            sum(values) / len(values)
            if values
            else 0.0
        )

    peak_day = int(day_means.index(max(day_means))) + 1
    low_day = int(day_means.index(min(day_means))) + 1

    return {
        "mean72h_mm": round(mean_total, 1),
        "spread72h_mm": round(spread, 1) if spread is not None else None,
        "peak_day": peak_day,
        "peak_day_mm": round(day_means[peak_day - 1], 1),
        "low_day": low_day,
        "low_day_mm": round(day_means[low_day - 1], 1),
        "tamil_brief": build_tamil_rain_brief(
            usable,
            peak_day,
            agreement,
        ),
        "usable_model_count": count,
        "agreement": agreement,
    }


# ============================================================================
# JOB SNAPSHOT
# ============================================================================

def job_snapshot(job_id: str) -> dict[str, Any] | None:
    with TALK_JOBS_LOCK:
        job = TALK_JOBS.get(job_id)

        if not job:
            return None

        models = {
            key: dict(value)
            for key, value in job["models"].items()
        }

        completed = sum(
            1
            for model in TALK_CANDIDATE_MODELS
            if model in models
        )

        usable = sum(
            1
            for value in models.values()
            if isinstance(value, dict)
            and "error" not in value
        )

        aggregate = aggregate_job(
            job["place"],
            models,
        )

        return {
            "ok": True,
            "job_id": job_id,
            "place": job["place"],
            "models": models,
            "model_order": TALK_CANDIDATE_MODELS,
            "usable_model_count": usable,
            "completed_model_count": completed,
            "pending_model_count": (
                len(TALK_CANDIDATE_MODELS) - completed
            ),
            "running": bool(job.get("running", False)),
            "complete": bool(job.get("complete", False)),
            "queued": bool(job.get("queued", False)),
            "current_model": job.get("current_model"),
            "started_at": job.get("started_at"),
            "updated_at": job.get("updated_at"),
            "error": job.get("error"),
            **aggregate,
            "disclaimer": (
                "Model guidance only — not an official forecast. "
                "Actual rainfall can differ."
            ),
            "engine": (
                "MASRAINMAN TALK • memory-safe sequential live models • "
                "ECMWF + GFS + ICON + AIFS + UKMET + GEM + AIGFS"
            ),
        }


# ============================================================================
# WORKER EXECUTION
# ============================================================================

def run_one_model_worker(
    model: str,
    place: dict[str, Any],
    workdir: Path,
) -> dict[str, Any]:
    """
    Run the already-validated app.py Talk worker.

    Critical memory rule:
        This function is called while TALK_WORKER_LOCK is held.

    Therefore only ONE native model decoder can exist on this Render
    instance at any moment.
    """

    if not APP_PY.exists():
        return {
            "error": (
                f"Required worker file not found: {APP_PY.name}"
            )
        }

    result_path = workdir / f"{model.lower()}_result.json"

    try:
        result_path.unlink(missing_ok=True)
    except Exception:
        pass

    place_json = json.dumps(
        place,
        ensure_ascii=False,
        separators=(",", ":"),
    )

    cmd = [
        sys.executable,
        str(APP_PY),
        "--talk-worker",
        model,
        place_json,
        str(result_path),
    ]

    print(
        f"[TALK WORKER] START {model} "
        f"timeout={MODEL_TIMEOUTS.get(model, 180)}s",
        flush=True,
    )

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
    except Exception as exc:
        return {
            "error": (
                f"Worker launch failed: "
                f"{type(exc).__name__}: {exc}"
            )
        }

    timeout = MODEL_TIMEOUTS.get(model, 180)

    try:
        return_code = proc.wait(timeout=timeout)

    except subprocess.TimeoutExpired:
        print(
            f"[TALK WORKER] TIMEOUT {model} "
            f"after {timeout}s",
            flush=True,
        )

        try:
            proc.kill()
        except Exception:
            pass

        try:
            proc.wait(timeout=15)
        except Exception:
            pass

        return {
            "error": (
                f"{model} Talk worker timed out "
                f"after {timeout}s"
            )
        }

    # Give the filesystem a moment only if the process exited cleanly.
    # The worker writes its JSON before returning.
    if result_path.exists():
        data = safe_json_load(result_path)

        if isinstance(data, dict):
            if data.get("ok"):
                data.pop("ok", None)
                data.pop("model", None)

                print(
                    f"[TALK WORKER] COMPLETE {model} "
                    f"total72h={data.get('total72h')}",
                    flush=True,
                )

                return data

            error = data.get(
                "error",
                f"Worker exited with code {return_code}",
            )

            return {
                "error": error,
                "worker_traceback": data.get(
                    "traceback",
                    "",
                ),
            }

    if return_code < 0:
        return {
            "error": (
                f"{model} worker terminated by signal "
                f"{-return_code}; native decoder crash suspected"
            )
        }

    return {
        "error": (
            f"{model} worker exited with code "
            f"{return_code} before returning a result"
        )
    }


# ============================================================================
# SEQUENTIAL JOB RUNNER
# ============================================================================

def execute_job(job_id: str) -> None:
    """
    Execute exactly one model at a time.

    This is the key Render memory fix.
    """

    workdir = (
        RUNTIME_ROOT
        / job_id
    )

    workdir.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        with TALK_JOBS_LOCK:
            job = TALK_JOBS.get(job_id)

            if not job:
                return

            job["queued"] = False
            job["running"] = True
            job["started_at"] = utc_iso()
            job["updated_at"] = now_ts()

        print(
            f"[TALK JOB] START {job_id} "
            f"place={job['place']['name']}",
            flush=True,
        )

        for model in TALK_CANDIDATE_MODELS:

            with TALK_JOBS_LOCK:
                job = TALK_JOBS.get(job_id)

                if not job:
                    return

                if job.get("cancelled"):
                    job["running"] = False
                    job["complete"] = True
                    job["updated_at"] = now_ts()
                    return

                job["current_model"] = model
                job["updated_at"] = now_ts()

            # The lock is global across all Talk jobs.
            # This prevents two simultaneous website visitors from causing
            # two heavy app.py workers to run together.
            with TALK_WORKER_LOCK:
                result = run_one_model_worker(
                    model,
                    job["place"],
                    workdir,
                )

            with TALK_JOBS_LOCK:
                job = TALK_JOBS.get(job_id)

                if not job:
                    return

                job["models"][model] = result
                job["updated_at"] = now_ts()

                if "error" in result:
                    print(
                        f"[TALK JOB] {job_id} "
                        f"{model} FAILED: "
                        f"{result.get('error')}",
                        flush=True,
                    )
                else:
                    print(
                        f"[TALK JOB] {job_id} "
                        f"{model} OK",
                        flush=True,
                    )

            # Immediately allow the next model to become visible to the
            # Blogger polling client.
            time.sleep(0.05)

        with TALK_JOBS_LOCK:
            job = TALK_JOBS.get(job_id)

            if job:
                job["current_model"] = None
                job["running"] = False
                job["complete"] = True
                job["queued"] = False
                job["updated_at"] = now_ts()

        print(
            f"[TALK JOB] COMPLETE {job_id}",
            flush=True,
        )

    except Exception as exc:

        print(
            f"[TALK JOB] FATAL {job_id}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

        traceback.print_exc()

        with TALK_JOBS_LOCK:
            job = TALK_JOBS.get(job_id)

            if job:
                job["running"] = False
                job["complete"] = True
                job["current_model"] = None
                job["error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
                job["updated_at"] = now_ts()

    finally:
        try:
            shutil.rmtree(
                workdir,
                ignore_errors=True,
            )
        except Exception:
            pass


def queue_worker_loop() -> None:
    """
    Single background queue consumer.

    There is deliberately only one consumer.
    """

    print(
        "[TALK QUEUE] Sequential worker online "
        "• MAX_ACTIVE_WORKERS=1",
        flush=True,
    )

    while not SHUTDOWN:

        job_id = None

        with TALK_QUEUE_CONDITION:
            while (
                not TALK_QUEUE
                and not SHUTDOWN
            ):
                TALK_QUEUE_CONDITION.wait(
                    timeout=2.0
                )

            if SHUTDOWN:
                break

            if TALK_QUEUE:
                job_id = TALK_QUEUE.pop(0)

        if job_id:
            execute_job(job_id)

        time.sleep(QUEUE_SLEEP)


# ============================================================================
# CLEANUP
# ============================================================================

def cleanup_loop() -> None:
    while not SHUTDOWN:
        time.sleep(60)

        cutoff = now_ts() - TALK_JOB_TTL

        with TALK_JOBS_LOCK:

            old_ids = [
                job_id
                for job_id, job in TALK_JOBS.items()
                if float(job.get("updated_at", 0))
                < cutoff
                and not job.get("running")
            ]

            for job_id in old_ids:
                TALK_JOBS.pop(
                    job_id,
                    None,
                )

            # Keep memory bounded even if traffic spikes.
            if len(TALK_JOBS) > MAX_RETAINED_JOBS:

                sortable = sorted(
                    TALK_JOBS.items(),
                    key=lambda item: float(
                        item[1].get(
                            "updated_at",
                            0,
                        )
                    ),
                )

                for job_id, job in sortable:
                    if len(TALK_JOBS) <= MAX_RETAINED_JOBS:
                        break

                    if not job.get("running"):
                        TALK_JOBS.pop(
                            job_id,
                            None,
                        )


# ============================================================================
# API BUILDERS
# ============================================================================

def build_talk_request(query: str) -> dict[str, Any]:
    place = resolve_place(query)

    if place is None:
        suggestions = place_suggestions(
            query,
            limit=3,
        )

        names = [
            item["name"]
            for item in suggestions
        ]

        suggestion_text = (
            "; ".join(names)
            if names
            else "Chennai; Madurai; Coimbatore"
        )

        return {
            "ok": False,
            "query": query,
            "message": (
                "இந்த ஊரின் இருப்பிடம் கண்டுபிடிக்க முடியவில்லை. "
                "அருகிலுள்ள பெரிய நகரம் அல்லது மாவட்டத் தலைமையகத்தின் "
                "பெயரை தமிழில் சொல்லி முயற்சிக்கவும்."
            ),
            "voice_tamil": (
                "இந்த ஊரின் இருப்பிடம் கண்டுபிடிக்க முடியவில்லை. "
                "அருகிலுள்ள பெரிய நகரம் அல்லது மாவட்டத் தலைமையகத்தின் "
                "பெயரை தமிழில் சொல்லி முயற்சிக்கவும்."
            ),
            "suggestions": suggestions,
            "suggestion_text": suggestion_text,
            "models": {},
        }

    # Protect against excessive simultaneous jobs.
    with TALK_JOBS_LOCK:
        active_jobs = sum(
            1
            for job in TALK_JOBS.values()
            if job.get("running")
            or job.get("queued")
        )

        if active_jobs >= 3:
            return {
                "ok": False,
                "query": query,
                "place": place,
                "message": (
                    "The Talk Engine is currently processing "
                    "other live requests. Please try again shortly."
                ),
                "voice_tamil": (
                    "டாக் என்ஜின் தற்போது மற்றொரு நேரடி கோரிக்கையை "
                    "செயலாக்கிக் கொண்டிருக்கிறது. சிறிது நேரம் கழித்து "
                    "மீண்டும் முயற்சிக்கவும்."
                ),
                "models": {},
                "busy": True,
            }

        job_id = (
            f"talk-{int(time.time() * 1000)}-"
            f"{threading.get_ident()}"
        )

        job = {
            "job_id": job_id,
            "place": place,
            "models": {},
            "running": False,
            "complete": False,
            "queued": True,
            "current_model": None,
            "created_at": now_ts(),
            "updated_at": now_ts(),
        }

        TALK_JOBS[job_id] = job
        TALK_QUEUE.append(job_id)

        TALK_QUEUE_CONDITION.notify()

    print(
        f"[TALK REQUEST] "
        f"{query!r} -> {place['name']} "
        f"job={job_id}",
        flush=True,
    )

    snapshot = job_snapshot(job_id)

    if snapshot is None:
        return {
            "ok": False,
            "error": "Unable to create Talk job",
        }

    snapshot["started"] = True

    snapshot["message"] = (
        "Live model analysis queued. "
        "Models are processed sequentially to keep Render memory stable. "
        "Each verified model appears immediately."
    )

    snapshot["location_message"] = (
        f"Location resolved: "
        f"{place['name']} • "
        f"{place['lat']:.4f}°N, "
        f"{place['lon']:.4f}°E"
    )

    snapshot["geocode_source"] = place.get(
        "geocode_source",
        "",
    )

    return snapshot


# ============================================================================
# HTTP HEADERS
# ============================================================================

def send_talk_headers(
    handler: BaseHTTPRequestHandler,
    content_type: str = "application/json; charset=utf-8",
    length: int | None = None,
) -> None:

    handler.send_header(
        "Content-Type",
        content_type,
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        TALK_CORS_ORIGIN,
    )

    handler.send_header(
        "Vary",
        "Origin",
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, OPTIONS",
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type, Accept, Cache-Control",
    )

    handler.send_header(
        "Access-Control-Max-Age",
        "600",
    )

    handler.send_header(
        "Cache-Control",
        "no-store, no-cache, must-revalidate, max-age=0",
    )

    handler.send_header(
        "Pragma",
        "no-cache",
    )

    if length is not None:
        handler.send_header(
            "Content-Length",
            str(length),
        )


def write_json(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, Any],
    status_code: int = 200,
) -> None:

    data = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    handler.send_response(status_code)

    send_talk_headers(
        handler,
        length=len(data),
    )

    handler.end_headers()

    try:
        handler.wfile.write(data)
    except (
        BrokenPipeError,
        ConnectionResetError,
        ConnectionAbortedError,
    ):
        pass


# ============================================================================
# HTTP HANDLER
# ============================================================================

class Handler(BaseHTTPRequestHandler):

    server_version = "MasRainmanTalk/196"

    def log_message(
        self,
        format: str,
        *args: Any,
    ) -> None:
        print(
            "[HTTP] " + format % args,
            flush=True,
        )

    def do_OPTIONS(self) -> None:

        path = urlparse(
            self.path
        ).path

        if path.startswith("/talk/"):
            self.send_response(204)

            send_talk_headers(
                self,
            )

            self.end_headers()

            return

        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:

        parsed = urlparse(
            self.path
        )

        path = parsed.path
        query = parse_qs(
            parsed.query
        )

        try:

            # ------------------------------------------------------------
            # ROOT
            # ------------------------------------------------------------

            if path in ("/", "/index.html"):

                payload = {
                    "ok": True,
                    "service": APP_NAME,
                    "version": APP_VERSION,
                    "message": (
                        "MasRainman Talk Engine is running."
                    ),
                    "talk_endpoints": [
                        "/talk/ping",
                        "/talk/status",
                        "/talk/query?q=Chennai",
                        "/talk/progress?job=JOB_ID",
                    ],
                }

                write_json(
                    self,
                    payload,
                )

                return

            # ------------------------------------------------------------
            # PING
            # ------------------------------------------------------------

            if path == "/talk/ping":

                write_json(
                    self,
                    {
                        "ok": True,
                        "version": APP_VERSION,
                        "service": "talk",
                        "utc": utc_iso(),
                        "model_count": len(
                            TALK_CANDIDATE_MODELS
                        ),
                        "model_order": TALK_CANDIDATE_MODELS,
                        "public_url": TALK_PUBLIC_URL,
                        "cors_origin": TALK_CORS_ORIGIN,
                        "memory_policy": (
                            "ONE native app.py Talk worker at a time"
                        ),
                        "max_active_workers": (
                            MAX_ACTIVE_WORKERS
                        ),
                    },
                )

                return

            # ------------------------------------------------------------
            # STATUS
            # ------------------------------------------------------------

            if path == "/talk/status":

                with TALK_JOBS_LOCK:
                    queued = sum(
                        1
                        for job in TALK_JOBS.values()
                        if job.get("queued")
                    )

                    running = sum(
                        1
                        for job in TALK_JOBS.values()
                        if job.get("running")
                    )

                    completed = sum(
                        1
                        for job in TALK_JOBS.values()
                        if job.get("complete")
                    )

                write_json(
                    self,
                    {
                        "ok": True,
                        "version": APP_VERSION,
                        "service": "talk",
                        "models": {
                            model: {
                                "ready": True,
                                "mode": "isolated app.py worker",
                            }
                            for model
                            in TALK_CANDIDATE_MODELS
                        },
                        "model_order": (
                            TALK_CANDIDATE_MODELS
                        ),
                        "talk_policy": (
                            "progressive 1/7 to 7/7; "
                            "ONE model worker at a time; "
                            "no dashboard import in parent service"
                        ),
                        "memory_policy": {
                            "max_active_workers": (
                                MAX_ACTIVE_WORKERS
                            ),
                            "active_jobs": running,
                            "queued_jobs": queued,
                            "completed_jobs": completed,
                        },
                    },
                )

                return

            # ------------------------------------------------------------
            # QUERY
            # ------------------------------------------------------------

            if path == "/talk/query":

                place_query = (
                    query.get(
                        "q",
                        [""],
                    )[0]
                )

                result = build_talk_request(
                    place_query
                )

                write_json(
                    self,
                    result,
                    202 if result.get("ok") else 404,
                )

                return

            # ------------------------------------------------------------
            # PROGRESS
            # ------------------------------------------------------------

            if path == "/talk/progress":

                job_id = query.get(
                    "job",
                    [""],
                )[0]

                result = job_snapshot(
                    job_id
                )

                if result is None:

                    # Do not expose a generic HTML 404.
                    # Return a machine-readable JSON response so the Blogger
                    # Talk UI can display a useful connection/restart message.
                    write_json(
                        self,
                        {
                            "ok": False,
                            "job_id": job_id,
                            "error": (
                                "Talk job not found. "
                                "The Render instance may have restarted "
                                "or the job may have expired."
                            ),
                            "restart_detected": True,
                            "service": "talk",
                        },
                        404,
                    )

                    return

                write_json(
                    self,
                    result,
                )

                return

            # ------------------------------------------------------------
            # HEALTH
            # ------------------------------------------------------------

            if path == "/health":

                with TALK_JOBS_LOCK:
                    active = sum(
                        1
                        for job in TALK_JOBS.values()
                        if job.get("running")
                    )

                write_json(
                    self,
                    {
                        "ok": True,
                        "service": APP_NAME,
                        "version": APP_VERSION,
                        "port": PORT,
                        "utc": utc_iso(),
                        "active_workers": active,
                        "max_active_workers": (
                            MAX_ACTIVE_WORKERS
                        ),
                        "app_worker_exists": APP_PY.exists(),
                    },
                )

                return

            # ------------------------------------------------------------
            # 404
            # ------------------------------------------------------------

            write_json(
                self,
                {
                    "ok": False,
                    "error": "Endpoint not found",
                    "path": path,
                },
                404,
            )

        except Exception as exc:

            print(
                f"[HTTP ERROR] "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

            traceback.print_exc()

            write_json(
                self,
                {
                    "ok": False,
                    "error": (
                        f"{type(exc).__name__}: {exc}"
                    ),
                },
                500,
            )


# ============================================================================
# MAIN
# ============================================================================

def main() -> None:

    if not APP_PY.exists():
        print(
            f"[FATAL] {APP_PY.name} is required in the same "
            f"repository as {Path(__file__).name}",
            flush=True,
        )
        raise SystemExit(1)

    print("=" * 78)
    print(APP_NAME)
    print(APP_VERSION)
    print("=" * 78)
    print(
        f"Host              : {HOST}"
    )
    print(
        f"Port              : {PORT}"
    )
    print(
        f"Public URL        : {TALK_PUBLIC_URL}"
    )
    print(
        f"CORS origin       : {TALK_CORS_ORIGIN}"
    )
    print(
        f"Worker script     : {APP_PY}"
    )
    print(
        f"Models            : {', '.join(TALK_CANDIDATE_MODELS)}"
    )
    print(
        f"Max active workers: {MAX_ACTIVE_WORKERS}"
    )
    print(
        "Memory policy     : SEQUENTIAL — never 7 simultaneous workers"
    )
    print("=" * 78)

    queue_thread = threading.Thread(
        target=queue_worker_loop,
        name="TalkQueueWorker",
        daemon=True,
    )

    cleanup_thread = threading.Thread(
        target=cleanup_loop,
        name="TalkCleanup",
        daemon=True,
    )

    queue_thread.start()
    cleanup_thread.start()

    server = ThreadingHTTPServer(
        (HOST, PORT),
        Handler,
    )

    try:
        print(
            f"[SERVER] Listening on "
            f"{HOST}:{PORT}",
            flush=True,
        )

        server.serve_forever()

    except KeyboardInterrupt:
        print(
            "[SERVER] Shutdown requested",
            flush=True,
        )

    finally:

        global SHUTDOWN
        SHUTDOWN = True

        with TALK_QUEUE_CONDITION:
            TALK_QUEUE_CONDITION.notify_all()

        try:
            server.shutdown()
        except Exception:
            pass

        try:
            server.server_close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
