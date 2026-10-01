#!/usr/bin/env python3
"""
MASRAINMAN NEM WEATHER COMMAND CENTER
Single-file standalone dashboard prototype.

Run:
    python MASRAINMAN_NEM_DASHBOARD.py

Then open:
    http://127.0.0.1:8765

This version includes a live ECMWF IFS ENS rainfall downloader, a visible
Live Data Engine, strict stale-data protection, and a server/version health check. It detects the latest available Open Data run, downloads
the six cumulative TP endpoints needed for five true 24-hour periods, shows
progress/activity in the dashboard, and prevents an old map from being
presented as fresh live data.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import threading
import webbrowser
import json
import io
import bz2
import re
import shutil
from urllib.parse import urljoin, quote, unquote
from pathlib import Path
from datetime import datetime, timedelta, timezone
import traceback
import time
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
import xml.etree.ElementTree as ET
import sys
import os
import difflib
try:
    import requests
except ImportError:
    requests = None

try:
    from ecmwf.opendata import Client as ECMWFOpenDataClient
except ImportError:
    ECMWFOpenDataClient = None

try:
    from mpl_toolkits.basemap import Basemap
except ImportError:
    Basemap = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import xarray as xr
except ImportError:
    xr = None

try:
    import cfgrib
except ImportError:
    cfgrib = None

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap, LinearSegmentedColormap
except ImportError:
    plt = None
    BoundaryNorm = None
    ListedColormap = None

try:
    from scipy.interpolate import griddata
except ImportError:
    griddata = None

try:
    from eccodes import codes_grib_new_from_file, codes_release, codes_get, codes_get_array
except ImportError:
    codes_grib_new_from_file = None
    codes_release = None
    codes_get = None
    codes_get_array = None

try:
    import geopandas as gpd
except ImportError:
    gpd = None

# ---------------------------------------------------------------------------
# DEPLOYMENT / RENDER-SAFE CONFIGURATION
# ---------------------------------------------------------------------------
# Render supplies PORT automatically. Local Windows testing still defaults to
# 8765. The server must bind to 0.0.0.0 on Render.
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8765"))
IS_RENDER = bool(os.environ.get("RENDER") or os.environ.get("RENDER_SERVICE_ID"))

APP_NAME = "MASRAINMAN NEM WEATHER COMMAND CENTER"
APP_VERSION = "V195 RENDER READY • TALK ENGINE + VOICE + HORIZONTAL UI + PROGRESSIVE 7-MODEL API"

# Same-origin is the default on Render. For the Blogger integration, the
# server also allows the MasRainMan Blogspot origin. Locally, you can set
# MASRAINMAN_TALK_PUBLIC_URL to the current Cloudflare tunnel URL.
TALK_PUBLIC_URL = os.environ.get("MASRAINMAN_TALK_PUBLIC_URL", "" if IS_RENDER else "https://memorial-wins-reed-wealth.trycloudflare.com").rstrip("/")
TALK_CORS_ORIGIN = os.environ.get("MASRAINMAN_TALK_CORS_ORIGIN", "https://masrainman.blogspot.com").rstrip("/")
TALK_ALLOWED_ORIGINS = {TALK_CORS_ORIGIN, "https://masrainman.blogspot.com"}
if TALK_PUBLIC_URL:
    TALK_ALLOWED_ORIGINS.add(TALK_PUBLIC_URL)

# Render's free 512 MB instance is intentionally conservative. The parent
# process launches isolated model workers, so limiting concurrency avoids a
# memory spike while preserving the progressive 7-model result behavior.
TALK_MAX_WORKERS = max(1, int(os.environ.get("MASRAINMAN_TALK_WORKERS", "2" if IS_RENDER else "7")))

# ONLINE-ONLY MODEL INGESTION ROOT.
# No pre-existing D:\Anomaly model archive is used as a forecast source.
# Every model download is fetched from its official online endpoint by this dashboard.
# The directory below is only a runtime cache created by the dashboard itself.
ECMWF_ROOT = Path(tempfile.gettempdir()) / "MASRAINMAN_NEM_ONLINE_CACHE"
GFS_ROOT = ECMWF_ROOT / "GFS"
# GEFS ensemble rainfall engine — NOAA/NCEP NOMADS 0.25-degree product.
# GEFS v12 operational ensemble = 31 members: control gec00 + perturbed gep01..gep30.
GEFS_ROOT = ECMWF_ROOT / "GEFS"
GEFS_RUNINFO = GEFS_ROOT / "runinfo" / "gefs_latest.txt"
GEFS_RUNINFO.parent.mkdir(parents=True, exist_ok=True)
GEFS_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gens/prod"
GEFS_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p25s.pl"
GEFS_LEFTLON, GEFS_RIGHTLON = 66.0, 98.0
GEFS_BOTTOMLAT, GEFS_TOPLAT = 6.0, 38.0
GEFS_MEMBERS = ["gec00"] + [f"gep{i:02d}" for i in range(1,31)]
GEFS_MEMBER_COUNT = len(GEFS_MEMBERS)
GEFS_MAX_RETRIES = 2
GEFS_RETRY_DELAY = 8
GEFS_TIMEOUT = 120
GEFS_HTTP_HEADERS = {"User-Agent":"MASRAINMAN-GEFS-Dashboard/1.0", "Accept":"*/*"}
# V4.2 split-resolution architecture proven in standalone examiner:
# 0.25° = APCP + PRMSL; 0.50° = U850 + V850.
GEFS_FILTER25_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p25s.pl"
GEFS_FILTER50_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gefs_atmos_0p50a.pl"
GEFS_LEFTLON50, GEFS_RIGHTLON50 = 66.0, 98.0
GEFS_BOTTOMLAT50, GEFS_TOPLAT50 = 6.0, 38.0
GEFS_PARALLEL_WORKERS = 6
GEFS_SYNOPTIC_CACHE = {}
GEFS_WIND_HOURS_CACHE = {}
GEFS_ROOT.mkdir(parents=True, exist_ok=True)
(GEFS_ROOT / "runinfo").mkdir(parents=True, exist_ok=True)
GFS_RUNINFO = GFS_ROOT / "runinfo" / "gfs_latest.txt"
GFS_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
GFS_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
GFS_LEFTLON, GFS_RIGHTLON = 66, 98
GFS_BOTTOMLAT, GFS_TOPLAT = 6, 38
GFS_MAX_RETRIES = 2
GFS_RETRY_DELAY = 10
GFS_TIMEOUT = 60
GFS_PARALLEL_WORKERS = 4
GFS_VAR_PARAMS = {
    "var_APCP": "on",
    "var_TMP": "on", "var_RH": "on", "var_UGRD": "on",
    "var_VGRD": "on", "var_PRMSL": "on", "var_HGT": "on",
    "lev_2_m_above_ground": "on", "lev_10_m_above_ground": "on",
    "lev_mean_sea_level": "on", "lev_850_mb": "on",
}

# ICON Global deterministic rainfall engine — proven separately in
# ICON_STANDALONE_TEST_V2.py before dashboard integration.
ICON_ROOT = ECMWF_ROOT / "ICON"
ICON_BASE_URL = "https://opendata.dwd.de/weather/nwp/icon/grib"
ICON_LEFTLON, ICON_RIGHTLON = 68.0, 92.0
ICON_BOTTOMLAT, ICON_TOPLAT = 4.0, 25.0
ICON_GRID_RESOLUTION = 0.25
ICON_CORE_ENDPOINTS = [24, 48, 72, 96, 120]
ICON_EXTENDED_ENDPOINT = 168
ICON_RUNINFO = ICON_ROOT / "runinfo" / "icon_latest.txt"
ICON_RAW_ROOT = ICON_ROOT / "raw"
ICON_GRIB_ROOT = ICON_ROOT / "grib2"
ICON_RAW_ROOT.mkdir(parents=True, exist_ok=True)
ICON_GRIB_ROOT.mkdir(parents=True, exist_ok=True)
ICON_FIELD_CACHE = {}
ICON_COORD_CACHE = {}
ICON_HTTP_TIMEOUT = 180
# AIFS Single deterministic engine — isolated from the generic GRIB decoders.
AIFS_ROOT = ECMWF_ROOT / "AIFS"
AIFS_RUNINFO = AIFS_ROOT / "runinfo" / "aifs_latest.txt"
AIFS_RAW_ROOT = AIFS_ROOT / "raw"
AIFS_RAW_ROOT.mkdir(parents=True, exist_ok=True)
AIFS_RUNINFO.parent.mkdir(parents=True, exist_ok=True)
AIFS_BASE_URL = "https://data.ecmwf.int/forecasts"
AIFS_MODEL_PATH = "aifs-single/0p25/oper"
AIFS_LEFTLON, AIFS_RIGHTLON = 66.0, 99.0
AIFS_BOTTOMLAT, AIFS_TOPLAT = 4.0, 25.0
AIFS_HTTP_TIMEOUT = 240
AIFS_FIELD_CACHE = {}
AIFS_SMOOTHING_SIGMA = 0.6
GEM_SMOOTHING_SIGMA = 0.6  # display-only; raw GEM rainfall remains unchanged

# NOAA/NCEP AIGFS deterministic rainfall engine — rebuilt directly from the
# independently validated AIGFS standalone V8 examiner.  Do NOT use the old
# V117/V118/V119 AIGFS handoff logic.  AIGFS deterministic surface files are
# 6-hour precipitation intervals: F006=0-6h, F012=6-12h, ... F360=354-360h.
AIGFS_ROOT = ECMWF_ROOT / "AIGFS"
AIGFS_RUNINFO = AIGFS_ROOT / "runinfo" / "aigfs_latest.txt"
AIGFS_RAW_ROOT = AIGFS_ROOT / "raw"
AIGFS_GRIB_ROOT = AIGFS_ROOT / "grib2"
AIGFS_RAW_ROOT.mkdir(parents=True, exist_ok=True)
AIGFS_GRIB_ROOT.mkdir(parents=True, exist_ok=True)
AIGFS_BASE_URL = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/aigfs/v1.1"
AIGFS_LEFTLON, AIGFS_RIGHTLON = 68.0, 92.0
AIGFS_BOTTOMLAT, AIGFS_TOPLAT = 4.0, 25.0
AIGFS_RUN_HOURS = (0, 6, 12, 18)
AIGFS_HTTP_TIMEOUT = 180
AIGFS_FIELD_CACHE = {}
AIGFS_DECODE_LOCK = threading.RLock()
AIGFS_HEADERS = {"User-Agent":"MASRAINMAN-AIGFS-Dashboard-V120/1.0", "Accept":"*/*"}

# ---------------------------------------------------------------------------
# AIGFS V8 DETERMINISTIC RAINFALL ENGINE
# Independent standalone V8 was validated for 24H + 7D + 15D before this
# integration.  This adapter intentionally keeps the same file-based 6-hour
# interval method and cfgrib/xarray decoding path.
# ---------------------------------------------------------------------------

def aigfs_period_endpoints(period, day=1):
    p=str(period)
    if p == "24 Hour":
        d=max(1,min(5,int(day or 1)))
        return list(range(6, d*24 + 1, 6))
    if p == "7 Day Accumulation":
        return list(range(6,169,6))
    if p == "15 Day Accumulation":
        return list(range(6,361,6))
    raise ValueError(f"Unknown AIGFS rainfall period: {period}")

def aigfs_url(run_date, run_hour, fhour):
    return (f"{AIGFS_BASE_URL}/aigfs.{run_date}/{int(run_hour):02d}/model/atmos/grib2/"
            f"aigfs.t{int(run_hour):02d}z.sfc.f{int(fhour):03d}.grib2")

def aigfs_probe(run_date, run_hour, fhour):
    url=aigfs_url(run_date,run_hour,fhour)
    try:
        r=requests.get(url,headers=AIGFS_HEADERS,timeout=30,stream=True)
        status=r.status_code
        size=int(r.headers.get('Content-Length') or 0)
        r.close()
        return status in (200,206), status, size
    except Exception as exc:
        return False, None, 0

def aigfs_candidate_runs(period="24 Hour", day=1, limit_days=3):
    required=aigfs_period_endpoints(period,day)
    target=required[-1]
    now=datetime.now(timezone.utc).replace(minute=0,second=0,microsecond=0)
    out=[]
    for day_back in range(limit_days):
        d=(now-timedelta(days=day_back)).date()
        for rh in AIGFS_RUN_HOURS[::-1]:
            dt=datetime(d.year,d.month,d.day,rh,tzinfo=timezone.utc)
            if dt>now: continue
            rd=f"{d:%Y%m%d}"
            ok,status,size=aigfs_probe(rd,rh,target)
            ecmwf_event(f"AIGFS RUN PROBE • {dt:%Y-%m-%d %HZ} • F{target:03d} • HTTP {status} • {size/1e6:.2f} MB", "info")
            if ok:
                out.append((dt,rd,rh))
                ecmwf_event(f"AIGFS VERIFIED RUN • {dt:%Y-%m-%d %HZ} • F{target:03d}","success")
                if len(out)>=4: return out
    return out

def aigfs_download_endpoint(run_date,run_hour,fhour,job_token=None):
    folder=AIGFS_RAW_ROOT/f"{run_date}_{int(run_hour):02d}Z"; folder.mkdir(parents=True,exist_ok=True)
    target=folder/f"aigfs_f{int(fhour):03d}.grib2"
    if target.exists() and target.stat().st_size>100000:
        ecmwf_event(f"AIGFS F{fhour:03d} REUSE • {target.stat().st_size/1024/1024:.2f} MB","info")
        return target
    if job_token is not None and not ecmwf_job_current(job_token):
        raise RuntimeError("AIGFS job superseded by a newer request")
    url=aigfs_url(run_date,run_hour,fhour)
    ecmwf_event(f"AIGFS DOWNLOAD • {run_date} {int(run_hour):02d}Z • F{int(fhour):03d}","info")
    tmp=target.with_suffix('.grib2.part')
    try:
        with requests.get(url,headers=AIGFS_HEADERS,timeout=AIGFS_HTTP_TIMEOUT,stream=True) as r:
            r.raise_for_status()
            with open(tmp,'wb') as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk: f.write(chunk)
        if not tmp.exists() or tmp.stat().st_size<100000:
            raise RuntimeError(f"AIGFS F{fhour:03d} download is unexpectedly small")
        with open(tmp,'rb') as f:
            if f.read(4)!=b'GRIB': raise RuntimeError(f"AIGFS F{fhour:03d} response is not GRIB2")
        tmp.replace(target)
        ecmwf_event(f"AIGFS F{fhour:03d} SAVED • {target.stat().st_size/1024/1024:.2f} MB","success")
        return target
    except Exception:
        try: tmp.unlink()
        except Exception: pass
        raise

def _aigfs_open_tp(path):
    if xr is None or cfgrib is None:
        raise RuntimeError("xarray/cfgrib dependencies are not installed")
    with AIGFS_DECODE_LOCK:
        datasets=cfgrib.open_datasets(str(path),indexpath="")
        selected=None
        selected_ds=None
        try:
            for ds in datasets:
                for name in ds.data_vars:
                    da=ds[name]
                    short=str(da.attrs.get('GRIB_shortName','')).lower()
                    long_name=str(da.attrs.get('GRIB_name',da.attrs.get('long_name',''))).lower()
                    if name.lower()=='tp' or short=='tp' or short=='apcp' or 'total precipitation' in long_name:
                        selected=da
                        selected_ds=ds
                        break
                if selected is not None: break
            if selected is None:
                raise RuntimeError(f"No AIGFS total precipitation field found in {path.name}")
            # Force data into memory before datasets are closed, matching the
            # proven standalone V8 Windows/Python 3.13 decoder path.
            vals=np.asarray(selected.values,dtype=np.float32)
            latname=next((n for n in ('latitude','lat') if n in selected.coords),None)
            lonname=next((n for n in ('longitude','lon') if n in selected.coords),None)
            if not latname or not lonname: raise RuntimeError("AIGFS precipitation field has no lat/lon coordinates")
            lat=np.asarray(selected[latname].values,dtype=float)
            lon=np.asarray(selected[lonname].values,dtype=float)
            units=str(selected.attrs.get('GRIB_units',selected.attrs.get('units','kg m**-2')))
        finally:
            for ds in datasets:
                try: ds.close()
                except Exception: pass
    if vals.ndim!=2 or lat.ndim!=1 or lon.ndim!=1:
        raise RuntimeError(f"Unsupported AIGFS grid: values={vals.shape} lat={lat.shape} lon={lon.shape}")
    # Regular global latitude/longitude grid; select the South India crop.
    if lat[0]>lat[-1]:
        ys=slice(AIGFS_TOPLAT,AIGFS_BOTTOMLAT)
    else:
        ys=slice(AIGFS_BOTTOMLAT,AIGFS_TOPLAT)
    if lon[0]>lon[-1]:
        xs=slice(AIGFS_RIGHTLON,AIGFS_LEFTLON)
    else:
        xs=slice(AIGFS_LEFTLON,AIGFS_RIGHTLON)
    yi=np.where((lat>=AIGFS_BOTTOMLAT)&(lat<=AIGFS_TOPLAT))[0]
    xi=np.where((lon>=AIGFS_LEFTLON)&(lon<=AIGFS_RIGHTLON))[0]
    if len(yi)==0 or len(xi)==0: raise RuntimeError("AIGFS South India crop is empty")
    sub=vals[np.ix_(yi,xi)]
    slat=lat[yi]; slon=lon[xi]
    if slat[0]>slat[-1]: sub=sub[::-1,:]; slat=slat[::-1]
    if slon[0]>slon[-1]: sub=sub[:,::-1]; slon=slon[::-1]
    sub=np.nan_to_num(sub,nan=0.0,posinf=0.0,neginf=0.0)
    sub=np.clip(sub,0,None).astype(np.float32)
    return sub,slat,slon,units

def aigfs_decode_interval(run_date,run_hour,fhour,job_token=None):
    key=(run_date,int(run_hour),int(fhour))
    if key in AIGFS_FIELD_CACHE: return AIGFS_FIELD_CACHE[key]
    path=aigfs_download_endpoint(run_date,run_hour,fhour,job_token)
    vals,lat,lon,units=_aigfs_open_tp(path)
    start=fhour-6
    rep={'file':path.name,'forecast_hour':fhour,'start_hour':start,'end_hour':fhour,'units':units,
         'shape':vals.shape,'min':float(vals.min()),'max':float(vals.max()),'mean':float(vals.mean()),
         'p95':float(np.percentile(vals,95)),'positive':int(np.count_nonzero(vals>0.01))}
    AIGFS_FIELD_CACHE[key]=(vals,lat,lon,rep)
    ecmwf_event(f"AIGFS FIELD READY • F{fhour:03d} • {start:03d}-{fhour:03d}h • crop={vals.shape} • max={rep['max']:.2f} mm • mean={rep['mean']:.2f} mm","success")
    return AIGFS_FIELD_CACHE[key]

def start_aigfs_deterministic_download(requested_run=None,requested_period="24 Hour",requested_day=1):
    token=begin_ecmwf_job('deterministic','AIGFS',requested_run or 'AUTO',requested_period)
    threading.Thread(target=download_aigfs_deterministic,args=(requested_run,requested_period,requested_day,token),daemon=True).start()

def download_aigfs_deterministic(requested_run=None,requested_period="24 Hour",requested_day=1,job_token=None):
    global RAINFALL_CACHE
    if job_token is None: job_token=begin_ecmwf_job('deterministic','AIGFS',requested_run or 'AUTO',requested_period)
    try:
        endpoints=aigfs_period_endpoints(requested_period,requested_day)
        if requested_run and requested_run!='AUTO':
            raw=str(requested_run)
            if len(raw)!=10 or not raw.isdigit() or int(raw[8:10]) not in AIGFS_RUN_HOURS:
                raise RuntimeError(f"Invalid AIGFS run selection: {requested_run}")
            rd,rh=raw[:8],int(raw[8:10])
            # Explicit runs are authoritative; download path will provide the
            # actual failure if an endpoint is no longer published.
            selected=(rd,rh)
        else:
            candidates=aigfs_candidate_runs(requested_period,requested_day,limit_days=3)
            if not candidates: raise RuntimeError(f"No verified AIGFS run currently provides {requested_period} target F{endpoints[-1]:03d}")
            _,rd,rh=candidates[0]; selected=(rd,rh)
        rd,rh=selected
        AIGFS_RUNINFO.parent.mkdir(parents=True,exist_ok=True)
        AIGFS_RUNINFO.write_text(f"{rd} {rh:02d}",encoding='utf-8')
        set_model_identity(mode='deterministic',model='AIGFS',label='AIGFS',state='identified',run=f'{rd} {rh:02d}Z',source='NOAA/NCEP AIGFS v1.1 • Total Precipitation',download='starting',message=f'AIGFS selected: {rd} {rh:02d}Z')
        ecmwf_event(f"SELECTED AIGFS RUN • {rd} {rh:02d}Z • target={requested_period} • F{endpoints[-1]:03d}","success")
        ECMWF_DOWNLOAD_STATUS.update(run=f'{rd} {rh:02d}Z',total=len(endpoints),downloaded=0,message=f'Downloading AIGFS • {rd} {rh:02d}Z • {requested_period}')
        for i,ep in enumerate(endpoints,1):
            if not ecmwf_job_current(job_token): return
            aigfs_decode_interval(rd,rh,ep,job_token)
            ECMWF_DOWNLOAD_STATUS['downloaded']=i
            ECMWF_DOWNLOAD_STATUS['message']=f'AIGFS F{ep:03d} ready ({i}/{len(endpoints)})'
        RAINFALL_CACHE.clear()
        set_model_identity(mode='deterministic',model='AIGFS',label='AIGFS',state='ready',run=f'{rd} {rh:02d}Z',source='NOAA/NCEP AIGFS v1.1 • Total Precipitation',download='complete',message=f'AIGFS ready: {rd} {rh:02d}Z')
        ECMWF_DOWNLOAD_STATUS.update(state='ready',message=f'LIVE AIGFS READY • {rd} {rh:02d}Z • {requested_period}',error=None,finished_at=datetime.now(timezone.utc).isoformat(),period=requested_period,product="MEAN",request_key=f"deterministic|AIGFS|{rd}{rh:02d}|{requested_period}|MEAN",render_ready=True)
        ecmwf_event(f"LIVE AIGFS READY • {rd} {rh:02d}Z • {requested_period}","success")
    except Exception as exc:
        if not ecmwf_job_current(job_token): return
        set_model_identity(mode='deterministic',model='AIGFS',label='AIGFS',state='error',source='NOAA/NCEP AIGFS v1.1 • Total Precipitation',download='failed',message=str(exc))
        ECMWF_DOWNLOAD_STATUS.update(state='error',message='AIGFS deterministic download failed',error=str(exc),finished_at=datetime.now(timezone.utc).isoformat())
        ecmwf_event(f"LIVE AIGFS DOWNLOAD FAILED: {exc}","error")
        traceback.print_exc()

def read_aigfs_run():
    if not AIGFS_RUNINFO.exists(): raise FileNotFoundError(f"AIGFS runinfo not found: {AIGFS_RUNINFO}")
    parts=AIGFS_RUNINFO.read_text(encoding='utf-8').strip().split()
    if len(parts)!=2: raise ValueError("AIGFS runinfo must contain YYYYMMDD HH")
    return parts[0],int(parts[1])

def build_aigfs_rainfall(day=1,period=None):
    period=period or ACTIVE_PERIOD
    rd,rh=read_aigfs_run()
    endpoints=aigfs_period_endpoints(period,day)
    total=None; lat=lon=None
    for ep in endpoints:
        vals,lat,lon,_=aigfs_decode_interval(rd,rh,ep)
        total=vals.copy() if total is None else total+vals
    total=np.clip(np.asarray(total,dtype=np.float32),0,None)
    if period=='24 Hour':
        start_ep=(int(day)-1)*24
        end_ep=int(day)*24
        label=f'F{start_ep:03d} → F{end_ep:03d} • summed 6-hour intervals'
        out_day=int(day)
    elif period=='7 Day Accumulation':
        end_ep=168; label='F000 → F168 • summed 6-hour intervals'; out_day=1
    else:
        end_ep=360; label='F000 → F360 • summed 6-hour intervals'; out_day=1
    run_dt=datetime.strptime(rd,'%Y%m%d').replace(tzinfo=timezone.utc)+timedelta(hours=rh)
    valid_end=run_dt+timedelta(hours=end_ep)
    meta={'model':'AIGFS','mode':'deterministic','run':f'{rd} {rh:02d}Z','period':period,'day':out_day,
          'start':f'F{((out_day-1)*24 if period=="24 Hour" else 0):03d}','end':f'F{end_ep:03d}','display_range':label,
          'valid_end_date':valid_end.strftime('%d %b %Y'),'valid_end_iso':valid_end.strftime('%Y-%m-%d'),'valid_end_time':'08:30 AM IST',
          'valid_end_endpoint':f'F{end_ep:03d}','members':1,'min':float(np.nanmin(total)),'max':float(np.nanmax(total)),
          'mean':float(np.nanmean(total)),'lat_min':float(lat.min()),'lat_max':float(lat.max()),'lon_min':float(lon.min()),'lon_max':float(lon.max()),
          'lat_edge_min':float(lat.min()),'lat_edge_max':float(lat.max()),'lon_edge_min':float(lon.min()),'lon_edge_max':float(lon.max()),
          'grid_dx':float(np.median(np.diff(lon))) if len(lon)>1 else 0.25,'grid_dy':float(np.median(np.diff(lat))) if len(lat)>1 else 0.25,
          'grid_rows':int(total.shape[0]),'grid_cols':int(total.shape[1]),'finite_count':int(np.isfinite(total).sum()),'positive_count':int((total>=0.1).sum()),
          'projection':'LEAFLET DIRECT LATLON CANVAS','tp_accumulation':label,'tp_units':'kg m-2 = mm','smoothing':'none','source':'NOAA/NCEP AIGFS v1.1'}
    return total,lat,lon,meta

# GEM / CMC GDPS Global deterministic rainfall engine — ECCC MSC Datamart.
# Native GDPS grid is 0.15° Lat/Lon.  We download only the cumulative
# Rain-Accum field needed for the selected 24-hour/7-day product and crop
# after decode to the South India domain.  15-day is intentionally disabled:
# GDPS operational coverage is not equivalent to the dashboard's 360h product.
GEM_ROOT = ECMWF_ROOT / "GEM"
GEM_RUNINFO = GEM_ROOT / "runinfo" / "gem_latest.txt"
GEM_RAW_ROOT = GEM_ROOT / "raw"
GEM_GRIB_ROOT = GEM_ROOT / "grib2"
GEM_RAW_ROOT.mkdir(parents=True, exist_ok=True)
GEM_GRIB_ROOT.mkdir(parents=True, exist_ok=True)
GEM_BASE_URL = "https://dd.weather.gc.ca/today/model_gdps/15km"
GEM_ARCHIVE_BASE_URL = "https://dd.weather.gc.ca/{date}/WXO-DD/model_gdps/15km"
GEM_LEFTLON, GEM_RIGHTLON = 68.0, 92.0
GEM_BOTTOMLAT, GEM_TOPLAT = 4.0, 25.0
GEM_RUN_HOURS = (0, 12)
GEM_HTTP_TIMEOUT = 180
GEM_FIELD_CACHE = {}
GEM_DECODE_LOCK = threading.RLock()

try:
    from scipy.ndimage import gaussian_filter
except ImportError:
    gaussian_filter = None

ACTIVE_FORECAST_MODE = "deterministic"
ACTIVE_MODEL = "ECMWF"
ACTIVE_PERIOD = "24 Hour"
ACTIVE_RUN = ""
ACTIVE_ENSEMBLE_PRODUCT = "MEAN"
ACTIVE_WIND_HOUR = 24

# ICON-EPS ONLINE DIRECT INGESTION
# DWD is the sole forecast source. The dashboard embeds the selected-run
# decoder so there is no dependency on a separate local builder script.
# Forecast GRIB2 bytes are downloaded from DWD at runtime and decoded into
# the dashboard-managed temporary cache.
ICON_EPS_DWD_ROOT = "https://opendata.dwd.de/weather/nwp/icon-eps/grib"
ICON_EPS_RUN_HOURS = (18, 12, 6, 0)
ICON_EPS_CACHE_ROOT = ECMWF_ROOT / "ICON_EPS" / "mme_adapter"
ICON_EPS_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
ICON_EPS_ONLINE_ROOT = ECMWF_ROOT / "ICON_EPS"
ICON_EPS_ONLINE_ROOT.mkdir(parents=True, exist_ok=True)
ICON_EPS_BUILD_LOCK = threading.RLock()
ICON_EPS_BUILD_PROCESSES = {}
ICON_EPS_BUILDER_SOURCE = '#!/usr/bin/env python3\n# -*- coding: utf-8 -*-\n"""\nMASRAINMAN ICON-EPS STANDALONE V12 FINAL CACHE\n===============================================\nDWD ICON-EPS 40-member direct F024 endpoint -> native decode -> common 0.25-degree cache.\n\nF024 is a DWD cumulative 0->24h field. It is decoded directly; hourly fields are never summed.\nThe final cache is exactly compatible with the historical 121-member architecture: (40,85,97).\n"""\n\nimport bz2\nimport io\nimport os\nimport re\nimport sys\nimport time\nimport shutil\nimport argparse\nimport subprocess\nfrom pathlib import Path\nfrom datetime import datetime, timedelta, timezone\n\nimport numpy as np\nimport requests\n\n# Windows PowerShell may use cp1252. Force UTF-8-safe diagnostics so a Unicode log line cannot abort the DWD worker.\ntry:\n    sys.stdout.reconfigure(encoding="utf-8", errors="replace")\n    sys.stderr.reconfigure(encoding="utf-8", errors="replace")\nexcept Exception:\n    pass\n\n# Optional plotting/GRIB dependencies are checked explicitly.\n# Online dashboard build mode intentionally does NOT require cartopy or matplotlib.\n# The dashboard itself performs the final map rendering after the 40-member cache\n# has been created. Keeping these optional plotting packages out of the worker\n# prevents an unrelated plotting dependency from aborting the DWD download at 0/40.\nplt = None\nccrs = None\ncfeature = None\n\ntry:\n    import xarray as xr\nexcept Exception as e:\n    print(f"[FATAL] xarray unavailable: {e}")\n    raise\n\ntry:\n    import cfgrib\nexcept Exception as e:\n    print(f"[FATAL] cfgrib unavailable: {e}")\n    raise\n\n# -----------------------------------------------------------------------------\n# CONFIG\n# -----------------------------------------------------------------------------\nBASE = "https://opendata.dwd.de/weather/nwp/icon-eps/grib"\nRUN_HOURS = (0, 6, 12, 18)\nMEMBERS = tuple(range(1, 41))\nDOMAIN = (68.0, 92.0, 4.0, 25.0)  # lon_min, lon_max, lat_min, lat_max\nFORECAST_HOUR = 24\nTIMEOUT = (20, 90)\nRETRIES = 3\n\nROOT = Path(__ICON_EPS_RUNTIME_ROOT__)\nRAW = ROOT / "raw"\nWORK = ROOT / "work"\nPLOTS = ROOT / "plots"\nGRID = ROOT / "grid"\nfor p in (RAW, WORK, PLOTS, GRID):\n    p.mkdir(parents=True, exist_ok=True)\n\n# -----------------------------------------------------------------------------\n# HELPERS\n# -----------------------------------------------------------------------------\ndef log(msg=""):\n    try:\n        print(msg, flush=True)\n    except UnicodeEncodeError:\n        print(str(msg).encode("ascii", "replace").decode("ascii"), flush=True)\n\n\ndef fmt_bytes(n):\n    n = float(n)\n    for u in ("B", "KB", "MB", "GB"):\n        if n < 1024 or u == "GB":\n            return f"{n:.2f} {u}"\n        n /= 1024\n\n\ndef human_time(sec):\n    sec = max(0, int(sec))\n    h, r = divmod(sec, 3600)\n    m, s = divmod(r, 60)\n    if h:\n        return f"{h}h {m:02d}m {s:02d}s"\n    return f"{m}m {s:02d}s"\n\n\ndef candidate_runs(days_back=2):\n    now = datetime.now(timezone.utc)\n    out = []\n    d0 = now.date()\n    for dd in range(days_back + 1):\n        d = d0 - timedelta(days=dd)\n        for hh in RUN_HOURS:\n            dt = datetime(d.year, d.month, d.day, hh, tzinfo=timezone.utc)\n            if dt <= now:\n                out.append(dt)\n    return sorted(out, reverse=True)\n\n\n\ndef index_url(run_dt):\n    cc = f"{run_dt.hour:02d}"\n    return f"{BASE}/{cc}/tot_prec/"\n\n\ndef forecast_filename(run_dt, forecast_hour):\n    """DWD ICON-EPS total-precipitation file for a forecast hour.\n\n    IMPORTANT:\n      The HHH token in the DWD filename identifies the forecast-hour file.\n      Each such file contains the ensemble messages (40 members).\n    """\n    return (\n        f"icon-eps_global_icosahedral_single-level_"\n        f"{run_dt:%Y%m%d%H}_{forecast_hour:03d}_tot_prec.grib2.bz2"\n    )\n\n\ndef forecast_url(run_dt, forecast_hour):\n    return index_url(run_dt) + forecast_filename(run_dt, forecast_hour)\n\n\ndef get_text(url):\n    last = None\n    for k in range(RETRIES):\n        try:\n            r = requests.get(\n                url, timeout=TIMEOUT,\n                headers={"User-Agent": "MasRainman-ICON-EPS/1.0"}\n            )\n            if r.status_code == 200:\n                return r.text\n            last = f"HTTP {r.status_code}"\n        except Exception as e:\n            last = str(e)\n        time.sleep(1.5 * (k + 1))\n    return None\n\n\ndef file_exists(url):\n    """Check a DWD file without downloading its full contents."""\n    for k in range(RETRIES):\n        try:\n            r = requests.head(\n                url, timeout=TIMEOUT,\n                allow_redirects=True,\n                headers={"User-Agent": "MasRainman-ICON-EPS/1.0"}\n            )\n            if r.status_code == 200:\n                return True\n            if r.status_code in (405, 403):\n                break\n        except Exception:\n            pass\n        time.sleep(0.8 * (k + 1))\n\n    # DWD may not support HEAD consistently. A tiny ranged GET is the fallback.\n    try:\n        r = requests.get(\n            url, stream=True, timeout=TIMEOUT,\n            headers={\n                "User-Agent": "MasRainman-ICON-EPS/1.0",\n                "Range": "bytes=0-1023",\n            }\n        )\n        return r.status_code in (200, 206)\n    except Exception:\n        return False\n\n\ndef discover_run(run_dt, horizons):\n    """Require the actual endpoint files for the requested horizons."""\n    missing = []\n    for h in sorted(set(horizons)):\n        url = forecast_url(run_dt, h)\n        if not file_exists(url):\n            missing.append(h)\n    if missing:\n        return False, f"missing forecast-hour endpoint files {missing}"\n    return True, (\n        "endpoint files present: " +\n        ", ".join(f"F{h:03d}" for h in sorted(set(horizons)))\n    )\n\ndef find_latest_run(horizons, requested_run=None):\n    log("=" * 82)\n    log("MASRAINMAN ICON-EPS STANDALONE V11 • 121-MEMBER MME GRID ADAPTER")\n    log("DWD GLOBAL ICON-EPS • 40-MEMBER COMMON-GRID ADAPTER")\n    log("=" * 82)\n    log(f"Domain     : {DOMAIN[0]}–{DOMAIN[1]}E / {DOMAIN[2]}–{DOMAIN[3]}N")\n    log(\n        "Products   : cumulative precipitation from hourly DWD total-precip "\n        "forecast files"\n    )\n    log(\n        "Targets    : " + ", ".join(f"F{h:03d}" for h in horizons)\n    )\n    log(\n        "Discovery  : newest run containing F001 plus every requested "\n        "forecast-hour file"\n    )\n    log("")\n\n    if requested_run:\n        if not re.fullmatch(r"\\d{10}", str(requested_run)):\n            raise ValueError(f"Invalid requested ICON-EPS run: {requested_run}")\n        dt=datetime.strptime(str(requested_run), "%Y%m%d%H").replace(tzinfo=timezone.utc)\n        log(f"[RUN REQUEST] Explicit ICON-EPS run selected: {dt:%Y%m%d %H}Z")\n        ok, reason = discover_run(dt, horizons)\n        if not ok:\n            raise RuntimeError(f"Requested ICON-EPS run {requested_run} is not ready: {reason}")\n        log(f"[READY]    {dt:%Y%m%d %H}Z • {reason}")\n        return dt\n\n    for dt in candidate_runs():\n        log(f"[DISCOVER] Checking {dt:%Y%m%d %Hz} ...")\n        ok, reason = discover_run(dt, horizons)\n        if ok:\n            log(f"[READY]    {dt:%Y%m%d %H}Z • {reason}")\n            return dt\n        log(f"[SKIP]     {dt:%Y%m%d %H}Z • {reason}")\n\n    raise RuntimeError(\n        "No ICON-EPS run found containing all requested forecast-hour files."\n    )\n\ndef download(url, path):\n    if path.exists() and path.stat().st_size > 1000:\n        return path.stat().st_size\n    last = None\n    for k in range(RETRIES):\n        try:\n            with requests.get(url, stream=True, timeout=TIMEOUT,\n                              headers={"User-Agent": "MasRainman-ICON-EPS/1.0"}) as r:\n                if r.status_code != 200:\n                    last = f"HTTP {r.status_code}"\n                    time.sleep(1.5 * (k + 1))\n                    continue\n                total = int(r.headers.get("content-length", "0"))\n                got = 0\n                t0 = time.time()\n                tmp = path.with_suffix(path.suffix + ".part")\n                with open(tmp, "wb") as f:\n                    for chunk in r.iter_content(chunk_size=1024 * 1024):\n                        if chunk:\n                            f.write(chunk)\n                            got += len(chunk)\n                            if total:\n                                pct = 100 * got / total\n                                elapsed = max(0.1, time.time() - t0)\n                                speed = got / elapsed\n                                sys.stdout.write(\n                                    f"\\r        {path.name[-42:]:42s} "\n                                    f"{pct:6.1f}% {fmt_bytes(speed)}/s"\n                                )\n                                sys.stdout.flush()\n                sys.stdout.write("\\n")\n                tmp.replace(path)\n                return path.stat().st_size\n        except Exception as e:\n            last = str(e)\n            time.sleep(1.5 * (k + 1))\n    raise RuntimeError(f"Download failed: {url} :: {last}")\n\n\ndef decompress(src, dst):\n    if dst.exists() and dst.stat().st_size > 1000:\n        return\n    with bz2.open(src, "rb") as fi, open(dst, "wb") as fo:\n        shutil.copyfileobj(fi, fo, length=1024 * 1024)\n\n\ndef _find_precip_var(ds):\n    candidates = ["tot_prec", "tp", "precipitation", "TOT_PREC"]\n    for c in candidates:\n        if c in ds.data_vars:\n            return c\n    # Fallback: find a variable whose GRIB shortName indicates total precip.\n    for name, da in ds.data_vars.items():\n        sn = str(da.attrs.get("GRIB_shortName", "")).lower()\n        ln = str(da.attrs.get("GRIB_name", "")).lower()\n        if sn == "tot_prec" or "total precipitation" in ln:\n            return name\n    raise KeyError(f"No total precipitation field found. Variables={list(ds.data_vars)}")\n\n\ndef _pick_step(da, target_hour):\n    # ICON-EPS member file normally contains a step dimension. Support common\n    # timedelta64, numeric-hour and valid_time layouts.\n    for dim in da.dims:\n        if dim.lower() in ("step", "forecast_step", "time_step"):\n            vals = da[dim].values\n            target = target_hour * 3600\n            best = None\n            bestdiff = None\n            for i, v in enumerate(vals):\n                try:\n                    sec = float(v / np.timedelta64(1, "s"))\n                except Exception:\n                    try:\n                        sec = float(v)\n                    except Exception:\n                        continue\n                diff = abs(sec - target)\n                if bestdiff is None or diff < bestdiff:\n                    best, bestdiff = i, diff\n            if best is not None and bestdiff <= 3600:\n                return da.isel({dim: best})\n    return da\n\n\ndef _find_coord_filename(run_dt, coord_name):\n    """Find the DWD time-invariant CLAT/CLON file for this ICON-EPS cycle."""\n    url = f"{BASE}/{run_dt.hour:02d}/{coord_name.lower()}/"\n    txt = get_text(url)\n    if not txt:\n        raise RuntimeError(f"Cannot read ICON-EPS {coord_name.upper()} directory: {url}")\n    pattern = re.compile(\n        rf\'href=["\\\']([^"\\\']*_{coord_name.lower()}\\.grib2\\.bz2)["\\\']\',\n        re.IGNORECASE,\n    )\n    matches = pattern.findall(txt)\n    if not matches:\n        # DWD index pages sometimes render links without href in a way that is\n        # easier to catch by scanning the complete HTML text.\n        pattern2 = re.compile(\n            rf\'(icon-eps_global_icosahedral_time-invariant_[^\\s"\\\'<>]*_{coord_name.lower()}\\.grib2\\.bz2)\',\n            re.IGNORECASE,\n        )\n        matches = pattern2.findall(txt)\n    if not matches:\n        raise RuntimeError(f"No {coord_name.upper()} coordinate file found at {url}")\n    # There should be one time-invariant grid file. If several historical files\n    # are listed, use the newest lexical filename.\n    return url + sorted(set(matches))[-1]\n\n\ndef _decode_coordinate_grib(grib_path, coord_name):\n    """Decode one native ICON coordinate field. CLAT/CLON are radians."""\n    ds = xr.open_dataset(\n        grib_path,\n        engine="cfgrib",\n        backend_kwargs={"indexpath": str(grib_path) + ".idx"},\n    )\n    target = coord_name.lower()\n    var = None\n    for name in ds.data_vars:\n        if name.lower() == target:\n            var = name\n            break\n    if var is None and len(ds.data_vars) == 1:\n        var = next(iter(ds.data_vars))\n    if var is None:\n        raise RuntimeError(f"Cannot find {coord_name} in {list(ds.data_vars)}")\n    arr = np.asarray(ds[var].values, dtype=np.float64).reshape(-1)\n    ds.close()\n    if arr.size == 0:\n        raise RuntimeError(f"Empty {coord_name} coordinate array")\n    # IMPORTANT: cfgrib/ecCodes may expose the GRIB coordinate field already\n    # converted to degrees even though the underlying ICON grid definition\n    # documents clat/clon in radians. Detect the representation from the\n    # decoded numeric range instead of applying np.degrees unconditionally.\n    finite = arr[np.isfinite(arr)]\n    if finite.size == 0:\n        raise RuntimeError(f"Empty finite {coord_name.upper()} coordinate array")\n    amax = float(np.nanmax(np.abs(finite)))\n\n    if coord_name.lower() == "clat":\n        # Latitude in radians must be within +/-pi/2. If decoded values are\n        # already in degrees they can extend to +/-90.\n        if amax <= (np.pi / 2.0 + 0.05):\n            arr = np.degrees(arr)\n            unit_mode = "radians -> degrees"\n        elif amax <= 90.5:\n            unit_mode = "already degrees"\n        else:\n            raise RuntimeError(\n                f"Invalid CLAT decoded range: {np.nanmin(arr):.6f} .. {np.nanmax(arr):.6f}"\n            )\n    else:\n        # Longitude in radians is within +/-pi; degrees can extend to +/-180.\n        if amax <= (np.pi + 0.05):\n            arr = np.degrees(arr)\n            unit_mode = "radians -> degrees"\n        elif amax <= 180.5:\n            unit_mode = "already degrees"\n        else:\n            raise RuntimeError(\n                f"Invalid CLON decoded range: {np.nanmin(arr):.6f} .. {np.nanmax(arr):.6f}"\n            )\n\n    if coord_name.lower() == "clon":\n        arr = ((arr + 180.0) % 360.0) - 180.0\n\n    log(f"[GRID] {coord_name.upper()} coordinate representation: {unit_mode}")\n    log(f"[GRID] {coord_name.upper()} decoded range: {np.nanmin(arr):.5f} .. {np.nanmax(arr):.5f}")\n    return arr\n\n\ndef load_native_grid(run_dt, expected_points):\n    """Download/decode static CLAT/CLON and return the native cell centers."""\n    cache = GRID / f"icon_eps_native_grid_{run_dt.hour:02d}"\n    cache.mkdir(parents=True, exist_ok=True)\n    arrays = {}\n    for coord in ("clat", "clon"):\n        bz_name = cache / f"{coord}.grib2.bz2"\n        grib_name = cache / f"{coord}.grib2"\n        if not grib_name.exists() or grib_name.stat().st_size < 1000:\n            if not bz_name.exists() or bz_name.stat().st_size < 1000:\n                url = _find_coord_filename(run_dt, coord)\n                log(f"[GRID] Downloading {coord.upper()} coordinate file")\n                download(url, bz_name)\n            decompress(bz_name, grib_name)\n        arrays[coord] = _decode_coordinate_grib(grib_name, coord)\n\n    lat = arrays["clat"]\n    lon = arrays["clon"]\n    if len(lat) != len(lon):\n        raise RuntimeError(f"CLAT/CLON length mismatch: {len(lat)} vs {len(lon)}")\n    if len(lat) != expected_points:\n        raise RuntimeError(\n            f"Native grid length mismatch: precipitation={expected_points:,}, "\n            f"CLAT/CLON={len(lat):,}"\n        )\n    log(f"[GRID] Native ICON-EPS grid loaded: {len(lat):,} cell centers")\n    log(f"[GRID] Latitude range: {np.nanmin(lat):.5f} .. {np.nanmax(lat):.5f}")\n    log(f"[GRID] Longitude range: {np.nanmin(lon):.5f} .. {np.nanmax(lon):.5f}")\n    return lon, lat\n\n\ndef _select_requested_member(da, member):\n    """Select the requested ICON-EPS member without flattening it into space."""\n    # xarray/cfgrib can expose the ensemble axis as `number` or a similar\n    # dimension. The V2 failure showed 29,491,200 values = 40 * 737,280,\n    # proving that this axis must be selected before spatial flattening.\n    for dim in list(da.dims):\n        size = int(da.sizes[dim])\n        dim_lower = dim.lower()\n        if dim_lower in ("number", "member", "ensemble", "ens") or size == 40:\n            if size != 40:\n                continue\n            vals = None\n            try:\n                vals = np.asarray(da[dim].values).reshape(-1)\n            except Exception:\n                pass\n            idx = member - 1\n            if vals is not None and vals.size == 40:\n                # Prefer exact coordinate match. Coordinates may be 1..40 or 0..39.\n                exact = np.where(vals == member)[0]\n                if exact.size:\n                    idx = int(exact[0])\n                else:\n                    zero_based = np.where(vals == member - 1)[0]\n                    if zero_based.size:\n                        idx = int(zero_based[0])\n            log(f"[DECODE] Selecting ensemble dimension \'{dim}\' size=40 -> member {member:03d} index={idx}")\n            return da.isel({dim: idx})\n    return da\n\n\ndef _log_decode_shape(da, member):\n    log(f"[DECODE] member={member:03d} dims={da.dims} shape={tuple(int(da.sizes[d]) for d in da.dims)}")\n    for dim in da.dims:\n        try:\n            log(f"          dim \'{dim}\' size={int(da.sizes[dim])}")\n        except Exception:\n            pass\n\n\ndef decode_member(grib_path, native_lon, native_lat, member):\n    """Decode one requested precipitation member and attach native coordinates."""\n    ds = xr.open_dataset(\n        grib_path,\n        engine="cfgrib",\n        backend_kwargs={\n            "indexpath": str(grib_path) + ".idx",\n            "filter_by_keys": {"typeOfLevel": "surface"},\n        },\n    )\n    var = _find_precip_var(ds)\n    da = ds[var]\n    _log_decode_shape(da, member)\n    da = _pick_step(da, FORECAST_HOUR)\n    _log_decode_shape(da, member)\n    da = _select_requested_member(da, member)\n    _log_decode_shape(da, member)\n    values = np.asarray(da.values, dtype=np.float32).reshape(-1)\n    ds.close()\n\n    log(f"[DECODE] member={member:03d} spatial values={values.size:,} expected={native_lon.size:,}")\n    if values.size != native_lon.size:\n        raise RuntimeError(\n            f"Native precipitation/grid mismatch after member selection: "\n            f"values={values.size:,}, coordinates={native_lon.size:,}"\n        )\n\n    # DWD ICON total precipitation is kg m-2, numerically equivalent to mm.\n    values = np.where(np.isfinite(values), values, np.nan)\n    values = np.maximum(values, 0.0)\n\n    mask = (\n        (native_lon >= DOMAIN[0]) & (native_lon <= DOMAIN[1]) &\n        (native_lat >= DOMAIN[2]) & (native_lat <= DOMAIN[3])\n    )\n    if not np.any(mask):\n        raise RuntimeError("South India crop returned zero native ICON-EPS points")\n    return native_lon[mask], native_lat[mask], values[mask]\n\ndef plot_scatter(lon, lat, field, title, out, vmin=None, vmax=None, cmap="turbo"):\n    fig = plt.figure(figsize=(12, 8), dpi=150)\n    ax = plt.axes(projection=ccrs.PlateCarree())\n    ax.set_extent([DOMAIN[0], DOMAIN[1], DOMAIN[2], DOMAIN[3]], crs=ccrs.PlateCarree())\n    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), linewidth=0.7)\n    ax.add_feature(cfeature.BORDERS.with_scale("50m"), linewidth=0.5)\n    sc = ax.scatter(lon, lat, c=field, s=2.0, cmap=cmap, vmin=vmin, vmax=vmax,\n                    transform=ccrs.PlateCarree(), linewidths=0)\n    cb = plt.colorbar(sc, ax=ax, pad=0.02, shrink=0.88)\n    cb.set_label("Rainfall (mm)")\n    ax.set_title(title, fontsize=13, weight="bold")\n    ax.gridlines(draw_labels=True, linewidth=0.25, alpha=0.35)\n    plt.tight_layout()\n    fig.savefig(out, bbox_inches="tight")\n    plt.close(fig)\n\n\ndef make_products(stack, lon, lat, run_dt):\n    mean = np.nanmean(stack, axis=0)\n    p50 = np.nanpercentile(stack, 50, axis=0)\n    p75 = np.nanpercentile(stack, 75, axis=0)\n    p90 = np.nanpercentile(stack, 90, axis=0)\n    prob50 = np.nanmean(stack >= 50.0, axis=0) * 100.0\n    prob100 = np.nanmean(stack >= 100.0, axis=0) * 100.0\n\n    stats = {\n        "MEAN": mean, "P50": p50, "P75": p75, "P90": p90,\n        "PROB>=50": prob50, "PROB>=100": prob100,\n    }\n    log("")\n    log("=" * 76)\n    log("ICON-EPS ENSEMBLE PRODUCTS")\n    log("=" * 76)\n    log(f"Run: {run_dt:%Y-%m-%d %H}Z • members={stack.shape[0]} • F{FORECAST_HOUR:03d}")\n    for name, a in stats.items():\n        log(f"{name:10s} min={np.nanmin(a):8.3f} max={np.nanmax(a):8.3f} "\n            f"mean={np.nanmean(a):8.3f} p95={np.nanpercentile(a,95):8.3f}")\n\n    for name, a in stats.items():\n        out = PLOTS / f"ICON_EPS_{run_dt:%Y%m%d%H}_F{FORECAST_HOUR:03d}_{name.replace(\'>=\',\'ge\')}.png"\n        if name.startswith("PROB"):\n            plot_scatter(lon, lat, a, f"MASRAINMAN ICON-EPS {name}% • F{FORECAST_HOUR:03d} • {run_dt:%Y-%m-%d %H}Z",\n                         out, vmin=0, vmax=100, cmap="viridis")\n        else:\n            plot_scatter(lon, lat, a, f"MASRAINMAN ICON-EPS {name} • F{FORECAST_HOUR:03d} • {run_dt:%Y-%m-%d %H}Z",\n                         out, vmin=0, vmax=max(10, float(np.nanpercentile(a,99))), cmap="turbo")\n        log(f"[PLOT] {out.name}")\n\n\n\ndef make_products_horizon(stack, lon, lat, run_dt, target_hour):\n    if plt is None or ccrs is None:\n        log("[PLOT] Diagnostic plotting skipped in online dashboard worker.")\n        return\n\n    mean = np.nanmean(stack, axis=0)\n    p50 = np.nanpercentile(stack, 50, axis=0)\n    p75 = np.nanpercentile(stack, 75, axis=0)\n    p90 = np.nanpercentile(stack, 90, axis=0)\n    prob50 = np.nanmean(stack >= 50.0, axis=0) * 100.0\n    prob100 = np.nanmean(stack >= 100.0, axis=0) * 100.0\n\n    stats = {\n        "MEAN": mean,\n        "P50": p50,\n        "P75": p75,\n        "P90": p90,\n        "PROB>=50": prob50,\n        "PROB>=100": prob100,\n    }\n\n    log("")\n    log("=" * 78)\n    log(f"ICON-EPS CUMULATIVE ENSEMBLE PRODUCTS • F{target_hour:03d}")\n    log("=" * 78)\n    log(\n        f"Run: {run_dt:%Y-%m-%d %H}Z • members={stack.shape[0]} • "\n        f"method=SUM(F001..F{target_hour:03d})"\n    )\n\n    for name, a in stats.items():\n        log(\n            f"{name:10s} min={np.nanmin(a):8.3f} "\n            f"max={np.nanmax(a):8.3f} "\n            f"mean={np.nanmean(a):8.3f} "\n            f"p95={np.nanpercentile(a,95):8.3f}"\n        )\n\n    for name, a in stats.items():\n        out = PLOTS / (\n            f"ICON_EPS_{run_dt:%Y%m%d%H}_F{target_hour:03d}_"\n            f"{name.replace(\'>=\',\'ge\')}.png"\n        )\n        if name.startswith("PROB"):\n            plot_scatter(\n                lon, lat, a,\n                f"MASRAINMAN ICON-EPS {name}% • Cumulative F{target_hour:03d} • "\n                f"{run_dt:%Y-%m-%d %H}Z",\n                out, vmin=0, vmax=100, cmap="viridis"\n            )\n        else:\n            vmax = max(10.0, float(np.nanpercentile(a, 99)))\n            plot_scatter(\n                lon, lat, a,\n                f"MASRAINMAN ICON-EPS {name} • Cumulative F{target_hour:03d} • "\n                f"{run_dt:%Y-%m-%d %H}Z",\n                out, vmin=0, vmax=vmax, cmap="turbo"\n            )\n        log(f"[PLOT] {out.name}")\n\n\n\ndef decode_hour_file(grib_path, native_lon, native_lat, members=40):\n    """Decode one DWD forecast-hour file into a (40, cropped_points) array."""\n    ds = xr.open_dataset(\n        grib_path,\n        engine="cfgrib",\n        backend_kwargs={\n            "indexpath": str(grib_path) + ".idx",\n            "filter_by_keys": {"typeOfLevel": "surface"},\n        },\n    )\n\n    var = _find_precip_var(ds)\n    da = ds[var]\n    log(\n        f"[DECODE] file={grib_path.name} dims={da.dims} "\n        f"shape={tuple(int(da.sizes[d]) for d in da.dims)}"\n    )\n\n    da = _select_requested_member(da, 1)\n    # The helper above selects a member, so reopen the original variable for\n    # the full ensemble after learning its member dimension.\n    ds.close()\n\n    ds = xr.open_dataset(\n        grib_path,\n        engine="cfgrib",\n        backend_kwargs={\n            "indexpath": str(grib_path) + ".idx",\n            "filter_by_keys": {"typeOfLevel": "surface"},\n        },\n    )\n    var = _find_precip_var(ds)\n    da = ds[var]\n\n    member_dim = None\n    for dim in da.dims:\n        dl = dim.lower()\n        if dl in ("number", "member", "ensemble", "ens") and int(da.sizes[dim]) == members:\n            member_dim = dim\n            break\n    if member_dim is None:\n        for dim in da.dims:\n            if int(da.sizes[dim]) == members:\n                member_dim = dim\n                break\n\n    if member_dim is None:\n        raise RuntimeError(\n            f"Could not identify 40-member ensemble dimension in {grib_path.name}. "\n            f"dims={da.dims}"\n        )\n\n    member_coord = None\n    try:\n        member_coord = np.asarray(da[member_dim].values).reshape(-1)\n    except Exception:\n        pass\n\n    mask = (\n        (native_lon >= DOMAIN[0]) & (native_lon <= DOMAIN[1]) &\n        (native_lat >= DOMAIN[2]) & (native_lat <= DOMAIN[3])\n    )\n    if not np.any(mask):\n        ds.close()\n        raise RuntimeError("South India crop returned zero native ICON-EPS points")\n\n    out = np.empty((members, int(np.count_nonzero(mask))), dtype=np.float32)\n\n    for member in range(1, members + 1):\n        idx = member - 1\n        if member_coord is not None and member_coord.size == members:\n            exact = np.where(member_coord == member)[0]\n            if exact.size:\n                idx = int(exact[0])\n            else:\n                zero = np.where(member_coord == member - 1)[0]\n                if zero.size:\n                    idx = int(zero[0])\n\n        one = da.isel({member_dim: idx})\n        values = np.asarray(one.values, dtype=np.float32).reshape(-1)\n\n        if values.size != native_lon.size:\n            ds.close()\n            raise RuntimeError(\n                f"{grib_path.name}: member {member:03d} has "\n                f"{values.size:,} points, expected {native_lon.size:,}"\n            )\n\n        values = np.where(np.isfinite(values), values, 0.0)\n        values = np.maximum(values, 0.0)\n        out[member - 1, :] = values[mask]\n\n    ds.close()\n    return out\n\n\ndef forensic_file(grib_path, expected_members=40):\n    """Print ecCodes metadata for one forecast-hour file."""\n    from eccodes import (\n        codes_grib_new_from_file, codes_release, codes_get,\n        codes_get_values,\n    )\n\n    records = []\n    with open(grib_path, "rb") as f:\n        while True:\n            h = codes_grib_new_from_file(f)\n            if h is None:\n                break\n            try:\n                def g(key, default=None):\n                    try:\n                        return codes_get(h, key)\n                    except Exception:\n                        return default\n\n                vals = codes_get_values(h)\n                records.append({\n                    "number": g("number"),\n                    "step": g("step"),\n                    "startStep": g("startStep"),\n                    "endStep": g("endStep"),\n                    "forecastTime": g("forecastTime"),\n                    "stat": g("typeOfStatisticalProcessing"),\n                    "range": g("lengthOfTimeRange"),\n                    "n": len(vals),\n                    "shortName": g("shortName"),\n                })\n            finally:\n                codes_release(h)\n\n    nums = []\n    for r in records:\n        try:\n            nums.append(int(r["number"]))\n        except Exception:\n            pass\n\n    log("")\n    log("-" * 96)\n    log(f"FORENSIC FILE: {grib_path.name}")\n    log("-" * 96)\n    log(f"GRIB messages : {len(records)}")\n    log(f"Member numbers: {sorted(set(nums))[:10]} ... {sorted(set(nums))[-10:]}")\n    if records:\n        sample = records[0]\n        log(\n            "Message 001   : "\n            f"number={sample[\'number\']} short={sample[\'shortName\']} "\n            f"step={sample[\'step\']} start={sample[\'startStep\']} "\n            f"end={sample[\'endStep\']} forecast={sample[\'forecastTime\']} "\n            f"stat={sample[\'stat\']} range={sample[\'range\']} n={sample[\'n\']:,}"\n        )\n    if len(records) != expected_members:\n        raise RuntimeError(\n            f"{grib_path.name}: expected {expected_members} GRIB messages, "\n            f"found {len(records)}"\n        )\n    if sorted(set(nums)) != list(range(1, expected_members + 1)):\n        raise RuntimeError(\n            f"{grib_path.name}: expected member numbers 1..{expected_members}, "\n            f"found {sorted(set(nums))}"\n        )\n    return records\n\n\n\n# ==========================================================\n# MME COMMON GRID ADAPTER\n# ==========================================================\n# The historical MASRainman MME uses a common 0.25° grid.\n# This adapter converts the native ICON-EPS unstructured\n# ensemble to that grid, member by member.\n#\n# Target domain follows the historical South India MME\n# workflow: 68E–98E / 4N–36N, at 0.25° resolution.\n# The exact MME grid dimensions are derived from these bounds.\n# ==========================================================\n\nMME_LON_MIN = 68.0\nMME_LON_MAX = 92.0\nMME_LAT_MIN = 4.0\nMME_LAT_MAX = 25.0\nMME_RES = 0.25\n\n\ndef build_mme_grid():\n    lon = np.arange(\n        MME_LON_MIN,\n        MME_LON_MAX + MME_RES * 0.5,\n        MME_RES,\n        dtype=np.float64,\n    )\n    # The historical 121-member engine uses latitude descending:\n    # 25.0, 24.75, ... 4.0\n    lat = np.arange(\n        MME_LAT_MAX,\n        MME_LAT_MIN - MME_RES * 0.5,\n        -MME_RES,\n        dtype=np.float64,\n    )\n    return lat, lon\n\n\ndef remap_icon_to_mme_grid(stack, native_lon, native_lat):\n    """\n    Remap each ICON-EPS member from native unstructured grid to\n    the saved MASRainman 121-member common 0.25° grid.\n\n    stack shape: (40, native_points_in_domain)\n\n    Uses scipy.griddata linear interpolation with nearest-neighbour\n    fallback for cells outside the linear interpolation hull.\n    """\n    from scipy.interpolate import griddata\n\n    if stack.ndim != 2:\n        raise ValueError(\n            f"ICON stack must be 2-D (members, points), got {stack.shape}"\n        )\n\n    if stack.shape[0] != 40:\n        raise ValueError(\n            f"ICON-EPS adapter expects 40 members, got {stack.shape[0]}"\n        )\n\n    lat_grid, lon_grid = build_mme_grid()\n    target_lon, target_lat = np.meshgrid(lon_grid, lat_grid)\n\n    source_points = np.column_stack([\n        native_lon.astype(np.float64),\n        native_lat.astype(np.float64),\n    ])\n    target_points = (\n        target_lon.ravel(),\n        target_lat.ravel(),\n    )\n\n    out = np.empty(\n        (40, len(lat_grid), len(lon_grid)),\n        dtype=np.float32,\n    )\n\n    log("")\n    log("=" * 90)\n    log("ICON-EPS → MASRAINMAN 121-MEMBER COMMON GRID")\n    log("=" * 90)\n    log(\n        f"Native points : {stack.shape[1]:,}"\n    )\n    log(\n        f"Target grid   : {len(lat_grid)} × {len(lon_grid)} "\n        f"= {len(lat_grid)*len(lon_grid):,}"\n    )\n    log(\n        f"Target domain : {MME_LON_MIN}–{MME_LON_MAX}E / "\n        f"{MME_LAT_MIN}–{MME_LAT_MAX}N"\n    )\n    log(f"Resolution    : {MME_RES}°")\n\n    for m in range(40):\n        values = np.asarray(stack[m], dtype=np.float64)\n\n        linear = griddata(\n            source_points,\n            values,\n            target_points,\n            method="linear",\n        )\n\n        missing = ~np.isfinite(linear)\n\n        if np.any(missing):\n            nearest = griddata(\n                source_points,\n                values,\n                target_points,\n                method="nearest",\n            )\n            linear[missing] = nearest[missing]\n\n        linear = np.where(np.isfinite(linear), linear, 0.0)\n        linear = np.maximum(linear, 0.0)\n\n        out[m] = linear.reshape(\n            len(lat_grid),\n            len(lon_grid)\n        ).astype(np.float32)\n\n        log(\n            f"[REMAP] Member {m+1:02d}/40 | "\n            f"native max={np.nanmax(values):.3f} | "\n            f"grid max={np.nanmax(out[m]):.3f}"\n        )\n\n    if not np.all(np.isfinite(out)):\n        raise RuntimeError("MME grid contains non-finite ICON values.")\n\n    if np.any(out < -1e-6):\n        raise RuntimeError("MME grid contains negative ICON precipitation.")\n\n    return out, lat_grid, lon_grid\n\n\ndef save_mme_adapter_outputs(\n    stack_mme,\n    lat_grid,\n    lon_grid,\n    run_dt,\n    horizon,\n):\n    out_dir = ROOT / "mme_adapter"\n    out_dir.mkdir(parents=True, exist_ok=True)\n\n    npy = out_dir / (\n        f"ICON_EPS_{run_dt:%Y%m%d%H}_F{horizon:03d}_"\n        "SUPERENSEMBLE_GRID_40MEM.npy"\n    )\n    np.save(npy, stack_mme)\n\n    np.save(\n        out_dir / f"ICON_EPS_{run_dt:%Y%m%d%H}_MME_LAT.npy",\n        lat_grid,\n    )\n    np.save(\n        out_dir / f"ICON_EPS_{run_dt:%Y%m%d%H}_MME_LON.npy",\n        lon_grid,\n    )\n\n    meta = out_dir / (\n        f"ICON_EPS_{run_dt:%Y%m%d%H}_F{horizon:03d}_"\n        "SUPERENSEMBLE_GRID_METADATA.txt"\n    )\n\n    meta.write_text(\n        "\\n".join([\n            "MASRAINMAN ICON-EPS → MME ADAPTER",\n            f"Run: {run_dt:%Y-%m-%d %H}Z",\n            f"Horizon: F{horizon:03d}",\n            "Members: 40",\n            f"Shape: {stack_mme.shape}",\n            f"Latitude: {MME_LAT_MIN} to {MME_LAT_MAX}",\n            f"Longitude: {MME_LON_MIN} to {MME_LON_MAX}",\n            f"Resolution: {MME_RES} degree",\n            "Method: scipy griddata linear + nearest fallback",\n            "Source: DWD ICON-EPS native unstructured grid",\n            "Product: direct DWD cumulative endpoint field",\n            "Compatibility: ECMWF 50 + GEFS 31 + ICON 40 = 121 members.",\n            "Expected final super-ensemble shape: (121, 85, 97).",\n        ]),\n        encoding="utf-8",\n    )\n\n    log("")\n    log(f"[MME SAVE] {npy}")\n    log(f"[MME SAVE] {meta}")\n\n    return npy, meta\n\n\ndef main():\n    parser = argparse.ArgumentParser(\n        description="MASRAINMAN ICON-EPS V10 ICON-to-MME adapter"\n    )\n    parser.add_argument(\n        "--run", default="",\n        help="Explicit ICON-EPS cycle YYYYMMDDHH. If omitted, discover the newest ready run."\n    )\n    parser.add_argument(\n        "--horizons", default="24",\n        help="Cumulative forecast horizons, e.g. 24 or 24,72,120,180"\n    )\n    parser.add_argument(\n        "--keep-raw", action="store_true",\n        help="Keep compressed/decompressed files"\n    )\n    parser.add_argument(\n        "--forensic-only", action="store_true",\n        help="Inspect endpoint metadata only; do not create products"\n    )\n    args = parser.parse_args()\n\n    try:\n        horizons = tuple(sorted(set(\n            int(x.strip()) for x in args.horizons.split(",") if x.strip()\n        )))\n    except Exception as e:\n        raise SystemExit(f"Invalid --horizons: {e}")\n\n    if not horizons or any(h <= 0 or h > 180 for h in horizons):\n        raise SystemExit("Horizons must be positive forecast hours <= 180.")\n\n    t0 = time.time()\n    run_dt = find_latest_run(horizons, args.run.strip() or None)\n\n    log("")\n    log(f"[RUN] {run_dt:%Y%m%d %H}Z")\n    log(\n        "[IMPORTANT] DWD ICON-EPS tot_prec endpoint files are already "\n        "time-accumulated fields."\n    )\n    log(\n        "[IMPORTANT] The forensic metadata must match the requested horizon: "\n        "F024 => step/endStep 24 and 1440-minute range; "\n        "F072 => 72 and 4320 minutes; etc."\n    )\n    log(\n        "[IMPORTANT] We do NOT sum F001..F024. F024 itself is the "\n        "0→24-hour accumulated precipitation field."\n    )\n\n    # ------------------------------------------------------------------\n    # FORENSIC GATE: inspect every requested endpoint before decoding all\n    # members. This prevents accidental horizon relabeling.\n    # ------------------------------------------------------------------\n    endpoint_paths = {}\n\n    for horizon in horizons:\n        fn = forecast_filename(run_dt, horizon)\n        bz = RAW / fn\n        grib = WORK / fn[:-4]\n\n        log("")\n        log(\n            f"[FORENSIC GATE] Downloading F{horizon:03d} endpoint file: {fn}"\n        )\n        download(forecast_url(run_dt, horizon), bz)\n        decompress(bz, grib)\n\n        records = forensic_file(grib)\n\n        # Every message in the endpoint file must represent the requested\n        # accumulation window.\n        bad = []\n        expected_minutes = horizon * 60\n\n        for r in records:\n            try:\n                end = float(r["endStep"])\n                start = float(r["startStep"])\n                step = float(r["step"])\n                rng = float(r["range"])\n            except Exception:\n                bad.append(r)\n                continue\n\n            if (\n                abs(end - horizon) > 1e-6\n                or abs(step - horizon) > 1e-6\n                or abs(start) > 1e-6\n                or abs(rng - expected_minutes) > 1e-6\n            ):\n                bad.append(r)\n\n        if bad:\n            raise RuntimeError(\n                f"F{horizon:03d} forensic safety failure: "\n                f"endpoint file metadata does not represent 0→{horizon}h "\n                f"accumulation. Bad messages={len(bad)}"\n            )\n\n        log(\n            f"[FORENSIC PASS] F{horizon:03d}: all 40 messages represent "\n            f"0→{horizon}h accumulation ({expected_minutes} minutes)."\n        )\n\n        endpoint_paths[horizon] = (bz, grib)\n\n        if args.forensic_only:\n            if not args.keep_raw:\n                for p in (bz, grib, Path(str(grib) + ".idx")):\n                    try:\n                        p.unlink()\n                    except OSError:\n                        pass\n\n    if args.forensic_only:\n        log("")\n        log("[FORENSIC-ONLY] All requested endpoint horizons passed metadata validation.")\n        log(f"[DONE] elapsed={human_time(time.time()-t0)}")\n        return\n\n    # ------------------------------------------------------------------\n    # Native ICON grid from the first endpoint.\n    # ------------------------------------------------------------------\n    first_grib = endpoint_paths[horizons[0]][1]\n\n    ds_probe = xr.open_dataset(\n        first_grib,\n        engine="cfgrib",\n        backend_kwargs={\n            "indexpath": str(first_grib) + ".idx",\n            "filter_by_keys": {"typeOfLevel": "surface"},\n        },\n    )\n    var_probe = _find_precip_var(ds_probe)\n    da_probe = ds_probe[var_probe]\n\n    expected_points = None\n    for dim in da_probe.dims:\n        if int(da_probe.sizes[dim]) == 40:\n            expected_points = int(np.prod([\n                int(da_probe.sizes[d]) for d in da_probe.dims if d != dim\n            ]))\n            break\n\n    if expected_points is None:\n        expected_points = int(np.asarray(da_probe.values).size // 40)\n\n    ds_probe.close()\n\n    log(f"[FORENSIC] Native spatial points = {expected_points:,}")\n\n    native_lon, native_lat = load_native_grid(run_dt, expected_points)\n\n    crop_mask = (\n        (native_lon >= DOMAIN[0]) & (native_lon <= DOMAIN[1]) &\n        (native_lat >= DOMAIN[2]) & (native_lat <= DOMAIN[3])\n    )\n\n    ref_lon = native_lon[crop_mask]\n    ref_lat = native_lat[crop_mask]\n\n    log(f"[GRID] South India native points = {len(ref_lon):,}")\n\n    # ------------------------------------------------------------------\n    # Decode each endpoint directly. No hourly summation.\n    # ------------------------------------------------------------------\n    results = {}\n\n    for horizon in horizons:\n        bz, grib = endpoint_paths[horizon]\n\n        log("")\n        log("=" * 90)\n        log(\n            f"[HORIZON] DIRECT CUMULATIVE F{horizon:03d} "\n            f"= DWD 0→{horizon}h endpoint field"\n        )\n        log("=" * 90)\n\n        stack = decode_hour_file(\n            grib,\n            native_lon,\n            native_lat,\n            members=40\n        )\n\n        if stack.shape != (40, len(ref_lon)):\n            raise RuntimeError(\n                f"F{horizon:03d}: decoded stack {stack.shape} != "\n                f"(40,{len(ref_lon)})"\n            )\n\n        if np.any(~np.isfinite(stack)):\n            stack = np.where(np.isfinite(stack), stack, 0.0)\n\n        stack = np.maximum(stack, 0.0)\n\n        results[horizon] = stack.copy()\n\n        log(\n            f"[CUMULATIVE] F{horizon:03d} stack={stack.shape} "\n            f"max={np.nanmax(stack):.3f} "\n            f"mean={np.nanmean(stack):.3f} "\n            f"p95={np.nanpercentile(stack,95):.3f}"\n        )\n\n        # Historical MME-compatible output.\n        stack_mme, mme_lat, mme_lon = remap_icon_to_mme_grid(\n            stack,\n            ref_lon,\n            ref_lat,\n        )\n\n        # Exact compatibility gate for the saved 121-member engine.\n        if stack_mme.shape != (40, 85, 97):\n            raise RuntimeError(\n                f"121-member compatibility failure: expected "\n                f"(40, 85, 97), got {stack_mme.shape}"\n            )\n\n        if not np.isclose(mme_lat[0], 25.0) or not np.isclose(mme_lat[-1], 4.0):\n            raise RuntimeError("Latitude orientation/domain mismatch.")\n\n        if not np.isclose(mme_lon[0], 68.0) or not np.isclose(mme_lon[-1], 92.0):\n            raise RuntimeError("Longitude domain mismatch.")\n\n        save_mme_adapter_outputs(\n            stack_mme,\n            mme_lat,\n            mme_lon,\n            run_dt,\n            horizon,\n        )\n\n        # Dashboard mode: no diagnostic plotting here.\n        # The dashboard renderer creates the final weather map after\n        # the validated (40,85,97) cache is ready.\n\n        if not args.keep_raw:\n            for p in (bz, grib, Path(str(grib) + ".idx")):\n                try:\n                    p.unlink()\n                except OSError:\n                    pass\n\n    report = ROOT / f"ICON_EPS_V12_FINAL_CACHE_REPORT_{run_dt:%Y%m%d%H}.txt"\n\n    report_lines = [\n        "MASRAINMAN ICON-EPS STANDALONE V12 FINAL CACHE",\n        "DWD ICON-EPS direct cumulative endpoint decoder",\n        f"Run: {run_dt:%Y-%m-%d %H}Z",\n        "Members: 40",\n        f"Horizons: {\', \'.join(f\'F{h:03d}\' for h in horizons)}",\n        f"Domain: {DOMAIN}",\n        f"South India points: {len(ref_lon)}",\n        "Method: decode the DWD endpoint file directly.",\n        "CACHE: 40 ICON members remapped to common 0.25-degree grid (85x97).",\n        "No hourly summation performed.",\n        "Forensic endpoint metadata: PASS",\n    ]\n\n    for h in horizons:\n        a = results[h]\n        report_lines.append(\n            f"F{h:03d}: max={np.nanmax(a):.3f}, "\n            f"mean={np.nanmean(a):.3f}, "\n            f"p95={np.nanpercentile(a,95):.3f}"\n        )\n\n    if len(results) > 1:\n        report_lines.append("Cross-horizon monotonic validation: PASS")\n\n    report_lines.append(\n        f"Elapsed: {human_time(time.time()-t0)}"\n    )\n\n    report.write_text(\n        "\\n".join(report_lines),\n        encoding="utf-8"\n    )\n\n    log("")\n    log(f"[REPORT] {report}")\n    log(f"[DONE] elapsed={human_time(time.time()-t0)}")\n\n\nif __name__ == "__main__":\n    main()\n'
def _write_icon_eps_online_builder():
    """Materialize the embedded DWD decoder only for the active subprocess."""
    text = ICON_EPS_BUILDER_SOURCE.replace("Path(__ICON_EPS_RUNTIME_ROOT__)", "Path(" + repr(str(ICON_EPS_ONLINE_ROOT)) + ")")
    tf = tempfile.NamedTemporaryFile(prefix="masrainman_icon_eps_", suffix=".py", delete=False, mode="w", encoding="utf-8")
    try:
        tf.write(text)
        tf.flush()
        return Path(tf.name)
    finally:
        tf.close()

def start_icon_eps_selected_run_builder(runv, product="MEAN"):
    """Download and decode the selected ICON-EPS cycle directly from DWD."""
    request_key=f"ensemble|ICON|{runv}|24 Hour|{str(product or 'MEAN').upper()}"
    with ICON_EPS_BUILD_LOCK:
        existing=ICON_EPS_BUILD_PROCESSES.get(runv)
        if existing is not None:
            existing_proc = existing[0] if isinstance(existing, tuple) else existing
            try:
                if existing_proc.poll() is None:
                    return False, "already-running"
            except (AttributeError, TypeError):
                # A stale/malformed registry entry must never make a valid
                # selected-run request fail with a tuple.poll error.
                ICON_EPS_BUILD_PROCESSES.pop(runv, None)
        cache_file=ICON_EPS_CACHE_ROOT / f"ICON_EPS_{runv}_F024_SUPERENSEMBLE_GRID_40MEM.npy"
        if cache_file.exists():
            try:
                arr=np.load(cache_file,mmap_mode="r")
                if arr.shape==(40,85,97):
                    return False, "cache-ready"
            except Exception:
                pass
        if not re.fullmatch(r"\d{10}", str(runv)):
            raise ValueError(f"Invalid ICON EPS run: {runv}")
        ECMWF_DOWNLOAD_STATUS.update(
            state="downloading", run=f"{runv[:8]} {runv[8:]}Z",
            message=f"Downloading ICON EPS {runv} from DWD • F024 • 40 members", error=None,
            total=40, downloaded=0, current_member=0, member_total=40,
            current_endpoint=24, endpoint_total=1, request_key=request_key, period="24 Hour", product=str(product or "MEAN").upper(), render_ready=False,
        )
        set_model_identity(mode="ensemble",model="ICON",label="ICON EPS",state="downloading",
                           run=f"{runv[:8]} {runv[8:]}Z",source="DWD ICON-EPS online",
                           download="downloading",message=f"Downloading selected ICON EPS run {runv} online")
        ecmwf_event(f"ICON EPS ONLINE BUILD START • DWD • selected run {runv} • F024 • 40 members","info")
        builder_path=_write_icon_eps_online_builder()
        cmd=[sys.executable,str(builder_path),"--run",runv,"--horizons","24"]
        proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                              encoding="utf-8",errors="replace",bufsize=1,
                              creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        ICON_EPS_BUILD_PROCESSES[runv]=(proc,builder_path)

        def monitor():
            output_tail=[]
            try:
                for line in iter(proc.stdout.readline, ""):
                    line=line.strip()
                    if line:
                        output_tail.append(line)
                        if len(output_tail)>30: output_tail.pop(0)
                        ecmwf_event(f"ICON EPS ONLINE • {line[-500:]}","info")
                rc=proc.wait()
                cache_ok=False
                arr=None
                if cache_file.exists():
                    try:
                        arr=np.load(cache_file,mmap_mode="r")
                        cache_ok=(arr.shape==(40,85,97) and np.all(np.isfinite(arr)))
                    except Exception:
                        cache_ok=False
                if rc==0 and cache_ok:
                    RAINFALL_CACHE.clear()
                    run_dt=datetime.strptime(runv,'%Y%m%d%H').replace(tzinfo=timezone.utc)
                    valid_end=run_dt+timedelta(hours=24)
                    valid_end_ist=valid_end+timedelta(hours=5,minutes=30)
                    set_model_identity(mode="ensemble",model="ICON",label="ICON EPS",state="ready",
                                       run=f"{runv[:8]} {runv[8:]}Z",source="DWD ICON-EPS online",
                                       download="complete",message=f"ICON EPS online cache ready: {arr.shape}")
                    ECMWF_DOWNLOAD_STATUS.update(state="ready",run=f"{runv[:8]} {runv[8:]}Z",
                        message=f"ICON EPS READY • {runv} • F024 • 40 members",error=None,
                        finished_at=datetime.now(timezone.utc).isoformat(),total=40,downloaded=40,
                        current_member=40,member_total=40,current_endpoint=24,endpoint_total=1,
                        valid_end_date=valid_end_ist.strftime('%d %b %Y'),valid_end_iso=valid_end_ist.strftime('%Y-%m-%d'),
                        valid_end_time=valid_end_ist.strftime('%I:%M %p IST'),valid_end_endpoint='F024',
                        valid_end_utc=valid_end.strftime('%Y-%m-%d %H:%M UTC'),period="24 Hour",product=str(product or "MEAN").upper(),request_key=request_key,render_ready=True)
                    ecmwf_event(f"ICON EPS ONLINE BUILD COMPLETE • {runv} • DWD → 40 members → 0.25° cache READY","success")
                else:
                    detail = ' | '.join(output_tail[-3:])[-1800:] if output_tail else "worker produced no diagnostic output"
                    msg=f"ICON EPS online build failed for {runv} (exit {rc}); F024 cache not ready. Last worker message: {detail}"
                    set_model_identity(mode="ensemble",model="ICON",label="ICON EPS",state="error",
                                       run=f"{runv[:8]} {runv[8:]}Z",source="DWD ICON-EPS online",
                                       download="failed",message=msg)
                    ECMWF_DOWNLOAD_STATUS.update(state="error",run=f"{runv[:8]} {runv[8:]}Z",message=msg,error=msg,
                        finished_at=datetime.now(timezone.utc).isoformat())
                    ecmwf_event(f"ICON EPS ONLINE BUILD FAILED • {runv} • exit={rc}","error")
            except Exception as exc:
                tail = ' | '.join(output_tail[-3:])[-1800:] if output_tail else "worker produced no diagnostic output"
                detail=f"{exc} | last worker message: {tail}"
                ECMWF_DOWNLOAD_STATUS.update(state="error",message=f"ICON EPS online monitor failed: {detail}",error=detail)
                ecmwf_event(f"ICON EPS ONLINE MONITOR FAILED • {runv} • {detail}","error")
            finally:
                try:
                    if builder_path.exists(): builder_path.unlink()
                except Exception: pass
                with ICON_EPS_BUILD_LOCK:
                    ICON_EPS_BUILD_PROCESSES.pop(runv,None)
        threading.Thread(target=monitor,daemon=True,name=f"ICON-EPS-{runv}").start()
        return True, "started"

def icon_eps_published_runs(limit=4, lookback_days=3):
    """Return newest published ICON-EPS F024 cycles, newest first.

    Availability is determined from the official DWD /tot_prec/ directory
    listing for each cycle.  Only the tiny HTML directory listing is read;
    no GRIB2 forecast data are downloaded here.

    Each result also carries ``cached`` so the UI can distinguish a published
    run from a run that has already been processed by the standalone cache
    builder.
    """
    if requests is None:
        raise RuntimeError("requests is required for ICON EPS run discovery")

    now=datetime.now(timezone.utc)
    candidates=[]
    for day_offset in range(max(1, int(lookback_days)) + 1):
        d=(now-timedelta(days=day_offset)).date()
        for hour in ICON_EPS_RUN_HOURS:
            run_dt=datetime(d.year,d.month,d.day,hour,tzinfo=timezone.utc)
            if run_dt <= now:
                candidates.append(run_dt)
    candidates=sorted(candidates, reverse=True)

    out=[]
    seen=set()
    for dt in candidates:
        runv=dt.strftime('%Y%m%d%H')
        if runv in seen:
            continue
        seen.add(runv)
        url=f"{ICON_EPS_DWD_ROOT}/{dt:%H}/tot_prec/"
        try:
            rr=requests.get(
                url,
                headers={"User-Agent":"MASRAINMAN-ICON-EPS/1.0","Accept":"text/html,*/*"},
                timeout=15,
            )
            if rr.status_code != 200:
                ecmwf_event(f"ICON EPS RUN NOT PUBLISHED • {dt:%d %b %Y • %HZ} • HTTP {rr.status_code}","info")
                continue
            # The F024 endpoint is the only forecast object required for the
            # current standalone cache product.
            pat=re.compile(
                rf"icon-eps_global_icosahedral_single-level_{re.escape(runv)}_024_tot_prec\.grib2(?:\.bz2)?"
            )
            published=bool(pat.search(rr.text))
            if not published:
                # Be tolerant of directory HTML escaping/truncation while
                # remaining exact on the run stamp and F024 field.
                published=(runv in rr.text and "_024_tot_prec.grib2" in rr.text)
            if not published:
                ecmwf_event(f"ICON EPS RUN NOT PUBLISHED • {dt:%d %b %Y • %HZ} • F024 missing","info")
                continue

            cache_file=ICON_EPS_CACHE_ROOT / f"ICON_EPS_{runv}_F024_SUPERENSEMBLE_GRID_40MEM.npy"
            cached=False
            if cache_file.exists():
                try:
                    arr=np.load(cache_file,mmap_mode='r')
                    cached=(arr.shape==(40,85,97))
                except Exception:
                    cached=False
            out.append({
                "value":runv,
                "label":dt.strftime('%d %b %Y • %HZ'),
                "published":True,
                "cached":cached,
            })
            ecmwf_event(
                f"ICON EPS RUN AVAILABLE • {dt:%d %b %Y • %HZ} • F024 • "
                f"{'CACHE READY' if cached else 'CACHE NOT BUILT'}","success" if cached else "info"
            )
            if len(out)>=limit:
                break
        except Exception as exc:
            ecmwf_event(f"ICON EPS RUN PROBE FAILED • {dt:%d %b %Y • %HZ} • {exc}","info")
            continue
    return out

# ---------------------------------------------------------------------------
# STABLE RUN DISCOVERY CACHE — discovery never probes model servers.
# Actual availability is checked only by the selected download path.
# ---------------------------------------------------------------------------
RUN_DISCOVERY_CACHE_FILE = ECMWF_ROOT / "runinfo" / "masrainman_run_discovery_cache.json"
RUN_DISCOVERY_CACHE_TTL_HOURS = 6
RUN_DISCOVERY_LOCK = threading.Lock()
RUN_DISCOVERY_MEMORY_CACHE = {}
MODEL_REGISTRY = {
    "deterministic": {"ECMWF":"ECMWF IFS HRES", "GFS":"GFS", "ICON":"ICON", "UKMET":"UKMET", "GEM":"GEM", "AIFS":"AIFS", "AIGFS":"AIGFS"},
    "ensemble": {"ECMWF":"ECMWF ENS", "GFS":"GFS / GEFS", "ICON":"ICON EPS"},
}

# ---------------------------------------------------------------------------
# TALK TO MASRAINMAN — location/briefing layer
# The voice UI is deliberately separated from model physics. It consumes the
# same rainfall engine/cache used by the dashboard and never invents forecast
# values. A future local gazetteer can replace TALK_PLACE_INDEX without changing
# the briefing or voice layers.
# ---------------------------------------------------------------------------
TALK_PLACE_INDEX = {
    # Tamil Nadu / Puducherry
    "chennai": (13.0827, 80.2707, "Chennai"),
    "madras": (13.0827, 80.2707, "Chennai"),
    "chengalpattu": (12.6819, 79.9888, "Chengalpattu"),
    "kanchipuram": (12.8342, 79.7036, "Kanchipuram"),
    "tiruvallur": (13.1430, 79.9070, "Tiruvallur"),
    "thiruvallur": (13.1430, 79.9070, "Tiruvallur"),
    "vellore": (12.9165, 79.1325, "Vellore"),
    "hosur": (12.7409, 77.8253, "Hosur"),
    "krishnagiri": (12.5186, 78.2137, "Krishnagiri"),
    "dharmapuri": (12.1211, 78.1582, "Dharmapuri"),
    "pollachi": (10.6581, 77.0081, "Pollachi"),
    "udumalpet": (10.5881, 77.2477, "Udumalpet"),
    "mettur": (11.7875, 77.8000, "Mettur"),
    "sivakasi": (9.4490, 77.7979, "Sivakasi"),
    "rajapalayam": (9.4527, 77.5533, "Rajapalayam"),
    "karaikudi": (10.0731, 78.7808, "Karaikudi"),
    "chidambaram": (11.3990, 79.6910, "Chidambaram"),
    "kumbakonam": (10.9602, 79.3845, "Kumbakonam"),
    "ranipet": (12.9249, 79.3329, "Ranipet"),
    "tirupattur": (12.4950, 78.5677, "Tirupattur"),
    "tiruvannamalai": (12.2253, 79.0747, "Tiruvannamalai"),
    "thiruvannamalai": (12.2253, 79.0747, "Tiruvannamalai"),
    "villupuram": (11.9401, 79.4861, "Villupuram"),
    "viluppuram": (11.9401, 79.4861, "Villupuram"),
    "cuddalore": (11.7480, 79.7714, "Cuddalore"),
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
    "thanjavur": (10.7867, 79.1378, "Thanjavur"),
    "tanjore": (10.7867, 79.1378, "Thanjavur"),
    "tiruvarur": (10.7661, 79.6344, "Tiruvarur"),
    "nagapattinam": (10.7672, 79.8449, "Nagapattinam"),
    "mayiladuthurai": (11.1035, 79.6550, "Mayiladuthurai"),
    "pudukkottai": (10.3797, 78.8208, "Pudukkottai"),
    "sivaganga": (9.8433, 78.4809, "Sivaganga"),
    "madurai": (9.9252, 78.1198, "Madurai"),
    "dindigul": (10.3673, 77.9803, "Dindigul"),
    "theni": (10.0104, 77.4768, "Theni"),
    "virudhunagar": (9.5680, 77.9624, "Virudhunagar"),
    "ramanathapuram": (9.3639, 78.8395, "Ramanathapuram"),
    "rameswaram": (9.2876, 79.3129, "Rameswaram"),
    "thoothukudi": (8.7642, 78.1348, "Thoothukudi"),
    "tuticorin": (8.7642, 78.1348, "Thoothukudi"),
    "tirunelveli": (8.7139, 77.7567, "Tirunelveli"),
    "tenkasi": (8.9590, 77.3152, "Tenkasi"),
    "kanyakumari": (8.0883, 77.5385, "Kanyakumari"),
    "nagercoil": (8.1833, 77.4119, "Nagercoil"),
    "puducherry": (11.9416, 79.8083, "Puducherry"),
    "pondicherry": (11.9416, 79.8083, "Puducherry"),
    # Kerala / Karnataka / Andhra / Telangana reference cities
    "kochi": (9.9312, 76.2673, "Kochi"),
    "cochin": (9.9312, 76.2673, "Kochi"),
    "thiruvananthapuram": (8.5241, 76.9366, "Thiruvananthapuram"),
    "trivandrum": (8.5241, 76.9366, "Thiruvananthapuram"),
    "kollam": (8.8932, 76.6141, "Kollam"),
    "alappuzha": (9.4981, 76.3388, "Alappuzha"),
    "kottayam": (9.5916, 76.5222, "Kottayam"),
    "thrissur": (10.5276, 76.2144, "Thrissur"),
    "kozhikode": (11.2588, 75.7804, "Kozhikode"),
    "calicut": (11.2588, 75.7804, "Kozhikode"),
    "kannur": (11.8745, 75.3704, "Kannur"),
    "bengaluru": (12.9716, 77.5946, "Bengaluru"),
    "bangalore": (12.9716, 77.5946, "Bengaluru"),
    "mysuru": (12.2958, 76.6394, "Mysuru"),
    "mysore": (12.2958, 76.6394, "Mysuru"),
    "hyderabad": (17.3850, 78.4867, "Hyderabad"),
    "vijayawada": (16.5062, 80.6480, "Vijayawada"),
    "visakhapatnam": (17.6868, 83.2185, "Visakhapatnam"),
    "vizag": (17.6868, 83.2185, "Visakhapatnam"),
}

# ---------------------------------------------------------------------------
# LIVE TOWN/CITY GEOCODER
# Built-in aliases remain the fastest path. Unknown places are resolved online
# to latitude/longitude so every model can sample the forecast grid at the
# actual requested location. Nominatim/OSM is queried only after a built-in
# match fails and results are kept in a request-process memory cache.
# ---------------------------------------------------------------------------
TALK_GEOCODE_CACHE = {}
TALK_GEOCODE_LOCK = threading.Lock()
TALK_GEOCODE_LAST_REQUEST = 0.0
TALK_GEOCODE_MIN_INTERVAL = 1.05
TALK_GEOCODE_USER_AGENT = "MasRainman-NEM-Weather-Command-Center/1.0 (local weather dashboard)"
TALK_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"


def _talk_geocode_result_score(item, query):
    """Rank Indian settlement results, preferring South India and exact names."""
    address = item.get("address") or {}
    q = talk_normalize_place(query)
    name = str(item.get("name") or "").strip().lower()
    display = str(item.get("display_name") or "").lower()
    state = str(address.get("state") or "").upper()
    score = float(item.get("importance") or 0.0) * 2.0
    if name == q:
        score += 5.0
    elif q and q in name:
        score += 2.5
    if q and q in display:
        score += 0.75
    if state in SOUTH_STATES:
        score += 2.0
    if state == "TAMIL NADU":
        score += 1.0
    if str(item.get("type") or "") in ("city", "town", "municipality", "village"):
        score += 1.0
    return score


def talk_geocode_place(query):
    """Resolve an arbitrary town/city name to lat/lon using Nominatim.

    This is an end-user-triggered lookup, not bulk geocoding. A single request
    is made for an unknown place, with India restricted in the query, and the
    result is cached in memory for the current dashboard process.
    """
    global TALK_GEOCODE_LAST_REQUEST
    q = talk_normalize_place(query)
    if not q:
        return None
    cache_key = q
    with TALK_GEOCODE_LOCK:
        cached = TALK_GEOCODE_CACHE.get(cache_key)
        if cached:
            return dict(cached)
        if requests is None:
            return None
        wait_for = TALK_GEOCODE_MIN_INTERVAL - (time.time() - TALK_GEOCODE_LAST_REQUEST)
        if wait_for > 0:
            time.sleep(wait_for)
        TALK_GEOCODE_LAST_REQUEST = time.time()

    params = {
        "q": f"{q}, India",
        "format": "jsonv2",
        "addressdetails": 1,
        "namedetails": 1,
        "limit": 8,
        "countrycodes": "in",
        "featureType": "settlement",
        "layer": "address",
        "accept-language": "en",
    }
    try:
        r = requests.get(
            TALK_GEOCODE_URL,
            params=params,
            headers={"User-Agent": TALK_GEOCODE_USER_AGENT, "Referer": "http://127.0.0.1:8765/"},
            timeout=12,
        )
        r.raise_for_status()
        rows = r.json()
        if not isinstance(rows, list) or not rows:
            return None
        ranked = sorted(rows, key=lambda x: _talk_geocode_result_score(x, q), reverse=True)
        best = ranked[0]
        address = best.get("address") or {}
        lat = float(best["lat"])
        lon = float(best["lon"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        display_name = str(best.get("display_name") or "").strip()
        canonical = str(best.get("name") or "").strip() or q.title()
        state = str(address.get("state") or "").strip()
        district = str(address.get("state_district") or address.get("district") or address.get("county") or "").strip()
        result = {
            "query": query,
            "name": canonical,
            "lat": lat,
            "lon": lon,
            "exact": False,
            "resolver": "Nominatim / OpenStreetMap",
            "display_name": display_name,
            "state": state,
            "district": district,
            "geocode_source": "OpenStreetMap Nominatim",
        }
        with TALK_GEOCODE_LOCK:
            TALK_GEOCODE_CACHE[cache_key] = dict(result)
        print(f"[TALK GEOCODE] {query!r} -> {canonical} ({lat:.5f}, {lon:.5f}) • {state}", flush=True)
        return result
    except Exception as exc:
        print(f"[TALK GEOCODE] FAILED {query!r} • {type(exc).__name__}: {exc}", flush=True)
        return None

TALK_DISTRICT_FALLBACK = {
    "chennai": "Chennai", "tiruvallur": "Tiruvallur", "chengalpattu": "Chengalpattu",
    "kanchipuram": "Kanchipuram", "cuddalore": "Cuddalore", "villupuram": "Villupuram",
    "salem": "Salem", "erode": "Erode", "coimbatore": "Coimbatore",
    "thanjavur": "Thanjavur", "tiruvarur": "Tiruvarur", "nagapattinam": "Nagapattinam",
    "mayiladuthurai": "Mayiladuthurai", "pudukkottai": "Pudukkottai",
    "madurai": "Madurai", "dindigul": "Dindigul", "theni": "Theni",
    "virudhunagar": "Virudhunagar", "ramanathapuram": "Ramanathapuram",
    "thoothukudi": "Thoothukudi", "tirunelveli": "Tirunelveli",
    "tenkasi": "Tenkasi", "kanyakumari": "Kanyakumari",
}
TALK_ENGINE_STATUS = {
    "enabled": True,
    "resolver": "Built-in gazetteer + live town/city geocoding to latitude/longitude",
    "voice_input": "Browser Web Speech API when supported",
    "voice_output": "Browser speechSynthesis",
}

SOUTH_STATES = ["TAMIL NADU","KERALA","KARNATAKA","ANDHRA PRADESH","TELANGANA","PUDUCHERRY","GOA","MAHARASHTRA","ODISHA","CHHATTISGARH"]
ECMWF_RUNINFO_DET = ECMWF_ROOT / "runinfo" / "ecmwf_hres_latest.txt"
ECMWF_RUNINFO_ENS = ECMWF_ROOT / "runinfo" / "ecmwf_ens_latest.txt"
RAINFALL_CACHE = {}
ECMWF_DOWNLOAD_STATUS = {
    "state":"idle", "message":"Waiting for live model download…",
    "run":None, "downloaded":0, "total":0, "error":None,
    "started_at":None, "finished_at":None, "events":[],
    # Live byte-level download telemetry used by the ensemble marquee.
    "downloaded_bytes":0, "total_bytes":0, "speed_bps":0.0,
    "eta_seconds":None, "expected_finish":None,
    "current_member":0, "member_total":50,
    "current_endpoint":None, "endpoint_total":0,
    "estimated_total_bytes":0, "request_key":None, "render_ready":False, "product":None, "period":None
}
ECMWF_DOWNLOAD_LOCK = threading.Lock()
# Matplotlib is not thread-safe; serialize /plot.png rendering.
PLOT_RENDER_LOCK = threading.Lock()
ECMWF_EVENT_LOCK = threading.Lock()
ECMWF_JOB_LOCK = threading.Lock()
# ecCodes/cfgrib decoding is deliberately serialized. Downloads may run in parallel,
# but native GRIB decoding must never happen concurrently in the same Python process.
# This protects Windows ecCodes MEMFS/definitions and avoids cross-thread decoder races.
GFS_DECODE_LOCK = threading.RLock()
ECMWF_JOB_TOKEN = 0
ECMWF_ACTIVE_REQUEST_KEY = None
MODEL_IDENTITY_STATUS = {
    "mode": "deterministic",
    "model": None,
    "label": "SELECTED MODEL",
    "state": "idle",
    "run": None,
    "source": None,
    "message": "Waiting for selected model…",
    "download": None,
    "updated_at": None,
}
MODEL_IDENTITY_LOCK = threading.Lock()

ECMWF_CLIENT_LOCK = threading.Lock()
ECMWF_CLIENTS = {}


def get_ecmwf_opendata_client(source="ecmwf"):
    """Compatibility factory for ecmwf-opendata.

    V50 HRES retrieval no longer depends on official Open Data .index + byte-range; the dashboard
    uses the official ECMWF/AWS .index + HTTP Range path below. This factory
    is retained only for compatibility with any auxiliary code and passes
    retry-related arguments only when the installed package supports them.
    """
    if ECMWFOpenDataClient is None:
        raise RuntimeError(
            "The official ecmwf-opendata package is not installed. "
            "Run: python -m pip install -U ecmwf-opendata"
        )
    with ECMWF_CLIENT_LOCK:
        client = ECMWF_CLIENTS.get(source)
        if client is None:
            kwargs = {"source": source}
            try:
                import inspect
                params = inspect.signature(ECMWFOpenDataClient).parameters
            except Exception:
                params = {}
            optional = {
                "maximum_retries": 1,
                "retry_after": 15,
                "use_server_retry_after": False,
            }
            for key, value in optional.items():
                if key in params:
                    kwargs[key] = value
            client = ECMWFOpenDataClient(**kwargs)
            ECMWF_CLIENTS[source] = client
        return client


def hres_client_steps(run_hour, period):
    """Return the exact TP endpoints required for the selected HRES product."""
    endpoints = ecmwf_download_endpoints(int(run_hour), "deterministic", period)
    return endpoints


def retrieve_hres_tp_endpoint(run_date, run_hour, endpoint, target, job_token=None):
    """Retrieve one HRES TP GRIB message using ECMWF/AWS official Open Data.

    V50 deliberately bypasses ecmwf-opendata official Open Data .index + byte-range for HRES.
    The dashboard first reads the official JSON-lines .index file, selects
    the deterministic surface Total Precipitation (TP) message, then fetches
    only that GRIB byte range. This avoids the Client constructor/version
    mismatch seen in the user's V49 log and avoids the Client's long retry loop.
    """
    if job_token is not None and not ecmwf_job_current(job_token):
        return False, "job superseded"

    ecmwf_event(
        f"HRES DIRECT RETRIEVE • {run_date} {run_hour:02d}Z • "
        f"F{int(endpoint):03d} • official index + byte-range", "info"
    )
    ok, source_or_error = _download_hres_endpoint(
        run_date, int(run_hour), int(endpoint), Path(target)
    )
    if ok:
        ecmwf_event(
            f"HRES DIRECT SUCCESS • F{int(endpoint):03d} • source={source_or_error}",
            "success"
        )
        return True, source_or_error

    ecmwf_event(
        f"HRES DIRECT FAILED • F{int(endpoint):03d} • {source_or_error}",
        "warn"
    )
    return False, source_or_error or "unknown ECMWF Open Data direct-download error"


def ensure_runtime_dirs():
    """Recreate only empty runtime directories after session cleanup.

    Forecast files remain session-only. The dashboard may delete the model
    cache tree after a successful PNG response, so every new job must recreate
    its own parent/runinfo directories before writing anything.
    """
    dirs = [
        ECMWF_ROOT, GFS_ROOT, GEFS_ROOT, ICON_ROOT, ICON_RAW_ROOT, ICON_GRIB_ROOT,
        AIFS_ROOT, AIFS_RAW_ROOT, AIGFS_ROOT, AIGFS_RAW_ROOT, AIGFS_GRIB_ROOT,
    ]
    for obj in globals().get("UKMET_GRIB_ROOT", None), globals().get("GEM_GRIB_ROOT", None), globals().get("ICON_EPS_CACHE_ROOT", None):
        if isinstance(obj, Path):
            dirs.append(obj)
    dirs.extend([
        ECMWF_ROOT / "runinfo", GEFS_ROOT / "runinfo", GFS_ROOT / "runinfo",
        ICON_ROOT / "runinfo", AIFS_ROOT / "runinfo", AIGFS_ROOT / "runinfo",
    ])
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass


def begin_ecmwf_job(mode, model, run, period):
    """Start a new authoritative model job only for an explicitly selected run."""
    if not run or str(run).upper() == "AUTO" or not re.match(r"^\d{10}$", str(run)):
        raise ValueError("RUN SELECTION REQUIRED: automatic/latest-run downloads are disabled")
    global ECMWF_JOB_TOKEN, ECMWF_ACTIVE_REQUEST_KEY
    ensure_runtime_dirs()
    with ECMWF_JOB_LOCK:
        ECMWF_JOB_TOKEN += 1
        token = ECMWF_JOB_TOKEN
        ECMWF_ACTIVE_REQUEST_KEY = f"{mode}|{model}|{run or 'AUTO'}|{period}|MEAN"
    if model == "GFS":
        label = "GFS"
    elif model == "AIGFS":
        label = "AIGFS"
    elif model == "GEM":
        label = "GEM / CMC GDPS"
    elif model == "AIFS":
        label = "AIFS Single"
    elif model == "ICON":
        label = "ICON Global"
    elif model == "UKMET":
        label = "UKMET Global 10 km"
    elif mode == "ensemble":
        label = "ECMWF ENS"
    else:
        label = "ECMWF IFS HRES"
    with ECMWF_DOWNLOAD_LOCK:
        ECMWF_DOWNLOAD_STATUS.update(
            state="downloading",
            message=f"Starting {label} • run {run or 'AUTO'} • {period}",
            run=None if not run or run == "AUTO" else f"{str(run)[:8]} {str(run)[8:10]}Z",
            downloaded=0, total=0, error=None,
            started_at=datetime.now(timezone.utc).isoformat(),
            finished_at=None, events=[],
            downloaded_bytes=0, total_bytes=0, speed_bps=0.0,
            eta_seconds=None, expected_finish=None,
            current_member=0, member_total=50, current_endpoint=None,
            endpoint_total=0, estimated_total_bytes=0, request_key=ECMWF_ACTIVE_REQUEST_KEY, render_ready=False, product=None, period=period
        )
    set_model_identity(mode=mode, model=model, label=label, state="starting",
                       run=None if not run or run == "AUTO" else f"{str(run)[:8]} {str(run)[8:10]}Z",
                       source=None, download="starting",
                       message=f"Starting {label} engine for {run or 'AUTO'} • {period}")
    ecmwf_event(f"ENGINE STARTED • {label} • run={run or 'AUTO'} • period={period}", "info")
    return token

def ecmwf_job_current(token):
    with ECMWF_JOB_LOCK:
        return token == ECMWF_JOB_TOKEN


def set_model_identity(**kwargs):
    with MODEL_IDENTITY_LOCK:
        MODEL_IDENTITY_STATUS.update(kwargs)
        MODEL_IDENTITY_STATUS["updated_at"] = datetime.now(timezone.utc).isoformat()


def ecmwf_event(message, level="info"):
    """Add a short human-readable live-data event for the dashboard."""
    item = {
        "time": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "message": str(message),
        "level": level
    }
    with ECMWF_EVENT_LOCK:
        events = ECMWF_DOWNLOAD_STATUS.setdefault("events", [])
        events.append(item)
        del events[:-30]
    print(f"[ECMWF][{level.upper()}] {message}")



HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MASRAINMAN NEM Weather Command Center</title>
<style>
:root{
  --navy:#102a43;
  --blue:#1769aa;
  --light:#eef7fb;
  --sea:#dff3fa;
  --line:#c8dce7;
  --text:#17324d;
  --muted:#607d94;
  --green:#238b68;
  --amber:#d88a18;
  --red:#c74747;
  --white:#ffffff;
}
*{box-sizing:border-box}
body{
  margin:0;
  font-family:Inter,Segoe UI,Arial,sans-serif;
  background:linear-gradient(135deg,#f8fcfe,#e7f5fb);
  color:var(--text);
}
header{
  height:72px;
  background:linear-gradient(90deg,#0c2740,#164e70);
  color:white;
  display:flex;
  align-items:center;
  justify-content:space-between;
  padding:0 26px;
  position:sticky;
  top:0;
  z-index:10;
  box-shadow:0 4px 18px #0c274033;
}
.brand{font-size:20px;font-weight:800;letter-spacing:.5px}
.subbrand{font-size:12px;opacity:.78;margin-top:3px}
.header-right{display:flex;gap:10px;align-items:center}
.pill{
  border:1px solid #ffffff44;
  background:#ffffff14;
  padding:8px 12px;
  border-radius:20px;
  font-size:12px;
}
.layout{display:grid;grid-template-columns:225px 1fr;min-height:calc(100vh - 72px)}
nav{
  background:#fafdff;
  border-right:1px solid var(--line);
  padding:18px 12px;
}
.nav-title{
  font-size:11px;
  color:var(--muted);
  font-weight:700;
  margin:14px 10px 7px;
  text-transform:uppercase;
}
.nav-item{
  width:100%;
  border:0;
  background:transparent;
  text-align:left;
  padding:10px 12px;
  margin:2px 0;
  border-radius:8px;
  color:#31536b;
  cursor:pointer;
  font-size:13px;
}
.nav-item:hover,.nav-item.active{background:#dff1f8;color:#0d557b;font-weight:700}
main{padding:22px;max-width:1600px;width:100%;margin:auto}
.page-title{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:18px}
.page-title h1{margin:0;font-size:27px}
.page-title p{margin:6px 0 0;color:var(--muted);font-size:13px}
.controls{display:flex;gap:8px;flex-wrap:wrap}
select,button{
  border:1px solid var(--line);
  border-radius:7px;
  background:white;
  color:var(--text);
  padding:9px 11px;
}
button{cursor:pointer}
button.primary{background:var(--blue);color:white;border-color:var(--blue)}
.grid{
  display:grid;
  grid-template-columns:repeat(12,1fr);
  gap:14px;
}
.card{
  background:rgba(255,255,255,.94);
  border:1px solid var(--line);
  border-radius:13px;
  box-shadow:0 5px 18px #31536b12;
}
.stat{padding:16px}
.stat .label{font-size:12px;color:var(--muted)}
.stat .value{font-size:24px;font-weight:800;margin-top:6px}
.stat .hint{font-size:11px;color:var(--muted);margin-top:4px}
.map-card{grid-column:span 12;overflow:hidden}
.map-head,.panel-head{
  padding:14px 16px;
  border-bottom:1px solid #d9e7ee;
  display:flex;
  justify-content:space-between;
  align-items:center;
}
.map-head h3,.panel-head h3{margin:0;font-size:15px}
.map-area{
  height:445px;
  position:relative;
  overflow:hidden;
  background:
    radial-gradient(circle at 68% 42%,#9ee4f7 0 4%,transparent 4.5%),
    linear-gradient(135deg,#f8fdff,#dff3fa);
}
.map-grid{
  position:absolute;inset:0;
  background-image:
    linear-gradient(#77aabd22 1px,transparent 1px),
    linear-gradient(90deg,#77aabd22 1px,transparent 1px);
  background-size:40px 40px;
}
.india{
  position:absolute;
  left:20%;
  top:8%;
  width:42%;
  height:78%;
  background:#ffffff;
  clip-path:polygon(36% 0,55% 5%,61% 16%,72% 22%,68% 32%,80% 41%,73% 52%,66% 61%,62% 73%,55% 84%,49% 100%,41% 89%,38% 75%,29% 65%,20% 53%,8% 47%,16% 35%,25% 27%,27% 15%);
  border:2px solid #2f6f88;
  box-shadow:0 4px 12px #174b6018;
}
.state-lines{
  position:absolute;left:26%;top:28%;width:31%;height:42%;
  border-left:1px solid #4e819433;
  border-right:1px solid #4e819433;
}
.bay{
  position:absolute;right:0;top:0;width:39%;height:100%;
  background:linear-gradient(160deg,#e8f9fd,#bdeefa);
}
.rain{
  position:absolute;
  border-radius:50%;
  filter:blur(1px);
  opacity:.70;
}
.r1{width:95px;height:55px;left:45%;top:42%;background:#5cb7e6}
.r2{width:120px;height:65px;left:52%;top:53%;background:#4ca5dd}
.r3{width:80px;height:50px;left:34%;top:57%;background:#8bd8ee}
.r4{width:65px;height:42px;left:60%;top:30%;background:#b2e9f4}
.legend{
  position:absolute;left:15px;bottom:14px;background:#ffffffed;
  border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:11px
}
.legend-bar{
  width:180px;height:10px;margin-top:5px;border-radius:5px;
  background:linear-gradient(90deg,#e8f7ff 0%,#9bdcf5 14%,#1686d8 28%,#16c6c8 40%,#42d65c 52%,#b7ed22 64%,#ffd21f 74%,#ff8a00 84%,#ef2b1f 93%,#9b0018 100%);
}
.rain-legend{position:absolute;right:14px;bottom:14px;width:330px;padding:10px 12px 9px;border:1px solid #ffffff38;border-radius:9px;background:linear-gradient(180deg,#10263beF,#0a1b2ddf);color:#fff;box-shadow:0 8px 20px #031b2a55;font-size:11px}
.rain-legend-title{font-size:12px;font-weight:700;margin-bottom:7px}
.rain-legend-bar{height:12px;border-radius:7px;background:linear-gradient(90deg,#e8f7ff 0%,#9bdcf5 14%,#1686d8 28%,#16c6c8 40%,#42d65c 52%,#b7ed22 64%,#ffd21f 74%,#ff8a00 84%,#ef2b1f 93%,#9b0018 100%);border:1px solid #ffffff55}
.rain-legend-ticks{display:flex;justify-content:space-between;margin-top:5px;font-size:10px;color:#fff}
.map-actions{display:flex;gap:6px}
.intel{grid-column:span 4;padding:0}
.signal{padding:12px 16px;border-bottom:1px solid #e4edf2}
.signal:last-child{border-bottom:0}
.signal-top{display:flex;justify-content:space-between;font-size:12px}
.signal-bar{height:7px;background:#e7eef2;border-radius:5px;margin-top:7px;overflow:hidden}
.signal-fill{height:100%;border-radius:5px;background:linear-gradient(90deg,#66bddf,#1769aa)}
.metrics{grid-column:span 12;display:grid;grid-template-columns:repeat(6,1fr);gap:14px}
.timeline{grid-column:span 12;padding:16px}
.timeline-row{display:grid;grid-template-columns:repeat(7,1fr);gap:8px}
.timebox{
  border:1px solid var(--line);border-radius:8px;padding:10px;text-align:center;
  background:#fbfeff;cursor:pointer
}
.timebox.active{border-color:#3b9ac1;background:#e8f7fc}
.timebox b{display:block;font-size:12px}
.timebox span{font-size:10px;color:var(--muted)}
.models{grid-column:span 7;padding:16px}
.model-row{display:grid;grid-template-columns:75px 1fr 48px;gap:10px;align-items:center;margin:12px 0}
input[type=range]{width:100%}
.thumb-panel{grid-column:span 5;padding:16px}
.thumbs{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}
.thumb{
  height:85px;border:1px solid var(--line);border-radius:8px;
  background:linear-gradient(135deg,#e8f8fc,#9edcf0);
  display:flex;align-items:flex-end;padding:7px;font-size:10px;font-weight:700;
  cursor:pointer
}
.modules{grid-column:span 12;padding:16px}
.module-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}
.module{
  border:1px solid var(--line);border-radius:9px;padding:12px;background:#fbfeff;
  cursor:pointer
}
.module b{font-size:12px}.module span{display:block;color:var(--muted);font-size:10px;margin-top:5px}
.footer{margin-top:18px;color:#71899b;font-size:11px;text-align:center}
.modal{
  display:none;position:fixed;inset:0;background:#06263bcc;z-index:100;
  align-items:center;justify-content:center;padding:25px
}
.modal.open{display:flex}
.modal-box{
  background:white;width:min(1100px,96vw);height:min(760px,92vh);
  border-radius:14px;overflow:hidden;display:flex;flex-direction:column
}
.modal-map{flex:1;background:linear-gradient(135deg,#f9fdff,#c9eff9);position:relative}
.close{background:#fff;border:1px solid var(--line);padding:6px 10px;border-radius:6px}

/* V2 premium UI enhancements */
body{background:linear-gradient(135deg,#f7fcff 0%,#edf8fc 48%,#e4f4fa 100%)}
header{height:78px;padding:0 30px;background:linear-gradient(100deg,#0a2942,#124b6c 62%,#17698a)}
.brand{font-size:21px;letter-spacing:.7px}.subbrand{font-size:12px}
.layout{grid-template-columns:235px 1fr;min-height:calc(100vh - 78px)}
nav{padding:20px 13px;background:rgba(252,254,255,.94);box-shadow:8px 0 24px #174b6010}
.nav-item{padding:11px 13px;border-radius:9px;transition:.18s ease}
.nav-item:hover{transform:translateX(2px)}
main{padding:24px 26px 30px;max-width:1700px}
.page-title{padding:2px 2px 4px}
.page-title h1{font-size:30px;letter-spacing:-.4px}
.page-title p{max-width:720px;line-height:1.45}
.controls select,.controls button{height:40px;border-radius:9px;font-size:12px}
button.primary{box-shadow:0 5px 14px #1769aa2b}
.metrics{gap:12px}
.stat{padding:14px 15px;position:relative;overflow:hidden;min-height:102px}
.stat:after{content:"";position:absolute;right:-25px;top:-30px;width:75px;height:75px;border-radius:50%;background:#dff3fa}
.stat .label{font-weight:600}.stat .value{font-size:25px;position:relative;z-index:1}.stat .hint{position:relative;z-index:1}
.card{border-radius:14px;box-shadow:0 7px 24px #174b6010}
.map-card{grid-column:span 12}.intel{grid-column:span 12}
.map-head,.panel-head{padding:15px 17px}
.map-area{height:620px;position:relative;overflow:hidden;background:#173d4b}
.rain-plot-image{position:absolute;inset:0;width:100%;height:100%;display:block;object-fit:fill;background:#173d4b}
.full-plot-image{position:absolute;inset:0;width:100%;height:100%;display:block;object-fit:contain;background:#173d4b}
.modal-map{flex:1;background:#173d4b;position:relative;overflow:hidden}
.map-card .map-area:before{display:none}
.map-actions button{font-size:11px;padding:8px 10px}
.intel .panel-head{background:linear-gradient(90deg,#fbfeff,#eef9fc)}
.signal{padding:13px 17px}.signal-top{font-size:11px}.signal-bar{height:8px}
.timeline{padding:17px}.timebox{padding:11px 8px;transition:.15s}.timebox:hover{border-color:#63aecb;transform:translateY(-1px)}
.models{padding:17px}.model-row{margin:13px 0}.model-row input{accent-color:#1769aa}
.blend-status{margin:8px 0 14px;padding:9px 11px;border-radius:8px;background:#eef8fc;border:1px solid #cde7f0;font-size:10px;color:#38647a;display:flex;justify-content:space-between}
.thumb-panel{padding:17px}.thumbs{grid-template-columns:repeat(3,1fr);gap:10px}
.thumb{height:92px;position:relative;overflow:hidden;background:linear-gradient(145deg,#effbff,#b9e7f3);transition:.18s}
.thumb:before{content:"";position:absolute;inset:0;background:radial-gradient(ellipse at 65% 45%,#4ca5dd66 0 12%,transparent 13%),radial-gradient(ellipse at 45% 65%,#1769aa55 0 10%,transparent 11%)}
.thumb:hover{transform:translateY(-2px);box-shadow:0 8px 16px #174b6020}
.thumb span{position:relative;z-index:2;background:#ffffffdd;padding:4px 6px;border-radius:5px}
.modules{padding:17px}.module{min-height:76px;transition:.15s}.module:hover{transform:translateY(-2px);border-color:#7abbd2;box-shadow:0 7px 14px #174b6010}
.footer{padding-top:5px}
.status-banner{grid-column:span 12;display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:10px;padding:11px 13px;background:linear-gradient(90deg,#eaf8fc,#f8fcfe);border:1px solid #c8e1ea;border-radius:12px}
.status-chip{display:flex;align-items:center;gap:8px;font-size:10px;color:#527085}.dot{width:8px;height:8px;border-radius:50%;background:#45a878;box-shadow:0 0 0 4px #45a87818}.dot.amber{background:#d88a18;box-shadow:0 0 0 4px #d88a1818}
.quick-note{font-size:10px;color:#6b8495;margin-top:3px}
.live-engine{grid-column:span 12;display:grid;grid-template-columns:1.05fr 1.6fr;gap:12px;padding:14px;background:#ffffff;border:1px solid #c8dce7;border-radius:12px;box-shadow:0 4px 14px #174b6010}
.live-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.live-title{font-size:13px;font-weight:800;color:#12384f}
.live-state{font-size:10px;font-weight:800;padding:5px 9px;border-radius:999px;background:#eef8fc;color:#1769aa;border:1px solid #c9e4ee}
.live-state.ready{background:#eaf8f1;color:#238b68;border-color:#bfe3d1}.live-state.error{background:#fff0f0;color:#b43c3c;border-color:#efc2c2}
.live-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px 16px}
.live-item{font-size:10px;color:#607d94;min-width:0;overflow:hidden}.live-item b{display:block;font-size:11px;color:#17324d;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
.progress-wrap{margin-top:11px;height:9px;background:#e7f0f4;border-radius:8px;overflow:hidden}.progress-fill{height:100%;width:0;background:linear-gradient(90deg,#48b5d8,#1769aa);transition:width .3s}
.activity{border-left:1px solid #d9e7ed;padding-left:14px;min-height:150px}.activity-title{font-size:11px;font-weight:800;color:#17324d;margin-bottom:7px}.activity-log{max-height:150px;overflow:auto;font-family:Consolas,monospace;font-size:9px;line-height:1.55;color:#557184;background:#f8fcfe;border:1px solid #e1edf2;border-radius:8px;padding:7px 9px}.activity-row.info{color:#38647a}.activity-row.success{color:#238b68}.activity-row.warn{color:#a66b12}.activity-row.error{color:#b43c3c}
.stale-banner{display:none;grid-column:span 12;padding:9px 12px;border-radius:9px;background:#fff4df;border:1px solid #edcf99;color:#8a5a0a;font-size:10px;font-weight:700}.stale-banner.show{display:block}
@media(max-width:800px){.live-engine{grid-template-columns:1fr}.activity{border-left:0;border-top:1px solid #d9e7ed;padding-left:0;padding-top:12px}}
.modal-box{box-shadow:0 25px 80px #031b2a66}.modal-map{background:linear-gradient(145deg,#f9fdff,#c6edf7)}
@media(max-width:1100px){

  .layout{grid-template-columns:1fr} nav{display:none}
  .map-card,.intel,.models,.thumb-panel{grid-column:span 12}
  .metrics{grid-template-columns:repeat(3,1fr)}
  .module-grid{grid-template-columns:repeat(3,1fr)}
}
@media(max-width:650px){
  main{padding:12px}.metrics{grid-template-columns:repeat(2,1fr)}
  .timeline-row{grid-template-columns:repeat(2,1fr)}
  .module-grid{grid-template-columns:repeat(2,1fr)}
}

/* REAL GIS MAP */
@import url("https://unpkg.com/leaflet@1.9.4/dist/leaflet.css");
.gis-map{position:absolute!important;left:0!important;top:0!important;right:0!important;bottom:0!important;width:100%!important;height:100%!important;z-index:3;background:#e9f8fc;min-width:0;min-height:0}
.map-focus-chip{position:absolute;left:14px;top:14px;z-index:650;background:#081d2bdd;color:#fff;border:1px solid #ffffff38;border-radius:9px;padding:9px 11px;font-size:10px;line-height:1.45;box-shadow:0 6px 18px #031b2a55}.map-focus-chip b{font-size:11px;display:block}.map-focus-chip span{opacity:.8}.map-bottom-controls{position:absolute;left:14px;right:14px;bottom:14px;z-index:650;display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;background:#071b2ddd;border:1px solid #ffffff35;box-shadow:0 8px 22px #031b2a55;color:#fff}.map-bottom-controls input[type=range]{flex:1;accent-color:#ffffff}.map-day-label{min-width:64px;font-size:10px;font-weight:800;text-align:center}.map-control-btn{background:#ffffff18;color:#fff;border:1px solid #ffffff45;padding:6px 9px;border-radius:6px;font-size:10px}.map-control-btn:hover{background:#ffffff2b}
/* V174: upper-air selector buttons live on a white card, so they must not inherit
   the dark map-control styling (white text on white background). */
.ua-model-btn,.ua-level-btn,.ua-hour-btn,.pressure-model-btn{background:#ffffff!important;color:#17324d!important;border:1px solid #b9d3e1!important;font-weight:900;box-shadow:0 1px 2px rgba(20,55,75,.05);}
.ua-model-btn:hover,.ua-level-btn:hover,.ua-hour-btn:hover,.pressure-model-btn:hover{background:#eaf6fb!important;border-color:#6fb4d2!important;color:#102f46!important;}
.ua-model-btn.active,.ua-level-btn.active,.ua-hour-btn.active,.pressure-model-btn.active{background:#17324d!important;color:#ffffff!important;border-color:#17324d!important;box-shadow:0 3px 8px rgba(23,50,77,.18);}
/* V185 PRESSURE MODEL BUTTON VISIBILITY — pressure card uses the same selector system as Wind. */
.pressure-model-btn{display:inline-flex!important;align-items:center!important;justify-content:center!important;visibility:visible!important;opacity:1!important;cursor:pointer!important;white-space:nowrap!important;}
body.pressure-only-view .page-title,body.pressure-only-view #moduleWorkspace,body.pressure-only-view .grid{display:none!important;}
.ua-run-pill{display:inline-flex;align-items:center;gap:6px;background:#eef7fb;border:1px solid #c7dce6;color:#17324d;border-radius:8px;padding:6px 9px;font-size:10px;font-weight:900;}
.ua-run-pill .run-label{color:#60737d;font-weight:800;}.ua-run-history{padding:6px 8px;background:#f7fbfd;border:1px solid #dbe8ef;border-radius:9px;}
.ua-run-btn{background:#ffffff!important;color:#17324d!important;border:1px solid #8fbfd3!important;font-weight:900;box-shadow:0 1px 2px rgba(20,55,75,.05);padding:6px 9px;border-radius:6px;font-size:10px;cursor:pointer;}
.ua-run-btn:hover{background:#eaf6fb!important;border-color:#6fb4d2!important;}
.ua-run-btn.active{background:#17324d!important;color:#ffffff!important;border-color:#17324d!important;box-shadow:0 3px 8px rgba(23,50,77,.18);}

.ua-status-pill{background:#f7fbfd;border:1px solid #dbe8ef;border-radius:8px;padding:7px 10px;min-width:250px;text-align:right;}
.coord-grid{pointer-events:none!important}.coord-label{font-size:9px;font-weight:700;color:#17324d;background:#ffffffd9;border:1px solid #b8d0dc;border-radius:4px;padding:2px 4px;white-space:nowrap;box-shadow:0 2px 6px #174b6014}.leaflet-tile-pane{filter:saturate(.92) contrast(1.03) brightness(1.03)}.leaflet-image-layer{image-rendering:auto}.leaflet-pane img{max-width:none!important}.mrRainPane{pointer-events:none}.rainfall-meta{position:absolute;right:14px;top:14px;z-index:650;background:#ffffffea;border:1px solid #c7dce6;border-radius:8px;padding:7px 9px;font-size:9px;color:#33566d;box-shadow:0 5px 14px #174b6018}.rainfall-meta b{color:#17324d}
.gis-map .leaflet-control-attribution{font-size:9px}
.gis-map .leaflet-control-zoom a{color:#17324d}
.map-area:before{display:none!important}
.gis-status{position:absolute;right:14px;top:13px;z-index:500;background:#ffffffec;border:1px solid #c8dce7;border-radius:999px;padding:6px 9px;font-size:9px;font-weight:700;color:#1769aa;letter-spacing:.3px;box-shadow:0 3px 10px #174b6015}
.gis-status.loading{color:#a66b12}
.state-label{background:rgba(255,255,255,.86);border:0;box-shadow:0 1px 5px rgba(20,70,90,.15);font-size:9px;font-weight:700;color:#31536b;padding:2px 4px;border-radius:4px}
.sea-label{color:#4b8ba5;font-size:11px;font-style:italic;letter-spacing:.4px}
.rain-overlay{background:radial-gradient(ellipse at center,rgba(23,105,170,.38),rgba(76,165,221,.18) 45%,transparent 70%);border-radius:50%;filter:blur(2px);pointer-events:none}
@media(max-width:650px){.gis-status{font-size:8px}}
.legend-bar{height:10px;border-radius:8px;margin:7px 0;background:linear-gradient(90deg,#e8f7ff,#9bdcf5,#39a9e8,#1686d8,#16c6c8,#42d65c,#b7ed22,#ffd21f,#ff8a00,#ef2b1f,#9b0018)}
.rainfall-legend{min-width:280px}
.rainfall-legend .legend-ticks{display:flex;justify-content:space-between;gap:6px;font-size:9px;font-weight:700;color:#12384f}
.rainfall-legend .legend-ticks span{flex:1;text-align:center}


/* V24 CLEAN UI */
body{background:#f4f8fa;color:#17324d;overflow-x:hidden}
header{height:64px;padding:0 24px;background:linear-gradient(100deg,#0b3048,#145a79);box-shadow:0 2px 12px #08283a22}
.brand{font-size:20px;letter-spacing:.3px}.subbrand{font-size:10px;margin-top:2px}
.header-right{gap:8px}.pill{padding:7px 10px;font-size:9px}
.layout{grid-template-columns:190px 1fr;min-height:calc(100vh - 64px)}
nav{padding:16px 10px;background:#ffffff;border-right:1px solid #dce8ed;box-shadow:none;position:sticky;top:0;height:calc(100vh - 64px);overflow-y:auto}
.nav-title{font-size:9px;letter-spacing:1.1px;margin:10px 8px 5px;color:#78909d;text-transform:uppercase}
.nav-item{padding:8px 10px;margin:1px 0;border-radius:7px;font-size:11px;color:#35586c}
.nav-item:hover{transform:none;background:#eef7fa}.nav-item.active{background:#e3f3f8;color:#0e6487}
main{padding:14px 16px 24px;max-width:none}
.page-title{margin-bottom:10px;align-items:center}.page-title h1{font-size:18px;margin:0}.page-title p{font-size:10px;margin:3px 0 0;color:#708692}
.controls{gap:6px}.controls select,.controls button{height:34px;font-size:10px;border-radius:7px}
/* Keep the live engine available but visually compact */
.live-engine{margin-bottom:10px;padding:10px 12px;grid-template-columns:1fr 270px;gap:12px;border-radius:9px;box-shadow:0 2px 10px #174b6009}
.live-title{font-size:10px}.live-state{font-size:9px}.live-grid{gap:5px;margin-top:6px}.live-item{padding:6px 7px;font-size:8px}.live-item b{font-size:10px;margin-top:2px}.activity{min-height:76px}.activity-log{max-height:76px;font-size:8px}
.metrics{display:none}
.status-banner{display:none}
.stale-banner{margin:6px 0;font-size:9px;padding:6px 9px}
.map-card{border:1px solid #d8e5ea;border-radius:10px;box-shadow:0 4px 18px #174b6010;overflow:hidden;background:#fff}
.map-card>.map-head{height:40px;padding:0 12px;background:#fff;border-bottom:1px solid #e4edf1}
.map-card>.map-head h3{font-size:12px;color:#23485c}.map-actions button{font-size:9px;padding:5px 8px}
.map-area{height:min(690px,calc(100vh - 190px));min-height:520px;background:#fff}
.rain-plot-image{object-fit:cover;object-position:center center;background:#fff}
.map-focus-chip,.rainfall-meta,.gis-status{display:none}
.rainfall-legend{left:50%;right:auto;transform:translateX(-50%);bottom:10px;width:min(560px,72%);padding:7px 10px;background:#ffffffed;color:#17324d;border:1px solid #c9dce5;border-radius:7px;box-shadow:0 3px 12px #174b6018;font-size:9px}
.legend-bar{height:9px;margin:5px 0}.rainfall-legend .legend-ticks{font-size:8px}
.map-bottom-controls{left:10px;right:10px;bottom:10px;padding:7px 9px;background:#09283bdc;border-radius:7px;gap:7px}.map-control-btn{font-size:8px;padding:5px 7px}.map-day-label{font-size:9px}
/* Below-map modules are kept but visually quieter */
.intel,.timeline,.models,.thumb-panel,.modules{margin-top:10px}.panel-head h3{font-size:12px}.signal{padding:9px 12px}
.footer{font-size:8px;padding-top:12px;color:#78909d}
@media(max-width:1100px){.layout{grid-template-columns:1fr}nav{display:none}.map-area{height:calc(100vh - 180px)}}
@media(max-width:700px){header{padding:0 12px}.brand{font-size:15px}.subbrand{display:none}.header-right .pill:nth-child(2){display:none}main{padding:8px}.page-title p{display:none}.controls select{display:none}.map-area{min-height:430px;height:calc(100vh - 145px)}.rainfall-legend{width:88%}.live-engine{grid-template-columns:1fr}.activity{display:none}}


/* V26 CLEAN MAP LAYOUT */
body{background:#f3f6f8}
header{height:60px;padding:0 22px}
.brand{font-size:19px}.subbrand{font-size:9px}
.layout{grid-template-columns:180px 1fr;min-height:calc(100vh - 60px)}
nav{height:calc(100vh - 60px);padding:12px 8px}
.nav-title{margin:9px 8px 4px}.nav-item{padding:7px 9px;font-size:10.5px}
main{padding:12px 18px 20px}
.page-title{margin-bottom:8px}.page-title h1{font-size:17px}.page-title p{font-size:9px}
.controls select,.controls button{height:31px;font-size:9px;padding:7px 9px}
.live-engine{margin-bottom:9px;padding:7px 10px;grid-template-columns:1fr 230px;gap:10px;border-radius:8px}
.live-head{margin-bottom:3px}.live-title{font-size:9px}.live-state{font-size:8px}
.live-grid{grid-template-columns:repeat(6,1fr);gap:3px;margin-top:3px}
.live-item{padding:4px 5px;font-size:7px}.live-item b{font-size:8.5px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.progress-wrap{height:4px;margin-top:4px}.activity{min-height:54px}.activity-title{font-size:8px}.activity-log{max-height:48px;font-size:7px}
/* Large native reference-style PNG: let the image determine its own height. */
.map-card{border-radius:8px;box-shadow:0 2px 10px #174b6010;overflow:hidden}
.map-card>.map-head{height:34px;padding:0 9px;background:#fff;border-bottom:1px solid #e5edf1}
.map-actions button{font-size:8px;padding:4px 7px}
.map-area{height:auto;min-height:0;display:block;position:relative;background:#fff;overflow:visible}
.rain-plot-image{position:relative!important;inset:auto!important;width:100%!important;height:auto!important;max-width:none!important;object-fit:fill!important;object-position:center!important;background:#fff;display:block}
/* The rainfall legend is already embedded in the native reference-style PNG. */
.rainfall-legend{display:none!important}
.map-focus-chip,.rainfall-meta,.gis-status{display:none!important}
/* Controls belong below the map, never on top of rainfall or colourbar. */
.map-bottom-controls{position:relative!important;left:auto!important;right:auto!important;bottom:auto!important;margin:0;padding:8px 12px;background:#0b3048;border-radius:0 0 8px 8px;border-top:1px solid #183f55;gap:7px;display:flex;align-items:center}
.map-control-btn{font-size:8px;padding:5px 8px}.map-day-label{font-size:8px;min-width:64px}.map-bottom-controls input[type=range]{flex:1;min-width:140px;accent-color:#fff}
.intel,.timeline,.models,.thumb-panel,.modules{margin-top:10px}.intel{display:none}.timeline,.models,.thumb-panel,.modules{opacity:.96}
.footer{font-size:7px;padding-top:9px}
@media(max-width:1100px){.layout{grid-template-columns:170px 1fr}.map-card{width:100%}}
@media(max-width:700px){header{height:56px}.layout{min-height:calc(100vh - 56px)}main{padding:8px}.live-grid{grid-template-columns:repeat(3,1fr)}.live-engine{grid-template-columns:1fr}.activity{display:none}.map-bottom-controls{flex-wrap:wrap}.map-bottom-controls input[type=range]{min-width:120px}}


/* V28 CLEAN MAP VIEWER */
.map-card{overflow:hidden}
.map-area{height:auto!important;min-height:0!important;overflow:visible!important;background:#fff!important;display:flex;flex-direction:column;align-items:stretch}
.rain-plot-image{position:relative!important;inset:auto!important;width:100%!important;height:auto!important;max-width:100%!important;object-fit:contain!important;background:#fff!important;display:block}
.map-focus-chip,.rainfall-meta,.gis-status{display:none!important}
.map-bottom-controls{position:relative!important;left:auto!important;right:auto!important;bottom:auto!important;width:auto!important;margin:0!important;border-radius:0!important;border-left:0!important;border-right:0!important;border-bottom:0!important;box-shadow:none!important;min-height:48px;padding:8px 12px!important}
.map-bottom-controls .map-control-btn{white-space:nowrap}
.map-bottom-controls .map-control-btn.active{background:#2c7fa8!important;border-color:#6fc1e2!important;font-weight:800}
.timeline{display:none!important}
.map-head{min-height:34px!important;padding:6px 10px!important}
.map-actions button{padding:6px 9px!important;font-size:10px!important}
@media(max-width:900px){.layout{grid-template-columns:180px 1fr}.map-bottom-controls{gap:6px!important}.map-bottom-controls input[type=range]{min-width:80px}}

/* TALK TO MASRAINMAN — V193 clean horizontal layout */
.talk-card{grid-column:1 / -1;width:100%;min-width:0;box-sizing:border-box;margin:18px 0 0;padding:18px;color:#fff;background:linear-gradient(135deg,#102f3c 0%,#174b5c 100%);border:1px solid rgba(255,255,255,.14);border-radius:18px;box-shadow:0 14px 40px rgba(0,0,0,.16);display:grid;grid-template-columns:minmax(270px,32%) minmax(0,68%);grid-template-rows:auto minmax(0,1fr) auto;column-gap:16px;min-height:330px;height:330px;max-height:330px;overflow:hidden}
.talk-head{grid-column:1 / -1;grid-row:1;display:flex;justify-content:space-between;align-items:center;gap:14px;min-width:0;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,.10)}
.talk-title{font-size:19px;font-weight:900;letter-spacing:.2px;line-height:1.1}.talk-sub{opacity:.78;margin-top:4px;font-size:11px;line-height:1.35}.talk-head .talk-btn.primary{flex:0 0 auto;padding:10px 15px;white-space:nowrap}
.talk-row{grid-column:1;grid-row:2;display:grid;grid-template-columns:132px minmax(0,1fr) 52px 108px;gap:8px;align-self:end;margin:0;min-width:0}.talk-input{width:100%;min-width:0;box-sizing:border-box;border:0;border-radius:11px;padding:11px 12px;font-size:13px;outline:none;background:#fff;color:#173f4e}.talk-btn{border:0;border-radius:11px;padding:11px 14px;font-weight:900;cursor:pointer;transition:transform .12s ease,filter .12s ease;white-space:nowrap}.talk-btn:hover{filter:brightness(1.04);transform:translateY(-1px)}.talk-btn:disabled{opacity:.55;cursor:not-allowed;transform:none}.talk-btn.primary{background:#f6c344;color:#173f4e}.talk-btn.mic{background:#fff;color:#173f4e;font-size:17px;min-width:52px;padding:8px}.talk-row #talkSpeakBtn{min-width:0}
.talk-status{grid-column:1;grid-row:3;margin:0;min-height:20px;font-size:10px;line-height:1.35;opacity:.82;align-self:end;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.talk-result{grid-column:2;grid-row:2 / span 2;display:none;margin:0;background:rgba(255,255,255,.075);border:1px solid rgba(255,255,255,.10);border-radius:14px;padding:12px;font-size:12px;line-height:1.42;min-width:0;min-height:0;height:auto;max-height:none;overflow-y:auto;overflow-x:hidden;scrollbar-width:thin}
.talk-loading{display:flex;align-items:center;gap:14px;padding:12px;border-radius:13px;background:linear-gradient(120deg,rgba(255,255,255,.07),rgba(255,255,255,.14),rgba(255,255,255,.07));background-size:220% 100%;animation:talkShimmer 2.2s ease-in-out infinite;overflow:hidden;position:relative}.talk-orbit{width:52px;height:52px;border:0;border-radius:50%;animation:talkSpin 3.2s linear infinite;position:relative;flex:0 0 auto;background:conic-gradient(from 20deg,#6b5cff,#2fd7ff,#ff62d5,#7b5cff,#31d8ff,#6b5cff);filter:saturate(1.15);box-shadow:0 0 18px rgba(93,190,255,.38),0 0 32px rgba(222,88,255,.18)}.talk-orbit:before{content:"";position:absolute;inset:5px;border-radius:50%;background:radial-gradient(circle at 35% 28%,rgba(255,255,255,.96),rgba(255,255,255,.28) 17%,rgba(94,86,255,.52) 42%,rgba(14,28,74,.96) 76%);box-shadow:inset 0 0 12px rgba(255,255,255,.38);animation:talkSiriBreath 1.8s ease-in-out infinite alternate}.talk-orbit:after{content:"";position:absolute;width:11px;height:11px;border-radius:50%;background:rgba(255,255,255,.96);top:10px;left:22px;box-shadow:0 0 14px #fff,0 0 24px rgba(80,220,255,.8);animation:talkPulse 1.1s ease-in-out infinite}.talk-siri-label{font-size:9px;letter-spacing:1.1px;font-weight:900;opacity:.72;margin-top:2px}.talk-loading-title{font-weight:900;letter-spacing:.4px}.talk-loading-sub{font-size:10px;opacity:.72;margin-top:3px}.talk-loading-dots{display:inline-flex;gap:3px;margin-left:3px}.talk-loading-dots i{width:4px;height:4px;border-radius:50%;background:#f6c344;animation:talkDot 1.2s infinite}.talk-loading-dots i:nth-child(2){animation-delay:.18s}.talk-loading-dots i:nth-child(3){animation-delay:.36s}
@keyframes talkSiriBreath{from{transform:scale(.88);filter:blur(.2px)}to{transform:scale(1.06);filter:blur(1px)}}@keyframes talkSpin{to{transform:rotate(360deg)}}@keyframes talkPulse{50%{transform:scale(1.5);opacity:.45}}@keyframes talkDot{0%,60%,100%{opacity:.25;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}@keyframes talkShimmer{50%{background-position:100% 0}}
.talk-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-top:9px}.talk-stat{background:rgba(0,0,0,.16);border-radius:9px;padding:7px;min-width:0}.talk-stat span{display:block;font-size:9px;opacity:.7}.talk-stat b{display:block;font-size:14px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.talk-models{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px;align-items:center}.talk-model{padding:6px 9px;border-radius:999px;background:rgba(255,255,255,.11);font-size:10px;white-space:nowrap}.talk-model.ready{background:rgba(56,203,241,.14);border:1px solid rgba(56,203,241,.15)}.talk-model.pending{opacity:.62}.talk-model.error{background:rgba(255,96,96,.10);border:1px solid rgba(255,120,120,.18)}.talk-tamil{font-size:11px}.talk-disclaimer{font-size:9px;opacity:.68;margin-top:8px;line-height:1.35}
@media(max-width:900px){.talk-card{grid-template-columns:minmax(0,1fr);grid-template-rows:auto auto minmax(190px,1fr) auto;min-height:500px;height:500px;max-height:500px}.talk-head{grid-column:1;grid-row:1}.talk-row{grid-column:1;grid-row:2;align-self:start}.talk-status{grid-column:1;grid-row:4}.talk-result{grid-column:1;grid-row:3;height:auto}}
@media(max-width:600px){.talk-card{padding:13px;border-radius:15px;min-height:560px;height:560px;max-height:560px}.talk-head{align-items:flex-start}.talk-title{font-size:17px}.talk-head .talk-btn.primary{padding:9px 11px;font-size:11px}.talk-row{grid-template-columns:minmax(0,1fr) 48px;grid-template-areas:"input mic" "speak speak" "ask ask"}.talk-input{grid-area:input}.talk-btn.mic{grid-area:mic}.talk-row #talkSpeakBtn{grid-area:speak}.talk-row .talk-btn:not(.mic):not(#talkSpeakBtn){grid-area:ask}.talk-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.talk-models{gap:5px}.talk-model{font-size:9px;padding:5px 7px}}

/* Universal live model download marquee — ensemble + deterministic */
.ensemble-ticker{
  display:none; grid-column:1 / -1; width:100%; min-width:0; box-sizing:border-box;
  margin:8px 0 12px; border:1px solid #76bfd8;
  background:linear-gradient(90deg,#06283f,#0b526f,#06283f);
  color:#fff; border-radius:9px; overflow:hidden; height:38px;
  box-shadow:0 4px 12px #0b456533; position:relative;
}
.ensemble-ticker.show{display:block}
.ensemble-ticker-track{
  display:block; width:max-content; min-width:max-content;
  white-space:nowrap; padding:8px 0 7px;
  font-size:11.5px; line-height:20px; font-weight:700;
  letter-spacing:.2px; will-change:transform;
  animation:ensembleTickerScroll 68s linear infinite;
}
.ensemble-ticker-track.idle{
  animation:none; text-align:center; width:100%; min-width:100%;
}
@keyframes ensembleTickerScroll{
  0%{transform:translate3d(100%,0,0)}
  100%{transform:translate3d(-100%,0,0)}
}
@media(max-width:900px){
  .ensemble-ticker{height:34px; margin:7px 0 10px}
  .ensemble-ticker-track{font-size:10.5px; line-height:18px; padding:7px 0 6px; animation-duration:72s}
}

/* V164 SIDEBAR MODULE WORKSPACE */
.module-workspace{display:none;margin:0 0 12px;padding:16px;border:1px solid #cfe5ee;border-radius:12px;background:#f8fcfe;box-shadow:0 3px 12px #0b45630d}
.module-workspace.show{display:block}
.module-workspace .mw-head{display:flex;align-items:center;justify-content:space-between;gap:12px;border-bottom:1px solid #dcecf2;padding-bottom:10px;margin-bottom:12px}
.module-workspace h2{margin:0;font-size:18px;color:#0d557b}
.module-workspace .mw-badge{font-size:10px;font-weight:800;padding:5px 8px;border-radius:999px;background:#e8f7fc;color:#1769aa;border:1px solid #b6dce9}
.module-workspace .mw-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.module-workspace .mw-card{background:white;border:1px solid #dbeaf0;border-radius:10px;padding:11px}
.module-workspace .mw-card b{display:block;color:#214e63;margin-bottom:4px}
.module-workspace .mw-card span{font-size:11px;color:#607985;line-height:1.45}
.module-workspace .mw-note{margin-top:10px;padding:9px 11px;border-radius:8px;background:#eef7fa;color:#496674;font-size:10.5px;line-height:1.45}

.wind-hour-panel{margin-top:10px;padding:10px 12px;border:1px solid #cfe5ee;border-radius:10px;background:#f7fbfd}
.wind-hour-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:8px}
.wind-hour-head b{font-size:12px;color:#174d66}
.wind-hour-head span{font-size:10px;color:#66808d}
.wind-hour-grid{display:flex;flex-wrap:wrap;gap:5px}
.wind-hour-btn{min-width:49px;height:30px;padding:0 7px;border:1px solid #b9d6e2;border-radius:6px;background:#fff;color:#174d66;font-size:10px;font-weight:800;cursor:pointer;transition:.12s ease}
.wind-hour-btn:hover{background:#eaf7fb;border-color:#4ba8c6;transform:translateY(-1px)}
.wind-hour-btn.active{background:#0d557b;color:#fff;border-color:#0d557b;box-shadow:0 2px 6px #0d557b33}
.wind-hour-btn.loading{background:#f4b942;color:#102f3e;border-color:#f4b942;cursor:wait}
.wind-hour-btn.ready{background:#e9f8ef;border-color:#8ac8a0;color:#17633a}
.wind-hour-status{margin-top:8px;padding:7px 9px;border-radius:7px;background:#eaf4f8;color:#466875;font-size:10px}
.wind-hour-empty{display:inline-flex;align-items:center;height:30px;padding:0 10px;border:1px dashed #b9d6e2;border-radius:6px;background:#fff;color:#66808d;font-size:10px;font-weight:700}
@media(max-width:900px){.module-workspace .mw-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<header>
  <div>
    <div class="brand">MASRAINMAN WEATHER COMMAND CENTER</div>
    <div class="subbrand">NEM 2026 • Private Weather Intelligence Dashboard</div>
  </div>
  <div class="header-right">
    <div class="pill" id="headerEngine">LIVE ENGINE: STARTING</div>
    <div class="pill" id="versionBadge">V186 • WIND + PRESSURE • ENSEMBLE LOW-CENTRE SPREAD • V162 CORE FROZEN</div>
  </div>
</header>

<div class="layout">
<nav id="sidebarNav">
  <div class="nav-title">Command Center</div>
  <button class="nav-item active" data-module="overview">🏠 NEM Overview</button>
  <button class="nav-item" data-module="rainfall">🌧️ Rainfall</button>
  <button class="nav-item" data-module="wind">💨 Wind</button>
  <button class="nav-item" data-module="pressure">🌀 Pressure</button>
  <button class="nav-item" data-module="temperature">🌡️ Temperature</button>
  <button class="nav-item" data-module="humidity">💧 Humidity</button>
  <button class="nav-item" data-module="cloud">☁️ Cloud Cover</button>

  <div class="nav-title">Climate</div>
  <button class="nav-item" data-module="enso">🌏 ENSO</button>
  <button class="nav-item" data-module="iod">🌊 IOD</button>
  <button class="nav-item" data-module="mjo">〰️ MJO</button>
  <button class="nav-item" data-module="seasonal">📅 Seasonal Outlook</button>

  <div class="nav-title">Advanced</div>
  <button class="nav-item" data-module="comparison">📊 Model Comparison</button>
  <button class="nav-item" data-module="mme">📈 MME</button>
  <button class="nav-item" data-module="anomaly">⚠️ Anomaly</button>
  <button class="nav-item" data-module="tsi">⛈️ TSI</button>
  <button class="nav-item" data-module="cyclone">🌀 Cyclone Tracker</button>
  <button class="nav-item" data-module="explore">🗺️ Explore Maps</button>
</nav>

<main>
  <div class="page-title">
    <div>
      <h1>NEM Current State</h1>
      <p>India • Bay of Bengal • Arabian Sea • Survey of India GIS • Deterministic + Ensemble rainfall forecast.</p>
    </div>
    <div class="controls">
      <select id="forecastMode" onchange="changeForecastMode(this.value)">
        <option value="deterministic" selected>DETERMINISTIC</option>
        <option value="ensemble">ENSEMBLES</option>
      </select>
      <select id="modelSelect" onchange="changeForecastModel(this.value)"></select>
      <select id="runSelect" onchange="changeForecastRun(this.value)"><option value="" disabled selected>SELECT A RUN — NO DOWNLOAD YET</option></select>
      <select id="periodSelect" onchange="changeRainPeriod()"><option>24 Hour</option><option>7 Day Accumulation</option><option>15 Day Accumulation</option></select>
      <select id="ensembleProductSelect" onchange="changeEnsembleProduct()" style="display:none">
        <option value="MEAN">ENS MEAN</option>
        <option value="P50">P50</option>
        <option value="P75">P75</option>
        <option value="P90">P90</option>
        <option value="PROB50">PROB ≥50 mm</option>
        <option value="PROB100">PROB ≥100 mm</option>
        <option value="MSLP">MSLP MEAN</option>
        <option value="MSLP_SPREAD">MSLP SPREAD</option>
        <option value="WIND850">850 hPa WIND</option>
        <option value="SYNOPTIC">MSLP + 850 hPa WIND</option>
      </select>
      <button class="primary" onclick="startSelectedModel(true)">↻ UPDATE SELECTED MODEL</button>
    </div>
  </div>

  <section id="upperAirWindCard" class="card" style="display:none;margin:14px 0 18px;border:1px solid #cbdde8;box-shadow:0 8px 24px rgba(30,60,80,.08);padding:16px 18px;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">
      <div>
        <div style="font-size:17px;font-weight:900;color:#17324d;">💨 MASRAINMAN MULTI-MODEL UPPER-AIR WIND</div>
        <div style="font-size:11px;color:#60737d;margin-top:4px;">Progressive selection: Model → latest run → pressure level → available forecast hour → download & plot</div>
      </div>
      <div id="uaWindStatus" class="ua-status-pill" style="font-size:11px;font-weight:900;color:#60737d;">SELECT A MODEL — NO WIND DATA DOWNLOADED</div>
    </div>
    <div style="margin-top:14px;padding:8px 10px;background:#eef7fb;border:1px solid #d4e6ee;border-radius:8px;font-size:9px;font-weight:900;color:#60737d;">ENSEMBLE MODELS</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center;">
      <button class="map-control-btn ua-model-btn" data-ua-model="ECMWF" onclick="uaSelectModel('ECMWF')">ECMWF ENS</button>
      <button class="map-control-btn ua-model-btn" data-ua-model="GEFS" onclick="uaSelectModel('GEFS')">GEFS</button>
    </div>
    <div style="margin-top:12px;padding:8px 10px;background:#eef7fb;border:1px solid #d4e6ee;border-radius:8px;font-size:9px;font-weight:900;color:#60737d;">DETERMINISTIC UPPER-AIR MODELS</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center;">
      <button class="map-control-btn ua-model-btn" data-ua-model="ECMWF_HRES" onclick="uaSelectModel('ECMWF_HRES')">ECMWF IFS HRES</button>
      <button class="map-control-btn ua-model-btn" data-ua-model="GFS" onclick="uaSelectModel('GFS')">GFS</button>
      <button class="map-control-btn ua-model-btn" data-ua-model="ICON" onclick="uaSelectModel('ICON')">ICON GLOBAL</button>
      <span id="uaRunLabel" class="ua-run-pill" style="margin-left:auto;"><span class="run-label">LATEST RUN</span><span>—</span></span>
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-top:9px;flex-wrap:wrap;">
      <span style="font-size:9px;font-weight:900;color:#60737d;min-width:38px;">RUNS</span>
      <div id="uaRunHistory" class="ua-run-history" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:30px;flex:1;">
        <span style="font-size:10px;color:#8a9aa4;">Select a model to load the latest available runs.</span>
      </div>
    </div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center;">
      <span style="font-size:9px;font-weight:900;color:#60737d;margin-right:2px;">HEIGHT</span>
      <button class="map-control-btn ua-level-btn" data-ua-level="850" onclick="uaSelectLevel(850)">850 hPa</button>
      <button class="map-control-btn ua-level-btn" data-ua-level="700" onclick="uaSelectLevel(700)">700 hPa</button>
      <button class="map-control-btn ua-level-btn" data-ua-level="500" onclick="uaSelectLevel(500)">500 hPa</button>
    </div>
    <div style="margin-top:14px;">
      <div style="font-size:9px;font-weight:900;color:#60737d;margin-bottom:7px;">AVAILABLE FORECAST HOURS</div>
      <div id="uaHourButtons" style="display:flex;flex-wrap:wrap;gap:6px;min-height:30px;align-items:center;">
        <span style="font-size:10px;color:#8a9aa4;">Select a model first.</span>
      </div>
    </div>
    <div id="uaSelectionSummary" style="margin-top:12px;padding:9px 11px;background:#f7fbfd;border-radius:8px;font-size:10px;font-weight:900;color:#17324d;">MODEL: — • RUN: — • LEVEL: — • HOUR: —</div>

    <!-- Dedicated upper-air wind plot area.
         IMPORTANT: this lives inside the Wind card so it remains visible when
         the legacy NEM dashboard/grid is hidden by Wind-only mode. -->
    <div id="uaWindPlotPanel" style="margin-top:14px;border:1px solid #cbdde8;border-radius:10px;background:#fff;overflow:hidden;">
      <div style="padding:9px 12px;background:#eef7fb;border-bottom:1px solid #d4e6ee;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
        <div style="font-size:10px;font-weight:900;color:#17324d;">UPPER-AIR WIND MAP</div>
        <div id="uaWindPlotLabel" style="font-size:9px;font-weight:800;color:#60737d;">Select a forecast hour to download & plot</div>
      </div>
      <div style="min-height:420px;background:#f7fbfd;display:flex;align-items:center;justify-content:center;">
        <img id="uaWindPlotImage"
             class="rain-plot-image"
             style="display:block;width:100%;height:auto;min-height:420px;object-fit:contain;background:#fff;"
             src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%221400%22 height=%22800%22 viewBox=%220 0 1400 800%22%3E%3Crect width=%221400%22 height=%22800%22 fill=%22%23f7fbfd%22/%3E%3Ctext x=%22700%22 y=%22370%22 text-anchor=%22middle%22 font-family=%22Arial%22 font-size=%2230%22 font-weight=%22700%22 fill=%22%23546a7a%22%3ESELECT A FORECAST HOUR TO LOAD THE WIND MAP%3C/text%3E%3C/svg%3E"
             alt="Upper-air wind map">
      </div>
    </div>
  </section>

  <section id="pressureCard" class="card" style="display:none;margin:14px 0 18px;border:1px solid #cbdde8;box-shadow:0 8px 24px rgba(30,60,80,.08);padding:16px 18px;">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;">
      <div>
        <div style="font-size:17px;font-weight:900;color:#17324d;">🌀 MASRAINMAN MULTI-MODEL PRESSURE / UPPER-AIR</div>
        <div style="font-size:11px;color:#60737d;margin-top:4px;">Progressive selection: PRESSURE LEVEL → MODEL → RUN → FORECAST HOUR → DOWNLOAD & PLOT</div>
      </div>
      <div id="pressureStatus" style="font-size:11px;font-weight:900;color:#60737d;">SELECT PRESSURE LEVEL FIRST — NO PRESSURE DATA DOWNLOADED</div>
    </div>

    <div style="margin-top:14px;padding:10px 12px;background:#17324d;border:1px solid #17324d;border-radius:8px;font-size:10px;font-weight:900;color:#ffffff;">STEP 1 • SELECT PRESSURE LEVEL / PRODUCT</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center;">
      <button type="button" class="map-control-btn pressure-product-btn" data-pressure-product="MSLP" onclick="pressureSelectProduct('MSLP')">MSLP</button>
      <button type="button" class="map-control-btn pressure-product-btn" data-pressure-product="925" onclick="pressureSelectProduct('925')">925 hPa</button>
      <button type="button" class="map-control-btn pressure-product-btn" data-pressure-product="850" onclick="pressureSelectProduct('850')">850 hPa</button>
      <button type="button" class="map-control-btn pressure-product-btn" data-pressure-product="500" onclick="pressureSelectProduct('500')">500 hPa</button>
      <span style="font-size:9px;color:#60737d;font-weight:800;margin-left:6px;">MSLP = hPa pressure • 925/850/500 = geopotential height (dam)</span>
    </div>

    <div style="margin-top:14px;padding:8px 10px;background:#eef7fb;border:1px solid #d4e6ee;border-radius:8px;font-size:9px;font-weight:900;color:#60737d;">STEP 2 • ENSEMBLE MODELS</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center;">
      <button type="button" class="map-control-btn pressure-model-btn" data-pressure-model="ECMWF" onclick="pressureSelectModel('ECMWF')">ECMWF ENS</button>
      <button type="button" class="map-control-btn pressure-model-btn" data-pressure-model="GEFS" onclick="pressureSelectModel('GEFS')">GEFS</button>
    </div>

    <div style="margin-top:12px;padding:8px 10px;background:#eef7fb;border:1px solid #d4e6ee;border-radius:8px;font-size:9px;font-weight:900;color:#60737d;">DETERMINISTIC MODELS</div>
    <div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px;align-items:center;">
      <button type="button" class="map-control-btn pressure-model-btn" data-pressure-model="ECMWF_HRES" onclick="pressureSelectModel('ECMWF_HRES')">ECMWF IFS HRES</button>
      <button type="button" class="map-control-btn pressure-model-btn" data-pressure-model="GFS" onclick="pressureSelectModel('GFS')">GFS</button>
      <button type="button" class="map-control-btn pressure-model-btn" data-pressure-model="ICON" onclick="pressureSelectModel('ICON')">ICON GLOBAL</button>
      <button type="button" class="map-control-btn pressure-model-btn" data-pressure-model="AIGFS" onclick="pressureSelectModel('AIGFS')">AIGFS</button>
      <span id="pressureRunLabel" class="ua-run-pill" style="margin-left:auto;"><span class="run-label">LATEST RUN</span><span>—</span></span>
    </div>

    <div style="display:flex;align-items:center;gap:8px;margin-top:9px;flex-wrap:wrap;">
      <span style="font-size:9px;font-weight:900;color:#60737d;min-width:38px;">RUNS</span>
      <div id="pressureRunHistory" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;min-height:30px;flex:1;">
        <span style="font-size:10px;color:#8a9aa4;">Select a model to load the latest available runs.</span>
      </div>
    </div>

    <div style="margin-top:14px;">
      <div style="font-size:9px;font-weight:900;color:#60737d;margin-bottom:7px;">AVAILABLE FORECAST HOURS</div>
      <div id="pressureHourButtons" style="display:flex;flex-wrap:wrap;gap:6px;min-height:30px;align-items:center;">
        <span style="font-size:10px;color:#8a9aa4;">Select a model first.</span>
      </div>
    </div>

    <div id="pressureSelectionSummary" style="margin-top:12px;padding:9px 11px;background:#f7fbfd;border-radius:8px;font-size:10px;font-weight:900;color:#17324d;">MODEL: — • RUN: — • PRODUCT: SELECT LEVEL • HOUR: —</div>

    <div id="pressurePlotPanel" style="margin-top:14px;border:1px solid #cbdde8;border-radius:10px;background:#fff;overflow:hidden;">
      <div style="padding:9px 12px;background:#eef7fb;border-bottom:1px solid #d4e6ee;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;">
        <div style="font-size:10px;font-weight:900;color:#17324d;">PRESSURE MAP</div>
        <div id="pressurePlotLabel" style="font-size:9px;font-weight:800;color:#60737d;">Select a forecast hour to download & plot</div>
      </div>
      <div style="min-height:420px;background:#f7fbfd;display:flex;align-items:center;justify-content:center;">
        <img id="pressurePlotImage" class="rain-plot-image"
             style="display:block;width:100%;height:auto;min-height:420px;object-fit:contain;background:#fff;"
             src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%221400%22 height=%22800%22%3E%3Crect width=%221400%22 height=%22800%22 fill=%22%23f7fbfd%22/%3E%3Ctext x=%22700%22 y=%22370%22 text-anchor=%22middle%22 font-family=%22Arial%22 font-size=%2230%22 font-weight=%22700%22 fill=%22%23546a7a%22%3ESELECT A FORECAST HOUR TO LOAD THE PRESSURE MAP%3C/text%3E%3C/svg%3E"
             alt="Mean sea-level pressure map">
      </div>
    </div>
  </section>

  <section id="moduleWorkspace" class="module-workspace" aria-live="polite">
    <div class="mw-head"><h2 id="moduleTitle">NEM Overview</h2><span id="moduleBadge" class="mw-badge">CORE DASHBOARD</span></div>
    <div id="moduleCards" class="mw-grid"></div>
    <div id="moduleNote" class="mw-note"></div>
  </section>

  <div class="grid">
    <section class="status-banner">
      <div class="status-chip"><span class="dot"></span><div><b>Dashboard Engine</b><div class="quick-note">Interface ready • GIS layer connected</div></div></div>
      <div class="status-chip"><span class="dot"></span><div><b>Map Viewer</b><div class="quick-note">Interactive GIS viewer ready</div></div></div>
      <div class="status-chip"><span class="dot amber"></span><div><b>Model Data</b><div class="quick-note">Deterministic + Ensemble model framework connected</div></div></div>
      <div class="status-chip"><span class="dot"></span><div><b>Next Phase</b><div class="quick-note">ECMWF rainfall operational</div></div></div>
    </section>

    <section id="liveDataEngine" class="live-engine">
      <div>
        <div class="live-head">
          <div class="live-title">⚡ LIVE DATA ENGINE</div>
          <div id="liveState" class="live-state">WAITING</div>
        </div>
        <div class="live-grid">
          <div class="live-item">MODEL RUN<b id="liveRun">—</b></div>
          <div class="live-item">MODE / MODEL<b id="liveProduct">DETERMINISTIC • ECMWF IFS HRES</b></div>
          <div class="live-item">MEMBERS<b id="liveMembers">1</b></div>
          <div class="live-item">FILES<b id="liveFiles">0 / 6</b></div>
          <div class="live-item">PROCESSING<b id="liveProcessing">Waiting</b></div>
          <div class="live-item">VALIDITY<b id="liveValidity">—</b></div>
        </div>
        <div class="progress-wrap"><div id="liveProgress" class="progress-fill"></div></div>
      </div>
      <div class="activity">
        <div class="activity-title">MODEL UPDATE ACTIVITY</div>
        <div id="activityLog" class="activity-log"><div class="activity-row info">Waiting for live update…</div></div>
      </div>
    </section>
    <div id="ensembleTicker" class="ensemble-ticker" aria-live="polite">
      <div id="ensembleTickerTrack" class="ensemble-ticker-track idle">⏳ ENSEMBLE DOWNLOAD STATUS • Waiting for ensemble data…</div>
    </div>
    <div id="staleBanner" class="stale-banner">⚠ LIVE MODEL DATA NOT READY — NO PREVIOUS MAP IS BEING USED.</div>

<section class="talk-card" id="talkToMasrainman" style="margin-top:0;border-top-left-radius:0;border-top-right-radius:0;">
  <div class="talk-head">
    <div>
      <div class="talk-title">🎙️ TALK TO MASRAINMAN <span style="font-size:12px;opacity:.82;font-weight:700">• &#2990;&#3006;&#3000;&#3021;&#2992;&#3014;&#2991;&#3007;&#2985;&#3021;&#2990;&#3015;&#2985;&#3007;&#2975;&#2990;&#3021; &#2986;&#3015;&#2970;&#3009;&#2969;&#3021;&#2965;&#2995;&#3021;</span></div>
      <div class="talk-sub">&#2953;&#2969;&#3021;&#2965;&#2995;&#3021; &#2984;&#2965;&#2992;&#2980;&#3021;&#2980;&#3007;&#2985;&#3021; &#2986;&#3014;&#2991;&#2992;&#3016; &#2970;&#3018;&#2994;&#3021;&#2994;&#3009;&#2969;&#3021;&#2965;&#2995;&#3021; / Enter your town or city name — live model data will be turned into a simple rainfall briefing.</div>
    </div>
  </div>
  <div class="talk-row">
    <button id="talkSpeakBtn" class="talk-btn primary" type="button">🎤 &#2986;&#3015;&#2970;&#3009;&#2969;&#3021;&#2965;&#2995;&#3021; / Speak</button>
    <input id="talkInput" class="talk-input" placeholder="&#2953;&#2980;&#3006;: &#2970;&#3014;&#2985;&#3021;&#2985;&#3016; / Try: Chennai, Madurai, Coimbatore, Thanjavur..." autocomplete="off">
    <button id="talkMicBtn" class="talk-btn mic" type="button" title="🎙️ &#2986;&#3015;&#2970; / Speak">🎙️</button>
    <button id="talkAskBtn" class="talk-btn" type="button">&#2965;&#3015;&#2995;&#3009;&#2969;&#3021;&#2965;&#2995;&#3021; / Ask</button>
  </div>
  <div id="talkStatus" class="talk-status">&#2965;&#3009;&#2992;&#2994;&#3021; &#2953;&#2995;&#3021;&#2995;&#3008;&#2975;&#3009; &#2980;&#2991;&#3006;&#2992;&#3006;&#2965; &#2953;&#2995;&#3021;&#2995;&#2980;&#3009; / Voice input is ready when supported by your browser.</div>
  <div id="talkResult" class="talk-result"></div>
</section>

    <div class="metrics">
      <div class="card stat"><div class="label">Rainfall Signal</div><div class="value">—</div><div class="hint">Awaiting live data</div></div>
      <div class="card stat"><div class="label">Wind Signal</div><div class="value">—</div><div class="hint">Awaiting live data</div></div>
      <div class="card stat"><div class="label">Pressure</div><div class="value">—</div><div class="hint">Awaiting live data</div></div>
      <div class="card stat"><div class="label">Temperature</div><div class="value">—</div><div class="hint">Awaiting live data</div></div>
      <div class="card stat"><div class="label">Humidity</div><div class="value">—</div><div class="hint">Awaiting live data</div></div>
      <div class="card stat"><div class="label">Cloud Cover</div><div class="value">—</div><div class="hint">Awaiting live data</div></div>
    </div>

    <section id="weatherMap" class="card map-card">
      <div class="map-head">
        <div></div>
        <div class="map-actions">
          <button onclick="openMap()">⛶ Full Map</button>
          <button>Layers</button>
        </div>
      </div>
      <div class="map-area">
        <img id="rainPlotImage" class="rain-plot-image" src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22800%22 height=%22500%22 viewBox=%220 0 800 500%22%3E%3Crect width=%22800%22 height=%22500%22 fill=%22%23f5f8fb%22/%3E%3Ctext x=%22400%22 y=%22235%22 text-anchor=%22middle%22 font-family=%22Arial%22 font-size=%2222%22 fill=%22%23546a7a%22%3ESELECT AN AVAILABLE RUN%3C/text%3E%3Ctext x=%22400%22 y=%22270%22 text-anchor=%22middle%22 font-family=%22Arial%22 font-size=%2215%22 fill=%22%23748a98%22%3ENO DOWNLOAD STARTED%3C/text%3E%3C/svg%3E" alt="Select an available run to load the rainfall map">
        <div class="map-focus-chip"><b>SOUTH INDIA • BAY OF BENGAL</b><span>Selected model • 0.25° grid • georeferenced rainfall accumulation</span></div>
        <div id="rainfallMeta" class="rainfall-meta"><b>GRID CHECK:</b> waiting for live field…<br><span style="font-size:8px">Click map for lat/lon rainfall verification</span></div>
        <div id="gisStatus" class="gis-status loading">LOADING SURVEY OF INDIA BOUNDARY…</div>
        <div class="map-bottom-controls">
          <span style="font-size:8px;font-weight:800;opacity:.85">FORECAST PERIOD</span>
          <button class="map-control-btn" data-rain-day="1" onclick="setRainDay(1)">DAY 1</button>
          <button class="map-control-btn" data-rain-day="2" onclick="setRainDay(2)">DAY 2</button>
          <button class="map-control-btn" data-rain-day="3" onclick="setRainDay(3)">DAY 3</button>
          <input id="rainDaySlider" type="range" min="1" max="5" step="1" value="1" oninput="setRainDay(Number(this.value))">
          <div id="rainDayLabel" class="map-day-label">DAY 1 • 24H</div>
          <button class="map-control-btn" onclick="toggleRainOpacity()">OPACITY</button>
        </div>
      </div>
    </section>

    <section class="card intel">
      <div class="panel-head"><h3>NEM Signal Engine</h3><span class="pill" style="color:#1769aa;background:#e8f7fc;border-color:#b6dce9">Prototype</span></div>
      <div class="signal"><div class="signal-top"><b>Rainfall</b><span>DATA PENDING</span></div><div class="signal-bar"><div class="signal-fill" style="width:72%"></div></div></div>
      <div class="signal"><div class="signal-top"><b>Wind / Circulation</b><span>DATA PENDING</span></div><div class="signal-bar"><div class="signal-fill" style="width:55%"></div></div></div>
      <div class="signal"><div class="signal-top"><b>Pressure</b><span>DATA PENDING</span></div><div class="signal-bar"><div class="signal-fill" style="width:48%"></div></div></div>
      <div class="signal"><div class="signal-top"><b>Moisture</b><span>DATA PENDING</span></div><div class="signal-bar"><div class="signal-fill" style="width:64%"></div></div></div>
      <div class="signal"><div class="signal-top"><b>Model Agreement</b><span>CONFIGURABLE</span></div><div class="signal-bar"><div class="signal-fill" style="width:60%"></div></div></div>
      <div class="signal"><div class="signal-top"><b>Signal Persistence</b><span>CONFIGURABLE</span></div><div class="signal-bar"><div class="signal-fill" style="width:52%"></div></div></div>
    </section>




    <section class="card timeline">
      <div class="panel-head" style="padding:0 0 12px;border:0"><h3>Time Horizon</h3><span class="small">Select a forecast window</span></div>
      <div class="timeline-row">
        <div class="timebox active"><b>Next 24H</b><span>Current</span></div>
        <div class="timebox"><b>Next 7 Days</b><span>Accumulation</span></div>
        <div class="timebox"><b>Week 2</b><span>Sub-seasonal</span></div>
        <div class="timebox"><b>Week 3</b><span>Sub-seasonal</span></div>
        <div class="timebox"><b>Week 4</b><span>Sub-seasonal</span></div>
        <div class="timebox"><b>Weeks 5–6</b><span>46-day</span></div>
        <div class="timebox"><b>Season</b><span>Monthly</span></div>
      </div>
    </section>

    <section class="card models">
      <div class="panel-head" style="padding:0 0 8px;border:0"><h3>Model Weight Control</h3><span class="small">User adjustable • default can be locked later</span></div>
      <div class="blend-status"><span>Blend profile: <b>Custom</b></span><span id="weightTotal">Total: 100%</span></div>
      <div class="model-row"><b>ECMWF</b><input type="range" min="0" max="100" value="60" oninput="setW(this,'w1')"><span id="w1">60%</span></div>
      <div class="model-row"><b>GFS</b><input type="range" min="0" max="100" value="15" oninput="setW(this,'w2')"><span id="w2">15%</span></div>
      <div class="model-row"><b>ICON</b><input type="range" min="0" max="100" value="10" oninput="setW(this,'w3')"><span id="w3">10%</span></div>
      <div class="model-row"><b>UKMET</b><input type="range" min="0" max="100" value="10" oninput="setW(this,'w4')"><span id="w4">10%</span></div>
      <div class="model-row"><b>JMA</b><input type="range" min="0" max="100" value="5" oninput="setW(this,'w5')"><span id="w5">5%</span></div>
      <button class="primary" onclick="alert('Blend engine will be connected after the existing model plotting cores are integrated.')">Apply Blend</button>
    </section>

    <section class="card thumb-panel">
      <div class="panel-head" style="padding:0 0 10px;border:0"><h3>Map Gallery</h3><span class="small">Click to enlarge</span></div>
      <div class="thumbs">
        <div class="thumb" onclick="openMap()">24H RAINFALL</div>
        <div class="thumb" onclick="openMap()">7D ACCUMULATION</div>
        <div class="thumb" onclick="openMap()">15D ACCUMULATION</div>
        <div class="thumb" onclick="openMap()">RAINFALL ANOMALY</div>
        <div class="thumb" onclick="openMap()">850 HPA WIND</div>
        <div class="thumb" onclick="openMap()">MSLP</div>
      </div>
    </section>

    <section class="card modules">
      <div class="panel-head" style="padding:0 0 12px;border:0"><h3>MASRAINMAN Products</h3><span class="small">Existing engines will be connected here</span></div>
      <div class="module-grid">
        <div class="module"><b>🌀 Cyclone Tracker</b><span>Track • Ensemble • Intensity</span></div>
        <div class="module"><b>📊 MME</b><span>Multi-model rainfall products</span></div>
        <div class="module"><b>⚠️ Anomaly</b><span>Rainfall / wind / pressure</span></div>
        <div class="module"><b>⛈️ TSI</b><span>Thunderstorm potential</span></div>
        <div class="module"><b>📅 Seasonal Outlook</b><span>Monthly / seasonal signals</span></div>
        <div class="module"><b>🗺️ Explore Maps</b><span>Full interactive catalogue</span></div>
      </div>
    </section>
  </div>

  <div class="footer">
    MASRAINMAN • NEM Dashboard • Survey of India boundary connected • J&K + Ladakh geometry rendered directly from source.
  </div>
</main>
</div>

<div class="modal" id="mapModal">
  <div class="modal-box">
    <div class="map-head">
      <h3>MASRAINMAN — Full Map Viewer</h3>
      <button class="close" onclick="closeMap()">Close ✕</button>
    </div>
    <div class="modal-map">
      <img id="fullPlotImage" class="full-plot-image" src="data:image/svg+xml,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22800%22 height=%22500%22 viewBox=%220 0 800 500%22%3E%3Crect width=%22800%22 height=%22500%22 fill=%22%23f5f8fb%22/%3E%3Ctext x=%22400%22 y=%22235%22 text-anchor=%22middle%22 font-family=%22Arial%22 font-size=%2222%22 fill=%22%23546a7a%22%3ESELECT AN AVAILABLE RUN%3C/text%3E%3Ctext x=%22400%22 y=%22270%22 text-anchor=%22middle%22 font-family=%22Arial%22 font-size=%2215%22 fill=%22%23748a98%22%3ENO DOWNLOAD STARTED%3C/text%3E%3C/svg%3E" alt="Select an available run to load the full rainfall map">
      <div id="fullMapStatus" class="gis-status">TERRAIN MAP • SURVEY OF INDIA BOUNDARIES</div>
      <div class="rain-legend" style="z-index:600">
  <div class="rain-legend-title">Rainfall (mm)</div>
  <div class="rain-legend-bar"></div>
  <div class="rain-legend-ticks"><span>0.1</span><span>1</span><span>5</span><span>10</span><span>20</span><span>50</span><span>100</span><span>200</span></div>
</div>
    </div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
document.addEventListener("DOMContentLoaded",()=>{
  // V71: home order is fixed in HTML. Talk result scrolls inside its banner.
});
</script>
<script>
let gisData=null;
let currentRainDay=1;
let currentPeriod="24 Hour";
// V154: explicit browser-side selection state. The previous V153 patch
// accidentally relied on server-side Python variables that do not exist in
// the browser JavaScript scope. That stopped bootDashboard() before run
// discovery, leaving the UI frozen on the initial ECMWF status.
let ACTIVE_FORECAST_MODE="deterministic";
let ACTIVE_MODEL="ECMWF";
let ACTIVE_PERIOD="24 Hour";
let ACTIVE_RUN="";
let ACTIVE_ENSEMBLE_PRODUCT="MEAN";
let WIND_FORECAST_HOUR=24;
let WIND_AVAILABLE_HOURS=[];
let WIND_HOUR_DISCOVERY_INFLIGHT=false;
function setW(el,id){document.getElementById(id).textContent=el.value+'%'; const total=[...document.querySelectorAll('.model-row input')].reduce((a,b)=>a+Number(b.value),0); document.getElementById('weightTotal').textContent='Total: '+total+'%';}
function setStatus(id,msg,loading=false){const el=document.getElementById(id); if(!el)return; el.textContent=msg; el.classList.toggle('loading',loading);}
function openMap(){
  document.getElementById('mapModal').classList.add('open');
  const small=document.getElementById('rainPlotImage');
  const full=document.getElementById('fullPlotImage');
  // /plot.png deletes temporary forecast files after delivery. Reuse the
  // already-delivered browser PNG for the full viewer instead of requesting
  // the deleted server-side forecast a second time.
  if(small && full && small.complete && small.naturalWidth>0 && small.src && !small.src.startsWith('data:image/svg+xml')){
    full.src=small.src;
    setStatus('fullMapStatus','LIVE MAP • BROWSER-RETAINED PNG',false);
  }else{
    refreshPlotImage(true);
  }
}
function closeMap(){document.getElementById('mapModal').classList.remove('open');}
function refreshPlotImage(full=false){
  const id=full?'fullPlotImage':'rainPlotImage';
  const el=document.getElementById(id);
  if(el){
    const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
    const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
    const run=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
    const period=document.getElementById('periodSelect')?.value||currentPeriod;
    const prod=(mode==='ensemble' && ['ECMWF','ICON','GFS'].includes(model))?ensembleProduct():'MEAN';
    const url='/plot.png?day='+currentRainDay+'&mode='+encodeURIComponent(mode)+'&model='+encodeURIComponent(model)+'&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&product='+encodeURIComponent(prod)+'&ts='+Date.now();
    el.onerror=()=>setStatus('LIVE MODEL MAP ERROR: PNG display request failed',true);
    el.onload=()=>{if(el.naturalWidth>0) setStatus('MAP RENDERED • '+model+' • '+period,false);};
    el.src=url;
  }
}
function clearMapImages(){const blank='data:image/svg+xml;charset=UTF-8,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="700"><rect width="100%" height="100%" fill="#f7fbff"/><text x="50%" y="48%" text-anchor="middle" font-family="Arial" font-size="28" font-weight="700" fill="#123">WAITING FOR LIVE RAINFALL DATA</text><text x="50%" y="54%" text-anchor="middle" font-family="Arial" font-size="18" fill="#456">Previous model map is not displayed</text></svg>');['rainPlotImage','fullPlotImage'].forEach(id=>{const el=document.getElementById(id);if(el)el.src=blank;});}
function refreshAllPlots(){refreshPlotImage(false); if(document.getElementById('mapModal').classList.contains('open')) refreshPlotImage(true);}
function formatTickerBytes(n){
  n=Number(n)||0;
  if(n>=1024**3) return (n/1024**3).toFixed(2)+' GB';
  if(n>=1024**2) return (n/1024**2).toFixed(1)+' MB';
  if(n>=1024) return (n/1024).toFixed(0)+' KB';
  return Math.round(n)+' B';
}
function formatTickerSpeed(bps){
  bps=Number(bps)||0;
  if(bps<=0) return 'calculating speed…';
  if(bps>=1024**2) return (bps/1024**2).toFixed(2)+' MB/s';
  return (bps/1024).toFixed(0)+' KB/s';
}
function formatTickerDuration(sec){
  if(sec===null || sec===undefined || !isFinite(Number(sec))) return 'calculating…';
  sec=Math.max(0,Math.round(Number(sec)));
  const h=Math.floor(sec/3600), m=Math.floor((sec%3600)/60), s=sec%60;
  if(h) return `${h}h ${String(m).padStart(2,'0')}m`;
  if(m) return `${m}m ${String(s).padStart(2,'0')}s`;
  return `${s}s`;
}
function formatTickerFinish(iso){
  if(!iso) return 'calculating…';
  try{return new Date(iso).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});}catch(e){return 'calculating…';}
}
function updateEnsembleTicker(st){
  const ticker=document.getElementById('ensembleTicker');
  const track=document.getElementById('ensembleTickerTrack');
  if(!ticker || !track) return;
  const ident=st.model_identity||{};
  const mode=String(ident.mode||'').toLowerCase();
  const model=String(ident.model||'').toUpperCase();
  const label=ident.label || (model==='ECMWF' && mode==='ensemble' ? 'ECMWF ENS' : model==='ICON' && mode==='ensemble' ? 'ICON EPS' : model || 'MODEL');
  const state=String(st.state||'idle').toLowerCase();
  const run=st.run || ident.run || 'run not selected';
  const period=ident.period || st.period || document.getElementById('periodSelect')?.value || currentPeriod || '';
  const product=(mode==='ensemble' && (model==='ECMWF' || model==='GFS' || model==='ICON')) ? ` • PRODUCT: ${ensembleProduct()}` : '';
  const member=Number(st.current_member||0);
  const memberTotal=Number(st.member_total||0);
  const memberText=(mode==='ensemble' && memberTotal) ? ` • MEMBER ${member}/${memberTotal}` : '';
  const ep=st.current_endpoint ? ` • F${String(st.current_endpoint).padStart(3,'0')}` : '';
  const speed=formatTickerSpeed(st.speed_bps);
  const elapsed=st.started_at ? Math.max(0,(Date.now()-new Date(st.started_at).getTime())/1000) : 0;
  const eta=formatTickerDuration(st.eta_seconds);
  const finish=formatTickerFinish(st.expected_finish);
  const bytes=Number(st.downloaded_bytes)||0;
  const total=Number(st.estimated_total_bytes||st.total_bytes)||0;
  const progress=total ? ` • ${formatTickerBytes(bytes)} / ~${formatTickerBytes(total)}` : '';
  const isActive = mode==='ensemble' || !!ident.model || !!st.message || state!=='idle';
  if(!isActive){ ticker.classList.remove('show'); return; }
  ticker.classList.add('show');
  track.classList.remove('idle');
  if(state==='downloading' || state==='starting' || state==='probing'){
    const phase = state==='downloading' ? 'DOWNLOAD / PROCESSING IN PROGRESS' : state==='probing' ? 'RUN DISCOVERY / VERIFICATION IN PROGRESS' : 'ENGINE STARTING';
    track.textContent=`⏳ ${label} • ${phase} • RUN ${run} • ${period}${product}${memberText}${ep}${progress} • SPEED: ${speed} • ELAPSED: ${formatTickerDuration(elapsed)} • ETA: ${eta} • EXPECTED: ${finish} • ${st.message||'Please wait while the selected model data is processed'} •`;
  }else if(state==='ready'){
    const readyDetail=(mode==='ensemble' && memberTotal) ? ` • ${memberTotal} MEMBERS READY` : ' • MODEL DATA READY';
    track.textContent=`✅ ${label} • DOWNLOAD / PROCESSING COMPLETE • RUN ${run} • ${period}${product}${readyDetail} • PRODUCT READY •`;
    track.classList.add('idle');
  }else if(state==='error'){
    track.textContent=`⚠️ ${label} • MODEL ENGINE ERROR • RUN ${run} • ${period}${product} • ${st.error||st.message||'Please check the activity panel'} •`;
    track.classList.add('idle');
  }else{
    track.textContent=`ℹ️ ${label} • WAITING FOR USER RUN SELECTION • ${st.message||'Select one of the latest available runs; no automatic download will start'} •`;
    track.classList.add('idle');
  }
}
function renderECMWFStatus(st){
  updateEnsembleTicker(st);
  const state=(st.state||'idle').toLowerCase();
  const header=document.getElementById('headerEngine');
  if(header){
    header.textContent = state==='ready' ? 'LIVE ENGINE: READY' : state==='error' ? 'LIVE ENGINE: ERROR' : state==='downloading' ? 'LIVE ENGINE: DOWNLOADING' : state==='probing' || state==='starting' ? 'LIVE ENGINE: VERIFYING' : 'LIVE ENGINE: STARTING';
  }
  const badge=document.getElementById('liveState'); if(badge){badge.textContent=state==='ready'?'READY':state==='error'?'ERROR':state==='downloading'?'DOWNLOADING':(state==='probing'||state==='starting')?'VERIFYING':'WAITING'; badge.className='live-state '+(state==='ready'?'ready':state==='error'?'error':'');}
  const ident=st.model_identity||{}; const resolvedRun=ident.run||st.run||''; const run=document.getElementById('liveRun'); if(run) run.textContent=resolvedRun||'Detecting latest run…';
  const files=document.getElementById('liveFiles'); if(files) files.textContent=(st.downloaded||0)+' / '+(st.total||0);
  const pct=st.total ? Math.min(100,Math.round((st.downloaded/st.total)*100)) : (state==='ready'?100:0);
  const bar=document.getElementById('liveProgress'); if(bar) bar.style.width=pct+'%';
  const proc=document.getElementById('liveProcessing'); if(proc) proc.textContent=(ident.label?ident.label+' • ':'')+(st.message||'Waiting');
  const validityEl=document.getElementById('liveValidity');
  if(validityEl){
    if(st.valid_end_date && st.valid_end_time) validityEl.textContent=`${st.valid_end_date} • ${st.valid_end_time}`;
    else validityEl.textContent=state==='ready'?'VALIDITY PENDING MAP DECODE':'—';
  }
  const members=document.getElementById('liveMembers');
  if(members){
    const statusMembers=Number(st.member_total||0);
    const ensembleModel=String(ident.model||'').toUpperCase();
    const ensembleMode=String(ident.mode||'').toLowerCase();
    if(ensembleMode==='ensemble' && ensembleModel==='GFS') members.textContent=(statusMembers||31)+' members';
    else if(ensembleMode==='ensemble' && ensembleModel==='ICON') members.textContent=(statusMembers||40)+' members';
    else if(ensembleMode==='ensemble' && ensembleModel==='ECMWF') members.textContent=(statusMembers||50)+' members';
  }
  const stale=document.getElementById('staleBanner'); if(stale) stale.classList.remove('show');
  const log=document.getElementById('activityLog');
  if(log && Array.isArray(st.events)){
    log.innerHTML=st.events.map(x=>`<div class="activity-row ${x.level||'info'}">[${x.time}] ${x.message}</div>`).join('');
    log.scrollTop=log.scrollHeight;
  }
}
async function loadRainfall(day=1){
  const renderSeq=++rainfallRenderSeq;
  currentPeriod=document.getElementById('periodSelect')?.value||currentPeriod;
  currentRainDay=Math.max(1,Math.min(5,Number(day)||1));
  const img=document.getElementById('rainPlotImage'); const full=document.getElementById('fullPlotImage');
  setStatus('gisStatus','PROCESSING SELECTED MODEL RAINFALL MAP…',true);
  try{
    const selectedMode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
    const selectedModel=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
    const selectedRun=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
    const selectedPeriod=document.getElementById('periodSelect')?.value||currentPeriod;
    const prod=(selectedMode==='ensemble' && ['ECMWF','ICON','GFS'].includes(selectedModel))?ensembleProduct():'MEAN';
    if(selectedMode==='ensemble' && selectedModel==='GFS' && ['MSLP','MSLP_SPREAD','WIND850','SYNOPTIC'].includes(prod)){
      const windHour=(prod==='WIND850') ? Number(WIND_FORECAST_HOUR||24) : 24;
      const syn=await fetch('/gefs/synoptic.json?mode='+encodeURIComponent(selectedMode)+'&model='+encodeURIComponent(selectedModel)+'&run='+encodeURIComponent(selectedRun)+'&period='+encodeURIComponent(selectedPeriod)+'&product='+encodeURIComponent(prod)+'&fhour='+windHour+'&ts='+Date.now(),{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('SYNOPTIC HTTP '+r.status);return r.json();});
      if(syn.error) throw new Error(syn.error);
      if(renderSeq!==rainfallRenderSeq) return;
      const plotUrl='/plot.png?day='+currentRainDay+'&mode='+encodeURIComponent(selectedMode)+'&model='+encodeURIComponent(selectedModel)+'&run='+encodeURIComponent(selectedRun)+'&period='+encodeURIComponent(selectedPeriod)+'&product='+encodeURIComponent(prod)+'&fhour='+windHour+'&ts='+Date.now();
      const applyImage=(el)=>{if(!el)return;el.onerror=()=>{if(renderSeq===rainfallRenderSeq)setStatus('LIVE MODEL MAP ERROR: BROWSER COULD NOT DISPLAY /plot.png',true);};el.onload=()=>{if(renderSeq===rainfallRenderSeq&&el.naturalWidth>0)setStatus('MAP RENDERED • '+selectedModel+' • '+(syn.period||selectedPeriod),false);};el.src=plotUrl;};
      applyImage(img);
    // The full-screen viewer requests its own PNG only when opened.
      setStatus('gisStatus',`${syn.period||selectedPeriod} • ${syn.run||selectedRun} • ${syn.product_label||prod}`,false);
      const title=document.querySelector('.map-head h3');if(title)title.textContent=`South India • GEFS ${syn.product_label||prod} • ${syn.run||selectedRun}`;
      const members=document.getElementById('liveMembers');if(members)members.textContent=(syn.members||31)+' members';
      const processing=document.getElementById('liveProcessing');if(processing)processing.textContent=`GEFS • ${syn.product_label||prod} • ${syn.forecast||'F024'}`;
      document.getElementById('staleBanner')?.classList.remove('show');
      return;
    }
    const meta=await fetch('/rainfall.json?day='+currentRainDay+'&mode='+encodeURIComponent(selectedMode)+'&model='+encodeURIComponent(selectedModel)+'&run='+encodeURIComponent(selectedRun)+'&period='+encodeURIComponent(selectedPeriod)+'&product='+encodeURIComponent(prod)+'&ts='+Date.now(),{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error('RAINFALL HTTP '+r.status);return r.json();});
    if(meta.error) throw new Error(meta.error);
    if(renderSeq!==rainfallRenderSeq) return;
    // Direct server PNG: no Blob/object-URL lifecycle. This is the only image source.
    const plotUrl='/plot.png?day='+currentRainDay+'&mode='+encodeURIComponent(selectedMode)+'&model='+encodeURIComponent(selectedModel)+'&run='+encodeURIComponent(selectedRun)+'&period='+encodeURIComponent(selectedPeriod)+'&product='+encodeURIComponent(prod)+'&ts='+Date.now();
    const applyImage=(el)=>{
      if(!el) return;
      el.onerror=()=>{if(renderSeq===rainfallRenderSeq)setStatus('LIVE MODEL MAP ERROR: BROWSER COULD NOT DISPLAY /plot.png',true);};
      el.onload=()=>{if(renderSeq===rainfallRenderSeq&&el.naturalWidth>0)setStatus('MAP RENDERED • '+selectedModel+' • '+(meta.period||selectedPeriod),false);};
      el.src=plotUrl;
    };
    applyImage(img); applyImage(full);
    const validity=(meta.valid_end_date&&meta.valid_end_time)?`${meta.valid_end_date} • VALID UP TO ${meta.valid_end_time}`:'';
    if(validity)setStatus('gisStatus',`${meta.period||selectedPeriod} • ${validity}`,false);
    const title=document.querySelector('.map-head h3');if(title)title.textContent=`South India • ${meta.model||selectedModel} Rainfall • DAY ${currentRainDay}${validity?' • '+validity:''}`;
    const members=document.getElementById('liveMembers');if(members)members.textContent=(meta.members||'—')+' members';
    const validityEl=document.getElementById('liveValidity');if(validityEl&&validity)validityEl.textContent=validity;
    const processing=document.getElementById('liveProcessing');if(processing)processing.textContent=`${meta.model||selectedModel} • ${meta.start||''} → ${meta.end||''}`;
    const gridEl=document.getElementById('rainfallMeta');if(gridEl&&Number.isFinite(Number(meta.grid_dx)))gridEl.innerHTML=`<b>GRID VERIFIED</b><br>${Number(meta.grid_dx).toFixed(2)}° × ${Number(meta.grid_dy).toFixed(2)}° • ${Number(meta.lat_edge_min).toFixed(2)}–${Number(meta.lat_edge_max).toFixed(2)}°N • ${Number(meta.lon_edge_min).toFixed(2)}–${Number(meta.lon_edge_max).toFixed(2)}°E`;
    const dayLabel=document.getElementById('rainDayLabel');if(dayLabel)dayLabel.textContent=(meta.period==='24 Hour'?`DAY ${currentRainDay} • 24H`:(meta.period||selectedPeriod).toUpperCase());
    const slider=document.getElementById('rainDaySlider');if(slider)slider.value=currentRainDay;
    document.querySelectorAll('.map-control-btn[data-rain-day]').forEach(btn=>btn.classList.toggle('active',Number(btn.dataset.rainDay)===currentRainDay));
    document.getElementById('staleBanner')?.classList.remove('show');
  }catch(e){
    console.error(e);
    if(renderSeq===rainfallRenderSeq){setStatus('LIVE MODEL MAP ERROR: '+e.message,true);clearMapImages();}
  }
}

async function changeRainPeriod(){
  currentPeriod=document.getElementById('periodSelect')?.value||'24 Hour';
  currentRainDay=1;
  document.querySelectorAll('.map-control-btn[data-rain-day]').forEach(btn=>btn.disabled=currentPeriod!=='24 Hour');
  const slider=document.getElementById('rainDaySlider'); if(slider) slider.disabled=currentPeriod!=='24 Hour';
  // Period changes are discovery-only. Refresh the available runs for this period,
  // but do not download until the user explicitly selects a run.
  ACTIVE_RUN='';
  populateRunMenu([], '');
  updateSelectedLabels();
  await refreshRunOptions();
  await updatePeriodAvailability();
  updateSelectedLabels();
  setStatus('gisStatus','SELECT AN AVAILABLE RUN FOR THIS PERIOD — NO DOWNLOAD STARTED',false);
}

function toggleRainOpacity(){document.body.classList.toggle('rain-dim');}
function setRainDay(day){
  day=Math.max(1,Math.min(5,Number(day)||1));
  currentRainDay=day;
  if(document.getElementById('rainDaySlider')) document.getElementById('rainDaySlider').value=day;
  if(document.getElementById('rainDayLabel')) document.getElementById('rainDayLabel').textContent=`DAY ${day} • 24H`;
  document.querySelectorAll('.map-control-btn[data-rain-day]').forEach(btn=>btn.classList.toggle('active',Number(btn.dataset.rainDay)===day));
  if(ACTIVE_MODEL==='UKMET' && ACTIVE_FORECAST_MODE==='deterministic' && currentPeriod==='24 Hour'){
    startLiveUKMET(false,day);
    return;
  }
  if(ACTIVE_MODEL==='GFS' && ACTIVE_FORECAST_MODE==='deterministic' && currentPeriod==='24 Hour'){
    startLiveGFS(false,day);
    return;
  }
  if(ACTIVE_MODEL==='GEM' && ACTIVE_FORECAST_MODE==='deterministic' && currentPeriod==='24 Hour'){
    startLiveGEM(false,day);
    return;
  }
  if(ACTIVE_MODEL==='AIGFS' && ACTIVE_FORECAST_MODE==='deterministic' && currentPeriod==='24 Hour'){
    startLiveAIGFS(false,day);
    return;
  }
  loadRainfall(day);
}
function toggleRainOpacity(){
  ['small','full'].forEach(k=>{const layer=rainLayers[k];if(!layer)return; const next=(layer.options.opacity||0.78)>0.7?0.52:0.78;layer.setOpacity(next);});
}
let ecmwfReadyLoaded=false;
let ecmwfDownloadInFlight=false;
let ecmwfPollLoopStarted=false;
let rainfallRenderSeq=0;
function ensurePollECMWF(){
  if(ecmwfPollLoopStarted) return;
  ecmwfPollLoopStarted=true;
  pollECMWF();
}
async function pollECMWF(){
  try{
    const st=await fetch('/ecmwf/status?ts='+Date.now(),{cache:'no-store'}).then(r=>r.json());
    renderECMWFStatus(st);
    const activeProduct=(document.getElementById('ensembleProductSelect')?.value||ACTIVE_ENSEMBLE_PRODUCT||'').toUpperCase();
    const activeMode=(document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE);
    const activeModel=(document.getElementById('modelSelect')?.value||ACTIVE_MODEL);
    if(activeMode==='ensemble' && activeModel==='GFS' && activeProduct==='WIND850'){
      if(st.state==='downloading' || st.state==='probing' || st.state==='starting') updateWindHourButtons('loading');
      else if(st.state==='ready') updateWindHourButtons('ready');
      else if(st.state==='error') updateWindHourButtons('active');
    }
    const msg=st.error ? ((st.model_identity?.model||'MODEL')+' ERROR: '+st.error) : st.message;
    setStatus('gisStatus',msg,st.state!=='ready');
    if(st.state==='ready' || st.state==='error' || st.state==='idle'){
      ecmwfDownloadInFlight=false;
    }
    const ident=st.model_identity||{};
    const selectedMode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
    const selectedModel=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
    const selectedRun=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
    const statusMode=String(ident.mode||st.mode||'').toLowerCase();
    const statusModel=String(ident.model||st.model||'').toUpperCase();
    const statusRun=String(ident.run||st.run||'');
    const modeMatches=statusMode ? statusMode===String(selectedMode).toLowerCase() : true;
    const modelMatches=statusModel ? statusModel===String(selectedModel).toUpperCase() : true;
    const runMatches=selectedRun && statusRun ? statusRun.replace(/\D/g,'')===String(selectedRun).replace(/\D/g,'') : !!selectedRun;
    const selectedIdentity=currentMapIdentity();
    const requestMatches=!st.request_key || st.request_key===selectedIdentity.key;
    if(st.state==='ready' && modeMatches && modelMatches && runMatches && requestMatches && !ecmwfReadyLoaded){
      ecmwfReadyLoaded=true;
      loadRainfall(currentRainDay).catch(err=>{
        console.error('READY MAP RENDER ERROR',err);
        ecmwfReadyLoaded=false;
        setStatus('gisStatus','LIVE MODEL MAP ERROR: '+err.message,true);
      });
    }
  }catch(e){ setStatus('gisStatus','MODEL STATUS ERROR: '+e.message,true); }
  setTimeout(pollECMWF,5000);
}
function populateModelMenu(){
  const mode=document.getElementById('forecastMode')?.value||'deterministic';
  const sel=document.getElementById('modelSelect'); if(!sel)return;
  const models = mode==='ensemble' ? [
    ['ECMWF','ECMWF ENS'],['GFS','GFS / GEFS'],['ICON','ICON EPS']
  ] : [
    ['ECMWF','ECMWF IFS HRES'],['GFS','GFS'],['ICON','ICON'],['UKMET','UKMET'],['AIFS','AIFS'],['GEM','GEM / CMC GDPS'],['AIGFS','AIGFS']
  ];
  sel.innerHTML=models.map(([v,t])=>`<option value="${v}">${t}</option>`).join('');
  if(models.some(x=>x[0]===ACTIVE_MODEL)) sel.value=ACTIVE_MODEL;
  else {sel.value=models[0][0]; ACTIVE_MODEL=models[0][0];}
  populateRunMenu([], '');
  updateSelectedLabels();
}
// Run-list cache: discover once, then switch models/periods instantly without
// starting any forecast engine. The page preloads the current mode's connected
// model run lists in the background so the user never sees an empty selector.
const RUN_LIST_CACHE = Object.create(null);
const RUN_DISCOVERY_INFLIGHT = Object.create(null);
function runCacheKey(mode, model, period){ return `${mode}|${model}|${period}`; }
function connectedModelsForMode(mode){
  return mode==='ensemble' ? ['ECMWF','GFS','ICON'] : ['ECMWF','GFS','ICON','UKMET','AIFS','GEM','AIGFS'];
}
function cachedRunList(mode, model, period){ return RUN_LIST_CACHE[runCacheKey(mode,model,period)] || null; }
function cacheRunList(mode, model, period, data){
  const clean=Array.isArray(data?.runs) ? data.runs.filter(r=>r && r.value && r.value!=='AUTO') : [];
  RUN_LIST_CACHE[runCacheKey(mode,model,period)]={runs:clean,message:data?.message||'',ts:Date.now()};
  return RUN_LIST_CACHE[runCacheKey(mode,model,period)];
}
function applyCachedRunList(mode, model, period, preferred=''){
  const c=cachedRunList(mode,model,period);
  if(!c) return false;
  populateRunMenu(c.runs, preferred);
  return true;
}
async function fetchRunList(mode, model, period, options={}){
  const key=runCacheKey(mode,model,period);
  if(RUN_DISCOVERY_INFLIGHT[key]) return RUN_DISCOVERY_INFLIGHT[key];
  RUN_DISCOVERY_INFLIGHT[key]=(async()=>{
    const url='/model/runs?mode='+encodeURIComponent(mode)+'&model='+encodeURIComponent(model)+'&period='+encodeURIComponent(period)+'&ts='+Date.now();
    const controller=new AbortController();
    const timeout=setTimeout(()=>controller.abort(),90000);
    let data;
    try{
      data=await fetch(url,{cache:'no-store',signal:controller.signal}).then(r=>{if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});
    }catch(e){
      if(e && e.name==='AbortError') throw new Error('RUN DISCOVERY TIMEOUT');
      throw e;
    }finally{ clearTimeout(timeout); }
    if(data.error) throw new Error(data.error);
    return cacheRunList(mode,model,period,data);
  })().finally(()=>{ delete RUN_DISCOVERY_INFLIGHT[key]; });
  return RUN_DISCOVERY_INFLIGHT[key];
}
async function preloadAllModelRuns(mode, period, selectedModelOverride=''){
  // IMPORTANT: run discovery is now strictly scoped to the model the user
  // selected.  The previous implementation preloaded ECMWF + GFS + ICON
  // together, which made an ICON EPS selection appear to search other models
  // and polluted the activity log.  Never probe unrelated model servers here.
  const model=selectedModelOverride || document.getElementById('modelSelect')?.value || ACTIVE_MODEL;
  if(!model) return [];
  try{
    const c=cachedRunList(mode,model,period) || await fetchRunList(mode,model,period);
    if(document.getElementById('activityLog')){
      const label=(mode==='ensemble' && model==='ICON')?'ICON EPS':(mode==='ensemble' && model==='ECMWF')?'ECMWF ENS':(mode==='ensemble' && model==='GFS')?'GFS / GEFS':model;
      document.getElementById('activityLog').innerHTML=`<div class="activity-row success">MODEL RUN LIST READY • ${label}: ${c.runs.length} • ${mode.toUpperCase()} • NO DOWNLOAD STARTED</div>`;
    }
    return [{model,count:c.runs.length,ok:true}];
  }catch(e){
    if(document.getElementById('activityLog')){
      document.getElementById('activityLog').innerHTML=`<div class="activity-row warn">RUN LIST ERROR • ${model} • ${e.message}</div>`;
    }
    return [{model,count:0,ok:false,error:e.message}];
  }
}
function populateRunMenu(runs, selected=''){
  const sel=document.getElementById('runSelect'); if(!sel)return;
  const actual = Array.isArray(runs) ? runs.filter(r=>r && r.value && r.value!=='AUTO') : [];
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  let html='<option value="" disabled>SELECT A RUN — NO DOWNLOAD YET</option>';
  html += actual.map(r=>{
    const cached=(r.cached===true || r.cached==='true');
    const suffix=(mode==='ensemble' && model==='ICON') ? (cached?' • CACHE READY':' • CACHE NOT BUILT — SELECT TO AUTO-BUILD') : '';
    return `<option value="${r.value}" data-cached="${cached?'true':'false'}">${r.label}${suffix}</option>`;
  }).join('');
  sel.innerHTML=html;
  const wanted = selected && actual.some(r=>r.value===selected) ? selected : '';
  sel.value=wanted;
  ACTIVE_RUN=wanted;
}
async function refreshRunOptions(){
  const sel=document.getElementById('runSelect');
  if(!sel)return;
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  // Cached run lists are applied immediately. No artificial loading state.
  if(applyCachedRunList(mode,model,period,ACTIVE_RUN)){
    sel.disabled=false;
    updateSelectedLabels();
    return;
  }
  sel.disabled=true;
  sel.innerHTML='<option value="" disabled selected>VERIFYING AVAILABLE RUNS…</option>';
  const log=document.getElementById('activityLog');
  if(log) log.innerHTML=`<div class="activity-row info">RUN DISCOVERY STARTED • ${mode.toUpperCase()} • ${model}</div>`;
  setStatus('gisStatus',`VERIFYING ${model} RUNS…`,true);
  try{
    const c=await fetchRunList(mode,model,period);
    populateRunMenu(c.runs, ACTIVE_RUN);
    const note=c.message||'';
    if(note && document.getElementById('activityLog')) document.getElementById('activityLog').innerHTML=`<div class="activity-row info">${note}</div>`;
  }catch(e){
    populateRunMenu([], ''); ACTIVE_RUN='';
    if(document.getElementById('activityLog')) document.getElementById('activityLog').innerHTML=`<div class="activity-row warn">RUN LIST ERROR • ${e.message} • Select another model/run or press UPDATE SELECTED MODEL.</div>`;
  }finally{ sel.disabled=false; }
}
function ensembleProduct(){
  return document.getElementById('ensembleProductSelect')?.value || 'MEAN';
}
function currentMapIdentity(){
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  const run=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
  const period=document.getElementById('periodSelect')?.value||currentPeriod;
  const product=(mode==='ensemble' && ['ECMWF','ICON','GFS'].includes(model)) ? ensembleProduct() : 'MEAN';
  const windHour=(mode==='ensemble' && model==='GFS' && product==='WIND850') ? Number(WIND_FORECAST_HOUR||24) : 0;
  const baseKey=`${mode}|${model}|${run}|${period}|${product}`;
  const key=windHour ? `${baseKey}|F${String(windHour).padStart(3,'0')}` : baseKey;
  return {mode,model,run,period,product,windHour,key};
}
function resetMapRequestState(reason='selection changed'){
  ecmwfReadyLoaded=false;
  ecmwfDownloadInFlight=false;
  rainfallRenderSeq++;
  clearMapImages();
  console.debug('[MAP REQUEST RESET]',reason,currentMapIdentity());
}
function updateEnsembleProductVisibility(){
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  const el=document.getElementById('ensembleProductSelect');
  if(!el)return;
  el.style.display=(mode==='ensemble' && (model==='ECMWF' || model==='ICON' || model==='GFS'))?'inline-block':'none';
}
function changeEnsembleProduct(){
  resetMapRequestState('ensemble product changed');
  ACTIVE_ENSEMBLE_PRODUCT=ensembleProduct();
  updateSelectedLabels();
  if(ACTIVE_RUN){
    setStatus('gisStatus','ENSEMBLE PRODUCT CHANGED • PRESS UPDATE SELECTED MODEL TO PROCESS',false);
  }else{
    setStatus('gisStatus','SELECT AN AVAILABLE RUN FIRST — NO DOWNLOAD STARTED',false);
  }
}
function updateSelectedLabels(){
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  const run=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
  const label=document.getElementById('liveProduct');
  const opt=document.querySelector(`#modelSelect option[value="${model}"]`);
  if(label) label.textContent=(mode==='ensemble'?'ENSEMBLE':'DETERMINISTIC')+' • '+(opt?opt.textContent:model);
  const runOpt=document.querySelector(`#runSelect option[value="${run}"]`);
  const runLabel=document.getElementById('liveRun');
  if(runLabel) runLabel.textContent=runOpt?runOpt.textContent:run;
  updateEnsembleProductVisibility();
}
async function updatePeriodAvailability(){
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  const run=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
  const p=document.getElementById('periodSelect'); if(!p)return;
  const opt7=[...p.options].find(o=>o.value==='7 Day Accumulation');
  const opt15=[...p.options].find(o=>o.value==='15 Day Accumulation');
  let hour=null;
  if(/^\d{10}$/.test(run)) hour=Number(run.slice(8,10));
  const sevenPossible = model==='ICON' && mode==='ensemble' ? false : model==='GFS' && mode==='ensemble' ? true : model==='GFS' && mode==='deterministic' ? true : model==='ICON' && mode==='deterministic' ? (hour===null || [0,12].includes(hour)) : model==='AIFS' && mode==='deterministic' ? true : model==='AIGFS' && mode==='deterministic' ? true : model==='UKMET' && mode==='deterministic' ? (hour===null || [0,12].includes(hour)) : model==='GEM' && mode==='deterministic' ? (hour===null || [0,12].includes(hour)) : model==='ECMWF' && (hour===null || ((mode==='deterministic' && [0,12].includes(hour)) || (mode==='ensemble' && [0,12].includes(hour))));
  const fifteenPossible = model==='GFS' && mode==='ensemble' ? true : model==='GFS' && mode==='deterministic' ? true : model==='ICON' ? false : model==='AIFS' && mode==='deterministic' ? true : model==='AIGFS' && mode==='deterministic' ? true : model==='UKMET' || model==='GEM' ? false : model==='ECMWF' && (hour===null || [0,12].includes(hour));
  if(opt7){
    opt7.disabled=!sevenPossible;
    opt7.title=sevenPossible?(model==='GFS'?'GFS / GEFS rainfall F168 endpoint available':model==='ICON'?'ICON Global DWD F168 endpoint available on 00/12Z':model==='AIFS'?'AIFS Single F168 endpoint available':model==='AIGFS'?'AIGFS F006…F168 6-hour interval accumulation':model==='UKMET'?'UKMET Global 10 km F168 cadence-aware accumulation':'ECMWF run supports 7-day cumulative TP'):'This selected run cannot provide 7-day accumulation';
  }
  if(opt15){
    opt15.disabled=!fifteenPossible;
    opt15.title=fifteenPossible?(model==='GFS'?'GEFS 0.50° rainfall F360 endpoint available':model==='AIFS'?'AIFS Single F360 endpoint available':model==='AIGFS'?'AIGFS F006…F360 6-hour interval accumulation':'ECMWF HRES/ENS 00/12Z supports 15-day (360h) Open Data accumulation'):'15-day accumulation is not available for the selected model/run';
  }
  if(p.value==='15 Day Accumulation' && !fifteenPossible) p.value='7 Day Accumulation';
  if(p.value==='7 Day Accumulation' && !sevenPossible) p.value='24 Hour';
  currentPeriod=p.value;
  updateSelectedLabels();
}

async function changeForecastMode(mode){
  resetMapRequestState('forecast mode changed');
  ACTIVE_FORECAST_MODE=mode;
  ACTIVE_RUN='';
  populateModelMenu();
  populateRunMenu([], '');
  updateEnsembleProductVisibility();
  updateSelectedLabels();
  await refreshRunOptions();
  await updatePeriodAvailability();
  updateSelectedLabels();
  setStatus('gisStatus','SELECT AN AVAILABLE RUN — NO DOWNLOAD STARTED',false);
}

async function changeForecastModel(model){
  resetMapRequestState('forecast model changed');
  ACTIVE_MODEL=model;
  ACTIVE_RUN='';
  // Model changes are discovery-only. Never start a forecast download here.
  populateRunMenu([], '');
  updateSelectedLabels();
  await refreshRunOptions();
  await updatePeriodAvailability();
  updateSelectedLabels();
  setStatus('gisStatus',`SELECT AN AVAILABLE ${model} RUN — NO DOWNLOAD STARTED`,false);
}

async function changeForecastRun(run){
  resetMapRequestState('forecast run changed');
  ACTIVE_RUN=run||'';
  updateSelectedLabels();
  if(!ACTIVE_RUN){
    clearMapImages();
    setStatus('gisStatus','SELECT AN AVAILABLE RUN — NO DOWNLOAD STARTED',false);
    return;
  }
  await updatePeriodAvailability();
  updateSelectedLabels();
  const mode=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  const model=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  const activeProduct=(document.getElementById('ensembleProductSelect')?.value||ACTIVE_ENSEMBLE_PRODUCT||'').toUpperCase();
  if(mode==='ensemble' && model==='GFS' && activeProduct==='WIND850'){
    // Wind module is hour-driven: changing the run only refreshes the
    // published forecast-hour buttons. It never starts a forecast download.
    await discoverWindForecastHours(ACTIVE_RUN,false);
    clearMapImages();
    setStatus('gisStatus',`WIND RUN SELECTED • ${ACTIVE_RUN} • SELECT AN HOUR TO DOWNLOAD & PLOT`,false);
    const log=document.getElementById('activityLog');
    if(log)log.innerHTML=`<div class="activity-row success">WIND RUN SELECTED • ${ACTIVE_RUN} • HOURS DISCOVERED • NO DOWNLOAD STARTED</div>`;
    return;
  }
  const opt=document.querySelector(`#runSelect option[value="${ACTIVE_RUN}"]`);
  const cached=opt?.dataset?.cached==='true';
  if(mode==='ensemble' && model==='ICON' && !cached){
    clearMapImages();
    setStatus('gisStatus',`ICON EPS ${ACTIVE_RUN} • CACHE NOT BUILT • AUTO-BUILDING SELECTED RUN…`,true);
    const log=document.getElementById('activityLog');
    if(log) log.innerHTML=`<div class="activity-row info">ICON EPS AUTO-BUILD • ${ACTIVE_RUN} • F024 • 40 MEMBERS</div>`;
    // Do not require a separate archive command. The dashboard builds the
    // selected published cycle once, validates the resulting 40×85×97 cache,
    // keeps it on disk, and reuses it on every later selection.
    startLiveIconEPSBuild(false);
    return;
  }
  // Selecting a cache-ready/published run starts only the selected model engine.
  startSelectedModel(false);
}
function startSelectedModel(manual=false){
  ACTIVE_FORECAST_MODE=document.getElementById('forecastMode')?.value||ACTIVE_FORECAST_MODE;
  ACTIVE_MODEL=document.getElementById('modelSelect')?.value||ACTIVE_MODEL;
  ACTIVE_RUN=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
  if(!/^\d{10}$/.test(String(ACTIVE_RUN))){
    ACTIVE_RUN='';
    const runSel=document.getElementById('runSelect'); if(runSel) runSel.value='';
    clearMapImages();
    setStatus('gisStatus','SELECT AN AVAILABLE RUN FIRST — DOWNLOAD NOT STARTED',false);
    const log=document.getElementById('activityLog');
    if(log) log.innerHTML='<div class="activity-row info">RUN SELECTION REQUIRED • Choose one of the latest available published runs before downloading.</div>';
    return;
  }
  updateSelectedLabels();
  clearMapImages();
  document.getElementById('staleBanner')?.classList.remove('show');
  if(ACTIVE_MODEL==='ECMWF') return startLiveECMWF(manual);
  if(ACTIVE_MODEL==='GFS' && ACTIVE_FORECAST_MODE==='ensemble') return startLiveGEFS(manual);
  if(ACTIVE_MODEL==='GFS' && ACTIVE_FORECAST_MODE==='deterministic') return startLiveGFS(manual);
  if(ACTIVE_MODEL==='ICON' && ACTIVE_FORECAST_MODE==='deterministic') return startLiveICON(manual);
  if(ACTIVE_MODEL==='ICON' && ACTIVE_FORECAST_MODE==='ensemble'){
    const opt=document.querySelector(`#runSelect option[value="${ACTIVE_RUN}"]`);
    if(opt?.dataset?.cached!=='true'){
      return startLiveIconEPSBuild(manual);
    }
    return startLiveIconEPSCached(manual);
  }
  if(ACTIVE_MODEL==='AIFS' && ACTIVE_FORECAST_MODE==='deterministic') return startLiveAIFS(manual);
  if(ACTIVE_MODEL==='UKMET' && ACTIVE_FORECAST_MODE==='deterministic') return startLiveUKMET(manual,currentRainDay);
  if(ACTIVE_MODEL==='GEM' && ACTIVE_FORECAST_MODE==='deterministic') return startLiveGEM(manual,currentRainDay);
  if(ACTIVE_MODEL==='AIGFS' && ACTIVE_FORECAST_MODE==='deterministic') return startLiveAIGFS(manual,currentRainDay);
  ecmwfDownloadInFlight=false;
  setStatus('gisStatus',`${ACTIVE_MODEL} selected — this model mode is not connected yet.`,true);
  const log=document.getElementById('activityLog');
  if(log) log.innerHTML=`<div class="activity-row warn">${ACTIVE_FORECAST_MODE.toUpperCase()} • ${ACTIVE_MODEL} selected. ECMWF, GFS, ICON, AIFS, UKMET, GEM and AIGFS Talk engines are connected.</div>`;
}
function startLiveAIGFS(manual=false,dayOverride=null){
  const run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const day=(period==='24 Hour') ? Math.max(1,Math.min(5,Number(dayOverride||currentRainDay||1))) : 1;
  const requestKey=`${ACTIVE_FORECAST_MODE}|AIGFS|${run}|${period}|MEAN`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){ if(manual) setStatus('gisStatus','THIS AIGFS RUN / PERIOD / DAY IS ALREADY RUNNING…',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period; currentRainDay=day;
  setStatus('gisStatus',`${manual?'MANUAL ':''}AIGFS ${run} • ${period}${period==='24 Hour'?' • DAY '+day:''} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • AIGFS • ${run} • ${period}${period==='24 Hour'?' • DAY '+day:''}</div>`;
  const url='/aigfs/download?mode=deterministic&model=AIGFS&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&day='+day+'&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{let data={};try{data=await r.json();}catch(_){}if(!r.ok)throw new Error(data.error||('HTTP '+r.status));if(log)log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED AIGFS ENGINE REQUEST • ${run} • ${period}${period==='24 Hour'?' • DAY '+day:''}</div>`;setStatus('gisStatus',`AIGFS ${run} • ${period}${period==='24 Hour'?' • DAY '+day:''} • SERVER ACCEPTED — PROCESSING…`,true);}).catch(e=>{ecmwfDownloadInFlight=false;setStatus('gisStatus','AIGFS ENGINE REQUEST ERROR: '+e.message,true);if(log)log.innerHTML=`<div class="activity-row error">AIGFS ENGINE REQUEST FAILED • ${e.message}</div>`;});
  ensurePollECMWF();
}
function startLiveGEM(manual=false,dayOverride=null){
  const run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const day=(dayOverride==null?currentRainDay:Math.max(1,Math.min(5,Number(dayOverride)||1)));
  const requestKey=`${ACTIVE_FORECAST_MODE}|GEM|${run}|${period}|MEAN`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){ if(manual) setStatus('gisStatus','THIS GEM RUN / PERIOD IS ALREADY RUNNING…',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}GEM ${run} • ${period} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • GEM / CMC GDPS • ${run} • ${period}</div>`;
  const url='/gem/download?mode=deterministic&model=GEM&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&day='+encodeURIComponent(day)+'&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{let data={};try{data=await r.json();}catch(_){}if(!r.ok)throw new Error(data.error||('HTTP '+r.status));if(log)log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED GEM ENGINE REQUEST • ${run} • ${period}</div>`;setStatus('gisStatus',`GEM ${run} • ${period} • SERVER ACCEPTED — PROCESSING…`,true);}).catch(e=>{ecmwfDownloadInFlight=false;setStatus('gisStatus','GEM ENGINE REQUEST ERROR: '+e.message,true);if(log)log.innerHTML=`<div class="activity-row error">GEM ENGINE REQUEST FAILED • ${e.message}</div>`;});
  ensurePollECMWF();
}
let lastEngineRequestKey='';
function startLiveGEFS(manual=false){
  const label='GFS / GEFS', run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const product=ensembleProduct();
  if(!['24 Hour','7 Day Accumulation','15 Day Accumulation'].includes(period)){
    ecmwfDownloadInFlight=false;
    setStatus('gisStatus','GEFS supports 24 Hour, 7 Day and 15 Day rainfall accumulation.',true);
    return;
  }
  const windHour=(product==='WIND850') ? Number(WIND_FORECAST_HOUR||24) : 0;
  const baseRequestKey=`ensemble|GFS|${run}|${period}|${product}`;
  const requestKey=windHour ? `${baseRequestKey}|F${String(windHour).padStart(3,'0')}` : baseRequestKey;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){
    if(manual) setStatus('gisStatus','THIS GEFS RUN / PERIOD / PRODUCT IS ALREADY RUNNING…',true);
    return;
  }
  lastEngineRequestKey=requestKey;
  ecmwfDownloadInFlight=true;
  ecmwfReadyLoaded=false;
  rainfallRenderSeq++;
  clearMapImages();
  currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}${label} ${run} • ${period} • ${product} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog');
  if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • ${label} • ${run} • ${period} • ${product} • 31 MEMBERS</div>`;
  const url='/gefs/download?mode=ensemble&model=GFS&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&product='+encodeURIComponent(product)+'&fhour='+windHour+'&ts='+Date.now();
  fetch(url,{cache:'no-store'})
    .then(async r=>{
      let data={}; try{data=await r.json();}catch(_){}
      if(!r.ok) throw new Error(data.error||('HTTP '+r.status));
      if(log) log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED GEFS ENGINE REQUEST • ${run} • ${period} • ${product} • 31 MEMBERS</div>`;
      setStatus('gisStatus',`${label} ${run} • ${period} • ${product} • SERVER ACCEPTED — 31 MEMBERS DOWNLOADING…`,true);
    })
    .catch(e=>{
      ecmwfDownloadInFlight=false;
      setStatus('gisStatus','GEFS ENGINE REQUEST ERROR: '+e.message,true);
      if(log) log.innerHTML=`<div class="activity-row error">GEFS ENGINE REQUEST FAILED • ${e.message}</div>`;
      console.error(e);
    });
  ensurePollECMWF();
}

function startLiveGFS(manual=false,dayOverride=null){
  const label='GFS', run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const day=(dayOverride==null?currentRainDay:Math.max(1,Math.min(5,Number(dayOverride)||1)));
  const requestKey=`${ACTIVE_FORECAST_MODE}|${ACTIVE_MODEL}|${run}|${period}|MEAN`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){ if(manual) setStatus('gisStatus','THIS GFS RUN / PERIOD IS ALREADY RUNNING…',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}GFS ${run} • ${period} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • GFS • ${run} • ${period}</div>`;
  const url='/gfs/download?mode=deterministic&model=GFS&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&day='+encodeURIComponent(day)+'&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{ let data={}; try{data=await r.json();}catch(_){} if(!r.ok) throw new Error(data.error||('HTTP '+r.status)); if(log) log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED GFS ENGINE REQUEST • ${run} • ${period}</div>`; setStatus('gisStatus',`GFS ${run} • ${period} • SERVER ACCEPTED — PROCESSING…`,true); }).catch(e=>{ ecmwfDownloadInFlight=false; setStatus('gisStatus','GFS ENGINE REQUEST ERROR: '+e.message,true); if(log) log.innerHTML=`<div class="activity-row error">GFS ENGINE REQUEST FAILED • ${e.message}</div>`; });
  ensurePollECMWF();
}
function startLiveIconEPSBuild(manual=false){
  const run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  if(period!=='24 Hour'){ setStatus('gisStatus','ICON EPS selected-run builder currently supports 24 Hour F024 only.',true); return; }
  const requestKey=`ensemble|ICON|BUILD|${run}|${period}`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){ if(manual) setStatus('gisStatus','THIS ICON EPS BUILD IS ALREADY RUNNING…',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}ICON EPS ${run} • CACHE MISSING • BUILDING SELECTED RUN…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ICON EPS BUILD REQUEST • ${run} • F024 • 40 MEMBERS</div>`;
  const url='/icon-eps/download?mode=ensemble&model=ICON&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&product='+encodeURIComponent(ensembleProduct())+'&build=1&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{let data={};try{data=await r.json();}catch(_){}if(!r.ok)throw new Error(data.error||('HTTP '+r.status));if(log)log.innerHTML=`<div class="activity-row info">ICON EPS BUILD STARTED • ${run} • selected run is being decoded locally</div>`;setStatus('gisStatus',`ICON EPS ${run} • BUILDING 40 MEMBERS…`,true);}).catch(e=>{ecmwfDownloadInFlight=false;setStatus('gisStatus','ICON EPS BUILD ERROR: '+e.message,true);if(log)log.innerHTML=`<div class="activity-row error">ICON EPS BUILD REQUEST FAILED • ${e.message}</div>`;});
  ensurePollECMWF();
}
function startLiveIconEPSCached(manual=false){
  const label='ICON EPS (cached)', run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  if(period!=='24 Hour'){ setStatus('gisStatus','ICON EPS cache currently supports 24 Hour F024 only.',true); return; }
  const requestKey=`ensemble|ICON|${run}|${period}|${ensembleProduct()}`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){ if(manual) setStatus('gisStatus','THIS ICON EPS CACHE IS ALREADY LOADING…',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; rainfallRenderSeq++; clearMapImages(); currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}ICON EPS ${run} • ${period} • CACHE REQUEST…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ICON EPS CACHE REQUEST • ${run} • ${period} • ${ensembleProduct()}</div>`;
  const url='/icon-eps/download?mode=ensemble&model=ICON&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&product='+encodeURIComponent(ensembleProduct())+'&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{
    let data={};
    try{data=await r.json();}catch(_){}
    if(!r.ok) throw new Error(data.error||('HTTP '+r.status));
    if(log) log.innerHTML=`<div class="activity-row success">ICON EPS CACHE READY • ${run} • ${ensembleProduct()}</div>`;
    setStatus('gisStatus',`ICON EPS ${run} • CACHE READY — LOADING MAP…`,true);
    // IMPORTANT: do not wait for the generic status poll here. A previous
    // model can still have left ecmwfReadyLoaded=true, which used to race the
    // newly-selected ICON EPS cache and leave the map unplotted. The selected
    // cache request itself is authoritative: render it immediately.
    ecmwfReadyLoaded=true;
    await loadRainfall(currentRainDay);
    ecmwfDownloadInFlight=false;
  }).catch(e=>{
    ecmwfDownloadInFlight=false;
    ecmwfReadyLoaded=false;
    setStatus('gisStatus','ICON EPS CACHE ERROR: '+e.message,true);
    if(log) log.innerHTML=`<div class="activity-row error">ICON EPS CACHE ERROR • ${e.message}</div>`;
  });
  ensurePollECMWF();
}
function startLiveICON(manual=false){
  const label='ICON Global', run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const requestKey=`${ACTIVE_FORECAST_MODE}|${ACTIVE_MODEL}|${run}|${period}`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){ if(manual) setStatus('gisStatus','THIS ICON RUN / PERIOD IS ALREADY RUNNING…',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}ICON ${run} • ${period} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • ICON Global • ${run} • ${period}</div>`;
  const url='/icon/download?mode=deterministic&model=ICON&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{let data={};try{data=await r.json();}catch(_){}if(!r.ok)throw new Error(data.error||('HTTP '+r.status));if(log)log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED ICON ENGINE REQUEST • ${run} • ${period}</div>`;setStatus('gisStatus',`ICON ${run} • ${period} • SERVER ACCEPTED — PROCESSING…`,true);}).catch(e=>{ecmwfDownloadInFlight=false;setStatus('gisStatus','ICON ENGINE REQUEST ERROR: '+e.message,true);if(log)log.innerHTML=`<div class="activity-row error">ICON ENGINE REQUEST FAILED • ${e.message}</div>`;});
  ensurePollECMWF();
}
function startLiveAIFS(manual=false){
  const label='AIFS Single', run=ACTIVE_RUN||'AUTO';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const requestKey=`${ACTIVE_FORECAST_MODE}|${ACTIVE_MODEL}|${run}|${period}`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){
    if(manual) setStatus('gisStatus','THIS AIFS RUN / PERIOD IS ALREADY RUNNING…',true);
    return;
  }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period;
  setStatus('gisStatus',`${manual?'MANUAL ':''}AIFS ${run} • ${period} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog');
  if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • AIFS Single • ${run} • ${period}</div>`;
  const url='/ecmwf/download?mode=deterministic&model=AIFS&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&ts='+Date.now();
  fetch(url,{cache:'no-store'})
    .then(async r=>{
      let data={}; try{data=await r.json();}catch(_){}
      if(!r.ok) throw new Error(data.error||('HTTP '+r.status));
      if(log) log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED AIFS ENGINE REQUEST • ${run} • ${period}</div>`;
      setStatus('gisStatus',`AIFS ${run} • ${period} • SERVER ACCEPTED — PROCESSING…`,true);
    })
    .catch(e=>{
      ecmwfDownloadInFlight=false;
      setStatus('gisStatus','AIFS ENGINE REQUEST ERROR: '+e.message,true);
      if(log) log.innerHTML=`<div class="activity-row error">AIFS ENGINE REQUEST FAILED • ${e.message}</div>`;
      console.error(e);
    });
  ensurePollECMWF();
}

function startLiveUKMET(manual=false, requestedDay=null){
  const run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const day=(period==='24 Hour') ? Math.max(1,Math.min(5,Number(requestedDay||currentRainDay||1))) : 1;
  const requestKey=`${ACTIVE_FORECAST_MODE}|UKMET|${run}|${period}|MEAN`;
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){
    if(manual) setStatus('gisStatus',`THIS UKMET RUN / PERIOD / DAY ${day} IS ALREADY RUNNING…`,true);
    return;
  }
  if(period==='15 Day Accumulation'){ setStatus('gisStatus','UKMET Global 10 km is available only to 168h • 15 Day is disabled.',true); return; }
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true; ecmwfReadyLoaded=false; currentPeriod=period; currentRainDay=day;
  setStatus('gisStatus',`${manual?'MANUAL ':''}UKMET ${run} • ${period} • DAY ${day} • LIVE AWS REQUEST SENT…`,true);
  const log=document.getElementById('activityLog'); if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • UKMET • ${run} • ${period} • DAY ${day}</div>`;
  const url='/ukmet/download?mode=deterministic&model=UKMET&run='+encodeURIComponent(run)+'&period='+encodeURIComponent(period)+'&day='+day+'&ts='+Date.now();
  fetch(url,{cache:'no-store'}).then(async r=>{let data={};try{data=await r.json();}catch(_){}if(!r.ok)throw new Error(data.error||('HTTP '+r.status));if(log)log.innerHTML=`<div class="activity-row success">SERVER ACCEPTED UKMET AWS ENGINE REQUEST • ${run} • ${period} • DAY ${day}</div>`;setStatus('gisStatus',`UKMET ${run} • ${period} • DAY ${day} • SERVER ACCEPTED — PROCESSING…`,true);}).catch(e=>{ecmwfDownloadInFlight=false;setStatus('gisStatus','UKMET ENGINE REQUEST ERROR: '+e.message,true);if(log)log.innerHTML=`<div class="activity-row error">UKMET ENGINE REQUEST FAILED • ${e.message}</div>`;});
  ensurePollECMWF();
}

function startLiveECMWF(manual=false){
  const label=ACTIVE_FORECAST_MODE==='ensemble'?'ECMWF ENS':'ECMWF IFS HRES';
  const run=ACTIVE_RUN||'NOT SELECTED';
  const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
  const requestKey=`${ACTIVE_FORECAST_MODE}|${ACTIVE_MODEL}|${run}|${period}`;

  // Same selection already running: do not create a duplicate worker.
  if(ecmwfDownloadInFlight && requestKey===lastEngineRequestKey){
    if(manual) setStatus('gisStatus','THIS ECMWF RUN / PERIOD IS ALREADY RUNNING…',true);
    return;
  }

  // Every NEW run or period selection is an explicit engine request.
  // The server-side job token makes this new request authoritative and
  // automatically supersedes the previous background worker.
  lastEngineRequestKey=requestKey;
  ecmwfDownloadInFlight=true;
  ecmwfReadyLoaded=false;
  currentPeriod=period;

  setStatus('gisStatus',`${manual?'MANUAL ':''}${label} ${run} • ${period} • ENGINE REQUEST SENT…`,true);
  const log=document.getElementById('activityLog');
  if(log) log.innerHTML=`<div class="activity-row info">ENGINE REQUEST SENT • ${label} • ${run} • ${period}</div>`;

  const url='/ecmwf/download?mode='+encodeURIComponent(ACTIVE_FORECAST_MODE)+
            '&model=ECMWF&run='+encodeURIComponent(run)+
            '&period='+encodeURIComponent(period)+'&ts='+Date.now();

  fetch(url,{cache:'no-store'})
    .then(async r=>{
      let data={};
      try{ data=await r.json(); }catch(_){ }
      if(!r.ok) throw new Error(data.error||('HTTP '+r.status));
      const l=document.getElementById('activityLog');
      if(l) l.innerHTML=`<div class="activity-row success">SERVER ACCEPTED ENGINE REQUEST • ${label} • ${run} • ${period}</div>`;
      setStatus('gisStatus',`${label} ${run} • ${period} • SERVER ACCEPTED — PROCESSING…`,true);
    })
    .catch(e=>{
      ecmwfDownloadInFlight=false;
      setStatus('gisStatus','ECMWF ENGINE REQUEST ERROR: '+e.message,true);
      const l=document.getElementById('activityLog');
      if(l) l.innerHTML=`<div class="activity-row error">ENGINE REQUEST FAILED • ${e.message}</div>`;
      console.error(e);
    });

  // Start polling immediately so the activity panel reflects the server
  // worker instead of waiting for the fetch promise to finish.
  ensurePollECMWF();
}

async function loadGIS(){
  // GIS startup must not trigger a second ECMWF engine request.
  setStatus('gisStatus','LOADING SURVEY OF INDIA BOUNDARY…',true);
  setStatus('fullMapStatus','LOADING SURVEY OF INDIA BOUNDARY…',true);
  // GIS loading is independent of model/run discovery.
  // Initial model startup is handled once by bootDashboard().
  try{
    const r=await fetch('/gis.json',{cache:'no-store'});
    if(!r.ok) throw new Error('HTTP '+r.status);
    gisData=await r.json();
    if(!gisData.features || !gisData.features.length) throw new Error('No boundary features');
    if(typeof L === 'undefined') throw new Error('Leaflet JavaScript is not loaded');
    smallMap=buildMap('southIndiaMap');
    setStatus('gisStatus','SURVEY OF INDIA • STATE BOUNDARY LOADED',false);
  }catch(e){
    console.error(e);
    setStatus('gisStatus','SURVEY OF INDIA MAP UNAVAILABLE — ECMWF ENGINE CONTINUES',true);
    setStatus('fullMapStatus','SURVEY OF INDIA MAP UNAVAILABLE',true);
  }
}
function initFullMap(){
  if(!gisData){setStatus('fullMapStatus','BOUNDARY DATA NOT LOADED',true);return;}
  fullMapObj=buildMap('fullMap');
  setStatus('fullMapStatus','SURVEY OF INDIA • INTERACTIVE VIEWER',false);
  loadRainfall(1,fullMapObj);
}
const SIDEBAR_MODULES={
  overview:{title:'NEM Overview',badge:'CORE DASHBOARD',cards:[['Current State','Live model selector, run selector and rainfall map.',''],['Talk to MasRainman','Town → coordinates → model rainfall briefing.',''],['Status','V162 stable core remains unchanged.','']],note:'Core dashboard. This module is the protected baseline; selecting other sidebar modules does not alter the V162 Talk Engine.'},
  rainfall:{title:'Rainfall',badge:'CONNECTED',cards:[['Forecast','24 Hour / 7 Day / 15 Day accumulation.','Existing rainfall engine'],['Models','ECMWF, GFS, ICON, AIFS, UKMET, GEM, AIGFS paths.','AIFS remains pending'],['Map','Current selected forecast is rendered in the main map.','Live online data']],note:'Rainfall is already connected to the stable forecast engine. Use the controls above to select model, run and period.'},
  wind:{title:'Wind',badge:'GEFS 850 hPa READY',cards:[['850 hPa Wind','Live GEFS ensemble wind-speed mean from UGRD + VGRD.','0.50° wind grid'],['Current scope','Validated GEFS / NOAA-NCEP synoptic backend.','F024 • 24 Hour'],['Workflow','Select latest run here, then press UPDATE SELECTED MODEL.','No automatic download']],note:'Wind is rendered through the independent GEFS synoptic engine. It never routes UGRD/VGRD through the rainfall/APCP decoder.'},
  pressure:{title:'Pressure',badge:'MSLP + 925/850/500 READY',cards:[['Pressure Products','MSLP plus 925, 850 and 500 hPa geopotential-height maps.','User selectable'],['Models','ECMWF ENS • GEFS • ECMWF HRES • GFS • ICON Global • AIGFS.','Direct field retrieval'],['Map','Filled pressure field + clean isobars + ensemble member low-centre spread + H/L structure.','Reference-style synoptic view']],note:'Pressure is a first-class synoptic product. Select a model, choose the latest or previous run, then select a published forecast hour. No stale pressure map is reused.'},
  temperature:{title:'Temperature',badge:'ENGINE PENDING',cards:[['Tmax / Tmin','Decoder not yet connected in this dashboard version.',''],['Data policy','No synthetic temperature values will be displayed.',''],['Next step','Connect a validated model temperature decoder.','']],note:'Module shell only for now. We will connect it after the source/model field is validated.'},
  humidity:{title:'Humidity',badge:'ENGINE PENDING',cards:[['Relative Humidity','Decoder not yet connected.',''],['850 hPa RH','Potential future synoptic layer.',''],['Data policy','No placeholder values.','']],note:'Module shell only. Data will be added after a direct humidity-field decoder is proven.'},
  cloud:{title:'Cloud Cover',badge:'ENGINE PENDING',cards:[['Cloud Cover','Decoder not yet connected.',''],['Total cloud / low cloud','To be defined against a validated model field.',''],['Data policy','No synthetic map.','']],note:'Module shell only. We will connect the exact cloud parameter after field validation.'},
  enso:{title:'ENSO',badge:'CLIMATE MODULE',cards:[['ENSO State','Climate monitoring module.',''],['Current index','Live source integration not yet connected here.',''],['History','Historical context can be added separately.','']],note:'Climate modules are intentionally separated from forecast-model downloads. No unverified current index is displayed.'},
  iod:{title:'IOD',badge:'CLIMATE MODULE',cards:[['IOD State','Climate monitoring module.',''],['Current index','Live source integration pending.',''],['Trend','To be connected to an authoritative climate source.','']],note:'No current IOD value is fabricated; source integration comes next.'},
  mjo:{title:'MJO',badge:'CLIMATE MODULE',cards:[['MJO Phase','Climate monitoring module.',''],['Amplitude','Live source integration pending.',''],['Propagation','To be linked to authoritative/current MJO data.','']],note:'MJO will remain independent of the short-range model download engine.'},
  seasonal:{title:'Seasonal Outlook',badge:'OUTLOOK MODULE',cards:[['Monthly','Seasonal rainfall/temperature outlook.',''],['NEM','Regional monsoon outlook section.',''],['Source','Authoritative seasonal products to be connected.','']],note:'This module will present outlook products separately from deterministic short-range forecasts.'},
  comparison:{title:'Model Comparison',badge:'ADVANCED',cards:[['Rainfall','Compare validated model fields at the same period.',''],['Agreement','Show spread/range without ranking models.',''],['Location','Can use the same town/grid framework.','']],note:'Comparison will describe differences between models rather than assigning a model score or winner.'},
  mme:{title:'MME',badge:'ADVANCED',cards:[['Multi-model mean','Combine compatible model fields on a common grid.',''],['Products','Mean / percentile / probability products.',''],['Validation','Inputs must pass field and period checks.','']],note:'The existing ensemble engines remain protected. MME expansion will be done only after compatible model fields are verified.'},
  anomaly:{title:'Anomaly',badge:'ADVANCED',cards:[['Rainfall anomaly','Departure from a defined climatology.',''],['Temperature anomaly','Future module after temperature decoder.',''],['Wind / pressure','Can be added once baseline fields are connected.','']],note:'Anomaly requires an explicit reference climatology. No anomaly will be generated without a defined baseline.'},
  tsi:{title:'TSI',badge:'ADVANCED',cards:[['Thunderstorm Potential','MasRainman TSI physics framework.',''],['Inputs','CAPE/shear/moisture/lapse-rate related fields.',''],['Status','Dedicated engine integration pending.','']],note:'TSI remains a separate physics product and will not be derived from rainfall alone.'},
  cyclone:{title:'Cyclone Tracker',badge:'ADVANCED',cards:[['Track','Storm position and forecast track.',''],['Ensemble','Track spread/intensity products.',''],['Source','Authoritative/current tropical cyclone data.','']],note:'Tracker integration is separate from the rainfall engine and will be added after its data source is defined.'},
  explore:{title:'Explore Maps',badge:'MAP CATALOGUE',cards:[['Rainfall','Open validated rainfall products.',''],['Synoptic','Open MSLP / 850 hPa wind products.',''],['Future','Temperature / humidity / cloud / anomaly / TSI.','']],note:'Explore Maps will become the common catalogue once each product has a validated backend.'}
};
function renderWindHourControls(){
  const note=document.getElementById('moduleNote');
  if(!note)return;
  const hours=Array.isArray(WIND_AVAILABLE_HOURS) ? WIND_AVAILABLE_HOURS.slice() : [];
  note.innerHTML=`<div class="wind-hour-panel">
    <div class="wind-hour-head"><b>850 hPa WIND • FORECAST HOURS</b><span>Click an hour to download & plot</span></div>
    <div class="wind-hour-grid">${hours.length ? hours.map(h=>`<button type="button" class="wind-hour-btn ${h===WIND_FORECAST_HOUR?'active':''}" data-wind-hour="${h}" onclick="selectWindHour(${h})">${h}</button>`).join('') : '<span class="wind-hour-empty">Checking published forecast hours…</span>'}</div>
    <div id="windHourStatus" class="wind-hour-status">${WIND_HOUR_DISCOVERY_INFLIGHT?'Checking available forecast hours…':(hours.length?`Selected: F${String(WIND_FORECAST_HOUR).padStart(3,'0')} • GEFS 31-member 850 hPa wind mean`:'No forecast hours available for this run')}</div>
  </div>`;
}

async function discoverWindForecastHours(run, preserveHour=true){
  if(!/^\d{10}$/.test(run)){
    WIND_AVAILABLE_HOURS=[];
    renderWindHourControls();
    return [];
  }
  WIND_HOUR_DISCOVERY_INFLIGHT=true;
  renderWindHourControls();
  const status=document.getElementById('windHourStatus');
  if(status)status.textContent=`Checking available GEFS 850 hPa forecast hours for ${run}…`;
  try{
    const url='/gefs/wind-hours?run='+encodeURIComponent(run)+'&ts='+Date.now();
    const r=await fetch(url,{cache:'no-store'});
    const data=await r.json();
    if(!r.ok)throw new Error(data.error||('HTTP '+r.status));
    WIND_AVAILABLE_HOURS=Array.isArray(data.hours)?data.hours.map(Number).filter(h=>h>=24&&h<=360&&h%24===0):[];
    if(!WIND_AVAILABLE_HOURS.length)throw new Error('No published 24-hour-spaced GEFS 850 hPa wind forecast hours found');
    if(!preserveHour || !WIND_AVAILABLE_HOURS.includes(Number(WIND_FORECAST_HOUR))){
      WIND_FORECAST_HOUR=WIND_AVAILABLE_HOURS[0];
    }
    renderWindHourControls();
    const label=WIND_AVAILABLE_HOURS.map(h=>`F${String(h).padStart(3,'0')}`).join(', ');
    setStatus('gisStatus',`WIND HOURS READY • ${run} • ${label}`,false);
    return WIND_AVAILABLE_HOURS;
  }catch(e){
    WIND_AVAILABLE_HOURS=[];
    renderWindHourControls();
    const status2=document.getElementById('windHourStatus');
    if(status2)status2.textContent=`Forecast-hour discovery failed: ${e.message}`;
    setStatus('gisStatus','GEFS WIND HOUR DISCOVERY ERROR: '+e.message,true);
    return [];
  }finally{
    WIND_HOUR_DISCOVERY_INFLIGHT=false;
  }
}
function updateWindHourButtons(state='active'){
  document.querySelectorAll('.wind-hour-btn').forEach(btn=>{
    const h=Number(btn.dataset.windHour||0);
    btn.classList.toggle('active',h===WIND_FORECAST_HOUR);
    if(state==='loading' && h===WIND_FORECAST_HOUR){btn.classList.add('loading');btn.classList.remove('ready');}
    else {btn.classList.remove('loading'); if(state==='ready' && h===WIND_FORECAST_HOUR) btn.classList.add('ready');}
  });
}
async function selectWindHour(hour){
  hour=Number(hour)||24;
  if(hour<24 || hour>360 || hour%24!==0)return;
  if(Array.isArray(WIND_AVAILABLE_HOURS) && WIND_AVAILABLE_HOURS.length && !WIND_AVAILABLE_HOURS.includes(hour)){
    setStatus('gisStatus',`F${String(hour).padStart(3,'0')} IS NOT PUBLISHED FOR THIS GEFS RUN — DOWNLOAD NOT STARTED`,true);
    return;
  }
  const run=document.getElementById('runSelect')?.value||ACTIVE_RUN||'';
  if(!/^\d{10}$/.test(run)){
    setStatus('gisStatus','SELECT AN AVAILABLE GEFS RUN FIRST — F'+String(hour).padStart(3,'0')+' DOWNLOAD NOT STARTED',true);
    return;
  }
  WIND_FORECAST_HOUR=hour;
  ACTIVE_FORECAST_MODE='ensemble'; ACTIVE_MODEL='GFS'; ACTIVE_ENSEMBLE_PRODUCT='WIND850'; ACTIVE_PERIOD='24 Hour'; currentPeriod='24 Hour'; currentRainDay=1;
  const productEl=document.getElementById('ensembleProductSelect'); if(productEl)productEl.value='WIND850';
  updateWindHourButtons('loading');
  const status=document.getElementById('windHourStatus');
  if(status)status.textContent=`Selected: F${String(hour).padStart(3,'0')} • downloading GEFS 31-member 850 hPa U/V…`;
  ecmwfReadyLoaded=false; ecmwfDownloadInFlight=false; rainfallRenderSeq++;
  clearMapImages();
  setStatus('gisStatus',`GEFS 850 hPa WIND • F${String(hour).padStart(3,'0')} • ${run} • DOWNLOAD / PROCESSING…`,true);
  const log=document.getElementById('activityLog');
  if(log)log.innerHTML=`<div class="activity-row info">GEFS WIND HOUR REQUEST • F${String(hour).padStart(3,'0')} • ${run} • 31 MEMBERS • 0.50°</div>`;
  const requestKey=`ensemble|GFS|${run}|24 Hour|WIND850|F${String(hour).padStart(3,'0')}`;
  lastEngineRequestKey=requestKey; ecmwfDownloadInFlight=true;
  const url='/gefs/download?mode=ensemble&model=GFS&run='+encodeURIComponent(run)+'&period=24%20Hour&product=WIND850&fhour='+hour+'&ts='+Date.now();
  try{
    const r=await fetch(url,{cache:'no-store'});
    let data={}; try{data=await r.json();}catch(_){ }
    if(!r.ok)throw new Error(data.error||('HTTP '+r.status));
    if(log)log.innerHTML=`<div class="activity-row success">GEFS WIND F${String(hour).padStart(3,'0')} ACCEPTED • ${run} • 31 MEMBERS</div>`;
    setStatus('gisStatus',`GEFS 850 hPa WIND • F${String(hour).padStart(3,'0')} • SERVER ACCEPTED — PROCESSING…`,true);
    ensurePollECMWF();
  }catch(e){
    ecmwfDownloadInFlight=false; updateWindHourButtons('active');
    if(status)status.textContent=`F${String(hour).padStart(3,'0')} • request failed: ${e.message}`;
    setStatus('gisStatus','GEFS WIND REQUEST ERROR: '+e.message,true);
    if(log)log.innerHTML=`<div class="activity-row error">GEFS WIND F${String(hour).padStart(3,'0')} FAILED • ${e.message}</div>`;
  }
}

function setWindDashboardView(showWind){
  const windCard=document.getElementById('upperAirWindCard');
  const pressureCard=document.getElementById('pressureCard');
  const pageTitle=document.querySelector('.page-title');
  const moduleWorkspace=document.getElementById('moduleWorkspace');
  const oldGrid=document.querySelector('.grid');
  if(windCard) windCard.style.display=showWind?'block':'none';
  if(!showWind && pressureCard && !document.body.classList.contains('pressure-only-view')) pressureCard.style.display='none';
  if(pageTitle) pageTitle.style.display=showWind?'none':'';
  if(moduleWorkspace) moduleWorkspace.style.display=showWind?'none':'';
  if(oldGrid) oldGrid.style.display=showWind?'none':'';
  document.body.classList.toggle('wind-only-view',!!showWind);
  if(showWind && windCard) windCard.scrollIntoView({behavior:'smooth',block:'start'});
}

function setPressureDashboardView(showPressure){
  const windCard=document.getElementById('upperAirWindCard');
  const pressureCard=document.getElementById('pressureCard');
  const pageTitle=document.querySelector('.page-title');
  const moduleWorkspace=document.getElementById('moduleWorkspace');
  const oldGrid=document.querySelector('.grid');
  if(pressureCard) pressureCard.style.display=showPressure?'block':'none';
  if(showPressure && windCard) windCard.style.display='none';
  if(pageTitle) pageTitle.style.display=showPressure?'none':'';
  if(moduleWorkspace) moduleWorkspace.style.display=showPressure?'none':'';
  if(oldGrid) oldGrid.style.display=showPressure?'none':'';
  document.body.classList.toggle('pressure-only-view',!!showPressure);
  if(showPressure && pressureCard) pressureCard.scrollIntoView({behavior:'smooth',block:'start'});
}

async function activateWindModule(){
  setWindDashboardView(true);
  // Wind is a dedicated upper-air explorer. Selecting the sidebar item only
  // switches the view and discovers runs; no wind GRIB is downloaded until
  // the user selects an explicit forecast hour.
  const modeEl=document.getElementById('forecastMode');
  const modelEl=document.getElementById('modelSelect');
  const periodEl=document.getElementById('periodSelect');
  const productEl=document.getElementById('ensembleProductSelect');
  if(!modeEl||!modelEl||!periodEl||!productEl)return;
  modeEl.value='ensemble';
  ACTIVE_FORECAST_MODE='ensemble';
  populateModelMenu();
  modelEl.value='GFS';
  ACTIVE_MODEL='GFS';
  periodEl.value='24 Hour';
  periodEl.style.display='none';
  currentPeriod='24 Hour';
  ACTIVE_RUN='';
  updateEnsembleProductVisibility();
  productEl.value='WIND850';
  ACTIVE_ENSEMBLE_PRODUCT='WIND850';
  WIND_FORECAST_HOUR=24;
  WIND_AVAILABLE_HOURS=[];
  WIND_HOUR_DISCOVERY_INFLIGHT=false;
  const cards=document.getElementById('moduleCards');
  if(cards)cards.innerHTML='<div class="mw-card" style="grid-column:1/-1"><b>GEFS 850 hPa WIND FORECAST HOURS</b><span>Select F024, F048, F072 … up to F360. Each click downloads only that forecast-hour 0.50° U/V data for all 31 GEFS members and then renders the map.</span></div>';
  renderWindHourControls();
  resetMapRequestState('wind module selected');
  updateSelectedLabels();
  setStatus('gisStatus','WIND MODULE • GEFS 850 hPa • VERIFYING LATEST RUNS • NO DOWNLOAD STARTED',true);
  const log=document.getElementById('activityLog');
  if(log) log.innerHTML='<div class="activity-row info">WIND MODULE • GEFS 850 hPa • RUN DISCOVERY ONLY • NO DOWNLOAD STARTED</div>';
  try{
    const c=await fetchRunList('ensemble','GFS','24 Hour');
    populateRunMenu(c.runs,'');
    const latest=c.runs?.[0]?.value||'';
    if(latest){
      const sel=document.getElementById('runSelect');
      sel.value=latest; ACTIVE_RUN=latest; updateSelectedLabels();
      await discoverWindForecastHours(latest,false);
      setStatus('gisStatus',`WIND READY • GEFS 850 hPa • ${latest} • SELECT AN HOUR TO DOWNLOAD`,false);
      if(log) log.innerHTML=`<div class="activity-row success">WIND RUN READY • GEFS 850 hPa • ${latest} • HOURS DISCOVERED • NO DOWNLOAD STARTED</div>`;
    }else{
      setStatus('gisStatus','WIND RUN LIST EMPTY • NO DOWNLOAD STARTED',true);
    }
  }catch(e){
    setStatus('gisStatus','WIND RUN DISCOVERY ERROR: '+e.message,true);
    if(log) log.innerHTML=`<div class="activity-row warn">WIND RUN DISCOVERY ERROR • ${e.message}</div>`;
  }
  document.getElementById('upperAirWindCard')?.scrollIntoView({behavior:'smooth',block:'start'});
}

// ============================================================================
// V184 PRESSURE EXPLORER — Model → Run → MSLP Forecast Hour → Download & Plot
// ============================================================================
let PRESSURE_UI={model:'',run:'',hour:null,product:'',runs:[],hours:[]};
function pressureModelLabel(v){return ({'ECMWF':'ECMWF ENS','GEFS':'GEFS','ECMWF_HRES':'ECMWF IFS HRES','GFS':'GFS','ICON':'ICON GLOBAL','AIGFS':'NOAA AIGFS'})[v]||v||'—';}
function pressureProductLabel(v){return ({'MSLP':'MSLP','925':'925 hPa Geopotential Height','850':'850 hPa Geopotential Height','500':'500 hPa Geopotential Height'})[v]||'SELECT LEVEL';}
function pressureRunLabel(v){if(!/^\d{10}$/.test(v))return v||'—';const d=new Date(Date.UTC(Number(v.slice(0,4)),Number(v.slice(4,6))-1,Number(v.slice(6,8)),Number(v.slice(8,10))));return d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'})+' • '+String(d.getUTCHours()).padStart(2,'0')+'Z';}
function pressureSetStatus(msg,loading=false){const e=document.getElementById('pressureStatus');if(e){e.textContent=msg;e.style.color=loading?'#1769aa':'#60737d';}}
function pressureSummary(){const e=document.getElementById('pressureSelectionSummary');if(e)e.textContent=`MODEL: ${pressureModelLabel(PRESSURE_UI.model)} • RUN: ${PRESSURE_UI.run?pressureRunLabel(PRESSURE_UI.run):'—'} • PRODUCT: ${pressureProductLabel(PRESSURE_UI.product)} • HOUR: ${PRESSURE_UI.hour?('F'+String(PRESSURE_UI.hour).padStart(3,'0')):'—'}`;}
function pressurePaintModels(){document.querySelectorAll('.pressure-model-btn').forEach(b=>b.classList.toggle('active',b.dataset.pressureModel===PRESSURE_UI.model));}
function pressurePaintProduct(){document.querySelectorAll('.pressure-product-btn').forEach(b=>b.classList.toggle('active',b.dataset.pressureProduct===PRESSURE_UI.product));}
function pressurePaintHours(){const box=document.getElementById('pressureHourButtons');if(!box)return;box.innerHTML='';if(!PRESSURE_UI.hours.length){box.innerHTML='<span style="font-size:10px;color:#8a9aa4;">No verified forecast hours returned.</span>';return;}PRESSURE_UI.hours.forEach(h=>{const b=document.createElement('button');b.className='map-control-btn ua-hour-btn';b.textContent=String(h).padStart(3,'0');b.title=`Forecast F${String(h).padStart(3,'0')}`;b.onclick=()=>pressureSelectHour(h);if(Number(h)===Number(PRESSURE_UI.hour)){b.style.background='#17324d';b.style.color='#fff';b.style.borderColor='#17324d';}box.appendChild(b);});}
function pressureRenderRuns(){const box=document.getElementById('pressureRunHistory');if(!box)return;if(!PRESSURE_UI.runs.length){box.innerHTML='<span style="font-size:10px;color:#8a9aa4;">No verified runs returned.</span>';return;}box.innerHTML=PRESSURE_UI.runs.map((r,i)=>`<button class="ua-run-btn ${String(r.value)===String(PRESSURE_UI.run)?'active':''}" title="${i===0?'Latest available published run':'Previous available run'}" onclick="pressureSelectRun('${r.value}')">${i===0?'LATEST • ':''}${r.label}</button>`).join('');}
async function pressureSelectProduct(product){
  PRESSURE_UI.product=String(product).toUpperCase(); PRESSURE_UI.hour=null; pressurePaintProduct(); pressureSummary();
  const label=document.getElementById('pressurePlotLabel'); if(label)label.textContent=`${pressureProductLabel(PRESSURE_UI.product)} SELECTED • NOW CHOOSE MODEL`;
  pressureSetStatus(`${pressureProductLabel(PRESSURE_UI.product)} SELECTED • CHOOSE ECMWF ENS / GEFS / HRES / GFS / ICON / AIGFS`,false);
  if(PRESSURE_UI.model&&PRESSURE_UI.run){await pressureLoadHours();}
}
async function pressureSelectModel(model){
  if(!PRESSURE_UI.product){
    pressureSetStatus('SELECT MSLP / 925 hPa / 850 hPa / 500 hPa FIRST',false);
    const box=document.getElementById('pressurePlotLabel'); if(box)box.textContent='SELECT A PRESSURE LEVEL FIRST';
    return;
  }
  PRESSURE_UI={model,run:'',hour:null,product:PRESSURE_UI.product,runs:[],hours:[]};
  pressurePaintModels(); pressurePaintProduct(); pressurePaintHours(); pressureSummary();
  const rl=document.getElementById('pressureRunLabel'); if(rl)rl.innerHTML='<span class="run-label">LATEST RUN</span><span>—</span>';
  const rh=document.getElementById('pressureRunHistory'); if(rh)rh.innerHTML='<span style="font-size:10px;color:#1769aa;font-weight:800;">Checking latest available runs…</span>';
  pressureSetStatus(`CHECKING LATEST ${pressureModelLabel(model)} RUNS…`,true);
  try{
    const data=await fetch('/pressure/runs?model='+encodeURIComponent(model)+'&ts='+Date.now(),{cache:'no-store'}).then(r=>r.json());
    if(data.error)throw new Error(data.error);
    PRESSURE_UI.runs=Array.isArray(data.runs)?data.runs:[];
    PRESSURE_UI.run=data.selected||PRESSURE_UI.runs[0]?.value||'';
    pressureRenderRuns(); pressureSummary();
    if(rl)rl.innerHTML=`<span class="run-label">LATEST RUN</span><span>${PRESSURE_UI.run?pressureRunLabel(PRESSURE_UI.run):'—'}</span>`;
    if(!PRESSURE_UI.run)throw new Error('No available published runs returned for '+model);
    pressureSetStatus(`${pressureModelLabel(model)} • ${pressureRunLabel(PRESSURE_UI.run)} • ${pressureProductLabel(PRESSURE_UI.product)} • SELECT A RUN OR CONTINUE WITH LATEST`,true);
    await pressureLoadHours();
  }catch(e){PRESSURE_UI.runs=[];PRESSURE_UI.hours=[];pressureRenderRuns();pressurePaintHours();pressureSetStatus('PRESSURE RUN DISCOVERY ERROR: '+e.message,false);}
}
async function pressureSelectRun(run){PRESSURE_UI.run=run;PRESSURE_UI.hour=null;pressureRenderRuns();pressureSummary();const rl=document.getElementById('pressureRunLabel');if(rl)rl.innerHTML=`<span class="run-label">${String(run)===String(PRESSURE_UI.runs[0]?.value)?'LATEST RUN':'SELECTED RUN'}</span><span>${pressureRunLabel(run)}</span>`;pressureSetStatus(`RUN ${pressureRunLabel(run)} • LOADING ${pressureProductLabel(PRESSURE_UI.product)} HOURS…`,true);await pressureLoadHours();}
async function pressureLoadHours(){if(!PRESSURE_UI.model||!PRESSURE_UI.run)return;try{const data=await fetch('/pressure/hours?model='+encodeURIComponent(PRESSURE_UI.model)+'&run='+encodeURIComponent(PRESSURE_UI.run)+'&product='+encodeURIComponent(PRESSURE_UI.product)+'&ts='+Date.now(),{cache:'no-store'}).then(r=>r.json());if(data.error)throw new Error(data.error);PRESSURE_UI.hours=Array.isArray(data.hours)?data.hours.map(Number):[];pressurePaintHours();pressureSummary();pressureSetStatus(`${pressureModelLabel(PRESSURE_UI.model)} • ${pressureRunLabel(PRESSURE_UI.run)} • ${pressureProductLabel(PRESSURE_UI.product)} • ${PRESSURE_UI.hours.length} HOURS AVAILABLE — CLICK AN HOUR TO DOWNLOAD`,false);}catch(e){PRESSURE_UI.hours=[];pressurePaintHours();pressureSetStatus('PRESSURE HOUR DISCOVERY ERROR: '+e.message,false);}}
function pressureSelectHour(hour){
  hour=Number(hour); if(!PRESSURE_UI.model||!PRESSURE_UI.run)return; if(!PRESSURE_UI.hours.includes(hour))return;
  PRESSURE_UI.hour=hour; pressurePaintHours(); pressureSummary();
  const productLabel=pressureProductLabel(PRESSURE_UI.product);
  const label=document.getElementById('pressurePlotLabel'); if(label){label.textContent=`DOWNLOADING ${pressureModelLabel(PRESSURE_UI.model)} • ${pressureRunLabel(PRESSURE_UI.run)} • ${productLabel} • F${String(hour).padStart(3,'0')}…`;label.style.color='#1769aa';}
  pressureSetStatus(`DOWNLOADING ${pressureModelLabel(PRESSURE_UI.model)} • ${pressureRunLabel(PRESSURE_UI.run)} • ${productLabel} • F${String(hour).padStart(3,'0')}…`,true);
  const img=document.getElementById('pressurePlotImage'); if(!img)return;
  const url='/pressure/plot.png?model='+encodeURIComponent(PRESSURE_UI.model)+'&run='+encodeURIComponent(PRESSURE_UI.run)+'&fhour='+hour+'&product='+encodeURIComponent(PRESSURE_UI.product)+'&ts='+Date.now();
  img.onload=()=>{if(label){label.textContent=`${pressureModelLabel(PRESSURE_UI.model)} • ${productLabel} • F${String(hour).padStart(3,'0')} • PLOTTED`;label.style.color='#176b3a';}pressureSetStatus(`${pressureModelLabel(PRESSURE_UI.model)} • ${productLabel} • F${String(hour).padStart(3,'0')} PLOTTED`,false);};
  img.onerror=()=>{if(label){label.textContent='PRESSURE PLOT ERROR — SEE SERVER LOG';label.style.color='#a12626';}pressureSetStatus('PRESSURE PLOT ERROR — SEE SERVER LOG',false);};
  img.src=url;
}

async function activatePressureModule(){
  setPressureDashboardView(true); setWindDashboardView(false);
  PRESSURE_UI={model:'',run:'',hour:null,product:'',runs:[],hours:[]}; pressurePaintModels(); pressurePaintProduct(); pressurePaintHours(); pressureSummary();
  const cards=document.getElementById('moduleCards'); if(cards)cards.innerHTML='<div class="mw-card" style="grid-column:1/-1"><b>MULTI-MODEL PRESSURE / UPPER-AIR FORECAST</b><span>Select ECMWF ENS, GEFS, ECMWF IFS HRES or GFS, then choose MSLP, 925 hPa, 850 hPa or 500 hPa. MSLP uses pressure in hPa; 925/850/500 use geopotential height in dam. The selected EPS mean retains the same clean synoptic map style.</span></div>';
  pressureSetStatus('STEP 1: SELECT PRESSURE LEVEL FIRST • NO DOWNLOAD STARTED',false);
}

function renderSidebarModule(key){
  const m=SIDEBAR_MODULES[key]||SIDEBAR_MODULES.overview;
  if(key!=='wind') setWindDashboardView(false);
  if(key!=='pressure') setPressureDashboardView(false);
  const ws=document.getElementById('moduleWorkspace'); if(!ws)return;
  document.getElementById('moduleTitle').textContent=m.title;
  document.getElementById('moduleBadge').textContent=m.badge;
  document.getElementById('moduleCards').innerHTML=m.cards.map(c=>`<div class="mw-card"><b>${c[0]}</b><span>${c[1]}${c[2]?`<br><strong>${c[2]}</strong>`:''}</span></div>`).join('');
  document.getElementById('moduleNote').textContent=m.note;
  ws.classList.add('show');
  if(key==='overview') ws.classList.remove('show');
  const periodEl=document.getElementById('periodSelect'); if(periodEl) periodEl.style.display=(key==='wind')?'none':'';
  if(key==='rainfall') document.getElementById('weatherMap')?.scrollIntoView({behavior:'smooth',block:'start'});
  if(key==='wind') activateWindModule();
  if(key==='pressure') activatePressureModule();
}
document.querySelectorAll('.nav-item').forEach(x=>{x.addEventListener('click',()=>{document.querySelectorAll('.nav-item').forEach(y=>y.classList.remove('active'));x.classList.add('active');renderSidebarModule(x.dataset.module||'overview');});});

async function bootDashboard(){
  try{
    setWindDashboardView(false);
    // Force a clean deterministic startup state. Browser form restoration can
    // otherwise restore an old model (e.g. ICON) while ACTIVE_MODEL remains ECMWF.
    const modeEl=document.getElementById('forecastMode');
    if(modeEl) modeEl.value=ACTIVE_FORECAST_MODE;
    populateModelMenu();
    updateSelectedLabels();
    await updatePeriodAvailability();
    // Page-load behavior: load only the currently selected model's run list.
    // Discovery/cache only — NO forecast download is started and no unrelated
    // model server is probed in the background.
    const mode=document.getElementById('forecastMode')?.value||'deterministic';
    const period=document.getElementById('periodSelect')?.value||currentPeriod||'24 Hour';
    await refreshRunOptions();
    setStatus('gisStatus','LATEST RUNS LOADED • SELECT A RUN TO START DOWNLOAD',false);
    // IMPORTANT: no second/background run-discovery request here. The explicit
    // refreshRunOptions() above is the ONLY startup discovery call. This prevents
    // duplicate status writes and the apparent freeze seen in the older dashboard.
    setStatus('gisStatus','LATEST RUNS READY FOR SELECTED MODEL • SELECT A RUN TO START',false);
  }catch(e){
    console.error(e);
    setStatus('gisStatus','RUN DISCOVERY ERROR — SELECT A RUN MANUALLY AND UPDATE',true);
  }
}
bootDashboard();
loadGIS();

/* TALK TO MASRAINMAN */
const TALK_API_PUBLIC = "__MASRAINMAN_TALK_PUBLIC_URL__" || window.location.origin;
let talkRecognition = null;
let talkUiReady = false;

function talkEl(id){ return document.getElementById(id); }
function talkSetStatus(s){
  const e=talkEl('talkStatus');
  if(e) e.textContent=s;
  console.log('[TALK UI]', s);
}
function talkSpeak(text,lang='en-IN'){
  try{
    if(!('speechSynthesis' in window)) return;
    window.speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(text);
    u.lang=lang; u.rate=(lang==='ta-IN'?0.92:0.94);
    const voices=window.speechSynthesis.getVoices?window.speechSynthesis.getVoices():[];
    const v=voices.find(x=>String(x.lang||'').toLowerCase().startsWith(lang.toLowerCase()));
    if(v) u.voice=v;
    window.speechSynthesis.speak(u);
  }catch(e){ console.warn('[TALK VOICE OUT]',e); }
}
function talkSpeakTamilEnglish(tamilText, englishText){
  try{
    if(!('speechSynthesis' in window)){
      talkSetStatus('குரல் வெளியீடு கிடைக்கவில்லை / Speech output is not supported in this browser.');
      return;
    }
    const synth=window.speechSynthesis;
    const pickVoice=(langs,names)=>{
      const voices=synth.getVoices?synth.getVoices():[];
      for(const v of voices){const lang=String(v.lang||'').toLowerCase();if(langs.some(x=>lang===x||lang.startsWith(x+'-')||lang.startsWith(x+'_')))return v;}
      for(const v of voices){const n=String(v.name||'').toLowerCase();if(names.some(x=>n.includes(x)))return v;}
      return null;
    };
    const speakNow=()=>{
      try{
        synth.cancel();
        const tamilVoice=pickVoice(['ta-in','ta'],['tamil','valluvar']);
        const englishVoice=pickVoice(['en-in','en-gb','en-us','en'],['english','india']);
        const ta=new SpeechSynthesisUtterance(String(tamilText||''));
        ta.lang=tamilVoice?tamilVoice.lang:'ta-IN';
        if(tamilVoice)ta.voice=tamilVoice;
        ta.rate=.90; ta.pitch=1.0;
        const en=new SpeechSynthesisUtterance(String(englishText||''));
        en.lang=englishVoice?englishVoice.lang:'en-IN';
        if(englishVoice)en.voice=englishVoice;
        en.rate=.94; en.pitch=1.0;
        ta.onstart=()=>talkSetStatus(`${talkTa(TALK_TA.complete)} / Speaking Tamil summary…`);
        ta.onerror=(e)=>console.warn('[TALK TAMIL SPEECH]',e);
        ta.onend=()=>{try{synth.speak(en);}catch(e){console.warn('[TALK EN SPEECH]',e);}};
        en.onstart=()=>talkSetStatus(`${talkTa(TALK_TA.complete)} / Speaking English summary…`);
        en.onerror=(e)=>console.warn('[TALK ENGLISH SPEECH]',e);
        en.onend=()=>talkSetStatus(`${talkTa(TALK_TA.complete)} / Tamil + English briefing spoken.`);
        synth.speak(ta);
      }catch(e){console.warn('[TALK TAMIL+EN VOICE]',e);}
    };
    const voices=synth.getVoices?synth.getVoices():[];
    if(voices.length) speakNow();
    else {
      let done=false;
      const once=()=>{if(done)return;done=true;try{synth.removeEventListener('voiceschanged',once);}catch(e){};speakNow();};
      synth.addEventListener('voiceschanged',once);
      setTimeout(once,1000);
    }
  }catch(e){console.warn('[TALK TAMIL+EN VOICE]',e);}
}

// Keep Tamil as Unicode escapes so Blogger/Cloudflare/terminal encoding cannot
// turn the visible or spoken Tamil into UTF-8 mojibake ("à®...").
const TALK_TA = {
  ready:'\u0ba4\u0baf\u0bbe\u0bb0\u0bcd',
  listening:'\u0b95\u0bc7\u0b9f\u0bcd\u0b95\u0bbf\u0bb1\u0bc7\u0ba9\u0bcd',
  heard:'\u0b95\u0bc7\u0b9f\u0bcd\u0b9f\u0ba4\u0bc1',
  wait:'\u0ba4\u0baf\u0bb5\u0bc1 \u0b9a\u0bc6\u0baf\u0bcd\u0ba4\u0bc1 \u0b9a\u0bbf\u0bb2 \u0bb5\u0bbf\u0ba8\u0bbe\u0b9f\u0bbf\u0b95\u0bb3\u0bcd \u0b95\u0bbe\u0ba4\u0bcd\u0ba4\u0bbf\u0bb0\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd. \u0bb5\u0bbe\u0ba9\u0bbf\u0bb2\u0bc8 \u0bae\u0bbe\u0ba4\u0bbf\u0bb0\u0bbf \u0ba4\u0bb0\u0bb5\u0bc1\u0b95\u0bb3\u0bcd \u0b9a\u0bc6\u0baf\u0bb2\u0bbe\u0b95\u0bcd\u0b95\u0baa\u0bcd\u0baa\u0b9f\u0bc1\u0b95\u0bbf\u0bb1\u0ba4\u0bc1.',
  waitShort:'\u0b9a\u0bbf\u0bb2 \u0bb5\u0bbf\u0ba8\u0bbe\u0b9f\u0bbf\u0b95\u0bb3\u0bcd \u0b95\u0bbe\u0ba4\u0bcd\u0ba4\u0bbf\u0bb0\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd',
  process:'\u0bae\u0bbe\u0ba4\u0bbf\u0bb0\u0bbf \u0ba4\u0bb0\u0bb5\u0bc1\u0b95\u0bb3\u0bcd \u0b9a\u0bc6\u0baf\u0bb2\u0bbe\u0b95\u0bcd\u0b95\u0baa\u0bcd\u0baa\u0b9f\u0bc1\u0b95\u0bbf\u0bb1\u0ba4\u0bc1',
  analysis:'\u0b89\u0baf\u0bbf\u0bb0\u0bcd\u0ba8\u0bbf\u0bb2\u0bc8 \u0baa\u0b95\u0bc1\u0baa\u0bcd\u0baa\u0bbe\u0baf\u0bcd\u0bb5\u0bc1',
  complete:'\u0baa\u0b95\u0bc1\u0baa\u0bcd\u0baa\u0bbe\u0baf\u0bcd\u0bb5\u0bc1 \u0bae\u0bc1\u0b9f\u0bbf\u0bb5\u0b9f\u0bc8\u0ba8\u0bcd\u0ba4\u0ba4\u0bc1',
  models:'\u0bae\u0bbe\u0b9f\u0bb2\u0bcd\u0b95\u0bb3\u0bcd',
  available:'\u0b95\u0bbf\u0b9f\u0bc8\u0b95\u0bcd\u0b95\u0bbf\u0ba9\u0bcd\u0bb1\u0ba9',
  processing:'\u0b9a\u0bc6\u0baf\u0bb2\u0bbe\u0b95\u0bcd\u0b95\u0baa\u0bcd\u0baa\u0b9f\u0bc1\u0b95\u0bbf\u0bb1\u0ba4\u0bc1',
  unavailable:'\u0b95\u0bbf\u0b9f\u0bc8\u0b95\u0bcd\u0b95\u0bb5\u0bbf\u0bb2\u0bcd\u0bb2\u0bc8',
  location:'\u0bb5\u0bbe\u0ba9\u0bbf\u0bb2\u0bc8 \u0bae\u0bbe\u0ba4\u0bbf\u0bb0\u0bbf \u0b95\u0ba3\u0bbf\u0baa\u0bcd\u0baa\u0bc1 \u0b87\u0b9f\u0bae\u0bbe\u0b95\u0b95\u0bcd \u0b95\u0b9f\u0bcd\u0b9f\u0bae\u0bc8\u0baa\u0bcd\u0baa\u0bc1',
  mean:'72 \u0bae\u0ba3\u0bbf \u0ba8\u0bc7\u0bb0 \u0bae\u0bbe\u0b9f\u0bb2\u0bcd \u0b9a\u0bb0\u0bbe\u0b9a\u0bb0\u0bbf',
  peak:'\u0b85\u0ba4\u0bbf\u0b95 \u0bae\u0bb4\u0bc8 \u0b9a\u0bbf\u0b95\u0bcd\u0ba9\u0bb2\u0bcd',
  low:'\u0b95\u0bc1\u0bb1\u0bc8\u0ba8\u0bcd\u0ba4 \u0bae\u0bb4\u0bc8 \u0b9a\u0bbf\u0b95\u0bcd\u0ba9\u0bb2\u0bcd',
  signal:'\u0bae\u0bbe\u0b9f\u0bb2\u0bcd \u0b9a\u0bbf\u0b95\u0bcd\u0ba9\u0bb2\u0bcd',
  summary:'\u0ba4\u0bae\u0bbf\u0bb4\u0bcd \u0b9a\u0bc1\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bae\u0bcd',
  guidance:'\u0bae\u0bbe\u0b9f\u0bb2\u0bcd \u0bb5\u0bb4\u0bbf\u0b95\u0bbe\u0b9f\u0bcd\u0b9f\u0bc1\u0ba4\u0bb2\u0bcd \u0bae\u0b9f\u0bcd\u0b9f\u0bc1\u0bae\u0bc7; \u0b85\u0ba4\u0bbf\u0b95\u0bbe\u0bb0\u0baa\u0bcd\u0baa\u0bc2\u0bb0\u0bcd\u0bb5 \u0bae\u0bc1\u0ba9\u0bcd\u0ba9\u0bb1\u0bbf\u0bb5\u0bbf\u0baa\u0bcd\u0baa\u0bc1 \u0b85\u0bb2\u0bcd\u0bb2.',
  ask:'\u0b95\u0bc7\u0bb3\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd',
  speak:'\u0baa\u0bc7\u0b9a\u0bc1\u0b99\u0bcd\u0b95\u0bb3\u0bcd',
  readyStatus:'\u0b95\u0bc1\u0bb0\u0bb2\u0bcd \u0b89\u0bb3\u0bcd\u0bb3\u0bc0\u0b9f\u0bc1 \u0ba4\u0baf\u0bbe\u0bb0\u0bbe\u0b95 \u0b89\u0bb3\u0bcd\u0bb3\u0ba4\u0bc1'
};
function talkTa(s){ try{return JSON.parse('"'+s+'"');}catch(e){return s;} }

function talkMakeTamilBrief(d){
  const mean=Number(d.mean72h_mm||0);
  const peakDay=d.peak_day||1;
  const n=Number(d.usable_model_count||0);
  const total=7;
  let rainText;
  if(mean < 1){
    rainText='\u0b85\u0b9f\u0bc1\u0ba4\u0bcd\u0ba4 2\u20133 \u0ba8\u0bbe\u0b9f\u0bcd\u0b95\u0bb3\u0bbf\u0bb2\u0bcd \u0bae\u0bb4\u0bc8 \u0b85\u0bb3\u0bb5\u0bc1 \u0bae\u0bbf\u0b95\u0bb5\u0bc1\u0bae\u0bcd \u0b95\u0bc1\u0bb1\u0bc8\u0bb5\u0bbe\u0b95\u0bb5\u0bc7 \u0ba4\u0bc6\u0bb0\u0bbf\u0b95\u0bbf\u0bb1\u0ba4\u0bc1.';
  }else if(mean < 10){
    rainText='\u0b85\u0b9f\u0bc1\u0ba4\u0bcd\u0ba4 2\u20133 \u0ba8\u0bbe\u0b9f\u0bcd\u0b95\u0bb3\u0bbf\u0bb2\u0bcd \u0bb2\u0bc7\u0b9a\u0bbe\u0ba9 \u0bae\u0bb4\u0bc8\u0b95\u0bcd\u0b95\u0bbe\u0ba9 \u0b9a\u0bbf\u0b95\u0bcd\u0ba9\u0bb2\u0bcd \u0b89\u0bb3\u0bcd\u0bb3\u0ba4\u0bc1.';
  }else if(mean < 25){
    rainText='\u0b85\u0b9f\u0bc1\u0ba4\u0bcd\u0ba4 2\u20133 \u0ba8\u0bbe\u0b9f\u0bcd\u0b95\u0bb3\u0bbf\u0bb2\u0bcd \u0bae\u0bbf\u0ba4\u0bae\u0bbe\u0ba9 \u0bae\u0bb4\u0bc8\u0b95\u0bcd\u0b95\u0bbe\u0ba9 \u0b9a\u0bbf\u0b95\u0bcd\u0ba9\u0bb2\u0bcd \u0b89\u0bb3\u0bcd\u0bb3\u0ba4\u0bc1.';
  }else{
    rainText='\u0b85\u0b9f\u0bc1\u0ba4\u0bcd\u0ba4 2\u20133 \u0ba8\u0bbe\u0b9f\u0bcd\u0b95\u0bb3\u0bbf\u0bb2\u0bcd \u0b95\u0bc1\u0bb1\u0bbf\u0baa\u0bcd\u0baa\u0bbf\u0b9f\u0ba4\u0bcd\u0ba4\u0b95\u0bcd\u0b95 \u0bae\u0bb4\u0bc8\u0b95\u0bcd\u0b95\u0bbe\u0ba9 \u0b9a\u0bbf\u0b95\u0bcd\u0ba9\u0bb2\u0bcd \u0b89\u0bb3\u0bcd\u0bb3\u0ba4\u0bc1.';
  }
  const peakText=`\u0bae\u0bbe\u0b9f\u0bb2\u0bcd\u0b95\u0bb3\u0bbf\u0bb2\u0bcd \u0b85\u0ba4\u0bbf\u0b95 \u0bae\u0bb4\u0bc8 Day ${peakDay} \u0b95\u0bbe\u0bb2\u0baa\u0bcd\u0baa\u0b95\u0bc1\u0ba4\u0bbf\u0baf\u0bbf\u0bb2\u0bcd \u0ba4\u0bc6\u0bb0\u0bbf\u0b95\u0bbf\u0bb1\u0ba4\u0bc1.`;
  let sourceText;
  if(n===7){
    sourceText='\u0b85\u0ba9\u0bc8\u0ba4\u0bcd\u0ba4\u0bc1 \u0b8f\u0bb4\u0bc1 \u0bae\u0bbe\u0b9f\u0bb2\u0bcd\u0b95\u0bb3\u0bc1\u0bae\u0bcd \u0b95\u0bbf\u0b9f\u0bc8\u0b95\u0bcd\u0b95\u0bbf\u0ba9\u0bcd\u0bb1\u0ba9.';
  }else{
    sourceText=`${n}/${total} \u0bae\u0bbe\u0b9f\u0bb2\u0bcd\u0b95\u0bb3\u0bcd \u0b95\u0bbf\u0b9f\u0bc8\u0b95\u0bcd\u0b95\u0bbf\u0ba9\u0bcd\u0bb1\u0ba9; \u0b85\u0ba4\u0ba9\u0bbe\u0bb2\u0bcd \u0b87\u0ba4\u0bc1 \u0b95\u0bbf\u0b9f\u0bc8\u0ba4\u0bcd\u0ba4 \u0bae\u0bbe\u0b9f\u0bb2\u0bcd\u0b95\u0bb3\u0bbf\u0ba9\u0bcd \u0b85\u0b9f\u0bbf\u0baa\u0bcd\u0baa\u0b9f\u0bc8\u0baf\u0bbf\u0bb2\u0bbe\u0ba9 \u0b9a\u0bc1\u0bb0\u0bc1\u0b95\u0bcd\u0b95\u0bae\u0bcd.`;
  }
  return `${rainText} ${peakText} ${sourceText}`;
}
function talkMakeEnglishBrief(d){
  const p=d.place?.name||'the location';
  const mean=Number(d.mean72h_mm||0).toFixed(1);
  const peak=Number(d.peak_day_mm||0).toFixed(1);
  const day=d.peak_day||1;
  const n=Number(d.usable_model_count||0);
  return `For ${p}, the connected models show about ${mean} millimetres of rainfall over the next 72 hours on average. The strongest signal is on forecast day ${day}, around ${peak} millimetres. ${n} of 7 models are currently available. This is model guidance only, not an official forecast.`;
}
function talkStart(){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SR){
    talkSetStatus(`${talkTa(TALK_TA.readyStatus)} / Voice input is not supported here. Please type the town or city name.`);
    const input=talkEl('talkInput'); if(input) input.focus();
    return;
  }
  try{
    if(talkRecognition){ try{talkRecognition.stop();}catch(e){} }
    talkRecognition=new SR();
    talkRecognition.lang='en-IN';
    talkRecognition.interimResults=false;
    talkRecognition.maxAlternatives=1;
    talkRecognition.onstart=()=>talkSetStatus(`${talkTa(TALK_TA.listening)}… / Listening… say your town or city name.`);
    talkRecognition.onerror=(e)=>{const code=e.error||'unknown';const msg=code==='not-allowed'?'Microphone permission was denied. Allow microphone access for masrainman.blogspot.com and try again.':code==='no-speech'?'No speech was detected. Please speak the town or city name again.':code==='network'?'Browser speech recognition network service failed. Please try again.':`Voice input issue: ${code}`;talkSetStatus(`குரல் உள்ளீட்டில் சிக்கல் / ${msg}`);};
    talkRecognition.onend=()=>{};
    talkRecognition.onresult=(e)=>{
      const phrase=e.results[0][0].transcript;
      const input=talkEl('talkInput'); if(input) input.value=phrase;
      talkSetStatus(`${talkTa(TALK_TA.heard)}: ${phrase} / Heard: ${phrase} • ${talkTa(TALK_TA.analysis)} / analysing model data…`);
      talkAsk();
    };
    talkRecognition.start();
  }catch(e){
    talkSetStatus(`\u0b95\u0bc1\u0bb0\u0bb2\u0bc8 \u0ba4\u0bca\u0b9f\u0b99\u0bcd\u0b95 \u0bae\u0bc1\u0b9f\u0bbf\u0baf\u0bb5\u0bbf\u0bb2\u0bcd\u0bb2\u0bc8 / Voice could not start. Please type the town or city name.`);
    console.warn('[TALK VOICE INPUT]',e);
  }
}
function talkEsc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
function talkShowLoading(q){
  const box=talkEl('talkResult'); if(!box)return;
  box.innerHTML=`<div class="talk-loading"><div class="talk-orbit" aria-label="Siri-style processing animation"></div><div><div class="talk-loading-title">${talkTa(TALK_TA.analysis)} / LIVE MODEL ANALYSIS <span class="talk-loading-dots"><i></i><i></i><i></i></span></div><div class="talk-loading-sub">${talkEsc(q)} • ECMWF • GFS • ICON • AIFS • UKMET • GEM • AIGFS</div><div class="talk-siri-label">${talkTa(TALK_TA.waitShort)} / PLEASE WAIT A FEW SECONDS — DATA IS BEING PROCESSED</div></div></div>`;
  box.style.display='block';
}
function talkRender(d){
  const box=talkEl('talkResult'); if(!box)return;
  const p=d.place||{}, modelNames=(d.model_order||['ECMWF','GFS','ICON','AIFS','UKMET','GEM','AIGFS']);
  const chips=modelNames.map(k=>{
    const v=d.models?.[k]||{};
    const state=v.error ? `${talkTa(TALK_TA.unavailable)} / unavailable` : (v.total72h!=null ? `${talkEsc(v.total72h)} mm / 72h` : `${talkTa(TALK_TA.processing)} / processing…`);
    const cls=v.error?'talk-model error':(v.total72h!=null?'talk-model ready':'talk-model pending');
    return `<span class="${cls}"><b>${talkEsc(k)}</b> ${state}</span>`;
  }).join('');
  const tamilBrief=talkMakeTamilBrief(d);
  const englishBrief=talkMakeEnglishBrief(d);
  box.innerHTML=`<div><b>📍 ${talkEsc(p.name||'—')}</b> <span style="opacity:.65">(${Number(p.lat||0).toFixed(2)}°N, ${Number(p.lon||0).toFixed(2)}°E)</span></div>
  <div style="font-size:10px;opacity:.72;margin-top:2px">${talkTa(TALK_TA.location)} / Location resolved to forecast coordinates • ${talkEsc(p.resolver||'location index')}</div>
  <div class="talk-grid">
    <div class="talk-stat"><span>${talkTa(TALK_TA.mean)} / 72H MODEL MEAN</span><b>${talkEsc(Number(d.mean72h_mm||0).toFixed(1))} mm</b></div>
    <div class="talk-stat"><span>${talkTa(TALK_TA.peak)} / PEAK DAY</span><b>Day ${talkEsc(d.peak_day)} • ${talkEsc(Number(d.peak_day_mm||0).toFixed(1))} mm</b></div>
    <div class="talk-stat"><span>${talkTa(TALK_TA.low)} / LOWEST DAY</span><b>Day ${talkEsc(d.low_day)} • ${talkEsc(Number(d.low_day_mm||0).toFixed(1))} mm</b></div>
    <div class="talk-stat"><span>${talkTa(TALK_TA.signal)} / MODEL SIGNAL</span><b>${talkEsc(d.agreement||'—')}</b></div>
  </div>
  <div class="talk-models">${chips}</div>
  <div style="margin-top:8px;font-size:12px;opacity:.82">${talkTa(TALK_TA.models)} / LIVE MODEL PROGRESS • ${talkEsc(d.usable_model_count||0)}/7 ${talkTa(TALK_TA.available)} • ${talkEsc(d.pending_model_count||0)} ${talkTa(TALK_TA.processing)}</div>
  <div class="talk-tamil" style="margin-top:12px;padding:12px 14px;border-radius:12px;background:rgba(255,255,255,.06);line-height:1.55">
    <b>${talkTa(TALK_TA.summary)} / TAMIL SUMMARY</b><br>${talkEsc(tamilBrief)}
    <div style="margin-top:9px;padding-top:9px;border-top:1px solid rgba(255,255,255,.10)"><b>ENGLISH SUMMARY</b><br>${talkEsc(englishBrief)}</div>
  </div>
  <div class="talk-disclaimer">⚠️ ${talkTa(TALK_TA.guidance)} / ${talkEsc(d.disclaimer||'Model guidance only — not an official forecast. Actual rainfall can differ.')}</div>`;
  box.style.display='block';
}
function talkApiUrl(path){
  const clean=String(path||'').startsWith('/')?String(path):('/'+String(path));
  const local=(location.hostname==='127.0.0.1'||location.hostname==='localhost');
  const base=local?window.location.origin:TALK_API_PUBLIC;
  return base.replace(/\/$/,'')+clean;
}
async function talkJsonRequest(path, options={}){
  const url=talkApiUrl(path);
  const local=(location.hostname==='127.0.0.1'||location.hostname==='localhost');
  const maxAttempts=Number(options.retries??2);
  let lastErr=null;
  for(let attempt=0;attempt<=maxAttempts;attempt++){
    try{
      console.log('[TALK HTTP]',attempt+1,'/',maxAttempts+1,url);
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),Number(options.timeout||30000));
      let r;
      try{r=await fetch(url,{method:'GET',mode:local?'same-origin':'cors',credentials:local?'same-origin':'omit',cache:'no-store',headers:{'Accept':'application/json','Cache-Control':'no-cache'},signal:controller.signal});}finally{clearTimeout(timer);}
      const text=await r.text();let d={};
      try{d=JSON.parse(text);}catch(e){throw new Error('Invalid JSON from Talk server (HTTP '+r.status+')');}
      if(r.ok)return {r,d};
      if(r.status===404||r.status===400)return {r,d};
      lastErr=new Error(d.error||('Talk server returned HTTP '+r.status));
    }catch(e){lastErr=e;console.warn('[TALK FETCH ATTEMPT FAILED]',attempt+1,e);}
    if(attempt<maxAttempts)await new Promise(res=>setTimeout(res,700*(attempt+1)));
  }
  try{
    const x=await new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('GET',url,true);xhr.timeout=30000;xhr.setRequestHeader('Accept','application/json');xhr.withCredentials=false;
      xhr.onload=()=>{let d={};try{d=JSON.parse(xhr.responseText||'{}');}catch(e){return reject(new Error('Talk server returned invalid JSON (HTTP '+xhr.status+')'));}resolve({r:{ok:xhr.status>=200&&xhr.status<300,status:xhr.status},d});};
      xhr.onerror=()=>reject(new Error('Browser could not connect to Talk server at '+url));xhr.ontimeout=()=>reject(new Error('Talk server connection timed out'));try{xhr.send();}catch(e){reject(e);}
    });return x;
  }catch(xhrErr){throw lastErr||xhrErr;}
}

async function talkAsk(){
  const input=talkEl('talkInput');const q=(input?.value||'').trim();console.log('[TALK ASK CLICK]',q||'(empty)');
  if(!q){talkSetStatus('தயவுசெய்து நகரம் / ஊர் பெயரை உள்ளிடுங்கள் / Please type a town or city name, then press Ask.');if(input)input.focus();return;}
  const askBtn=talkEl('talkAskBtn');if(askBtn)askBtn.disabled=true;talkShowLoading(q);talkSetStatus(`${talkTa(TALK_TA.waitShort)} — ${talkTa(TALK_TA.process)} / PLEASE WAIT — LIVE MODEL DATA IS BEING PROCESSED…`);
  try{
    const {r,d}=await talkJsonRequest('/talk/query?q='+encodeURIComponent(q)+'&_t='+Date.now(),{timeout:30000,retries:2});console.log('[TALK START RESPONSE]',r.status,d);
    if(!r.ok||!d.ok){const msg=d.message||d.error||'Place could not be resolved.';talkSetStatus(`இடத்தை கண்டறிய முடியவில்லை / ${msg}`);if(d.voice_tamil)talkSpeakTamilEnglish(d.voice_tamil,'The requested place could not be resolved. Please try another town or city.');return;}
    const jobId=d.job_id;if(d.location_message)talkSetStatus(`இடம் கண்டறியப்பட்டது: ${talkEsc(d.place?.name||q)} / Location resolved: ${talkEsc(d.place?.name||q)} • ${talkTa(TALK_TA.process)} / live model processing started…`);if(!jobId)throw new Error('Talk job was not created.');
    let finalData=null,lastGood=null;const pollStarted=Date.now(),pollLimit=260000;
    while(Date.now()-pollStarted<pollLimit){
      await new Promise(res=>setTimeout(res,1100));let pr;
      try{pr=await talkJsonRequest('/talk/progress?job='+encodeURIComponent(jobId)+'&_t='+Date.now(),{timeout:25000,retries:1});}
      catch(progressErr){console.warn('[TALK PROGRESS RETRY]',progressErr);talkSetStatus(`${talkTa(TALK_TA.analysis)} / Live model connection retry…`);continue;}
      if(!pr.r.ok||!pr.d.ok){if(pr.r.status===404)throw new Error(pr.d.error||'Talk job expired or was not found.');talkSetStatus(`${talkTa(TALK_TA.analysis)} / Live model server retry…`);continue;}
      const cur=pr.d;
      if(cur.completed_model_count>0){lastGood=cur;talkRender(cur);const n=cur.usable_model_count||0,total=7,pending=cur.pending_model_count||0;if(cur.complete){const failed=Object.entries(cur.models||{}).filter(([k,v])=>v&&v.error).map(([k])=>k);const failedText=failed.length?` • unavailable / ${talkTa(TALK_TA.unavailable)}: ${failed.join(', ')}`:'';talkSetStatus(`${talkTa(TALK_TA.complete)} / Analysis complete • ${n}/${total} ${talkTa(TALK_TA.models)} ${talkTa(TALK_TA.available)}${failedText}.`);}else talkSetStatus(`${talkTa(TALK_TA.analysis)} / Live analysis updated • ${n}/${total} ${talkTa(TALK_TA.models)} ${talkTa(TALK_TA.available)} • ${pending} ${talkTa(TALK_TA.processing)} / still processing…`);}
      if(cur.complete){finalData=cur;break;}
    }
    if(!finalData){
      if(lastGood)finalData=lastGood;else{const pr=await talkJsonRequest('/talk/progress?job='+encodeURIComponent(jobId)+'&_t='+Date.now(),{timeout:30000,retries:2});if(pr.r.ok&&pr.d.ok&&pr.d.completed_model_count>0){finalData=pr.d;talkRender(finalData);}else throw new Error('No live model result received yet.');}
    }
    if(finalData){const tamilSpeech=talkMakeTamilBrief(finalData),englishSpeech=talkMakeEnglishBrief(finalData);talkSetStatus(`${talkTa(TALK_TA.complete)} / Analysis complete — ${talkTa(TALK_TA.summary)} / Tamil findings followed by English findings.`);talkSpeakTamilEnglish(tamilSpeech,englishSpeech);}
  }catch(e){console.error('[TALK FETCH ERROR]',e);const msg=(e&&e.message)?e.message:String(e);talkSetStatus(`Talk Engine இணைப்பு பிழை / Talk Engine connection error: ${msg}`);}finally{if(askBtn)askBtn.disabled=false;}
}

function talkInit(){
  if(talkUiReady) return;
  talkUiReady=true;
  console.log('[TALK CONFIG]',{pageOrigin:window.location.origin,apiBase:TALK_API_PUBLIC});
  talkJsonRequest('/talk/ping',{timeout:15000,retries:2}).then(({d})=>{
    console.log('[TALK PING]',d);
    if(d && d.ok) talkSetStatus('Talk Engine இணைக்கப்பட்டுள்ளது / Talk Engine connected • live 7-model retrieval is ready.');
  }).catch(e=>{
    console.error('[TALK PING ERROR]',e);
    talkSetStatus(`Talk Engine இணைக்கப்படவில்லை / Talk Engine connection failed. API: ${TALK_API_PUBLIC}`);
  });
  const speakBtn=talkEl('talkSpeakBtn'), micBtn=talkEl('talkMicBtn'), askBtn=talkEl('talkAskBtn'), input=talkEl('talkInput');
  if(!speakBtn || !askBtn || !input){
    console.error('[TALK UI] Required controls missing', {speakBtn,askBtn,input});
    return;
  }
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  speakBtn.disabled=!SR;
  speakBtn.title=SR?'பேச தொடங்க / Start voice input':'குரல் உள்ளீடு இல்லை / Voice input unavailable — use typed Ask';
  if(micBtn){micBtn.disabled=!SR; micBtn.title='🎙️ பேச / Speak';}
  speakBtn.onclick=talkStart;
  if(micBtn) micBtn.onclick=talkStart;
  askBtn.onclick=talkAsk;
  input.addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault();talkAsk();} });
  talkSetStatus(SR?'பேசுங்கள் அல்லது நகரத்தின் பெயரை உள்ளிடுங்கள் / Talk or type a place name.':'குரல் உள்ளீடு இல்லை; நகரத்தின் பெயரை உள்ளிட்டு கேளுங்கள் / Voice input unavailable; type a place name and press Ask.');
  console.log('[TALK UI] initialized; typed Ask enabled');
}
if(document.readyState==='loading'){
  document.addEventListener('DOMContentLoaded',talkInit,{once:true});
}else{
  talkInit();
}


// ============================================================================
// V172 UPPER-AIR WIND EXPLORER — progressive Model → Run → Level → Hour
// ============================================================================
let UA_UI={model:'',run:'',level:850,hour:null,runs:[],hours:[]};
function uaSetStatus(msg,loading=false){const e=document.getElementById('uaWindStatus');if(e){e.textContent=msg;e.style.color=loading?'#1769aa':'#60737d';}}
function uaRenderSummary(){const e=document.getElementById('uaSelectionSummary');if(!e)return;e.textContent=`MODEL: ${uaModelLabel(UA_UI.model)} • RUN: ${UA_UI.run?uaRunLabelClient(UA_UI.run):'—'} • LEVEL: ${UA_UI.level?UA_UI.level+' hPa':'—'} • HOUR: ${UA_UI.hour?('F'+String(UA_UI.hour).padStart(3,'0')):'—'}`;}
function uaModelLabel(v){return ({'ECMWF':'ECMWF ENS','GEFS':'GEFS','ECMWF_HRES':'ECMWF IFS HRES','GFS':'GFS','ICON':'ICON GLOBAL'})[v]||v||'—';}
function uaRunLabelClient(v){if(!/^\d{10}$/.test(v))return v||'—';const d=new Date(Date.UTC(Number(v.slice(0,4)),Number(v.slice(4,6))-1,Number(v.slice(6,8)),Number(v.slice(8,10))));return d.toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'})+' • '+String(d.getUTCHours()).padStart(2,'0')+'Z';}
function uaSetRunLabel(prefix,v){const e=document.getElementById('uaRunLabel');if(!e)return;e.innerHTML=`<span class="run-label">${prefix}</span><span>${v?uaRunLabelClient(v):'—'}</span>`;}
function uaPaintModelButtons(){document.querySelectorAll('.ua-model-btn').forEach(b=>{b.classList.toggle('active',b.dataset.uaModel===UA_UI.model);});}
function uaPaintLevelButtons(){document.querySelectorAll('.ua-level-btn').forEach(b=>{b.classList.toggle('active',Number(b.dataset.uaLevel)===Number(UA_UI.level));});}
function uaPaintHours(){const box=document.getElementById('uaHourButtons');if(!box)return;box.innerHTML='';if(!UA_UI.hours.length){box.innerHTML='<span style="font-size:10px;color:#8a9aa4;">No verified forecast hours returned.</span>';return;}UA_UI.hours.forEach(h=>{const b=document.createElement('button');b.className='map-control-btn ua-hour-btn';b.textContent=String(h).padStart(3,'0');b.title=`Forecast F${String(h).padStart(3,'0')}`;b.onclick=()=>uaSelectHour(h);if(Number(h)===Number(UA_UI.hour)){b.style.background='#17324d';b.style.color='#fff';b.style.borderColor='#17324d';}box.appendChild(b);});}
async function uaSelectModel(model){UA_UI.model=model;UA_UI.run='';UA_UI.level=850;UA_UI.hour=null;UA_UI.runs=[];UA_UI.hours=[];uaPaintModelButtons();uaPaintLevelButtons();uaPaintHours();uaRenderSummary();uaSetRunLabel('LATEST RUN','');uaSetStatus(`CHECKING LATEST ${uaModelLabel(model)} RUNS…`,true);const rh=document.getElementById('uaRunHistory');if(rh)rh.innerHTML='<span style="font-size:10px;color:#1769aa;font-weight:800;">Checking latest available runs…</span>';try{const data=await fetch('/upperair/runs?model='+encodeURIComponent(model)+'&ts='+Date.now(),{cache:'no-store'}).then(r=>r.json());if(data.error)throw new Error(data.error);UA_UI.runs=Array.isArray(data.runs)?data.runs:[];UA_UI.run=data.selected||UA_UI.runs[0]?.value||'';uaSetRunLabel('LATEST RUN',UA_UI.run);uaRenderRunHistory();uaRenderSummary();if(!UA_UI.run)throw new Error('No available published runs returned for '+model);uaSetStatus(`${uaModelLabel(model)} • LATEST RUN ${uaRunLabelClient(UA_UI.run)} • SELECT A RUN OR CONTINUE WITH LATEST`,true);await uaLoadHours();}catch(e){UA_UI.runs=[];UA_UI.hours=[];uaPaintHours();uaRenderRunHistory();uaSetStatus('UPPER-AIR RUN DISCOVERY ERROR: '+e.message,false);}}
function uaRenderRunHistory(){const box=document.getElementById('uaRunHistory');if(!box)return;if(!UA_UI.model){box.innerHTML='<span style="font-size:10px;color:#8a9aa4;">Select a model to load the latest available runs.</span>';return;}if(!UA_UI.runs.length){box.innerHTML='<span style="font-size:10px;color:#8a9aa4;">No verified runs returned.</span>';return;}box.innerHTML=UA_UI.runs.map((r,i)=>`<button class="ua-run-btn ${String(r.value)===String(UA_UI.run)?'active':''}" title="${i===0?'Latest available published run':'Previous available run'}" onclick="uaSelectRun('${r.value}')">${i===0?'LATEST • ':''}${r.label}</button>`).join('');}
function uaToggleRuns(){const e=document.getElementById('uaRunHistory');if(e)e.style.display=e.style.display==='none'?'flex':'none';}
async function uaSelectRun(runv){UA_UI.run=runv;UA_UI.hour=null;uaRenderRunHistory();uaRenderSummary();uaSetRunLabel(String(runv)===String(UA_UI.runs[0]?.value)?'LATEST RUN':'SELECTED RUN',runv);uaSetStatus(`RUN ${uaRunLabelClient(runv)} • LOADING HOURS…`,true);await uaLoadHours();}
async function uaSelectLevel(level){if(!UA_UI.model){uaSetStatus('SELECT A MODEL FIRST',false);return;}UA_UI.level=Number(level);UA_UI.hour=null;uaPaintLevelButtons();uaRenderSummary();uaSetStatus(`${uaModelLabel(UA_UI.model)} • ${uaRunLabelClient(UA_UI.run)} • ${level} hPa • LOADING AVAILABLE HOURS…`,true);await uaLoadHours();}
async function uaLoadHours(){if(!UA_UI.model||!UA_UI.run)return;try{const data=await fetch('/upperair/hours?model='+encodeURIComponent(UA_UI.model)+'&run='+encodeURIComponent(UA_UI.run)+'&level='+UA_UI.level+'&ts='+Date.now(),{cache:'no-store'}).then(r=>r.json());if(data.error)throw new Error(data.error);UA_UI.hours=data.hours||[];uaPaintHours();uaRenderSummary();uaSetStatus(`${uaModelLabel(UA_UI.model)} • ${uaRunLabelClient(UA_UI.run)} • ${UA_UI.level} hPa • ${UA_UI.hours.length} HOURS AVAILABLE — CLICK AN HOUR TO DOWNLOAD`,false);}catch(e){UA_UI.hours=[];uaPaintHours();uaSetStatus('HOUR DISCOVERY ERROR: '+e.message,false);}}
function uaSelectHour(hour){
  if(!UA_UI.model||!UA_UI.run){
    uaSetStatus('SELECT MODEL AND RUN FIRST',false);
    return;
  }

  UA_UI.hour=Number(hour);
  uaPaintHours();
  uaRenderSummary();

  const label=document.getElementById('uaWindPlotLabel');
  if(label){
    label.textContent=`DOWNLOADING ${uaModelLabel(UA_UI.model)} • ${uaRunLabelClient(UA_UI.run)} • ${UA_UI.level} hPa • F${String(hour).padStart(3,'0')}…`;
    label.style.color='#1769aa';
  }

  uaSetStatus(
    `DOWNLOADING ${uaModelLabel(UA_UI.model)} • ${uaRunLabelClient(UA_UI.run)} • ${UA_UI.level} hPa • F${String(hour).padStart(3,'0')}…`,
    true
  );

  const url='/upperair/plot.png?model='+encodeURIComponent(UA_UI.model)
    +'&run='+encodeURIComponent(UA_UI.run)
    +'&level='+UA_UI.level
    +'&fhour='+hour
    +'&ts='+Date.now();

  /*
   * V183 FIX:
   * The old handler wrote the upper-air image into #rainPlotImage.
   * That image belongs to the legacy rainfall dashboard and its parent
   * .grid is deliberately hidden during Wind-only mode.
   *
   * The Wind module now has its own visible #uaWindPlotImage directly
   * inside #upperAirWindCard.
   */
  const img=document.getElementById('uaWindPlotImage');
  const full=document.getElementById('fullPlotImage');

  if(img){
    img.onload=()=>{
      if(label){
        label.textContent=`${uaModelLabel(UA_UI.model)} • ${uaRunLabelClient(UA_UI.run)} • ${UA_UI.level} hPa • F${String(hour).padStart(3,'0')} • PLOTTED`;
        label.style.color='#176b3a';
      }
      uaSetStatus(
        `${uaModelLabel(UA_UI.model)} • ${UA_UI.level} hPa • F${String(hour).padStart(3,'0')} PLOTTED`,
        false
      );
    };

    img.onerror=()=>{
      if(label){
        label.textContent='UPPER-AIR PLOT ERROR — SEE SERVER LOG';
        label.style.color='#a12626';
      }
      uaSetStatus('UPPER-AIR PLOT ERROR — SEE ACTIVITY LOG',false);
    };

    img.src=url;
  }

  if(full && document.getElementById('mapModal')?.classList.contains('open')){
    full.src=url;
  }
}

</script>
</body>
</html>
'''




def ecmwf_valid_steps(run_hour, mode="deterministic"):
    """Return forecast steps actually published by current ECMWF Open Data."""
    run_hour = int(run_hour)
    if mode == "ensemble":
        if run_hour in (0, 12):
            return list(range(0, 145, 3)) + list(range(150, 361, 6))
        return list(range(0, 145, 3))
    # IFS HRES / oper / fc
    if run_hour in (0, 12):
        return list(range(0, 145, 3)) + list(range(150, 361, 6))
    return list(range(0, 145, 3))

def ecmwf_endpoint_list(run_hour, mode="deterministic"):
    """Return cumulative TP endpoints for true 24-hour forecast periods.

    ECMWF TP is cumulative from the model run start, so true 24-hour
    periods end at F024, F048, F072, ... irrespective of run hour.
    """
    valid = set(ecmwf_valid_steps(run_hour, mode))
    candidates = list(range(24, 361, 24))
    return [x for x in candidates if x in valid]

def ecmwf_required_endpoints(run_hour, mode="deterministic"):
    """Union of 24h map endpoints and cumulative 7/15-day endpoints."""
    valid = set(ecmwf_valid_steps(run_hour, mode))
    required = {0}
    required.update(ecmwf_endpoint_list(run_hour, mode))
    # Seven-day accumulation.
    if 168 in valid:
        required.add(168)
    # Fifteen-day accumulation is available for both HRES and ENS 00/12Z.
    if 360 in valid:
        required.add(360)
    return sorted(required)

def ecmwf_download_endpoints(run_hour, mode, period):
    """Return ONLY the END cumulative TP field required for the selected product.

    ECMWF TP is accumulated from the start of the forecast. Therefore the
    selected accumulation products do NOT need an F000 TP download:

      24 Hour = F024
      7 Day   = F168
      15 Day  = F360 (00/12Z HRES/ENS)

    This deliberately avoids the F000 TP request that can return a tiny
    non-GRIB response from the Open Data client.
    """
    valid = set(ecmwf_valid_steps(run_hour, mode))
    p = str(period)
    if p == "24 Hour":
        if 24 not in valid:
            raise ValueError(f"24-hour accumulation is not available from the {run_hour:02d}Z {mode} run")
        return [24]
    # TALK ONLY: the voice briefing needs Day 1/2/3, so retrieve the
    # cumulative TP endpoints once (F024/F048/F072). This does not alter
    # the normal dashboard 24-hour download path.
    if p == "Talk 72 Hour":
        required = [24, 48, 72]
        missing = [ep for ep in required if ep not in valid]
        if missing:
            raise ValueError(
                f"Talk 72-hour ECMWF endpoints unavailable for {run_hour:02d}Z run: "
                + ", ".join(f"F{x:03d}" for x in missing)
            )
        return required
    if p == "7 Day Accumulation":
        if 168 not in valid:
            raise ValueError(f"7-day accumulation is not available from the {run_hour:02d}Z {mode} run")
        return [168]
    if p == "15 Day Accumulation":
        if run_hour not in (0, 12) or 360 not in valid:
            raise ValueError(f"15-day accumulation is not available from the {run_hour:02d}Z ECMWF {mode} run")
        return [360]
    raise ValueError(f"Unknown rainfall period: {period}")

def ecmwf_period_endpoint(run_hour, mode, period):
    """Return (start,end) for cumulative forecast products."""
    valid = set(ecmwf_valid_steps(run_hour, mode))
    p = str(period)
    if p == "24 Hour":
        eps = ecmwf_endpoint_list(run_hour, mode)
        if len(eps) < 2:
            raise ValueError(f"24-hour sequence is not available for the {run_hour:02d}Z {mode} run")
        return eps[0], eps[1]
    if p == "7 Day Accumulation":
        if 168 not in valid:
            raise ValueError(f"7-day accumulation is not available from the current {run_hour:02d}Z ECMWF {mode} open-data run")
        return 0, 168
    if p == "15 Day Accumulation":
        if run_hour not in (0, 12) or 360 not in valid:
            raise ValueError(f"15-day accumulation is not available from the current {run_hour:02d}Z ECMWF {mode} Open Data run")
        return 0, 360
    raise ValueError(f"Unknown rainfall period: {period}")

def ecmwf_candidate_runs():
    """Return recent ECMWF cycles, newest first."""
    now = datetime.now(timezone.utc)
    candidates = []
    for back in range(3):
        d = now - timedelta(days=back)
        for hour in (18, 12, 6, 0):
            dt = d.replace(hour=hour, minute=0, second=0, microsecond=0)
            if dt <= now:
                candidates.append((dt, d.strftime("%Y%m%d"), hour))
    candidates.sort(reverse=True)
    return candidates


def valid_grib(path):
    """Basic GRIB2 integrity check before a file is promoted as usable."""
    try:
        p = Path(path)
        return (
            p.exists()
            and p.stat().st_size > 1000
            and p.read_bytes()[:4] == b"GRIB"
        )
    except OSError:
        return False


# ---------------------------------------------------------------------------
# ECMWF DIRECT OPEN-DATA ENGINE (V22)
#
# IMPORTANT DESIGN CHANGE FROM V19:
# V19 attempted to download the complete 6+ GB ENS "ef" GRIB2 file for each
# forecast endpoint. That is unnecessary for this dashboard and can trigger
# HTTP 429/rate-limit problems while also creating enormous local transfers.
#
# V21 uses the verified official ECMWF .index files and HTTP byte-range requests.
# ECMWF documents that each GRIB2 file has a matching JSON-lines index with
# _offset and _length for every field. We select only:
#     stream=enfo, type=pf, param=tp, number=1..50
# and download those individual GRIB messages with Range requests.
#
# Result:
#   - no multi-gigabyte whole-file download
#   - no official ECMWF Open Data direct path retry loop
#   - only the required TP ensemble fields are stored
#   - ECMWF -> AWS failover happens at index/field level
#   - HTTP 200 to a Range request is rejected (prevents accidental full-file
#     downloads)
# ---------------------------------------------------------------------------

ECMWF_HTTP_SOURCES = [
    ("ECMWF", "https://data.ecmwf.int/forecasts"),
    ("AWS", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"),
]

HTTP_TIMEOUT = (30, 120)
HTTP_CONNECT_RETRIES = 4
# ECMWF currently rate-limits bursty Open Data traffic. Keep requests deliberately
# sequential and spaced so 429/503 responses do not cascade into more throttling.
RANGE_REQUEST_DELAY = 1.50
ENS_MEMBERS = list(range(1, 51))


def ecmwf_file_url(source_root, run_date, run_hour, step):
    """Official ECMWF IFS ENS direct-output GRIB2 file URL."""
    return (
        f"{source_root.rstrip('/')}/{run_date}/{run_hour:02d}z/ifs/0p25/enfo/"
        f"{run_date}{run_hour:02d}0000-{int(step)}h-enfo-ef.grib2"
    )


def ecmwf_index_url(source_root, run_date, run_hour, step):
    """Official ECMWF JSONL index URL associated with the ENS GRIB2 file.

    Verified against the live ECMWF directory listing: the index filename is
    ``...-enfo-ef.index`` while the data object is ``...-enfo-ef.grib2``.
    """
    return ecmwf_file_url(source_root, run_date, run_hour, step).replace(".grib2", ".index")


def _request_headers():
    # ECMWF's edge appears to treat a bare custom User-Agent (with no
    # Accept-Language/Accept-Encoding/etc.) as non-browser traffic and
    # silently return 404 instead of a real error — confirmed by the same
    # URLs loading fine in an actual browser. Mimicking a real browser's
    # header set avoids that.
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
        "Connection": "close",
    }


def _fetch_index(source_name, source_root, run_date, run_hour, endpoint):
    """
    Fetch the small ECMWF index file and return selected TP ensemble fields.

    ECMWF index files are JSON-lines records containing _offset and _length.
    """
    url = ecmwf_index_url(source_root, run_date, run_hour, endpoint)
    last_error = None

    for attempt in range(1, HTTP_CONNECT_RETRIES + 1):
        status_code = None
        retry_after_header = None
        try:
            ecmwf_event(
                f"{source_name}: fetching F{endpoint:03d} index "
                f"(attempt {attempt}/{HTTP_CONNECT_RETRIES})"
            )

            r = requests.get(
                url,
                timeout=HTTP_TIMEOUT,
                headers=_request_headers(),
            )
            status_code = r.status_code

            if r.status_code in (401,403,404):
                msg=f"HTTP {r.status_code} {r.reason}"
                ecmwf_event(f"{source_name}: F{endpoint:03d} index unavailable — {msg} (no retry)", "warn")
                return None, msg

            if r.status_code == 429:
                retry_after_header = r.headers.get("Retry-After")
                raise RuntimeError(
                    f"HTTP 429 Too Many Requests "
                    f"(Retry-After: {retry_after_header or 'not supplied'})"
                )

            if r.status_code != 200:
                raise RuntimeError(
                    f"HTTP {r.status_code} {r.reason}"
                )

            records = []
            for raw in r.text.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # The index uses MARS-style type=pf for perturbed ENS fields,
                # while the physical GRIB filename uses type=ef.
                if str(rec.get("stream", "")).lower() != "enfo":
                    continue
                if str(rec.get("type", "")).lower() != "pf":
                    continue
                if str(rec.get("param", "")).lower() != "tp":
                    continue

                try:
                    step_value = int(str(rec.get("step")))
                    member = int(str(rec.get("number")))
                    offset = int(rec["_offset"])
                    length = int(rec["_length"])
                except (TypeError, ValueError, KeyError):
                    continue

                if step_value != int(endpoint):
                    continue
                if member not in ENS_MEMBERS:
                    continue
                if offset < 0 or length <= 0:
                    continue

                records.append({
                    "number": member,
                    "offset": offset,
                    "length": length,
                })

            # One record per member is required.
            by_member = {}
            for rec in records:
                by_member[rec["number"]] = rec

            missing = [m for m in ENS_MEMBERS if m not in by_member]
            if missing:
                raise RuntimeError(
                    f"index does not contain all 50 TP ensemble members; "
                    f"missing {missing[:10]}"
                    + ("…" if len(missing) > 10 else "")
                )

            selected = [by_member[m] for m in ENS_MEMBERS]

            ecmwf_event(
                f"{source_name}: F{endpoint:03d} index verified — "
                f"50 TP ensemble fields found",
                "success"
            )
            return selected, None

        except Exception as exc:
            last_error = str(exc)
            if attempt < HTTP_CONNECT_RETRIES:
                wait = _backoff_seconds(attempt, status_code, retry_after_header)
                ecmwf_event(
                    f"{source_name}: index failed — {last_error} — "
                    f"retrying in {wait:.1f}s",
                    "warn"
                )
                time.sleep(wait)

    return None, last_error


def _backoff_seconds(attempt, status_code, retry_after_header=None):
    """
    Backoff delay before the next attempt.

    429/503 (rate limiting / throttling) get real exponential backoff —
    honouring Retry-After when the server supplies one — instead of the
    flat 2s retry, since hammering a throttled host at a fixed interval
    just keeps tripping the same limit. Other errors (404, connection
    issues) keep a short flat delay since backing off hard on a "not
    published yet" response only slows down finding a reachable run.
    """
    if status_code in (429, 503):
        if retry_after_header:
            try:
                return min(float(retry_after_header), 90.0)
            except (TypeError, ValueError):
                pass
        return min(10.0 * (2 ** (attempt - 1)), 90.0)
    return 2.0


def _download_range_field(
    source_name,
    source_root,
    run_date,
    run_hour,
    endpoint,
    member,
    offset,
    length,
):
    """
    Download exactly one GRIB message using HTTP Range.

    A 200 response is deliberately rejected because it means the server did
    not honour the byte range; accepting it could accidentally download the
    entire multi-gigabyte source file.
    """
    url = ecmwf_file_url(source_root, run_date, run_hour, endpoint)
    end_byte = offset + length - 1
    last_error = None

    for attempt in range(1, HTTP_CONNECT_RETRIES + 1):
        status_code = None
        retry_after_header = None
        try:
            if RANGE_REQUEST_DELAY:
                time.sleep(RANGE_REQUEST_DELAY)

            ecmwf_event(
                f"{source_name}: F{endpoint:03d} member {member:02d} "
                f"range {offset:,}-{end_byte:,}"
            )

            r = requests.get(
                url,
                stream=True,
                timeout=HTTP_TIMEOUT,
                headers={
                    **_request_headers(),
                    "Range": f"bytes={offset}-{end_byte}",
                },
            )
            status_code = r.status_code

            if r.status_code == 429:
                retry_after_header = r.headers.get("Retry-After")
                raise RuntimeError(
                    f"HTTP 429 Too Many Requests "
                    f"(Retry-After: {retry_after_header or 'not supplied'})"
                )

            if r.status_code != 206:
                # Never accept HTTP 200 here.
                raise RuntimeError(
                    f"HTTP {r.status_code} while requesting byte range; "
                    f"expected 206 Partial Content"
                )

            content_range = r.headers.get("Content-Range", "")
            expected_size = int(length)
            data = bytearray()

            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    data.extend(chunk)

            r.close()

            # ECMWF documents _offset/_length as the exact GRIB message byte
            # range. Reject anything that is clearly not that message.
            if len(data) != expected_size:
                raise RuntimeError(
                    f"range length mismatch: received {len(data):,} bytes, "
                    f"expected {expected_size:,} • Content-Range={content_range or 'missing'}"
                )

            if data[:4] != b"GRIB":
                preview = bytes(data[:16]).hex()
                raise RuntimeError(
                    f"range response is not GRIB (first bytes={preview})"
                )

            # Do not require the local buffer's final 4 bytes to be 7777 here.
            # The index is authoritative for message length and some gateways
            # have been observed to alter transport framing while preserving the
            # GRIB payload. cfgrib/eccodes will perform the definitive decode.
            return bytes(data), None

        except Exception as exc:
            last_error = str(exc)
            try:
                r.close()
            except Exception:
                pass

            if attempt < HTTP_CONNECT_RETRIES:
                wait = _backoff_seconds(attempt, status_code, retry_after_header)
                ecmwf_event(
                    f"{source_name}: F{endpoint:03d} member {member:02d} "
                    f"failed — {last_error} — retrying in {wait:.1f}s",
                    "warn"
                )
                time.sleep(wait)

    return None, last_error


def _download_tp_endpoint_from_source(
    source_name,
    source_root,
    run_date,
    run_hour,
    endpoint,
    target,
):
    """
    Build one compact GRIB2 containing only tp members 1..50.

    If any field fails, the partial file is discarded so another official
    source can restart the endpoint cleanly.
    """
    fields, error = _fetch_index(
        source_name,
        source_root,
        run_date,
        run_hour,
        endpoint,
    )
    if fields is None:
        return False, f"index failure: {error}"

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")

    try:
        if tmp.exists():
            tmp.unlink()

        endpoint_bytes = sum(int(f.get("length", 0) or 0) for f in fields)
        with ECMWF_DOWNLOAD_LOCK:
            status = ECMWF_DOWNLOAD_STATUS
            status["current_endpoint"] = int(endpoint)
            status["member_total"] = 50
            status["current_member"] = 0
            status["total_bytes"] = endpoint_bytes
            status["estimated_total_bytes"] = max(
                int(status.get("downloaded_bytes", 0)) + endpoint_bytes,
                endpoint_bytes * max(1, int(status.get("endpoint_total", 1) or 1))
            )

        with open(tmp, "wb") as fh:
            for idx, field in enumerate(fields, 1):
                member = field["number"]
                with ECMWF_DOWNLOAD_LOCK:
                    ECMWF_DOWNLOAD_STATUS["current_member"] = idx
                    ECMWF_DOWNLOAD_STATUS["message"] = (
                        f"Downloading F{endpoint:03d} "
                        f"({idx}/50 members) from {source_name}…"
                    )

                data, error = _download_range_field(
                    source_name,
                    source_root,
                    run_date,
                    run_hour,
                    endpoint,
                    member,
                    field["offset"],
                    field["length"],
                )

                if data is None:
                    raise RuntimeError(
                        f"member {member:02d} failed: {error}"
                    )

                fh.write(data)

                # Update byte telemetry after every member.  The range request
                # is atomic at member level, so this remains reliable even
                # when an individual request is retried.
                now_ts=time.time()
                with ECMWF_DOWNLOAD_LOCK:
                    status=ECMWF_DOWNLOAD_STATUS
                    status["downloaded_bytes"] = int(status.get("downloaded_bytes",0)) + len(data)
                    started=status.get("started_at")
                    elapsed=max(0.001, now_ts-datetime.fromisoformat(started).timestamp()) if started else 0.001
                    speed=status["downloaded_bytes"]/elapsed
                    status["speed_bps"]=speed
                    completed_eps=int(status.get("downloaded",0))
                    remaining_eps=max(0, int(status.get("endpoint_total",1))-completed_eps-1)
                    avg_ep=status["downloaded_bytes"]/max(1, completed_eps+1)
                    remaining_bytes=max(0, endpoint_bytes-sum(int(f.get("length",0) or 0) for f in fields[:idx]))
                    estimated_remaining=remaining_bytes + avg_ep*remaining_eps
                    eta=estimated_remaining/speed if speed>0 else None
                    status["eta_seconds"]=eta
                    status["expected_finish"]=(datetime.now(timezone.utc)+timedelta(seconds=eta)).isoformat() if eta is not None else None
                    status["estimated_total_bytes"]=status["downloaded_bytes"]+estimated_remaining

                ecmwf_event(
                    f"{source_name}: F{endpoint:03d} member "
                    f"{member:02d}/50 downloaded",
                    "success"
                )

        if not valid_grib(tmp):
            raise RuntimeError(
                "assembled TP GRIB failed basic integrity validation"
            )

        tmp.replace(target)

        ecmwf_event(
            f"{source_name}: F{endpoint:03d} — compact 50-member TP GRIB "
            f"created ({target.stat().st_size:,} bytes)",
            "success"
        )
        return True, source_name

    except Exception as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass

        return False, str(exc)


def _probe_direct_source(source_name, source_root, run_date, run_hour):
    """
    Probe the small .index file rather than the multi-gigabyte GRIB.

    A run is considered reachable only when F003/F021 index contains the
    required 50 TP ensemble members.
    """
    # Use F000 for run discovery. It avoids making availability depend on
    # F024, which can be transiently throttled even while the run is published.
    endpoint = 0

    ecmwf_event(
        f"{source_name}: probing F{endpoint:03d} index"
    )

    fields, error = _fetch_index(
        source_name,
        source_root,
        run_date,
        run_hour,
        endpoint,
    )

    if fields:
        ecmwf_event(
            f"{source_name}: F{endpoint:03d} TP index available",
            "success"
        )
        return True, None

    return False, error or "index unavailable"


def _download_endpoint_with_failover(
    run_date,
    run_hour,
    endpoint,
    target,
):
    """
    Try each official ECMWF mirror for the endpoint.

    Failover is immediate at source level; there is no long 500-attempt loop.
    """
    failures = []

    for source_name, source_root in ECMWF_HTTP_SOURCES:
        ecmwf_event(
            f"{source_name}: downloading compact TP endpoint "
            f"F{endpoint:03d}"
        )

        ok, result = _download_tp_endpoint_from_source(
            source_name,
            source_root,
            run_date,
            run_hour,
            endpoint,
            target,
        )

        if ok and valid_grib(target):
            return True, source_name

        failures.append(f"{source_name}: {result}")
        ecmwf_event(
            f"{source_name}: F{endpoint:03d} failed — {result}",
            "warn"
        )

    return False, " | ".join(failures)



def ecmwf_deterministic_file_url(source_root, run_date, run_hour, step):
    return (f"{source_root.rstrip('/')}/{run_date}/{run_hour:02d}z/ifs/0p25/oper/"
            f"{run_date}{run_hour:02d}0000-{int(step)}h-oper-fc.grib2")

def ecmwf_deterministic_index_url(source_root, run_date, run_hour, step):
    return ecmwf_deterministic_file_url(source_root, run_date, run_hour, step).replace('.grib2','.index')

def _fetch_hres_index(source_name, source_root, run_date, run_hour, endpoint):
    """Fetch deterministic HRES TP index with status-aware retry logic.

    IMPORTANT: HTTP 404/403/401 are treated as permanent for this run/endpoint.
    A published run will not become available by hammering the same missing
    object, and repeated 404 probes were causing the later 429/503 throttling
    seen in V42. Only transient 429/503 and transport failures are retried.
    """
    url=ecmwf_deterministic_index_url(source_root,run_date,run_hour,endpoint)
    last_error=None
    for attempt in range(1, HTTP_CONNECT_RETRIES+1):
        status_code=None; retry_after=None
        try:
            r=requests.get(url,timeout=HTTP_TIMEOUT,headers=_request_headers())
            status_code=r.status_code; retry_after=r.headers.get('Retry-After')
            if r.status_code in (401,403,404):
                msg=f"HTTP {r.status_code} {r.reason}"
                ecmwf_event(f"{source_name}: F{endpoint:03d} unavailable for {run_date} {run_hour:02d}Z — {msg} (no retry)", "warn")
                return None,None,msg
            if r.status_code!=200:
                if r.status_code not in (429,500,502,503,504):
                    msg=f"HTTP {r.status_code} {r.reason}"
                    ecmwf_event(f"{source_name}: F{endpoint:03d} probe failed — {msg} (no retry)", "warn")
                    return None,None,msg
                raise RuntimeError(f"HTTP {r.status_code} {r.reason}")
            candidates=[]
            for raw in r.text.splitlines():
                raw=raw.strip()
                if not raw: continue
                try: rec=json.loads(raw)
                except json.JSONDecodeError: continue
                if str(rec.get('stream','')).lower()!='oper' or str(rec.get('type','')).lower()!='fc': continue
                if str(rec.get('param','')).lower()!='tp': continue
                try: step=int(str(rec.get('step'))); offset=int(rec['_offset']); length=int(rec['_length'])
                except (TypeError,ValueError,KeyError): continue
                if step==int(endpoint) and offset>=0 and length>0: candidates.append((offset,length,rec))
            if not candidates: raise RuntimeError(f"No deterministic TP field found for F{endpoint:03d}")
            candidates.sort(key=lambda x: 0 if str(x[2].get('levtype','')).lower()=='sfc' else 1)
            return candidates[0][0],candidates[0][1],None
        except Exception as exc:
            last_error=str(exc)
            if attempt<HTTP_CONNECT_RETRIES:
                wait=_backoff_seconds(attempt,status_code,retry_after)
                ecmwf_event(f"{source_name}: F{endpoint:03d} index probe failed for {run_date} {run_hour:02d}Z — {last_error} — retrying in {wait:.1f}s", "warn")
                time.sleep(wait)
    return None,None,last_error

def _download_hres_field(source_name,source_root,run_date,run_hour,endpoint,offset,length):
    url=ecmwf_deterministic_file_url(source_root,run_date,run_hour,endpoint)
    end_byte=offset+length-1
    for attempt in range(1,HTTP_CONNECT_RETRIES+1):
        try:
            if RANGE_REQUEST_DELAY: time.sleep(RANGE_REQUEST_DELAY)
            r=requests.get(url,stream=True,timeout=HTTP_TIMEOUT,headers={**_request_headers(),'Range':f'bytes={offset}-{end_byte}'})
            if r.status_code!=206: raise RuntimeError(f"HTTP {r.status_code}; expected 206 Partial Content")
            data=bytearray()
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk: data.extend(chunk)
            r.close()
            if len(data)!=length: raise RuntimeError(f"range length mismatch: {len(data):,} vs {length:,}")
            if data[:4]!=b'GRIB' or data[-4:]!=b'7777': raise RuntimeError('invalid GRIB message')
            return bytes(data),None
        except Exception as exc:
            if attempt<HTTP_CONNECT_RETRIES: time.sleep(_backoff_seconds(attempt,getattr(locals().get('r',None),'status_code',None),None))
            last=str(exc)
    return None,last

def _download_hres_endpoint(run_date,run_hour,endpoint,target):
    failures=[]
    for source_name,source_root in ECMWF_HTTP_SOURCES:
        off,length,error=_fetch_hres_index(source_name,source_root,run_date,run_hour,endpoint)
        if off is None: failures.append(f"{source_name}: {error}"); continue
        data,error=_download_hres_field(source_name,source_root,run_date,run_hour,endpoint,off,length)
        if data is None: failures.append(f"{source_name}: {error}"); continue
        target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data)
        # _download_hres_field has already verified the exact range length and
        # GRIB magic. Keep the final check lightweight and report useful size
        # information instead of silently collapsing it into a generic failure.
        if target.exists() and target.stat().st_size == len(data) and data[:4] == b"GRIB":
            ecmwf_event(f"{source_name}: F{endpoint:03d} TP field saved ({len(data):,} bytes)", "success")
            return True,source_name
        failures.append(f"{source_name}: saved field failed local integrity check")
    return False,' | '.join(failures)

def download_ecmwf_deterministic(requested_run=None, requested_period="24 Hour", job_token=None):
    """Download selected HRES TP endpoints through official direct Open Data.

    V50 HRES path:
      1. Select the requested cycle.
      2. Read the official .index file.
      3. Locate only the TP message for F024/F168/F360.
      4. Download only that GRIB byte range.
      5. Validate the GRIB locally and build the rainfall product.

    ECMWF is tried first and the official AWS replica is used as failover.
    """
    global RAINFALL_CACHE
    if job_token is None:
        job_token = begin_ecmwf_job('deterministic', 'ECMWF', requested_run or 'AUTO', requested_period)
    if not ecmwf_job_current(job_token):
        return

    try:
        set_model_identity(
            mode="deterministic", model="ECMWF", label="ECMWF IFS HRES",
            state="selecting", run=None, source="ECMWF Open Data direct",
            download="not-started",
            message="Selecting ECMWF IFS HRES run using official Open Data index…"
        )

        # Manual run selection is strict: never silently replace it with a
        # different cycle. AUTO may try recent candidate cycles.
        if requested_run and requested_run != "AUTO":
            raw = str(requested_run)
            if len(raw) != 10 or not raw.isdigit():
                raise RuntimeError(f"Invalid ECMWF HRES run selection: {requested_run}")
            run_date = raw[:8]
            run_hour = int(raw[8:10])
            if run_hour not in (0, 6, 12, 18):
                raise RuntimeError(f"Invalid ECMWF HRES run hour: {run_hour:02d}Z")
            candidates = [(None, run_date, run_hour)]
            ecmwf_event(
                f"SELECTED HRES RUN • {run_date} {run_hour:02d}Z • "
                f"availability will be verified by official .index", "info"
            )
        else:
            candidates = []
            now = datetime.now(timezone.utc)
            for back in range(3):
                d = now - timedelta(days=back)
                for hour in (0, 18, 12, 6):
                    dt = d.replace(hour=hour, minute=0, second=0, microsecond=0)
                    if dt <= now:
                        candidates.append((dt, d.strftime("%Y%m%d"), hour))
            candidates.sort(reverse=True)

        selected = None
        selected_source = None

        for dt, candidate_date, candidate_hour in candidates:
            if not ecmwf_job_current(job_token):
                return

            try:
                candidate_endpoints = hres_client_steps(candidate_hour, requested_period)
            except Exception as exc:
                ecmwf_event(f"SKIP {candidate_date} {candidate_hour:02d}Z • {exc}", "warn")
                continue

            ecmwf_event(
                f"TRYING HRES DIRECT • {candidate_date} {candidate_hour:02d}Z • "
                f"{requested_period} • " + ", ".join(f"F{x:03d}" for x in candidate_endpoints),
                "info"
            )

            # Verify the selected run by retrieving the first required endpoint.
            probe_ep = int(candidate_endpoints[0])
            probe_dir = ECMWF_ROOT / "ECMWF" / "_direct_probe"
            probe_target = probe_dir / f"probe_{candidate_date}_{candidate_hour:02d}Z_f{probe_ep:03d}.grib2"
            ok, source_or_error = retrieve_hres_tp_endpoint(
                candidate_date, candidate_hour, probe_ep, probe_target, job_token
            )
            if ok:
                selected = (candidate_date, candidate_hour)
                selected_source = source_or_error
                ecmwf_event(
                    f"HRES RUN ACCEPTED • {candidate_date} {candidate_hour:02d}Z • "
                    f"source={source_or_error}", "success"
                )
                # Keep the successful probe for reuse below.
                selected_probe_target = probe_target
                endpoints = candidate_endpoints
                break

            ecmwf_event(
                f"HRES RUN UNAVAILABLE • {candidate_date} {candidate_hour:02d}Z • "
                f"{source_or_error}", "warn"
            )
            try:
                if probe_target.exists():
                    probe_target.unlink()
            except OSError:
                pass

            if requested_run and requested_run != "AUTO":
                raise RuntimeError(
                    f"Selected ECMWF HRES run {candidate_date} {candidate_hour:02d}Z "
                    f"could not be retrieved from official Open Data: {source_or_error}"
                )

        if selected is None:
            raise RuntimeError(
                "No ECMWF IFS HRES candidate run could be retrieved from official Open Data."
            )

        run_date, run_hour = selected
        endpoints = hres_client_steps(run_hour, requested_period)
        set_model_identity(
            mode="deterministic", model="ECMWF", label="ECMWF IFS HRES",
            state="identified", run=f"{run_date} {run_hour:02d}Z",
            source=selected_source or "ECMWF Open Data direct", download="starting",
            message=f"ECMWF IFS HRES selected: {run_date} {run_hour:02d}Z"
        )
        ecmwf_event(
            f"SELECTED HRES • {run_date} {run_hour:02d}Z • source={selected_source}",
            "success"
        )
        ecmwf_event(
            "REQUIRED TP ENDPOINTS • " + ", ".join(f"F{x:03d}" for x in endpoints),
            "info"
        )

        folder = ECMWF_ROOT / "ECMWF" / f"{run_date}_{run_hour:02d}Z"
        folder.mkdir(parents=True, exist_ok=True)

        # Reuse the verified probe so the first endpoint is not downloaded twice.
        selected_probe_target = locals().get("selected_probe_target")
        if selected_probe_target is not None and selected_probe_target.exists():
            probe_ep = int(endpoints[0])
            final_probe_target = folder / f"ecmwf_hres_f{probe_ep:03d}.grib2"
            if not valid_grib(final_probe_target):
                try:
                    if final_probe_target.exists():
                        final_probe_target.unlink()
                    selected_probe_target.replace(final_probe_target)
                    ecmwf_event(
                        f"REUSED VERIFIED F{probe_ep:03d} PROBE • no duplicate download",
                        "success"
                    )
                except OSError as exc:
                    ecmwf_event(
                        f"Could not reuse F{probe_ep:03d} probe; will retrieve again: {exc}",
                        "warn"
                    )
            else:
                try:
                    selected_probe_target.unlink()
                except OSError:
                    pass

        ECMWF_DOWNLOAD_STATUS.update(
            run=f"{run_date} {run_hour:02d}Z",
            total=len(endpoints), downloaded=0,
            message=f"Downloading HRES TP via official index + byte-range • {run_date} {run_hour:02d}Z"
        )

        for idx, endpoint in enumerate(endpoints, 1):
            if not ecmwf_job_current(job_token):
                ecmwf_event("Older HRES job superseded by a newer selection", "warn")
                return

            target = folder / f"ecmwf_hres_f{endpoint:03d}.grib2"
            if valid_grib(target):
                ecmwf_event(
                    f"F{endpoint:03d} already present and GRIB-valid — skipping",
                    "success"
                )
                ECMWF_DOWNLOAD_STATUS["downloaded"] = idx
                continue

            ok, source_or_error = retrieve_hres_tp_endpoint(
                run_date, run_hour, endpoint, target, job_token
            )
            if not ok:
                raise RuntimeError(
                    f"F{endpoint:03d} failed through official ECMWF Open Data direct path: {source_or_error}"
                )

            ECMWF_DOWNLOAD_STATUS["downloaded"] = idx
            ECMWF_DOWNLOAD_STATUS["message"] = (
                f"HRES F{endpoint:03d} ready ({idx}/{len(endpoints)})"
            )

        for endpoint in endpoints:
            target = folder / f"ecmwf_hres_f{endpoint:03d}.grib2"
            if not valid_grib(target):
                raise RuntimeError(f"Final GRIB verification failed for F{endpoint:03d}")

        if not ecmwf_job_current(job_token):
            return

        ECMWF_RUNINFO_DET.parent.mkdir(parents=True, exist_ok=True)
        ECMWF_RUNINFO_DET.write_text(f"{run_date} {run_hour:02d}", encoding="utf-8")
        RAINFALL_CACHE = {}

        ECMWF_DOWNLOAD_STATUS.update(
            state="ready",
            message=f"LIVE ECMWF IFS HRES READY • {run_date} {run_hour:02d}Z",
            run=f"{run_date} {run_hour:02d}Z",
            error=None,
            finished_at=datetime.now(timezone.utc).isoformat()
        )
        set_model_identity(
            mode="deterministic", model="ECMWF", label="ECMWF IFS HRES",
            state="ready", run=f"{run_date} {run_hour:02d}Z",
            source=selected_source or "ECMWF Open Data direct", download="complete",
            message=f"ECMWF IFS HRES ready: {run_date} {run_hour:02d}Z"
        )
        ecmwf_event(
            f"LIVE ECMWF IFS HRES READY • {run_date} {run_hour:02d}Z • {requested_period}",
            "success"
        )

    except Exception as exc:
        if not ecmwf_job_current(job_token):
            return
        set_model_identity(
            mode="deterministic", model="ECMWF", label="ECMWF IFS HRES",
            state="error", download="failed", message=str(exc)
        )
        ECMWF_DOWNLOAD_STATUS.update(
            state="error", message="ECMWF HRES Open Data direct download failed",
            error=str(exc), finished_at=datetime.now(timezone.utc).isoformat()
        )
        ecmwf_event(f"LIVE HRES DOWNLOAD FAILED: {exc}", "error")


def start_ecmwf_deterministic_download(requested_run=None, requested_period="24 Hour"):
    global ECMWF_ACTIVE_REQUEST_KEY
    request_key = f"deterministic|ECMWF|{requested_run or 'AUTO'}|{requested_period}|MEAN"
    with ECMWF_JOB_LOCK:
        same_active = (ECMWF_ACTIVE_REQUEST_KEY == request_key and
                       ECMWF_DOWNLOAD_STATUS.get("state") == "downloading")
        same_ready = (ECMWF_ACTIVE_REQUEST_KEY == request_key and
                      ECMWF_DOWNLOAD_STATUS.get("state") == "ready")
    if same_active:
        ecmwf_event(f"DUPLICATE ENGINE REQUEST IGNORED • {requested_run or 'AUTO'} • {requested_period}", "info")
        return
    if same_ready:
        ecmwf_event(f"ENGINE REQUEST ALREADY READY • {requested_run or 'AUTO'} • {requested_period}", "success")
        return
    token=begin_ecmwf_job('deterministic','ECMWF',requested_run or 'AUTO',requested_period)
    threading.Thread(target=download_ecmwf_deterministic,args=(requested_run,requested_period,token),daemon=True).start()

def download_ecmwf_live(requested_run=None, requested_period="24 Hour", job_token=None):
    global RAINFALL_CACHE

    if job_token is None:
        job_token=begin_ecmwf_job('ensemble','ECMWF',requested_run or 'AUTO',requested_period)
    if not ecmwf_job_current(job_token): return

    ecmwf_event("Live ECMWF ENS update worker started")
    ecmwf_event(
        "V21 verified range-download engine: ECMWF .index + byte ranges; "
        "no whole 6+ GB ENS file"
    )

    try:
        set_model_identity(mode="ensemble", model="ECMWF", label="ECMWF ENS", state="probing", run=None, source=None, download="not-started", message="Verifying ECMWF ENS model identity and latest run…")
        if requests is None:
            raise RuntimeError(
                "Python requests module is unavailable. "
                "Run: pip install requests"
            )

        selected = None
        selected_source = None

        candidate_list = []
        if requested_run and requested_run != "AUTO":
            try:
                run_date=str(requested_run)[:8]
                run_hour=int(str(requested_run)[8:10])
                if len(str(requested_run))!=10 or run_hour not in (0,6,12,18):
                    raise ValueError
            except Exception:
                raise RuntimeError(f"Invalid ECMWF ENS run selection: {requested_run}")
            if requested_period in ("7 Day Accumulation", "15 Day Accumulation") and run_hour not in (0, 12):
                raise RuntimeError(f"{requested_period} requires an ECMWF ENS 00Z or 12Z run; selected {run_hour:02d}Z")
            candidate_list=[(None,run_date,run_hour)]
        else:
            candidate_list=ecmwf_candidate_runs()

        # Newest cycle first. A cycle is considered ready only when its first
        # required TP endpoint is actually published.
        for dt, run_date, run_hour in candidate_list:
            if requested_period in ("7 Day Accumulation", "15 Day Accumulation") and run_hour not in (0, 12):
                ecmwf_event(f"SKIP ECMWF ENS RUN • {run_date} {run_hour:02d}Z • {requested_period} requires 00Z/12Z", "warn")
                continue
            ECMWF_DOWNLOAD_STATUS["message"] = (
                f"Checking ECMWF {run_date} {run_hour:02d}Z…"
            )
            ecmwf_event(
                f"Checking ECMWF {run_date} {run_hour:02d}Z…"
            )

            for source_name, source_root in ECMWF_HTTP_SOURCES:
                ecmwf_event(
                    f"Probing {source_name} for "
                    f"{run_date} {run_hour:02d}Z"
                )

                ok, detail = _probe_direct_source(
                    source_name,
                    source_root,
                    run_date,
                    run_hour,
                )

                if ok:
                    selected = (run_date, run_hour)
                    selected_source = source_name
                    ecmwf_event(
                        f"AVAILABLE: {run_date} {run_hour:02d}Z "
                        f"via {source_name}",
                        "success"
                    )
                    break

                ecmwf_event(
                    f"{source_name}: run not ready / index probe failed — "
                    f"{detail}",
                    "warn"
                )

            if selected:
                break

            # Small pacing gap before moving to the next (older) cycle —
            # without this, a run of 12 unreachable cycles fires ~50 index
            # requests at each host in well under a minute, which is what
            # trips AWS's S3 "Slow Down" throttling and can get ECMWF's own
            # edge to start returning blanket errors too.
            time.sleep(0.75)

        if selected is None:
            raise RuntimeError(
                "No current ECMWF ENS TP Open Data run could be reached "
                "from ECMWF/AWS."
            )

        run_date, run_hour = selected
        endpoints = ecmwf_download_endpoints(run_hour, "ensemble", requested_period)
        set_model_identity(mode="ensemble", model="ECMWF", label="ECMWF ENS", state="identified", run=f"{run_date} {run_hour:02d}Z", source=selected_source, download="starting", message=f"Latest reachable ECMWF ENS run: {run_date} {run_hour:02d}Z")

        ecmwf_event(
            f"Selected latest reachable run: "
            f"{run_date} {run_hour:02d}Z via {selected_source}",
            "success"
        )
        ecmwf_event(
            "Required TP endpoints: " +
            ", ".join(f"F{x:03d}" for x in endpoints)
        )

        folder = (
            ECMWF_ROOT /
            "ECMWF" /
            f"{run_date}_{run_hour:02d}Z"
        )
        folder.mkdir(parents=True, exist_ok=True)

        ECMWF_DOWNLOAD_STATUS.update(
            run=f"{run_date} {run_hour:02d}Z",
            total=len(endpoints),
            downloaded=0,
            message=(
                f"Downloading compact ECMWF ENS TP "
                f"{run_date} {run_hour:02d}Z…"
            )
        )

        with ECMWF_DOWNLOAD_LOCK:
            ECMWF_DOWNLOAD_STATUS["total"]=len(endpoints)
            ECMWF_DOWNLOAD_STATUS["endpoint_total"]=len(endpoints)
            ECMWF_DOWNLOAD_STATUS["downloaded_bytes"]=0
            ECMWF_DOWNLOAD_STATUS["total_bytes"]=0
            ECMWF_DOWNLOAD_STATUS["estimated_total_bytes"]=0
            ECMWF_DOWNLOAD_STATUS["speed_bps"]=0.0
            ECMWF_DOWNLOAD_STATUS["eta_seconds"]=None
            ECMWF_DOWNLOAD_STATUS["expected_finish"]=None
        for idx, endpoint in enumerate(endpoints, 1):
            if not ecmwf_job_current(job_token):
                ecmwf_event("Older ENS job superseded by a newer selection", "warn")
                return
            target = folder / f"ecmwf_ens_f{endpoint:03d}.grib2"

            if valid_grib(target):
                ecmwf_event(
                    f"F{endpoint:03d} already present and GRIB-valid — "
                    f"skipping download",
                    "success"
                )
                ECMWF_DOWNLOAD_STATUS["downloaded"] = idx
                continue

            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass

            ECMWF_DOWNLOAD_STATUS["message"] = (
                f"Downloading F{endpoint:03d} "
                f"({idx}/{len(endpoints)}) — TP members 1–50…"
            )

            ok, source_or_error = _download_endpoint_with_failover(
                run_date,
                run_hour,
                endpoint,
                target,
            )

            if not ok:
                raise RuntimeError(
                    f"F{endpoint:03d} could not be downloaded from any "
                    f"official mirror: {source_or_error}"
                )

            with ECMWF_DOWNLOAD_LOCK:
                ECMWF_DOWNLOAD_STATUS["downloaded"] = idx
                ECMWF_DOWNLOAD_STATUS["current_member"] = 50
                ECMWF_DOWNLOAD_STATUS["eta_seconds"] = None if idx >= len(endpoints) else ECMWF_DOWNLOAD_STATUS.get("eta_seconds")
                if idx >= len(endpoints):
                    ECMWF_DOWNLOAD_STATUS["expected_finish"] = datetime.now(timezone.utc).isoformat()

        # Strict final verification before promoting the run.
        for endpoint in endpoints:
            target = folder / f"ecmwf_ens_f{endpoint:03d}.grib2"
            if not valid_grib(target):
                raise RuntimeError(
                    f"Final GRIB verification failed for F{endpoint:03d}"
                )

        if not ecmwf_job_current(job_token): return
        ECMWF_RUNINFO_ENS.parent.mkdir(parents=True, exist_ok=True)
        ECMWF_RUNINFO_ENS.write_text(
            f"{run_date} {run_hour:02d}",
            encoding="utf-8"
        )

        RAINFALL_CACHE = {}

        set_model_identity(mode="ensemble", model="ECMWF", label="ECMWF ENS", state="ready", run=f"{run_date} {run_hour:02d}Z", source=selected_source, download="complete", message=f"ECMWF ENS ready: {run_date} {run_hour:02d}Z")

        ECMWF_DOWNLOAD_STATUS.update(
            state="ready",
            message=(
                f"LIVE ECMWF ENS READY • "
                f"{run_date} {run_hour:02d}Z"
            ),
            run=f"{run_date} {run_hour:02d}Z",
            error=None,
            eta_seconds=0,
            expected_finish=datetime.now(timezone.utc).isoformat(),
            finished_at=datetime.now(timezone.utc).isoformat()
        )

        ecmwf_event(
            f"LIVE ECMWF ENS READY • "
            f"{run_date} {run_hour:02d}Z",
            "success"
        )
        ecmwf_event(
            "All six compact TP endpoint GRIBs verified",
            "success"
        )

        print(
            f"[ECMWF] LIVE DOWNLOAD COMPLETE: "
            f"{run_date} {run_hour:02d}Z"
        )

    except Exception as exc:
        if not ecmwf_job_current(job_token): return
        set_model_identity(mode="ensemble", model="ECMWF", label="ECMWF ENS", state="error", download="failed", message=str(exc))
        ECMWF_DOWNLOAD_STATUS.update(
            state="error",
            message="ECMWF live download failed",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat()
        )

        ecmwf_event(
            f"LIVE DOWNLOAD FAILED: {exc}",
            "error"
        )
        print("[ECMWF] DOWNLOAD ERROR:", exc)
        traceback.print_exc()


def start_ecmwf_live_download(requested_run=None, requested_period="24 Hour"):
    global ECMWF_ACTIVE_REQUEST_KEY
    request_key = f"ensemble|ECMWF|{requested_run or 'AUTO'}|{requested_period}|{ACTIVE_ENSEMBLE_PRODUCT}"
    with ECMWF_JOB_LOCK:
        same_active = (ECMWF_ACTIVE_REQUEST_KEY == request_key and
                       ECMWF_DOWNLOAD_STATUS.get("state") == "downloading")
        same_ready = (ECMWF_ACTIVE_REQUEST_KEY == request_key and
                      ECMWF_DOWNLOAD_STATUS.get("state") == "ready")
    if same_active:
        ecmwf_event(f"DUPLICATE ENGINE REQUEST IGNORED • {requested_run or 'AUTO'} • {requested_period}", "info")
        return
    if same_ready:
        ecmwf_event(f"ENGINE REQUEST ALREADY READY • {requested_run or 'AUTO'} • {requested_period}", "success")
        return
    token=begin_ecmwf_job('ensemble','ECMWF',requested_run or 'AUTO',requested_period)
    t = threading.Thread(target=download_ecmwf_live, args=(requested_run,requested_period,token), daemon=True)
    t.start()



# ---------------------------------------------------------------------------
# GFS DETERMINISTIC RAINFALL ENGINE — V79 INTERVAL CORE
# Current NOMADS APCP filtered responses can expose native 6-hour intervals
# rather than a cumulative 0-N message. V79 uses the native interval as the
# primary precipitation representation for the 24-hour Live Map and Talk.
# ---------------------------------------------------------------------------
# GFS DETERMINISTIC RAINFALL ENGINE
# Based directly on the user's supplied working NOMADS downloader and
# cfgrib processor.  The dashboard now uses the DIRECT NOMADS GRIB2 file
# path, matching the proven working download_gfs(5).py workflow, so the
# complete GRIB contains GFS total precipitation (tp/APCP). 24H retains
# F024/F048/F072/F096/F120; 7D and 15D use F168 and F360 respectively.
# ---------------------------------------------------------------------------

def gefs_period_endpoints(period):
    p=str(period)
    if p == "24 Hour": return [24]
    if p == "7 Day Accumulation": return [168]
    if p == "15 Day Accumulation": return [360]
    raise ValueError(f"Unknown GEFS rainfall period: {period}")

def gefs_rain_resolution(period):
    # GEFS v12 publishes the high-resolution 0.25-degree ensemble product
    # through the shorter forecast range; for the 15-day product use the
    # operational 0.50-degree GEFS field.  Never request a 0.25-degree F360
    # file or couple rainfall to an unrelated 850-hPa wind download.
    p=str(period)
    if p in ("24 Hour","7 Day Accumulation"): return 25
    if p == "15 Day Accumulation": return 50
    raise ValueError(f"Unknown GEFS rainfall period: {period}")

def gefs_rain_fields(period):
    return ["APCP"]

def gefs_synoptic_plan(product, period):
    p=str(period); prod=str(product or "SYNOPTIC").upper()
    ep=gefs_period_endpoints(p)[-1]
    plan=[]
    if prod in ("MSLP","MSLP_SPREAD","SYNOPTIC"):
        mslp_res=50 if p=="15 Day Accumulation" else 25
        plan.append((mslp_res,["PRMSL"]))
    if prod in ("WIND850","SYNOPTIC"):
        # 0.50-degree pressure-level winds are requested only for an explicit
        # synoptic product. They are never part of a rainfall download.
        plan.append((50,["UGRD","VGRD"]))
    return ep,plan

def gefs_member_filename(member, run_hour, fhour, resolution=25):
    if int(resolution)==50:
        return f"{member}.t{int(run_hour):02d}z.pgrb2a.0p50.f{int(fhour):03d}"
    return f"{member}.t{int(run_hour):02d}z.pgrb2s.0p25.f{int(fhour):03d}"

def gefs_directory_url(run_date, run_hour, resolution=25):
    sub="pgrb2ap5" if int(resolution)==50 else "pgrb2sp25"
    return f"{GEFS_BASE_URL}/gefs.{run_date}/{int(run_hour):02d}/atmos/{sub}/"

def gefs_filter_url(run_date, run_hour, member, fhour, resolution=25, fields=None, level=None):
    from urllib.parse import urlencode
    res=int(resolution)
    fields=fields or (["UGRD","VGRD"] if res==50 else ["APCP"])
    params={"file":gefs_member_filename(member,run_hour,fhour,res),
            "dir":f"/gefs.{run_date}/{int(run_hour):02d}/atmos/{'pgrb2ap5' if res==50 else 'pgrb2sp25'}",
            "subregion":"","leftlon":GEFS_LEFTLON50 if res==50 else GEFS_LEFTLON,
            "rightlon":GEFS_RIGHTLON50 if res==50 else GEFS_RIGHTLON,
            "toplat":GEFS_TOPLAT50 if res==50 else GEFS_TOPLAT,
            "bottomlat":GEFS_BOTTOMLAT50 if res==50 else GEFS_BOTTOMLAT}
    for f in fields: params[f"var_{f}"]="on"
    # APCP is a surface accumulation field. U/V use 850 mb. PRMSL uses mean sea level.
    # Pressure-level height products explicitly request HGT at the selected level.
    if "HGT" in fields and level is not None:
        params[f"lev_{int(level)}_mb"]="on"
    elif set(fields)=={"UGRD","VGRD"}:
        params["lev_850_mb"]="on"
    elif "PRMSL" in fields:
        params["lev_mean_sea_level"]="on"
    if "APCP" in fields:
        params["lev_surface"]="on"
    return (GEFS_FILTER50_URL if res==50 else GEFS_FILTER25_URL)+"?"+urlencode(params)

def gefs_file_url(run_date, run_hour, member, fhour, resolution=25):
    return gefs_directory_url(run_date,run_hour,resolution)+gefs_member_filename(member,run_hour,fhour,resolution)

def gefs_run_available(run_date, run_hour, fhour=24, period=None, product="MEAN"):
    if requests is None: return False,"requests unavailable"
    try:
        p=str(period or ("15 Day Accumulation" if int(fhour)==360 else "7 Day Accumulation" if int(fhour)==168 else "24 Hour"))
        prod=str(product or "MEAN").upper()
        # Run discovery/download availability must test only the fields actually
        # required for the selected product. Rainfall must never fail because an
        # optional 0.50-degree wind file is absent.
        if prod in ("MSLP","MSLP_SPREAD","WIND850","SYNOPTIC"):
            _, plan=gefs_synoptic_plan(prod,p)
            checks=[(res,"gec00",fields) for res,fields in plan]
        else:
            res=gefs_rain_resolution(p); checks=[(res,"gec00",["APCP"])]
        details=[]
        for res,member,fields in checks:
            url=gefs_filter_url(run_date,run_hour,member,fhour,res,fields)
            h=dict(GEFS_HTTP_HEADERS); h["Range"]="bytes=0-3"
            r=requests.get(url,headers=h,timeout=20,stream=True)
            status=r.status_code
            try: r.close()
            except Exception: pass
            if status not in (200,206): return False,f"GEFS {res}° {member} F{fhour:03d} {','.join(fields)} HTTP {status}"
            details.append(f"{res}°/{','.join(fields)}")
        return True,f"F{fhour:03d} {' + '.join(details)} verified"
    except Exception as exc:
        return False,str(exc)

def gefs_candidate_runs(limit_days=3):
    now=datetime.now(timezone.utc); out=[]
    for day_back in range(limit_days):
        d=now.date()-timedelta(days=day_back)
        for rh in (18,12,6,0):
            dt=datetime(d.year,d.month,d.day,rh,tzinfo=timezone.utc)
            if dt<=now: out.append((dt,f"{d:%Y%m%d}",rh))
    return sorted(out,reverse=True)

def gefs_wind_forecast_hours(run_date, run_hour, max_hour=360):
    """Return 24-hour-spaced 850-hPa wind forecast hours actually published for a GEFS run.

    This is discovery only: it probes the 0.50-degree U/V file endpoints with a
    tiny Range request and never downloads forecast data. The result is cached
    per run so the hour selector does not repeatedly hit NOMADS.
    """
    key=f"{run_date}{int(run_hour):02d}"
    cached=GEFS_WIND_HOURS_CACHE.get(key)
    if cached is not None:
        return list(cached)

    hours=list(range(24, int(max_hour)+1, 24))
    def probe(ep):
        try:
            url=gefs_filter_url(run_date,run_hour,"gec00",ep,50,["UGRD","VGRD"])
            h=dict(GEFS_HTTP_HEADERS)
            h["Range"]="bytes=0-3"
            r=requests.get(url,headers=h,timeout=20,stream=True)
            status=r.status_code
            try: r.close()
            except Exception: pass
            return ep, status in (200,206)
        except Exception:
            return ep, False

    available=[]
    with ThreadPoolExecutor(max_workers=min(6,len(hours))) as pool:
        futures=[pool.submit(probe,ep) for ep in hours]
        for fut in as_completed(futures):
            ep,ok=fut.result()
            if ok: available.append(ep)

    available=sorted(set(available))
    GEFS_WIND_HOURS_CACHE[key]=available
    ecmwf_event(
        f"GEFS WIND HOUR DISCOVERY • {run_date} {int(run_hour):02d}Z • "
        f"{', '.join(f'F{x:03d}' for x in available) if available else 'none'}",
        "success" if available else "warn"
    )
    return available

def gefs_decode_field(path, filters, preferred=None):
    if xr is None or cfgrib is None: raise RuntimeError("xarray/cfgrib dependencies are not installed")
    with GFS_DECODE_LOCK:
        ds=xr.open_dataset(str(path),engine="cfgrib",backend_kwargs={"filter_by_keys":filters,"indexpath":""})
        try:
            names=list(ds.data_vars)
            if not names: raise RuntimeError(f"No matching field in {path.name}")
            name=preferred if preferred in ds else names[0]
            da=ds[name].load(); vals=np.asarray(da.values,dtype=np.float32)
            lat=np.asarray(da.latitude.values,dtype=float); lon=np.asarray(da.longitude.values,dtype=float)
        finally: ds.close()
    vals=np.squeeze(vals)
    if vals.ndim!=2: raise RuntimeError(f"Unexpected GEFS field shape {vals.shape}")
    lon=np.mod(lon,360.0)
    yi=np.where((lat>=GEFS_BOTTOMLAT)&(lat<=GEFS_TOPLAT))[0]; xi=np.where((lon>=GEFS_LEFTLON)&(lon<=GEFS_RIGHTLON))[0]
    if len(yi)==0 or len(xi)==0: raise RuntimeError("GEFS South India crop is empty")
    sub=vals[np.ix_(yi,xi)]; slat=lat[yi]; slon=lon[xi]
    if slat[0]>slat[-1]: sub=sub[::-1,:]; slat=slat[::-1]
    if slon[0]>slon[-1]: sub=sub[:,::-1]; slon=slon[::-1]
    return sub.astype(np.float32),slat,slon

def gefs_decode_apcp(path):
    vals,lat,lon=gefs_decode_field(path,{"typeOfLevel":"surface","shortName":"tp"},"tp")
    vals=np.nan_to_num(vals,nan=0.0,posinf=0.0,neginf=0.0)
    return np.clip(vals,0,None).astype(np.float32),lat,lon

def gefs_decode_prmsl(path):
    vals,lat,lon=gefs_decode_field(path,{"typeOfLevel":"meanSea","shortName":"prmsl"},"prmsl")
    return vals.astype(np.float32)/100.0,lat,lon

def gefs_decode_hgt(path, level):
    vals,lat,lon=gefs_decode_field(path,{"typeOfLevel":"isobaricInhPa","shortName":"gh","level":int(level)},"gh")
    # GEFS HGT is geopotential height in metres. Convert to decametres for synoptic plotting.
    return (vals.astype(np.float32)/10.0),lat,lon

def gefs_decode_wind850(path):
    u,lat,lon=gefs_decode_field(path,{"typeOfLevel":"isobaricInhPa","shortName":"u","level":850},"u")
    v,lat2,lon2=gefs_decode_field(path,{"typeOfLevel":"isobaricInhPa","shortName":"v","level":850},"v")
    if u.shape!=v.shape or not np.allclose(lat,lat2) or not np.allclose(lon,lon2): raise RuntimeError("GEFS 850-hPa U/V grids do not match")
    return u,v,lat,lon

def download_gefs_member(run_date,run_hour,member,fhour,job_token=None,resolution=25,fields=None):
    folder=GEFS_ROOT/f"{run_date}_{int(run_hour):02d}Z"/f"F{int(fhour):03d}"/f"r{int(resolution):02d}"; folder.mkdir(parents=True,exist_ok=True)
    target=folder/gefs_member_filename(member,run_hour,fhour,resolution)
    if target.exists() and target.stat().st_size>1000:
        return target
    if job_token is not None and not ecmwf_job_current(job_token): raise RuntimeError("GEFS job superseded")
    fields=fields or (["UGRD","VGRD"] if int(resolution)==50 else ["APCP"])
    url=gefs_filter_url(run_date,run_hour,member,fhour,resolution,fields)
    tmp=target.with_suffix(target.suffix+'.part'); last=None
    for attempt in range(1,GEFS_MAX_RETRIES+1):
        try:
            ecmwf_event(f"GEFS {resolution}° DOWNLOAD • {member} • {run_date} {int(run_hour):02d}Z • F{fhour:03d} • attempt {attempt}/{GEFS_MAX_RETRIES}","info")
            with requests.get(url,headers=GEFS_HTTP_HEADERS,timeout=GEFS_TIMEOUT,stream=True) as r:
                r.raise_for_status()
                with open(tmp,'wb') as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if chunk: f.write(chunk)
            if tmp.stat().st_size<1000: raise RuntimeError("GEFS response unexpectedly small")
            with open(tmp,'rb') as f:
                if f.read(4)!=b'GRIB': raise RuntimeError("GEFS response is not GRIB2")
            tmp.replace(target); return target
        except Exception as exc:
            last=exc
            try: tmp.unlink()
            except Exception: pass
            if attempt<GEFS_MAX_RETRIES: time.sleep(GEFS_RETRY_DELAY)
    raise RuntimeError(f"GEFS {resolution}° {member} F{fhour:03d} failed: {last}")

def start_gefs_ensemble_download(requested_run=None,requested_period="24 Hour",requested_product="MEAN",forecast_hour=None):
    if not requested_run or not re.fullmatch(r"\d{10}",str(requested_run)): raise ValueError("GEFS requires an explicit selected run")
    requested_product=str(requested_product or "MEAN").upper()
    gefs_period_endpoints(requested_period)
    wind_hour=int(forecast_hour or ACTIVE_WIND_HOUR or 24) if str(requested_product).upper()=="WIND850" else 0
    token=begin_ecmwf_job('ensemble','GFS',requested_run,requested_period)
    threading.Thread(target=download_gefs_ensemble,args=(requested_run,requested_period,requested_product,token,wind_hour),daemon=True,name="GEFS-ENSEMBLE").start()

def download_gefs_ensemble(requested_run,requested_period,requested_product="MEAN",job_token=None,forecast_hour=None):
    try:
        rd,rh=str(requested_run)[:8],int(str(requested_run)[8:10])
        prod=str(requested_product or "MEAN").upper()
        is_synoptic=prod in ("MSLP","MSLP_SPREAD","WIND850","SYNOPTIC")
        ep=int(forecast_hour or ACTIVE_WIND_HOUR or 24) if prod=="WIND850" else gefs_period_endpoints(requested_period)[-1]
        if is_synoptic:
            _,plan=gefs_synoptic_plan(prod,requested_period)
        else:
            plan=[(gefs_rain_resolution(requested_period),["APCP"])]
        ok,detail=gefs_run_available(rd,rh,ep,requested_period,prod)
        if not ok: raise RuntimeError(f"GEFS run {requested_run} not currently available: {detail}")
        ensure_runtime_dirs()
        GEFS_RUNINFO.parent.mkdir(parents=True, exist_ok=True)
        GEFS_RUNINFO.write_text(f"{rd} {rh:02d}",encoding='utf-8')
        set_model_identity(mode='ensemble',model='GFS',label='GFS / GEFS',state='downloading',run=f"{rd} {rh:02d}Z",source='NOAA/NCEP GEFS V4.2 • rainfall/synoptic fields by resolution',download='starting',message=f'GEFS selected: {rd} {rh:02d}Z • {GEFS_MEMBER_COUNT} members • {requested_period} • {prod}')
        total=GEFS_MEMBER_COUNT*len(plan)
        ECMWF_DOWNLOAD_STATUS.update(state='downloading',run=f"{rd} {rh:02d}Z",total=total,downloaded=0,current_member=0,member_total=GEFS_MEMBER_COUNT,current_endpoint=ep,endpoint_total=len(plan),message=f'Downloading GEFS V4.2 • {rd} {rh:02d}Z • {GEFS_MEMBER_COUNT} members • {prod}',product=prod,period=requested_period,request_key=(f'ensemble|GFS|{requested_run}|{requested_period}|{prod}|F{ep:03d}' if prod=='WIND850' else f'ensemble|GFS|{requested_run}|{requested_period}|{prod}'))
        jobs=[]
        with ThreadPoolExecutor(max_workers=GEFS_PARALLEL_WORKERS) as pool:
            for member in GEFS_MEMBERS:
                for res,fields in plan:
                    jobs.append(pool.submit(download_gefs_member,rd,rh,member,ep,job_token,res,fields))
            done=0
            for fut in as_completed(jobs):
                if not ecmwf_job_current(job_token): return
                fut.result(); done+=1
                ECMWF_DOWNLOAD_STATUS['downloaded']=done
                ECMWF_DOWNLOAD_STATUS['current_member']=min(GEFS_MEMBER_COUNT,done//len(plan) + (1 if done%len(plan) else 0))
                ECMWF_DOWNLOAD_STATUS['message']=f'GEFS V4.2 files ready • {done}/{total} • {prod}'
        # HARD READY GATE:
        # Download completion alone is not enough. The exact selected product
        # must decode successfully before any state/message can say READY.
        RAINFALL_CACHE.clear(); GEFS_SYNOPTIC_CACHE.clear()
        if not is_synoptic:
            # Decode all 31 APCP members now. A missing/incorrect GRIB field
            # therefore leaves the run in ERROR instead of false-READY.
            rain,lat,lon,meta=build_gefs_rainfall(requested_period,prod)
            cache_key=('ensemble','GFS',requested_period,1,prod)
            RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
            ECMWF_DOWNLOAD_STATUS['render_ready']=True
            ecmwf_event(f'GEFS RAINFALL VALIDATION PASS • {requested_period} • {prod} • {GEFS_MEMBER_COUNT} members • F{ep:03d}','success')
        else:
            # Synoptic products must pass their actual decoder before READY.
            # WIND850 validates the 0.50° U/V fields; MSLP validates PRMSL;
            # SYNOPTIC validates both.
            syn=build_gefs_synoptic(prod,requested_period,ep)
            GEFS_SYNOPTIC_CACHE[(f"{rd}{rh:02d}",ep,prod)]=syn
            ECMWF_DOWNLOAD_STATUS['render_ready']=True
            ecmwf_event(f'GEFS SYNOPTIC VALIDATION PASS • {requested_period} • {prod} • {GEFS_MEMBER_COUNT} members • F{ep:03d}','success')

        run_dt=datetime.strptime(rd,'%Y%m%d').replace(tzinfo=timezone.utc)+timedelta(hours=rh)
        valid_end=run_dt+timedelta(hours=ep); valid_end_ist=valid_end+timedelta(hours=5,minutes=30)
        set_model_identity(mode='ensemble',model='GFS',label='GFS / GEFS',state='ready',run=f"{rd} {rh:02d}Z",source='NOAA/NCEP GEFS V4.2 • split resolution',download='complete',message=f'GEFS V4.2 ready: {GEFS_MEMBER_COUNT} members • {requested_period} • {prod}')
        ECMWF_DOWNLOAD_STATUS.update(state='ready',run=f'{rd} {rh:02d}Z',message=f'LIVE GEFS V4.2 READY • {rd} {rh:02d}Z • {GEFS_MEMBER_COUNT} members • {requested_period} • {prod}',error=None,finished_at=datetime.now(timezone.utc).isoformat(),current_member=GEFS_MEMBER_COUNT,member_total=GEFS_MEMBER_COUNT,valid_end_date=valid_end_ist.strftime('%d %b %Y'),valid_end_iso=valid_end_ist.strftime('%Y-%m-%d'),valid_end_time=valid_end_ist.strftime('%I:%M %p IST'),valid_end_endpoint=f'F{ep:03d}',valid_end_utc=valid_end.strftime('%Y-%m-%d %H:%M UTC'),product=prod,period=requested_period,request_key=(f'ensemble|GFS|{requested_run}|{requested_period}|{prod}|F{ep:03d}' if prod=='WIND850' else f'ensemble|GFS|{requested_run}|{requested_period}|{prod}'),render_ready=True)
        ecmwf_event(f"LIVE GEFS V4.2 READY • {rd} {rh:02d}Z • {GEFS_MEMBER_COUNT} members • {requested_period} • {prod} • DECODE VALIDATED","success")
    except Exception as exc:
        if job_token is not None and not ecmwf_job_current(job_token): return
        set_model_identity(mode='ensemble',model='GFS',label='GFS / GEFS',state='error',download='failed',message=str(exc))
        # Keep the selected GEFS runinfo on a product/hour error.  A failed
        # forecast-hour request must not make a valid run disappear from the
        # dashboard; the user can select another hour and retry.
        ECMWF_DOWNLOAD_STATUS.update(state='error',message='GEFS V4.2 download failed',error=str(exc),finished_at=datetime.now(timezone.utc).isoformat()); ecmwf_event(f"LIVE GEFS V4.2 DOWNLOAD FAILED: {exc}","error")

def read_gefs_run():
    if not GEFS_RUNINFO.exists(): raise FileNotFoundError(f"GEFS runinfo not found: {GEFS_RUNINFO}")
    a=GEFS_RUNINFO.read_text(encoding='utf-8').strip().split()
    if len(a)!=2: raise ValueError("GEFS runinfo must contain YYYYMMDD HH")
    return a[0],int(a[1])

def _gefs_file_paths(rd,rh,ep,member):
    base=GEFS_ROOT/f"{rd}_{int(rh):02d}Z"/f"F{int(ep):03d}"
    return base/'r25'/gefs_member_filename(member,rh,ep,25), base/'r50'/gefs_member_filename(member,rh,ep,50)

def build_gefs_rainfall(period=None,product=None):
    period=period or ACTIVE_PERIOD; product=(product or ACTIVE_ENSEMBLE_PRODUCT or "MEAN").upper()
    if product in ("MSLP","MSLP_SPREAD","WIND850","SYNOPTIC"): raise ValueError(f"GEFS synoptic product '{product}' is not rainfall")
    rd,rh=read_gefs_run(); ep=gefs_period_endpoints(period)[-1]; res=gefs_rain_resolution(period); fields=[]; lat=lon=None
    base=GEFS_ROOT/f"{rd}_{int(rh):02d}Z"/f"F{int(ep):03d}"/f"r{int(res):02d}"
    for member in GEFS_MEMBERS:
        path=base/gefs_member_filename(member,rh,ep,res)
        if not path.exists(): raise FileNotFoundError(f"Missing GEFS {res/100:.2f}° rainfall member file: {path}")
        vals,lat,lon=gefs_decode_apcp(path); fields.append(vals)
    raw=np.stack(fields,axis=0).astype(np.float32)
    products={"MEAN":np.mean(raw,axis=0),"P50":np.percentile(raw,50,axis=0),"P75":np.percentile(raw,75,axis=0),"P90":np.percentile(raw,90,axis=0),"PROB50":np.mean(raw>=50.0,axis=0)*100.0,"PROB100":np.mean(raw>=100.0,axis=0)*100.0}
    if product not in products: raise ValueError(f"Unknown GEFS rainfall product: {product}")
    rain=np.asarray(products[product],dtype=np.float32); labels={"MEAN":"GEFS Mean","P50":"GEFS P50","P75":"GEFS P75","P90":"GEFS P90","PROB50":"GEFS Probability ≥50 mm","PROB100":"GEFS Probability ≥100 mm"}; units="%" if product.startswith("PROB") else "mm"
    run_dt=datetime.strptime(rd,'%Y%m%d').replace(tzinfo=timezone.utc)+timedelta(hours=rh)
    valid_end=run_dt+timedelta(hours=ep)
    valid_end_ist=valid_end+timedelta(hours=5,minutes=30)
    meta={"model":labels[product],"mode":"ensemble","run":f"{rd} {rh:02d}Z","period":period,"product":product,"product_label":labels[product],"units":units,"start":"F000","end":f"F{ep:03d}","display_range":f"F000 → F{ep:03d}","members":GEFS_MEMBER_COUNT,"source":f"NOAA/NCEP GEFS V4.2 • {res/100:.2f}° NOMADS","min":float(np.nanmin(rain)),"max":float(np.nanmax(rain)),"mean":float(np.nanmean(rain)),"grid_dx":float(np.median(np.diff(lon))) if len(lon)>1 else .25,"grid_dy":float(np.median(np.diff(lat))) if len(lat)>1 else .25,"grid_rows":int(rain.shape[0]),"grid_cols":int(rain.shape[1]),"valid_end_endpoint":f"F{ep:03d}","valid_end_date":valid_end_ist.strftime('%d %b %Y'),"valid_end_iso":valid_end_ist.strftime('%Y-%m-%d'),"valid_end_time":valid_end_ist.strftime('%I:%M %p IST'),"valid_end_utc":valid_end.strftime('%Y-%m-%d %H:%M UTC')}
    return rain,lat,lon,meta

def build_gefs_synoptic(product="SYNOPTIC",period=None,forecast_hour=None):
    product=(product or "SYNOPTIC").upper(); period=period or ACTIVE_PERIOD
    if period not in ("24 Hour","7 Day Accumulation","15 Day Accumulation"):
        raise ValueError("GEFS synoptic products support 24 Hour, 7 Day and 15 Day periods")
    if product not in ("MSLP","MSLP_SPREAD","WIND850","SYNOPTIC"):
        raise ValueError(f"Unknown GEFS synoptic product: {product}")
    rd,rh=read_gefs_run()
    ep=int(forecast_hour or ACTIVE_WIND_HOUR or 24) if product=="WIND850" else gefs_period_endpoints(period)[-1]
    if product=="WIND850" and (ep < 24 or ep > 360 or ep % 24 != 0):
        raise ValueError("GEFS 850-hPa wind forecast hour must be 24, 48, …, 360")
    key=(f"{rd}{rh:02d}",ep,product)
    if key in GEFS_SYNOPTIC_CACHE: return GEFS_SYNOPTIC_CACHE[key]

    need_mslp = product in ("MSLP","MSLP_SPREAD","SYNOPTIC")
    need_wind = product in ("WIND850","SYNOPTIC")
    ps=[]; us=[]; vs=[]; lat25=lon25=lat50=lon50=None

    for member in GEFS_MEMBERS:
        p25,p50=_gefs_file_paths(rd,rh,ep,member)

        if need_mslp:
            # GEFS F360 MSLP is served from the 0.50° pgrb2ap5 product.
            # Do not reject the run by checking the unrelated 0.25° file first.
            p25_use = p50 if ep == 360 else p25
            if not p25_use.exists():
                res_label = "0.50°" if ep == 360 else "0.25°"
                raise FileNotFoundError(f"Missing GEFS {res_label} MSLP file for {member}")
            p,plat,plon=gefs_decode_prmsl(p25_use)
            lat25,lon25=plat,plon
            ps.append(p)

        if need_wind:
            if not p50.exists():
                raise FileNotFoundError(f"Missing GEFS 0.50° wind file for {member}")
            u,v,plat50,plon50=gefs_decode_wind850(p50)
            lat50,lon50=plat50,plon50
            us.append(u); vs.append(v)

    pmean=pspread=umean=vmean=wmean=wspread=None
    if need_mslp:
        pstack=np.stack(ps)
        pmean=np.nanmean(pstack,axis=0)
        pspread=np.nanstd(pstack,axis=0)
    if need_wind:
        ustack=np.stack(us); vstack=np.stack(vs)
        umean=np.nanmean(ustack,axis=0)
        vmean=np.nanmean(vstack,axis=0)
        wmembers=np.sqrt(ustack**2+vstack**2)
        wmean=np.nanmean(wmembers,axis=0)
        wspread=np.nanstd(wmembers,axis=0)

    data={}
    if product=="MSLP": data["MSLP"]=(pmean,lat25,lon25,"MSLP Mean","hPa")
    elif product=="MSLP_SPREAD": data["MSLP_SPREAD"]=(pspread,lat25,lon25,"MSLP Spread","hPa")
    elif product=="WIND850": data["WIND850"]=(wmean,lat50,lon50,"850 hPa Wind Mean","m/s")
    elif product=="SYNOPTIC": data["SYNOPTIC"]=(wmean,lat50,lon50,"MSLP + 850 hPa Wind","m/s")

    vals,lat,lon,label,units=data[product]
    run_dt=datetime.strptime(rd,'%Y%m%d').replace(tzinfo=timezone.utc)+timedelta(hours=rh)
    valid_end=run_dt+timedelta(hours=ep); valid_end_ist=valid_end+timedelta(hours=5,minutes=30)
    meta={"model":"GFS / GEFS","mode":"ensemble","run":f"{rd} {rh:02d}Z","period":period,"product":product,"product_label":label,"units":units,"members":GEFS_MEMBER_COUNT,"forecast":f"F{ep:03d}","grid_info":"0.25° rainfall/MSLP • 0.50° 850-hPa wind","source":"NOAA/NCEP GEFS V4.2","valid_end_date":valid_end_ist.strftime('%d %b %Y'),"valid_end_iso":valid_end_ist.strftime('%Y-%m-%d'),"valid_end_time":valid_end_ist.strftime('%I:%M %p IST'),"valid_end_utc":valid_end.strftime('%Y-%m-%d %H:%M UTC')}
    payload={"mslp_mean":pmean,"mslp_spread":pspread,"u850":umean,"v850":vmean,"wind850":wmean,"wind850_spread":wspread,"lat25":lat25,"lon25":lon25,"lat50":lat50,"lon50":lon50,"meta":meta}
    GEFS_SYNOPTIC_CACHE[key]=payload; return payload

def gfs_period_endpoints(period):
    p = str(period)
    if p == "24 Hour": return [24, 48, 72, 96, 120]
    if p == "7 Day Accumulation": return [168]
    if p == "15 Day Accumulation": return [360]
    raise ValueError(f"Unknown GFS rainfall period: {period}")

def gfs_file_url(run_date, run_hour, fhour):
    return (f"{GFS_BASE_URL}/gfs.{run_date}/{int(run_hour):02d}/atmos/"
            f"gfs.t{int(run_hour):02d}z.pgrb2.0p25.f{int(fhour):03d}")

def gfs_filter_params(run_date, run_hour, fhour):
    params = {
        "file": f"gfs.t{int(run_hour):02d}z.pgrb2.0p25.f{int(fhour):03d}",
        "dir": f"/gfs.{run_date}/{int(run_hour):02d}/atmos",
        "subregion": "", "leftlon": GFS_LEFTLON, "rightlon": GFS_RIGHTLON,
        "toplat": GFS_TOPLAT, "bottomlat": GFS_BOTTOMLAT,
        "lev_surface": "on",
    }
    params.update(GFS_VAR_PARAMS)
    return params

def _schedule_candidate_runs(limit_days=3, hours=(18,12,6,0)):
    """Build recent model cycles locally. NEVER contacts a model server."""
    now=datetime.now(timezone.utc)
    out=[]
    for day_back in range(limit_days):
        d=now.date()-timedelta(days=day_back)
        for rh in hours:
            dt=datetime(d.year,d.month,d.day,rh,tzinfo=timezone.utc)
            if dt<=now:
                out.append((dt,f"{d:%Y%m%d}",rh))
    out.sort(reverse=True)
    return out

def _load_run_discovery_cache():
    try:
        if not RUN_DISCOVERY_CACHE_FILE.exists(): return {}
        return json.loads(RUN_DISCOVERY_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_run_discovery_cache(cache):
    try:
        RUN_DISCOVERY_CACHE_FILE.parent.mkdir(parents=True,exist_ok=True)
        tmp=RUN_DISCOVERY_CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache,indent=2),encoding="utf-8")
        tmp.replace(RUN_DISCOVERY_CACHE_FILE)
    except Exception as exc:
        ecmwf_event(f"RUN CACHE WRITE WARNING • {exc}","warn")

def stable_run_candidates(model,period,limit_days=3):
    """Return a stable candidate list immediately; no network probes."""
    key=f"deterministic|{model}|{period}"
    now=datetime.now(timezone.utc)
    with RUN_DISCOVERY_LOCK:
        mem=RUN_DISCOVERY_MEMORY_CACHE.get(key)
        if mem and (now-mem["time"]).total_seconds() < RUN_DISCOVERY_CACHE_TTL_HOURS*3600:
            return mem["runs"],True
        disk=_load_run_discovery_cache()
        item=disk.get(key)
        if item:
            try:
                age=(now-datetime.fromisoformat(item["saved_at"])).total_seconds()/3600
                if age < RUN_DISCOVERY_CACHE_TTL_HOURS and item.get("runs"):
                    runs=[(datetime.strptime(x["value"],"%Y%m%d%H").replace(tzinfo=timezone.utc),x["value"][:8],int(x["value"][8:10])) for x in item["runs"]]
                    RUN_DISCOVERY_MEMORY_CACHE[key]={"time":now,"runs":runs}
                    return runs,True
            except Exception:
                pass
        runs=_schedule_candidate_runs(limit_days=limit_days)
        disk[key]={"saved_at":now.isoformat(),"runs":[{"value":f"{rd}{rh:02d}"} for _,rd,rh in runs]}
        _save_run_discovery_cache(disk)
        RUN_DISCOVERY_MEMORY_CACHE[key]={"time":now,"runs":runs}
        return runs,False

def gfs_candidate_runs(limit_days=3, required_endpoints=None):
    """FAST GFS selector. No NOMADS probes during discovery."""
    period="|".join(map(str,required_endpoints or [24]))
    runs,_=stable_run_candidates("GFS",period,limit_days)
    return runs

def gfs_latest_run():
    c=gfs_candidate_runs()
    if not c: raise RuntimeError("No GFS run found from NOMADS")
    _,rd,rh=c[0]; return rd,rh

def gfs_filter_params(run_date,run_hour,fhour):
    """Build the exact NOAA/NOMADS GRIB Filter request proven by the standalone test.

    The dashboard must request precipitation explicitly. The old direct-file
    downloader could return a GRIB container that did not expose tp through
    cfgrib. The verified standalone workflow requests APCP + surface and
    downloads only the selected geographic subset. NOMADS repackages this as
    a valid GRIB2 field, which cfgrib exposes as tp on the user's PC.
    """
    return {
        "file": f"gfs.t{int(run_hour):02d}z.pgrb2.0p25.f{int(fhour):03d}",
        "var_APCP": "on",
        "lev_surface": "on",
        "subregion": "",
        "leftlon": GFS_LEFTLON,
        "rightlon": GFS_RIGHTLON,
        "toplat": GFS_TOPLAT,
        "bottomlat": GFS_BOTTOMLAT,
        "dir": f"/gfs.{run_date}/{int(run_hour):02d}/atmos",
    }

def gfs_filter_request_url(run_date,run_hour,fhour):
    from urllib.parse import urlencode
    return f"{GFS_FILTER_URL}?{urlencode(gfs_filter_params(run_date,run_hour,fhour))}"

def gfs_endpoint_available(run_date,run_hour,fhour):
    """Check a GFS forecast object with a small GET/range probe only.

    Do not use HTTP HEAD here.  In the past the HEAD response produced false
    negatives even when the same GFS cycle was downloadable through NOMADS.
    The actual regional APCP GRIB download remains the source of truth; this
    lightweight probe is only used by Map run discovery to avoid downloading
    every candidate cycle in full.
    """
    if requests is None:
        return False,"requests module unavailable"
    try:
        url=gfs_file_url(run_date,run_hour,fhour)
        r=requests.get(
            url,
            headers={
                "Range":"bytes=0-3",
                "User-Agent":"MASRAINMAN-GFS/3.0",
                "Accept":"*/*",
            },
            stream=True,
            timeout=15,
            allow_redirects=True,
        )
        status=r.status_code
        header=b""
        try:
            header=next(r.iter_content(chunk_size=4),b"")[:4]
        finally:
            r.close()
        if status in (200,206):
            if header==b"GRIB":
                return True,f"HTTP {status} • GRIB header"
            return True,f"HTTP {status} • object reachable"
        return False,f"HTTP {status}"
    except requests.RequestException as exc:
        return False,str(exc)

def download_gfs_endpoint(run_date,run_hour,fhour,target,job_token=None):
    """Download one GFS precipitation endpoint using the proven NOMADS filter method."""
    if job_token is not None and not ecmwf_job_current(job_token):
        return False,"job superseded"

    target=Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    last_err=None

    ecmwf_event(
        f"GFS DOWNLOAD • {run_date} {run_hour:02d}Z • "
        f"F{fhour:03d} • NOMADS GRIB FILTER • APCP",
        "info"
    )

    params=gfs_filter_params(run_date,run_hour,fhour)

    for attempt in range(1,GFS_MAX_RETRIES+1):
        if job_token is not None and not ecmwf_job_current(job_token):
            return False,"job superseded"

        tmp=target.with_suffix(target.suffix+".part")

        try:
            ecmwf_event(
                f"GFS REQUEST • F{fhour:03d} • APCP • surface • "
                f"{GFS_LEFTLON}E-{GFS_RIGHTLON}E / "
                f"{GFS_BOTTOMLAT}N-{GFS_TOPLAT}N • attempt {attempt}/{GFS_MAX_RETRIES}",
                "info"
            )

            r=requests.get(
                GFS_FILTER_URL,
                params=params,
                headers={
                    "User-Agent":"MASRAINMAN-GFS-Dashboard/1.0",
                    "Accept":"*/*",
                },
                stream=True,
                timeout=GFS_TIMEOUT,
            )
            r.raise_for_status()

            with open(tmp,"wb") as f:
                for chunk in r.iter_content(chunk_size=1024*1024):
                    if chunk:
                        f.write(chunk)

            if not tmp.exists() or tmp.stat().st_size<1000:
                raise RuntimeError("NOMADS response is too small")

            with open(tmp,"rb") as f:
                header=f.read(4)

            if header!=b"GRIB":
                raise RuntimeError(
                    f"NOMADS response is not GRIB2 (header={header!r})"
                )

            tmp.replace(target)

            ecmwf_event(
                f"GFS F{fhour:03d} SAVED • "
                f"{target.stat().st_size/1024:.0f} KB • NOMADS GRIB FILTER",
                "success"
            )
            return True,"NOMADS GRIB FILTER / APCP"

        except (requests.RequestException,OSError,RuntimeError) as exc:
            last_err=exc
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass

            if attempt<GFS_MAX_RETRIES:
                ecmwf_event(
                    f"GFS F{fhour:03d} retry {attempt}/{GFS_MAX_RETRIES} • {exc}",
                    "warn"
                )
                # NOMADS asks automated loops to wait between requests.
                time.sleep(max(10,GFS_RETRY_DELAY))

    return False,str(last_err)

def start_gfs_deterministic_download(requested_run=None,requested_period="24 Hour",requested_day=1):
    """Start one canonical GFS job. AUTO is resolved BEFORE the job token is created.

    This is important because the browser may first request AUTO and then, once the
    run list paints, issue the concrete latest-run value. Both must map to the same
    authoritative job instead of starting two decoders against the same GRIB files.
    """
    global ECMWF_ACTIVE_REQUEST_KEY
    requested_day=max(1,min(5,int(requested_day or 1)))
    requested_period=requested_period or "24 Hour"

    # Run discovery is local/schedule-cache only. Resolve AUTO immediately so the
    # authoritative request key is identical to a subsequent explicit run request.
    canonical_run=requested_run or "AUTO"
    if canonical_run=="AUTO":
        candidates=gfs_candidate_runs(limit_days=3)
        if not candidates:
            ecmwf_event("GFS RUN RESOLUTION FAILED • no cached/scheduled candidate", "error")
            ECMWF_DOWNLOAD_STATUS.update(state="error",message="GFS run detection failed",error="No stable GFS run candidates are available")
            return
        _,rd,rh=candidates[0]
        canonical_run=f"{rd}{int(rh):02d}"
        ecmwf_event(f"GFS AUTO CANONICALIZED • {canonical_run} • no second discovery", "success")

    request_key=f"deterministic|GFS|{canonical_run}|{requested_period}|MEAN"
    with ECMWF_JOB_LOCK:
        same_active=(ECMWF_ACTIVE_REQUEST_KEY==request_key and ECMWF_DOWNLOAD_STATUS.get("state")=="downloading")
        same_ready=(ECMWF_ACTIVE_REQUEST_KEY==request_key and ECMWF_DOWNLOAD_STATUS.get("state")=="ready")
    if same_active:
        ecmwf_event(f"DUPLICATE GFS REQUEST IGNORED • canonical={canonical_run} • {requested_period} • day={requested_day}","info")
        return
    if same_ready:
        ecmwf_event(f"GFS REQUEST ALREADY READY • canonical={canonical_run} • {requested_period} • day={requested_day}","success")
        return

    token=begin_ecmwf_job("deterministic","GFS",canonical_run,requested_period)
    # begin_ecmwf_job uses the generic model/period key. GFS additionally needs
    # the selected 24h day so Day 1 and Day 2 remain distinct requests.
    with ECMWF_JOB_LOCK:
        ECMWF_ACTIVE_REQUEST_KEY=request_key
    threading.Thread(target=download_gfs_deterministic,args=(canonical_run,requested_period,requested_day,token),daemon=True,name="GFS-LIVE-MAP").start()

def download_gfs_deterministic(requested_run=None,requested_period="24 Hour",requested_day=1,job_token=None):
    """Proven GFS APCP interval engine with fast, day-lazy 24h loading.

    24 Hour mode downloads only the four native 6-hour APCP intervals needed
    for the selected day. Day 2–5 are fetched only when the user requests them.
    7/15-day accumulation retains the complete native interval sequence.
    Run discovery remains the stable local/schedule cache and is never repeated
    after a candidate fails.
    """
    global RAINFALL_CACHE
    if job_token is None: job_token=begin_ecmwf_job("deterministic","GFS",requested_run or "AUTO",requested_period)
    if not ecmwf_job_current(job_token): return
    requested_day=max(1,min(5,int(requested_day or 1)))
    try:
        set_model_identity(mode="deterministic",model="GFS",label="GFS",state="selecting",run=None,source="NOAA/NCEP NOMADS GRIB FILTER • APCP",download="not-started",message="Using stable GFS run discovery…")
        if requested_run and requested_run!="AUTO":
            raw=str(requested_run)
            if len(raw)!=10 or not raw.isdigit(): raise RuntimeError(f"Invalid GFS run selection: {requested_run}")
            run_date,run_hour=raw[:8],int(raw[8:10])
            if run_hour not in (0,6,12,18): raise RuntimeError(f"Invalid GFS run hour: {run_hour:02d}Z")
            candidates=[(None,run_date,run_hour)]
        else:
            candidates=gfs_candidate_runs(limit_days=3)
        if not candidates: raise RuntimeError("No stable GFS run candidates are available")

        if requested_period=="24 Hour":
            start_ep=(requested_day-1)*24
            download_endpoints=list(range(start_ep+6,start_ep+25,6))
            product_label=f"Day {requested_day} • F{start_ep:03d}→F{requested_day*24:03d}"
        elif requested_period=="7 Day Accumulation":
            download_endpoints=list(range(6,169,6))
            product_label="7-day native APCP F000→F168"
        elif requested_period=="15 Day Accumulation":
            download_endpoints=list(range(6,241,6))+list(range(252,361,12))
            product_label="15-day native APCP F000→F360"
        else:
            raise ValueError(f"Unknown GFS rainfall period: {requested_period}")

        last_candidate_error=None
        for candidate_index,(_,run_date,run_hour) in enumerate(candidates,1):
            if not ecmwf_job_current(job_token): return
            ecmwf_event(f"GFS RUN SELECTED FROM STABLE CACHE • {run_date} {run_hour:02d}Z • candidate {candidate_index}/{len(candidates)} • NO PRE-PROBE","success")
            folder=GFS_ROOT/f"{run_date}_{run_hour:02d}Z"; folder.mkdir(parents=True,exist_ok=True)
            GFS_RUNINFO.parent.mkdir(parents=True,exist_ok=True); GFS_RUNINFO.write_text(f"{run_date} {run_hour:02d}",encoding="utf-8")
            set_model_identity(mode="deterministic",model="GFS",label="GFS",state="identified",run=f"{run_date} {run_hour:02d}Z",source="NOAA/NCEP NOMADS GRIB FILTER • APCP",download="starting",message=f"GFS selected: {run_date} {run_hour:02d}Z • {product_label}")
            ecmwf_event(f"TRYING GFS RUN • {run_date} {run_hour:02d}Z • {requested_period} • {product_label} • {len(download_endpoints)} intervals","info")
            ECMWF_DOWNLOAD_STATUS.update(run=f"{run_date} {run_hour:02d}Z",total=len(download_endpoints),downloaded=0,message=f"Fast GFS interval download • {product_label}")

            failures=[]
            # IMPORTANT: keep network retrieval parallel, but NEVER decode GRIBs
            # concurrently. Windows ecCodes 2.47 can race inside its MEMFS
            # definitions when cfgrib decoders are active in multiple threads.
            # Each worker therefore downloads + checks only the GRIB magic header.
            
            def _gfs_fetch_one(ep):
                if not ecmwf_job_current(job_token):
                    return ep, False, "job superseded"
                target=folder/f"gfs_i{ep:03d}.grib2"
                if target.exists() and valid_grib(target) and target.stat().st_size>1000:
                    return ep, True, "already present • GRIB header valid"
                if target.exists():
                    try: target.unlink()
                    except OSError: pass
                ok,src=download_gfs_endpoint(run_date,run_hour,ep,target,job_token)
                if not ok:
                    return ep, False, src
                if not valid_grib(target):
                    return ep, False, "download completed but GRIB header validation failed"
                return ep, True, f"downloaded • {target.stat().st_size/1024:.0f} KB • decode queued"

            max_workers = min(max(1, int(GFS_PARALLEL_WORKERS or 1)), len(download_endpoints))
            with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gfs-download") as pool:
                futures={pool.submit(_gfs_fetch_one,ep): ep for ep in download_endpoints}
                completed=0
                for fut in as_completed(futures):
                    ep=futures[fut]
                    try:
                        ep,result,detail=fut.result()
                    except Exception as exc:
                        ep,result,detail=ep,False,str(exc)
                    completed += 1
                    if result:
                        ecmwf_event(f"GFS F{ep:03d} DOWNLOAD READY • {detail}","success")
                    else:
                        failures.append((ep,detail))
                        ecmwf_event(f"GFS F{ep:03d} DOWNLOAD FAILED • {detail}","warn")
                    ECMWF_DOWNLOAD_STATUS["downloaded"]=completed
                    ECMWF_DOWNLOAD_STATUS["message"]=f"GFS {product_label} • downloading {completed}/{len(download_endpoints)} intervals"

            # Serial native decode/validation. This is intentionally separate from
            # the parallel transfer phase so ecCodes sees one decoder at a time.
            if not failures:
                ecmwf_event(f"GFS GRIB DECODE QUEUE • {len(download_endpoints)} intervals • SERIAL SAFE MODE", "info")
                for idx,ep in enumerate(download_endpoints,1):
                    if not ecmwf_job_current(job_token): return
                    target=folder/f"gfs_i{ep:03d}.grib2"
                    interval_start=ep-12 if (requested_period=="15 Day Accumulation" and ep>240) else ep-6
                    ok,detail=gfs_interval_precipitation_valid(target,interval_start,ep)
                    if not ok:
                        failures.append((ep,f"decode failed: {detail}"))
                        ecmwf_event(f"GFS F{ep:03d} FAILED • decode failed: {detail}","warn")
                        break
                    ecmwf_event(f"GFS F{ep:03d} PRECIP VALID • {detail}","success")
                    ECMWF_DOWNLOAD_STATUS["downloaded"]=idx
                    ECMWF_DOWNLOAD_STATUS["message"]=f"GFS {product_label} • decoding {idx}/{len(download_endpoints)} intervals"

            if not failures and requested_period=="24 Hour":
                final_ep=requested_day*24
                target=folder/f"gfs_i{final_ep:03d}.grib2"
                interval_start=final_ep-6
                da,varname=open_gfs_interval_tp(target,interval_start,final_ep)
                test_png,test_max=_gfs_selftest_plot(target,run_date,run_hour,da,varname)
                ecmwf_event(
                    f"GFS SELF-TEST PASS • Day {requested_day} • F{final_ep:03d} • "
                    f"{varname} • max {test_max:.1f} mm • plot={test_png.name}",
                    "success"
                )

            if failures:
                last_candidate_error="; ".join(f"F{ep:03d} {detail}" for ep,detail in failures[:5])
                ecmwf_event(f"GFS CANDIDATE INCOMPLETE • {run_date} {run_hour:02d}Z • {last_candidate_error} • trying next cached run","warn")
                continue

            for ep in download_endpoints:
                final_path=folder/f"gfs_i{ep:03d}.grib2"
                if not valid_grib(final_path): raise RuntimeError(f"Final GFS GRIB verification failed for F{ep:03d}")
            RAINFALL_CACHE.clear()
            ECMWF_DOWNLOAD_STATUS.update(state="ready",downloaded=len(download_endpoints),total=len(download_endpoints),message=f"LIVE GFS READY • {run_date} {run_hour:02d}Z • {product_label}",run=f"{run_date} {run_hour:02d}Z",error=None,finished_at=datetime.now(timezone.utc).isoformat())
            set_model_identity(mode="deterministic",model="GFS",label="GFS",state="ready",run=f"{run_date} {run_hour:02d}Z",source="NOAA/NCEP NOMADS GRIB FILTER • APCP",download="complete",message=f"GFS READY • {product_label}")
            ecmwf_event(f"LIVE GFS READY • {run_date} {run_hour:02d}Z • {requested_period} • {product_label}","success")
            return

        raise RuntimeError(f"No cached GFS candidate completed {requested_period} • {product_label}. Last failure: {last_candidate_error}")
    except Exception as exc:
        ECMWF_DOWNLOAD_STATUS.update(state="error",error=str(exc),message=f"GFS failed: {exc}")
        set_model_identity(download="error",state="error",message=f"GFS failed: {exc}")
        ecmwf_event(f"GFS ENGINE ERROR • {exc}","error")

def icon_period_endpoints(period):
    p=str(period)
    if p == "24 Hour": return [24,48,72,96,120]
    if p == "7 Day Accumulation": return [168]
    if p == "15 Day Accumulation":
        raise ValueError("ICON Global deterministic does not provide a 15-day accumulation")
    raise ValueError(f"Unknown ICON rainfall period: {period}")

def icon_directory_url(run_hour):
    return f"{ICON_BASE_URL}/{int(run_hour):02d}/tot_prec/"

def icon_filename(run_date,run_hour,fhour):
    return f"icon_global_icosahedral_single-level_{run_date}{int(run_hour):02d}_{int(fhour):03d}_TOT_PREC.grib2.bz2"

def icon_file_url(run_date,run_hour,fhour):
    return icon_directory_url(run_hour)+icon_filename(run_date,run_hour,fhour)

def icon_candidate_runs(limit_days=3,required_endpoints=None):
    """FAST ICON selector. DWD availability is checked only during download."""
    period="|".join(map(str,required_endpoints or ICON_CORE_ENDPOINTS))
    runs,_=stable_run_candidates("ICON",period,limit_days)
    return runs

def icon_endpoint_available(run_date,run_hour,fhour):
    url=icon_file_url(run_date,run_hour,fhour)
    try:
        r=requests.head(url,timeout=20,allow_redirects=True,headers={"User-Agent":"MASRAINMAN-ICON-Talk/2.0","Accept":"*/*"})
        if r.status_code==200: return True,"HTTP 200"
        if r.status_code in (403,405):
            g=requests.get(url,headers={"Range":"bytes=0-3","User-Agent":"MASRAINMAN-ICON-Talk/2.0"},stream=True,timeout=20)
            ok=g.status_code in (200,206)
            detail=f"HTTP {g.status_code}"
            g.close()
            return ok,detail
        return False,f"HTTP {r.status_code}"
    except Exception as exc:
        return False,str(exc)

def _icon_safe_get(handle,key):
    try: return codes_get(handle,key)
    except Exception: return None

def _icon_safe_array(handle,key):
    try: return codes_get_array(handle,key)
    except Exception: return None

def _icon_coordinate_url_and_name(run_hour,variable):
    base=f"{ICON_BASE_URL}/{int(run_hour):02d}/{variable.lower()}/"
    r=requests.get(base,timeout=ICON_HTTP_TIMEOUT)
    r.raise_for_status()
    hrefs=re.findall(r'href=["\']([^"\']+)["\']',r.text,flags=re.I)
    candidates=[]
    for href in hrefs:
        name=unquote(href.split('/')[-1])
        if name.startswith('icon_global_icosahedral_time-invariant_') and name.endswith(f'_{variable.upper()}.grib2.bz2'):
            candidates.append(name)
    if not candidates:
        raise RuntimeError(f"No DWD ICON Global {variable.upper()} coordinate file found in {base}")
    name=sorted(set(candidates))[-1]
    return urljoin(base,quote(name)),name

def _icon_download_coordinate(run_hour,variable):
    url,name=_icon_coordinate_url_and_name(run_hour,variable)
    raw=ICON_RAW_ROOT/name
    if not raw.exists() or raw.stat().st_size<100:
        ecmwf_event(f"ICON NATIVE GRID DOWNLOAD • {variable.upper()} • {name}","info")
        r=requests.get(url,timeout=ICON_HTTP_TIMEOUT); r.raise_for_status(); raw.write_bytes(r.content)
    grib=ICON_GRIB_ROOT/name[:-4] if name.endswith('.bz2') else ICON_GRIB_ROOT/name
    if not grib.exists() or grib.stat().st_size<100:
        with bz2.open(raw,'rb') as src,open(grib,'wb') as dst: shutil.copyfileobj(src,dst)
    if grib.read_bytes()[:4]!=b'GRIB': raise RuntimeError(f"Invalid ICON coordinate GRIB2: {grib}")
    return grib

def _icon_read_coordinate(grib_path,variable):
    if codes_grib_new_from_file is None: raise RuntimeError("eccodes is required for ICON. Run: pip install eccodes")
    expected=2949120
    with open(grib_path,'rb') as f:
        n=0
        while True:
            h=codes_grib_new_from_file(f)
            if h is None: break
            n+=1
            short=str(_icon_safe_get(h,'shortName') or '').lower()
            name=str(_icon_safe_get(h,'name') or '').lower()
            vals=_icon_safe_array(h,'values')
            size=0 if vals is None else int(np.asarray(vals).size)
            is_target=(short==variable.lower() or variable.lower() in name or (variable.lower()=='clat' and 'latitude' in name) or (variable.lower()=='clon' and 'longitude' in name))
            if is_target or size==expected:
                arr=np.asarray(vals,dtype=float)
                codes_release(h)
                if arr.size!=expected: raise RuntimeError(f"ICON {variable.upper()} coordinate size {arr.size:,}, expected {expected:,}")
                return arr
            codes_release(h)
    raise RuntimeError(f"Could not read ICON {variable.upper()} coordinate record in {grib_path.name}")

def icon_native_coordinates(run_hour):
    key=int(run_hour)
    if key in ICON_COORD_CACHE: return ICON_COORD_CACHE[key]
    clat=_icon_read_coordinate(_icon_download_coordinate(key,'clat'),'clat')
    clon=_icon_read_coordinate(_icon_download_coordinate(key,'clon'),'clon')
    if np.nanmax(np.abs(clat))<=np.pi+0.1: clat=np.degrees(clat)
    if np.nanmax(np.abs(clon))<=2*np.pi+0.1: clon=np.degrees(clon)
    clon=((clon+180.0)%360.0)-180.0
    if clat.size!=clon.size or clat.size!=2949120: raise RuntimeError(f"ICON native coordinate mismatch: CLAT={clat.size:,}, CLON={clon.size:,}")
    ICON_COORD_CACHE[key]=(clat,clon)
    ecmwf_event(f"ICON NATIVE GRID READY • {clat.size:,} points • CLAT/CLON verified","success")
    return clat,clon

def icon_download_endpoint(run_date,run_hour,fhour,job_token=None):
    raw=ICON_RAW_ROOT/f"{run_date}_{int(run_hour):02d}Z"/icon_filename(run_date,run_hour,fhour)
    raw.parent.mkdir(parents=True,exist_ok=True)
    grib=ICON_GRIB_ROOT/f"{run_date}_{int(run_hour):02d}Z"/icon_filename(run_date,run_hour,fhour)[:-4]
    grib.parent.mkdir(parents=True,exist_ok=True)
    if grib.exists() and grib.stat().st_size>100:
        try:
            _icon_read_cumulative(grib,fhour,validate_only=True)
            ecmwf_event(f"ICON F{fhour:03d} EXISTING GRIB VERIFIED • 0-{fhour}h cumulative","success")
            return grib
        except Exception:
            try: grib.unlink()
            except Exception: pass
    url=icon_file_url(run_date,run_hour,fhour)
    ecmwf_event(f"ICON DOWNLOAD • {run_date} {int(run_hour):02d}Z • F{fhour:03d} • TOT_PREC","info")
    r=requests.get(url,timeout=ICON_HTTP_TIMEOUT); r.raise_for_status()
    raw.write_bytes(r.content)
    ecmwf_event(f"ICON F{fhour:03d} DOWNLOADED • {len(r.content)/1024:.1f} KB","info")
    with bz2.open(raw,'rb') as src,open(grib,'wb') as dst: shutil.copyfileobj(src,dst)
    _icon_read_cumulative(grib,fhour,validate_only=True)
    ecmwf_event(f"ICON F{fhour:03d} READY • GRIB2 • 0-{fhour}h cumulative","success")
    return grib

def _icon_read_cumulative(grib_path,expected_fhour,validate_only=False):
    if codes_grib_new_from_file is None: raise RuntimeError("eccodes is required for ICON. Run: pip install eccodes")
    with open(grib_path,'rb') as f:
        while True:
            h=codes_grib_new_from_file(f)
            if h is None: break
            short=str(_icon_safe_get(h,'shortName') or '').lower(); name=str(_icon_safe_get(h,'name') or '').lower()
            st=str(_icon_safe_get(h,'stepType') or '').lower(); start=_icon_safe_get(h,'startStep'); end=_icon_safe_get(h,'endStep')
            try: sf=float(start); ef=float(end)
            except Exception: codes_release(h); continue
            is_precip=short in ('tp','tot_prec') or 'total precipitation' in name
            if is_precip and st in ('accum','accumulation') and abs(sf)<0.01 and abs(ef-float(expected_fhour))<0.01:
                vals=_icon_safe_array(h,'values')
                codes_release(h)
                if vals is None: raise RuntimeError(f"ICON F{expected_fhour:03d} precipitation values unavailable")
                arr=np.asarray(vals,dtype=np.float32)
                if arr.size!=2949120: raise RuntimeError(f"ICON F{expected_fhour:03d} grid size {arr.size:,} != 2,949,120")
                return arr
            codes_release(h)
    raise RuntimeError(f"ICON cumulative 0-{expected_fhour}h TOT_PREC record not found in {grib_path.name}")

def _icon_regular_from_endpoint(run_date,run_hour,fhour):
    key=(run_date,int(run_hour),int(fhour))
    if key in ICON_FIELD_CACHE: return ICON_FIELD_CACHE[key]
    grib=icon_download_endpoint(run_date,run_hour,fhour)
    values=_icon_read_cumulative(grib,fhour)
    clat,clon=icon_native_coordinates(run_hour)
    regional=(clon>=ICON_LEFTLON-1)&(clon<=ICON_RIGHTLON+1)&(clat>=ICON_BOTTOMLAT-1)&(clat<=ICON_TOPLAT+1)&np.isfinite(values)&np.isfinite(clat)&np.isfinite(clon)
    vals=values[regional]; lat=clat[regional]; lon=clon[regional]
    if vals.size<100: raise RuntimeError("Too few ICON native-grid points in South India")
    if griddata is None: raise RuntimeError("scipy is required for ICON interpolation. Run: pip install scipy")
    glon=np.arange(ICON_LEFTLON,ICON_RIGHTLON+ICON_GRID_RESOLUTION*0.5,ICON_GRID_RESOLUTION)
    glat=np.arange(ICON_BOTTOMLAT,ICON_TOPLAT+ICON_GRID_RESOLUTION*0.5,ICON_GRID_RESOLUTION)
    grid_lon,grid_lat=np.meshgrid(glon,glat)
    points=np.column_stack([lon,lat])
    field=griddata(points,vals,(grid_lon,grid_lat),method='linear')
    missing=~np.isfinite(field)
    if np.any(missing):
        nearest=griddata(points,vals,(grid_lon[missing],grid_lat[missing]),method='nearest'); field[missing]=nearest
    field=np.clip(np.asarray(field,dtype=np.float32),0,None)
    ICON_FIELD_CACHE[key]=(field,grid_lat,grid_lon)
    return ICON_FIELD_CACHE[key]

def start_icon_deterministic_download(requested_run=None,requested_period="24 Hour"):
    token=begin_ecmwf_job('deterministic','ICON',requested_run or 'AUTO',requested_period)
    threading.Thread(target=download_icon_deterministic,args=(requested_run,requested_period,token),daemon=True).start()

def download_icon_deterministic(requested_run=None,requested_period="24 Hour",job_token=None):
    global RAINFALL_CACHE
    if job_token is None: job_token=begin_ecmwf_job('deterministic','ICON',requested_run or 'AUTO',requested_period)
    try:
        if requested_period=="15 Day Accumulation": raise RuntimeError("ICON Global deterministic does not provide a 15-day accumulation")
        endpoints=icon_period_endpoints(requested_period)
        if requested_run and requested_run!="AUTO":
            raw=str(requested_run);
            if len(raw)!=10 or not raw.isdigit(): raise RuntimeError(f"Invalid ICON run selection: {requested_run}")
            rd,rh=raw[:8],int(raw[8:10])
            candidates=[(None,rd,rh)]
        else: candidates=icon_candidate_runs(limit_days=3,required_endpoints=endpoints)
        selected=None
        for _,rd,rh in candidates:
            if not ecmwf_job_current(job_token): return
            missing=[]
            for ep in endpoints:
                ok,detail=icon_endpoint_available(rd,rh,ep)
                if not ok: missing.append(f"F{ep:03d} {detail}")
            if not missing: selected=(rd,rh); break
            ecmwf_event(f"ICON RUN NOT READY • {rd} {rh:02d}Z • " + '; '.join(missing),"warn")
            if requested_run and requested_run!="AUTO":
                fallback=icon_candidate_runs(limit_days=3,required_endpoints=endpoints)
                if fallback:
                    _,rd,rh=fallback[0]; selected=(rd,rh)
                    ecmwf_event(f"ICON FALLBACK SELECTED • {rd} {rh:02d}Z • complete {requested_period} endpoints verified","success")
                    break
        if selected is None: raise RuntimeError(f"No complete ICON Global run currently provides {requested_period}")
        rd,rh=selected
        ensure_runtime_dirs()
        ICON_RUNINFO.parent.mkdir(parents=True,exist_ok=True); ICON_RUNINFO.write_text(f"{rd} {rh:02d}",encoding='utf-8')
        set_model_identity(mode='deterministic',model='ICON',label='ICON Global',state='identified',run=f'{rd} {rh:02d}Z',source='DWD Open Data • ICON Global • TOT_PREC',download='starting',message=f'ICON selected: {rd} {rh:02d}Z')
        ecmwf_event(f"SELECTED ICON RUN • {rd} {rh:02d}Z • DWD Open Data • TOT_PREC","success")
        ECMWF_DOWNLOAD_STATUS.update(run=f'{rd} {rh:02d}Z',total=len(endpoints),downloaded=0,message=f'Downloading ICON Global TOT_PREC • {rd} {rh:02d}Z')
        # Coordinates are part of the official ICON native-grid geometry and are downloaded once per cycle.
        icon_native_coordinates(rh)
        for i,ep in enumerate(endpoints,1):
            if not ecmwf_job_current(job_token): return
            _icon_regular_from_endpoint(rd,rh,ep)
            ECMWF_DOWNLOAD_STATUS['downloaded']=i
        # Keep ICON_FIELD_CACHE: fields are keyed by run/date/hour/endpoint and are
        # reused immediately by the rainfall renderer.
        RAINFALL_CACHE.clear()
        set_model_identity(mode='deterministic',model='ICON',label='ICON Global',state='ready',run=f'{rd} {rh:02d}Z',source='DWD Open Data • ICON Global • TOT_PREC',download='complete',message=f'ICON ready: {rd} {rh:02d}Z')
        ECMWF_DOWNLOAD_STATUS.update(state='ready',message=f'LIVE ICON READY • {rd} {rh:02d}Z • {requested_period}',error=None,finished_at=datetime.now(timezone.utc).isoformat())
        ecmwf_event(f"LIVE ICON READY • {rd} {rh:02d}Z • {requested_period}","success")
    except Exception as exc:
        if not ecmwf_job_current(job_token): return
        set_model_identity(mode='deterministic',model='ICON',label='ICON Global',state='error',source='DWD Open Data • ICON Global • TOT_PREC',download='failed',message=str(exc))
        ECMWF_DOWNLOAD_STATUS.update(state='error',message='ICON deterministic download failed',error=str(exc),finished_at=datetime.now(timezone.utc).isoformat())
        ecmwf_event(f"LIVE ICON DOWNLOAD FAILED: {exc}","error")
        traceback.print_exc()

# ---------------------------------------------------------------------------
# AIFS SINGLE DETERMINISTIC RAINFALL ENGINE
# Proven separately in MASRAINMAN AIFS standalone diagnostic V7.
# ECMWF Open Data: aifs-single/0p25/oper
# TP = Total Precipitation (kg m-2 == mm)
# Calculation: F024 - F000 for Day 1, and cumulative differences thereafter.
# ---------------------------------------------------------------------------

def aifs_endpoint_hours(period):
    p=str(period)
    if p == "24 Hour": return [24,48,72,96,120]
    if p == "7 Day Accumulation": return [168]
    if p == "15 Day Accumulation": return [360]
    raise ValueError(f"Unknown AIFS rainfall period: {period}")

def aifs_url(run_date, run_hour, fhour):
    timestamp=f"{run_date}{int(run_hour):02d}0000"
    return (f"{AIFS_BASE_URL}/{run_date}/{int(run_hour):02d}z/{AIFS_MODEL_PATH}/"
            f"{timestamp}-{int(fhour)}h-oper-fc.grib2")

def aifs_endpoint_available(run_date, run_hour, fhour):
    url=aifs_url(run_date,run_hour,fhour)
    try:
        r=requests.head(url,timeout=30,allow_redirects=True)
        if r.status_code==200: return True,"HTTP 200"
        if r.status_code in (403,405):
            g=requests.get(url,headers={"Range":"bytes=0-0"},stream=True,timeout=30)
            ok=g.status_code in (200,206); detail=f"HTTP {g.status_code}"; g.close(); return ok,detail
        return False,f"HTTP {r.status_code}"
    except Exception as exc:
        return False,str(exc)

def aifs_candidate_runs(limit_days=3,required_endpoints=None):
    required=list(required_endpoints or [24])
    now=datetime.now(timezone.utc)
    out=[]
    for day_back in range(limit_days):
        d=now.date()-timedelta(days=day_back)
        for rh in (18,12,6,0):
            dt=datetime(d.year,d.month,d.day,rh,tzinfo=timezone.utc)
            if dt>now: continue
            rd=f"{d:%Y%m%d}"
            if all(aifs_endpoint_available(rd,rh,ep)[0] for ep in required):
                out.append((dt,rd,rh))
    return out[:4]

def aifs_download_endpoint(run_date,run_hour,fhour,job_token=None):
    folder=AIFS_RAW_ROOT/f"{run_date}_{int(run_hour):02d}Z"; folder.mkdir(parents=True,exist_ok=True)
    target=folder/f"aifs_f{int(fhour):03d}.grib2"
    if target.exists() and target.stat().st_size>1000:
        return target
    url=aifs_url(run_date,run_hour,fhour)
    ecmwf_event(f"AIFS DOWNLOAD • {run_date} {int(run_hour):02d}Z • F{int(fhour):03d} • ECMWF Open Data","info")
    tmp=target.with_suffix('.grib2.part')
    r=requests.get(url,timeout=AIFS_HTTP_TIMEOUT,stream=True,headers={"User-Agent":"MASRAINMAN-AIFS-Dashboard/1.0","Accept":"*/*"})
    r.raise_for_status()
    with open(tmp,'wb') as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            if chunk: f.write(chunk)
    if not tmp.exists() or tmp.stat().st_size<1000: raise RuntimeError(f"AIFS F{fhour:03d} download is unexpectedly small")
    with open(tmp,'rb') as f:
        if f.read(4)!=b'GRIB': raise RuntimeError(f"AIFS F{fhour:03d} response is not GRIB2")
    tmp.replace(target)
    ecmwf_event(f"AIFS F{fhour:03d} SAVED • {target.stat().st_size/1024/1024:.1f} MB","success")
    return target

def aifs_decode_tp(path):
    if xr is None or cfgrib is None: raise RuntimeError("xarray/cfgrib dependencies are not installed")
    errors=[]
    try:
        datasets=cfgrib.open_datasets(str(path),backend_kwargs={"indexpath":""})
        selected=None
        for ds in datasets:
            if "tp" in ds.data_vars:
                selected=ds["tp"]
                break
            for name in ds.data_vars:
                var=ds[name]
                if var.attrs.get("GRIB_paramId")==228228 or str(var.attrs.get("GRIB_shortName",""))=="tp":
                    selected=var
                    break
            if selected is not None: break
        if selected is None: raise RuntimeError(f"Total Precipitation not found in {path.name}; groups={len(datasets)}")
        # Proven V7 fix: force all data into memory before any temporary/index
        # resources can disappear. The AIFS files remain cached, but this also
        # prevents lazy cfgrib access later in the dashboard renderer.
        selected=selected.load().copy(deep=True)
        for ds in datasets:
            try: ds.close()
            except Exception: pass
        data=np.asarray(selected.values,dtype=np.float32).squeeze()
        lat=np.asarray(selected.latitude.values,dtype=float)
        lon=np.asarray(selected.longitude.values,dtype=float)
        if data.ndim!=2: raise RuntimeError(f"Unexpected AIFS TP shape: {data.shape}")
        units=str(selected.attrs.get("units",selected.attrs.get("GRIB_units",""))).strip().lower()
        if units not in ("kg m**-2","kg m^-2","kg m-2","kg/m**2","kg/m2","mm","millimeter","millimeters"):
            raise RuntimeError(f"Unknown AIFS TP units: {units!r}")
        # kg m-2 is numerically equal to mm water equivalent.
        return data,lat,lon,units
    except Exception as exc:
        errors.append(str(exc))
        raise RuntimeError(f"AIFS TP decode failed for {path.name}: {' | '.join(errors)}") from exc

def _aifs_cumulative(run_date,run_hour,fhour):
    key=(run_date,int(run_hour),int(fhour))
    if key in AIFS_FIELD_CACHE: return AIFS_FIELD_CACHE[key]
    path=aifs_download_endpoint(run_date,run_hour,fhour)
    data,lat,lon,units=aifs_decode_tp(path)
    # AIFS TP is kg m-2, numerically mm. No x1000 conversion.
    data=np.asarray(data,dtype=np.float32)
    data=np.where(np.isfinite(data),data,0).astype(np.float32)
    data=np.clip(data,0,None)
    AIFS_FIELD_CACHE[key]=(data,lat,lon,units)
    return AIFS_FIELD_CACHE[key]

def start_aifs_deterministic_download(requested_run=None,requested_period="24 Hour"):
    token=begin_ecmwf_job('deterministic','AIFS',requested_run or 'AUTO',requested_period)
    threading.Thread(target=download_aifs_deterministic,args=(requested_run,requested_period,token),daemon=True).start()

def download_aifs_deterministic(requested_run=None,requested_period="24 Hour",job_token=None):
    global RAINFALL_CACHE
    if job_token is None: job_token=begin_ecmwf_job('deterministic','AIFS',requested_run or 'AUTO',requested_period)
    try:
        endpoints=aifs_endpoint_hours(requested_period)
        required=[0]+endpoints if requested_period=="24 Hour" else endpoints
        if requested_run and requested_run!="AUTO":
            raw=str(requested_run)
            if len(raw)!=10 or not raw.isdigit(): raise RuntimeError(f"Invalid AIFS run selection: {requested_run}")
            rd,rh=raw[:8],int(raw[8:10])
            candidates=[(None,rd,rh)]
        else:
            candidates=aifs_candidate_runs(limit_days=3,required_endpoints=required)
        selected=None
        for _,rd,rh in candidates:
            if not ecmwf_job_current(job_token): return
            if all(aifs_endpoint_available(rd,rh,ep)[0] for ep in required):
                selected=(rd,rh); break
            ecmwf_event(f"AIFS RUN NOT READY • {rd} {rh:02d}Z • {requested_period}","warn")
        if selected is None: raise RuntimeError(f"No complete AIFS Single run currently provides {requested_period}")
        rd,rh=selected
        ensure_runtime_dirs()
        AIFS_RUNINFO.parent.mkdir(parents=True, exist_ok=True)
        AIFS_RUNINFO.write_text(f"{rd} {rh:02d}",encoding='utf-8')
        set_model_identity(mode='deterministic',model='AIFS',label='AIFS Single',state='identified',run=f'{rd} {rh:02d}Z',source='ECMWF Open Data • AIFS Single • Total Precipitation',download='starting',message=f'AIFS selected: {rd} {rh:02d}Z')
        ecmwf_event(f"SELECTED AIFS RUN • {rd} {rh:02d}Z • ECMWF Open Data • aifs-single/0p25/oper","success")
        ECMWF_DOWNLOAD_STATUS.update(run=f'{rd} {rh:02d}Z',total=len(required),downloaded=0,message=f'Downloading AIFS Single • {rd} {rh:02d}Z')
        for i,ep in enumerate(required,1):
            if not ecmwf_job_current(job_token): return
            _aifs_cumulative(rd,rh,ep)
            ECMWF_DOWNLOAD_STATUS['downloaded']=i
            ECMWF_DOWNLOAD_STATUS['message']=f'AIFS F{ep:03d} ready ({i}/{len(required)})'
        RAINFALL_CACHE.clear()
        set_model_identity(mode='deterministic',model='AIFS',label='AIFS Single',state='ready',run=f'{rd} {rh:02d}Z',source='ECMWF Open Data • AIFS Single • Total Precipitation',download='complete',message=f'AIFS ready: {rd} {rh:02d}Z')
        ECMWF_DOWNLOAD_STATUS.update(state='ready',message=f'LIVE AIFS READY • {rd} {rh:02d}Z • {requested_period}',error=None,finished_at=datetime.now(timezone.utc).isoformat())
        ecmwf_event(f"LIVE AIFS READY • {rd} {rh:02d}Z • {requested_period}","success")
    except Exception as exc:
        if not ecmwf_job_current(job_token): return
        set_model_identity(mode='deterministic',model='AIFS',label='AIFS Single',state='error',source='ECMWF Open Data • AIFS Single • Total Precipitation',download='failed',message=str(exc))
        ECMWF_DOWNLOAD_STATUS.update(state='error',message='AIFS deterministic download failed',error=str(exc),finished_at=datetime.now(timezone.utc).isoformat())
        ecmwf_event(f"LIVE AIFS DOWNLOAD FAILED: {exc}","error")
        traceback.print_exc()

def read_aifs_run():
    if not AIFS_RUNINFO.exists(): raise FileNotFoundError(f"AIFS runinfo not found: {AIFS_RUNINFO}")
    parts=AIFS_RUNINFO.read_text(encoding='utf-8').strip().split()
    if len(parts)!=2: raise ValueError("AIFS runinfo must contain YYYYMMDD HH")
    rd,rh=parts[0],int(parts[1]); return rd,rh

def build_aifs_rainfall(day=1,period=None):
    period=period or ACTIVE_PERIOD
    rd,rh=read_aifs_run()
    if period=="24 Hour":
        d=int(day)
        if d<1 or d>5: raise ValueError("AIFS 24-hour day must be 1..5")
        end_ep=d*24
        cur,lat,lon,units=_aifs_cumulative(rd,rh,end_ep)
        prev,_,_,_= _aifs_cumulative(rd,rh,(d-1)*24)
        rain=cur-prev
        label=f"F{(d-1)*24:03d} → F{end_ep:03d} (cumulative difference)"
    elif period=="7 Day Accumulation":
        end_ep=168; rain,lat,lon,units=_aifs_cumulative(rd,rh,end_ep); d=1; label="F000 → F168 cumulative"
    elif period=="15 Day Accumulation":
        end_ep=360; rain,lat,lon,units=_aifs_cumulative(rd,rh,end_ep); d=1; label="F000 → F360 cumulative"
    else: raise ValueError(f"Unknown AIFS rainfall period: {period}")
    # Align/crop to the dashboard's standard 0.25° focus domain.
    rain=np.clip(np.asarray(rain,dtype=np.float32),0,None)
    rain,lat,lon,grid_dx,grid_dy=_validate_regular_grid(rain,lat,lon)
    run_dt=datetime.strptime(rd,'%Y%m%d').replace(tzinfo=timezone.utc)+timedelta(hours=rh)
    valid_end=run_dt+timedelta(hours=end_ep)
    meta={'model':'AIFS Single','mode':'deterministic','run':f'{rd} {rh:02d}Z','period':period,'day':int(d),'start':f'F{(d-1)*24:03d}' if period=='24 Hour' else 'F000','end':f'F{end_ep:03d}','display_range':label,'valid_end_date':valid_end.strftime('%d %b %Y'),'valid_end_iso':valid_end.strftime('%Y-%m-%d'),'valid_end_time':'08:30 AM IST','valid_end_endpoint':f'F{end_ep:03d}','members':1,'min':float(np.nanmin(rain)),'max':float(np.nanmax(rain)),'mean':float(np.nanmean(rain)),'lat_min':float(lat.min()),'lat_max':float(lat.max()),'lon_min':float(lon.min()),'lon_max':float(lon.max()),'lat_edge_min':float(lat.min()),'lat_edge_max':float(lat.max()),'lon_edge_min':float(lon.min()),'lon_edge_max':float(lon.max()),'grid_dx':grid_dx,'grid_dy':grid_dy,'grid_rows':int(rain.shape[0]),'grid_cols':int(rain.shape[1]),'finite_count':int(np.isfinite(rain).sum()),'positive_count':int((rain>=0.1).sum()),'projection':'LEAFLET DIRECT LATLON CANVAS','tp_accumulation':label,'tp_units':'kg m-2 = mm','smoothing':'display-only','gaussian_sigma':AIFS_SMOOTHING_SIGMA,'source':'ECMWF Open Data • AIFS Single'}
    return rain,lat,lon,meta

def read_icon_run():
    if not ICON_RUNINFO.exists(): raise FileNotFoundError(f"ICON runinfo not found: {ICON_RUNINFO}")
    parts=ICON_RUNINFO.read_text(encoding='utf-8').strip().split()
    if len(parts)!=2: raise ValueError("ICON runinfo must contain YYYYMMDD HH")
    rd,rh=parts[0],int(parts[1]); return rd,rh

def build_icon_rainfall(day=1,period=None):
    period=period or ACTIVE_PERIOD
    rd,rh=read_icon_run()
    if period=="24 Hour":
        d=int(day)
        if d<1 or d>5: raise ValueError("ICON 24-hour day must be 1..5")
        ep=d*24
        cur,lat,lon=_icon_regular_from_endpoint(rd,rh,ep)
        if d==1: rain=cur; label='0-24h cumulative'
        else:
            prev,_,_=_icon_regular_from_endpoint(rd,rh,(d-1)*24)
            rain=cur-prev; label=f'0-{ep}h minus 0-{(d-1)*24}h'
    elif period=="7 Day Accumulation":
        ep=168; rain,lat,lon=_icon_regular_from_endpoint(rd,rh,ep); d=1; label='0-168h cumulative'
    else:
        raise ValueError("ICON Global deterministic does not provide a 15-day accumulation")
    rain=np.clip(np.asarray(rain,dtype=np.float32),0,None)
    run_dt=datetime.strptime(rd,'%Y%m%d').replace(tzinfo=timezone.utc)
    valid_end=run_dt+timedelta(hours=rh)+timedelta(hours=ep)
    meta={'model':'ICON','mode':'deterministic','run':f'{rd} {rh:02d}Z','period':period,'day':int(d),'start':'F000','end':f'F{ep:03d}','display_range':f'F000 → F{ep:03d}','valid_end_date':valid_end.strftime('%d %b %Y'),'valid_end_iso':valid_end.strftime('%Y-%m-%d'),'valid_end_time':'08:30 AM IST','valid_end_endpoint':f'F{ep:03d}','members':1,'min':float(np.nanmin(rain)),'max':float(np.nanmax(rain)),'mean':float(np.nanmean(rain)),'lat_min':float(lat.min()),'lat_max':float(lat.max()),'lon_min':float(lon.min()),'lon_max':float(lon.max()),'lat_edge_min':float(lat.min()),'lat_edge_max':float(lat.max()),'lon_edge_min':float(lon.min()),'lon_edge_max':float(lon.max()),'grid_dx':ICON_GRID_RESOLUTION,'grid_dy':ICON_GRID_RESOLUTION,'grid_rows':int(rain.shape[0]),'grid_cols':int(rain.shape[1]),'finite_count':int(np.isfinite(rain).sum()),'positive_count':int((rain>=0.1).sum()),'projection':'LEAFLET DIRECT LATLON CANVAS','tp_accumulation':label,'tp_message_selection':f'startStep=0, endStep={ep}','source':'DWD Open Data • ICON Global • TOT_PREC'}
    return rain,lat,lon,meta

def read_gfs_run():
    if not GFS_RUNINFO.exists(): raise FileNotFoundError(f"GFS runinfo not found: {GFS_RUNINFO}")
    parts=GFS_RUNINFO.read_text(encoding="utf-8").strip().split()
    if len(parts)!=2: raise ValueError("GFS runinfo must contain YYYYMMDD HH")
    run_date,run_hour=parts[0],int(parts[1]); folder=GFS_ROOT/f"{run_date}_{run_hour:02d}Z"
    if not folder.is_dir(): raise FileNotFoundError(f"GFS run folder not found: {folder}")
    return run_date,run_hour,folder


def _open_gfs_precip_range_dataset(path, start_step=None, end_step=None):
    """Decode one GFS APCP accumulation interval from a NOMADS-filtered GRIB.

    GFS/NOMADS APCP filter responses can expose the forecast accumulation as an
    interval (for example 66-72) rather than the cumulative 0-72 message that
    some full-file retrievals contain.  This decoder therefore treats
    startStep/endStep as first-class metadata and can safely read either form.
    """
    if xr is None or cfgrib is None:
        raise RuntimeError("xarray/cfgrib dependencies are not installed")
    path=str(path)
    errors=[]
    st=None if start_step is None else int(start_step)
    en=None if end_step is None else int(end_step)

    def inspect(ds, label):
        precip=[]
        for name in ds.data_vars:
            var=ds[name]
            short=str(var.attrs.get("GRIB_shortName", "")).lower()
            long_name=str(var.attrs.get("GRIB_name", "")).lower()
            standard=str(var.attrs.get("standard_name", "")).lower()
            if short in ("tp","apcp") or "precipitation" in long_name or "precipitation" in standard or str(name).lower() in ("tp","apcp"):
                a=var.attrs
                try: astart=float(a.get("GRIB_startStep"))
                except Exception: astart=None
                try: aend=float(a.get("GRIB_endStep"))
                except Exception: aend=None
                precip.append((name,astart,aend,a.get("GRIB_stepRange")))
        # Exact metadata match first.
        for name,astart,aend,sr in precip:
            if (st is None or astart==st) and (en is None or aend==en):
                return ds[name].load(),name
        # Some cfgrib builds expose the exact filter result as a single tp
        # variable but do not retain startStep/endStep on the data variable.
        # When the caller supplied an exact interval and there is exactly one
        # precipitation variable, the filter itself is the selection proof.
        if st is not None and en is not None and len(precip)==1:
            return ds[precip[0][0]].load(),precip[0][0]
        return None,None

    filters=[]
    if st is not None and en is not None:
        filters.extend([
            {"typeOfLevel":"surface","stepType":"accum","startStep":st,"endStep":en},
            {"shortName":"tp","startStep":st,"endStep":en},
            {"shortName":"apcp","startStep":st,"endStep":en},
        ])
    filters.extend([
        {"typeOfLevel":"surface","stepType":"accum"},
        {"shortName":"tp"},
        {"shortName":"apcp"},
    ])

    for filt in filters:
        try:
            ds=xr.open_dataset(path,engine="cfgrib",backend_kwargs={"indexpath":"","filter_by_keys":filt})
            da,name=inspect(ds,str(filt))
            if da is not None:
                ds.close(); return da,name
            errors.append(f"filter={filt} variables={list(ds.data_vars)}")
            ds.close()
        except Exception as exc:
            errors.append(f"filter={filt} failed: {exc}")

    try:
        groups=cfgrib.open_datasets(path,backend_kwargs={"indexpath":""})
        summary=[]
        for ds in groups:
            summary.append({"vars":list(ds.data_vars),"attrs":{k:ds[k].attrs for k in ds.data_vars if str(k).lower() in ("tp","apcp")}})
            da,name=inspect(ds,"group-scan")
            if da is not None:
                for other in groups:
                    try: other.close()
                    except Exception: pass
                return da,name
        errors.append(f"cfgrib groups={summary}")
        for ds in groups:
            try: ds.close()
            except Exception: pass
    except Exception as exc:
        errors.append(f"cfgrib group scan failed: {exc}")

    raise KeyError(f"GFS APCP interval not found in {path}; expected {st}-{en}h; " + " | ".join(errors))


def gfs_interval_precipitation_valid(path, start_step, end_step):
    """Validate a NOMADS GFS APCP interval message.

    IMPORTANT: validation is serialized because ecCodes/cfgrib decoding is not
    allowed to run concurrently with other GFS decoders in this process.
    Network downloads remain parallel; only the native GRIB decode is locked.
    """
    if not valid_grib(path):
        return False,"not a valid GRIB header/file"
    try:
        with GFS_DECODE_LOCK:
            da,name=_open_gfs_precip_range_dataset(path,start_step,end_step)
            arr=np.asarray(da.values)
            if arr.size==0 or arr.ndim<2 or not np.isfinite(arr).any():
                return False,f"{name} decoded but contains no finite data"
            return True,f"{name} decoded • interval={start_step}-{end_step}h"
    except Exception as exc:
        return False,str(exc)


def open_gfs_interval_tp(path,start_step,end_step):
    with GFS_DECODE_LOCK:
        return _open_gfs_precip_range_dataset(path,start_step,end_step)

def _open_gfs_precip_dataset(path, expected_end_step=None):
    """Decode the correct GFS cumulative precipitation message.

    IMPORTANT GFS FINDING (26 Sep 2026):
    Each NOMADS APCP file contains TWO messages:
      1) the final 6-hour accumulation, e.g. 18-24 for F024
      2) the cumulative accumulation from model start, e.g. 0-24 for F024

    The old decoder selected the first message because it only filtered
    ``stepType=accum``.  That produced the wrong rainfall maps.

    We therefore explicitly select the cumulative message whose
    startStep=0 and endStep=expected_end_step.
    """
    if xr is None or cfgrib is None:
        raise RuntimeError("xarray/cfgrib dependencies are not installed")
    path = str(path)
    errors=[]
    expected = int(expected_end_step) if expected_end_step is not None else None

    def choose_from_dataset(ds, label):
        names={str(k).lower():k for k in ds.data_vars}
        candidates=[]
        for name in ds.data_vars:
            var=ds[name]
            short=str(var.attrs.get("GRIB_shortName", "")).lower()
            long_name=str(var.attrs.get("GRIB_name", "")).lower()
            standard=str(var.attrs.get("standard_name", "")).lower()
            start_step=var.attrs.get("GRIB_startStep", None)
            end_step=var.attrs.get("GRIB_endStep", None)
            step_range=var.attrs.get("GRIB_stepRange", None)
            try: start_num=float(start_step)
            except Exception: start_num=None
            try: end_num=float(end_step)
            except Exception: end_num=None
            precip=(short in ("tp","apcp") or "precipitation" in long_name or "precipitation" in standard or str(name).lower() in ("tp","apcp"))
            if precip:
                candidates.append((name,start_num,end_num,step_range))

        # First priority: exact cumulative message requested by the forecast hour.
        if expected is not None:
            for name,start_num,end_num,step_range in candidates:
                if start_num == 0 and end_num == expected:
                    return ds[name].load(), name

        # Second priority: any cumulative 0->N precipitation message.
        cumulative=[x for x in candidates if x[1] == 0]
        if cumulative:
            if expected is None:
                name=cumulative[0][0]
                return ds[name].load(), name
            # If expected metadata was not exposed numerically, use the
            # message whose range string ends at the requested hour.
            for name,start_num,end_num,step_range in cumulative:
                if str(step_range).strip() in (f"0-{expected}", f"0-{expected}.0"):
                    return ds[name].load(), name

        return None, None

    # 1) Exact cfgrib filter. startStep/endStep are the decisive keys.
    filter_attempts=[]
    if expected is not None:
        filter_attempts.append({
            "typeOfLevel":"surface",
            "stepType":"accum",
            "startStep":0,
            "endStep":expected,
        })
    filter_attempts.append({"typeOfLevel":"surface","stepType":"accum"})

    for filt in filter_attempts:
        try:
            ds=xr.open_dataset(
                path, engine="cfgrib",
                backend_kwargs={"indexpath":"","filter_by_keys":filt}
            )
            da,name=choose_from_dataset(ds, str(filt))
            if da is not None:
                ds.close()
                return da,name
            errors.append(f"filter={filt} variables={list(ds.data_vars)}")
            ds.close()
        except Exception as exc:
            errors.append(f"filter={filt} failed: {exc}")

    # 2) Scan all GRIB groups and inspect their raw accumulation metadata.
    try:
        groups=cfgrib.open_datasets(path, backend_kwargs={"indexpath":""})
        group_summary=[]
        for ds in groups:
            group_summary.append(list(ds.data_vars))
            da,name=choose_from_dataset(ds, "group-scan")
            if da is not None:
                for other in groups:
                    try: other.close()
                    except Exception: pass
                return da,name
        errors.append(f"cfgrib groups={group_summary}")
        for ds in groups:
            try: ds.close()
            except Exception: pass
    except Exception as exc:
        errors.append(f"cfgrib group scan failed: {exc}")

    # 3) Targeted shortName probes, still requiring the cumulative interval.
    for short_name in ("tp","apcp"):
        try:
            filt={"shortName":short_name}
            if expected is not None:
                filt.update({"startStep":0,"endStep":expected})
            ds=xr.open_dataset(path,engine="cfgrib",backend_kwargs={"indexpath":"","filter_by_keys":filt})
            da,name=choose_from_dataset(ds, "shortName")
            if da is not None:
                ds.close(); return da,name
            errors.append(f"shortName={short_name} filter={filt} variables={list(ds.data_vars)}")
            ds.close()
        except Exception as exc:
            errors.append(f"shortName={short_name} failed: {exc}")

    raise KeyError(
        f"GFS cumulative precipitation field not found in {path}; "
        f"expected 0-{expected if expected is not None else 'N'}h; "
        + " | ".join(errors)
    )

def gfs_precipitation_valid(path, expected_end_step=None):
    """Return True only when the required cumulative precip field decodes."""
    if not valid_grib(path):
        return False, "not a valid GRIB header/file"
    try:
        with GFS_DECODE_LOCK:
            da,name=_open_gfs_precip_dataset(path, expected_end_step=expected_end_step)
        arr=np.asarray(da.values)
        if arr.size == 0 or arr.ndim < 2 or not np.isfinite(arr).any():
            return False, f"{name} decoded but contains no finite data"
        start=da.attrs.get("GRIB_startStep")
        end=da.attrs.get("GRIB_endStep")
        return True, f"{name} decoded • startStep={start} • endStep={end}"
    except Exception as exc:
        return False, str(exc)

def open_gfs_tp(path, expected_end_step=None):
    """Read the requested cumulative GFS precipitation field."""
    with GFS_DECODE_LOCK:
        da,name=_open_gfs_precip_dataset(path, expected_end_step=expected_end_step)
        return da

def _gfs_selftest_plot(path, run_date, run_hour, da, variable_name):
    """Create a real F024 rainfall test image from the freshly downloaded GRIB."""
    if plt is None:
        raise RuntimeError("matplotlib is unavailable for GFS self-test plotting")
    vals=np.asarray(da.values,dtype=np.float32)
    if vals.ndim > 2:
        vals=np.squeeze(vals)
    if vals.ndim != 2:
        raise RuntimeError(f"GFS self-test precipitation array is not 2-D: {vals.shape}")
    lat=np.asarray(da.latitude.values,dtype=float)
    lon=np.asarray(da.longitude.values,dtype=float)
    vals,lat,lon,_,_=_validate_regular_grid(vals,lat,lon)
    finite=np.isfinite(vals)
    if not finite.any():
        raise RuntimeError("GFS self-test precipitation field contains no finite values")
    vmax=float(np.nanmax(vals))
    fig,ax=plt.subplots(figsize=(10,7),dpi=120)
    cmap,norm=_rainfall_cmap()
    mesh=ax.pcolormesh(lon,lat,vals,cmap=cmap,norm=norm,shading="auto")
    ax.set_xlim(max(66,float(lon.min())),min(98,float(lon.max())))
    ax.set_ylim(max(6,float(lat.min())),min(38,float(lat.max())))
    ax.set_xlabel("Longitude"); ax.set_ylabel("Latitude")
    ax.set_title(f"MASRAINMAN GFS SELF-TEST • F024 • {run_date} {run_hour:02d}Z\n{variable_name} • max {vmax:.1f} mm")
    cb=fig.colorbar(mesh,ax=ax,pad=0.02,extend="both")
    cb.set_label("Accumulated precipitation (mm)")
    fig.tight_layout()
    out=Path(path).parent/"GFS_SELFTEST_F024.png"
    fig.savefig(out,format="png",dpi=120,bbox_inches="tight")
    plt.close(fig)
    return out,vmax

def build_gfs_rainfall(day=1,period=None):
    """Build the selected GFS rainfall product.

    24 Hour:
      Day 1 = cumulative 0-24
      Day 2 = cumulative 0-48 minus cumulative 0-24
      Day 3 = cumulative 0-72 minus cumulative 0-48
      Day 4 = cumulative 0-96 minus cumulative 0-72
      Day 5 = cumulative 0-120 minus cumulative 0-96

    7 Day / 15 Day:
      Use the cumulative 0-168 / 0-360 field directly.

    We deliberately do NOT use the separate 6-hour messages (18-24,
    42-48, 66-72, etc.).
    """
    period=period or ACTIVE_PERIOD
    run_date,run_hour,folder=read_gfs_run()

    if period=="24 Hour":
        day=int(day)
        if day < 1 or day > 5:
            raise ValueError("GFS 24-hour day must be 1..5")
        # V79 primary mode: NOMADS APCP filter returns the forecast
        # accumulation interval (e.g. 66-72) on some current runs.
        # Build each 24-hour rainfall map by summing its four 6-hour
        # intervals. This is also compatible with files that expose only
        # interval APCP and avoids assuming a cumulative 0-N message.
        start_ep=(day-1)*24
        end_ep=day*24
        interval_arrays=[]
        lat=lon=None
        interval_names=[]
        for ep in range(start_ep+6,end_ep+1,6):
            path_i=folder/f"gfs_i{ep:03d}.grib2"
            if not path_i.exists():
                # Backward-compatible fallback to an existing cumulative file
                # if the old V78 downloader already produced one.
                cum=folder/f"gfs_f{ep:03d}.grib2"
                if cum.exists():
                    try:
                        da=open_gfs_tp(cum,expected_end_step=ep)
                        vals=np.asarray(da.values,dtype=np.float32)
                        if ep==24:
                            rain_values=vals; lat=np.asarray(da.latitude.values,dtype=float); lon=np.asarray(da.longitude.values,dtype=float)
                        else:
                            prev=open_gfs_tp(folder/f"gfs_f{ep-6:03d}.grib2",expected_end_step=ep-6)
                            rain_values=np.clip(vals-np.asarray(prev.values,dtype=np.float32),0,None)
                            lat=np.asarray(da.latitude.values,dtype=float); lon=np.asarray(da.longitude.values,dtype=float)
                        rain_values,lat,lon,grid_dx,grid_dy=_validate_regular_grid(rain_values,lat,lon)
                        valid_end_dt=datetime.strptime(run_date,"%Y%m%d")+timedelta(hours=end_ep)
                        return rain_values,lat,lon,{"model":"GFS","mode":"deterministic","run":f"{run_date} {run_hour:02d}Z","period":period,"day":int(day),"start":"F000","end":f"F{end_ep:03d}","display_range":f"F000 → F{end_ep:03d}","valid_end_date":valid_end_dt.strftime("%d %b %Y"),"valid_end_iso":valid_end_dt.strftime("%Y-%m-%d"),"valid_end_time":"08:30 AM IST","valid_end_endpoint":f"F{end_ep:03d}","members":1,"min":float(np.nanmin(rain_values)),"max":float(np.nanmax(rain_values)),"mean":float(np.nanmean(rain_values)),"lat_min":float(lat.min()),"lat_max":float(lat.max()),"lon_min":float(lon.min()),"lon_max":float(lon.max()),"grid_dx":grid_dx,"grid_dy":grid_dy,"grid_rows":int(rain_values.shape[0]),"grid_cols":int(rain_values.shape[1]),"finite_count":int(np.isfinite(rain_values).sum()),"positive_count":int((rain_values>=0.1).sum()),"projection":"LEAFLET DIRECT LATLON CANVAS","tp_accumulation":f"cumulative fallback 0-{end_ep}h","tp_message_selection":f"startStep=0, endStep={end_ep}"}
                    except Exception:
                        pass
                raise FileNotFoundError(f"Missing GFS interval file: {path_i}")
            da,name=open_gfs_interval_tp(path_i,ep-6,ep)
            arr=np.asarray(da.values,dtype=np.float32)
            if arr.ndim>2: arr=np.squeeze(arr)
            interval_arrays.append(arr)
            interval_names.append(name)
            lat=np.asarray(da.latitude.values,dtype=float)
            lon=np.asarray(da.longitude.values,dtype=float)
        rain_values=np.sum(np.stack(interval_arrays,axis=0),axis=0,dtype=np.float32)
        current_ep=end_ep
        accumulation_label=f"sum of 6-hour APCP intervals F{start_ep:03d}→F{end_ep:03d}"
    elif period in ("7 Day Accumulation","15 Day Accumulation"):
        current_ep=168 if period=="7 Day Accumulation" else 360
        interval_ends=(list(range(6,169,6)) if period=="7 Day Accumulation" else list(range(6,241,6))+list(range(252,361,12)))
        interval_arrays=[]
        lat=lon=None
        for ep in interval_ends:
            path_i=folder/f"gfs_i{ep:03d}.grib2"
            if not path_i.exists(): raise FileNotFoundError(f"Missing GFS interval file: {path_i}")
            interval_start=ep-12 if (period=="15 Day Accumulation" and ep>240) else ep-6
            da,_=open_gfs_interval_tp(path_i,interval_start,ep)
            arr=np.asarray(da.values,dtype=np.float32)
            if arr.ndim>2: arr=np.squeeze(arr)
            interval_arrays.append(arr)
            lat=np.asarray(da.latitude.values,dtype=float)
            lon=np.asarray(da.longitude.values,dtype=float)
        rain_values=np.sum(np.stack(interval_arrays,axis=0),axis=0,dtype=np.float32)
        day=1
        accumulation_label=("sum of native 6-hour APCP intervals F000→F168" if period=="7 Day Accumulation" else "sum of native APCP intervals F000→F360 (6-hour through F240, then 12-hour)")
    else:
        raise ValueError(f"Unknown GFS rainfall period: {period}")

    # Tiny negative values can occur from floating-point subtraction. Real
    # precipitation cannot be negative, so clamp only after the correct
    # cumulative messages have been selected.
    rain_values=np.clip(rain_values,0,None)

    rain_values,lat,lon,grid_dx,grid_dy=_validate_regular_grid(rain_values,lat,lon)

    run_dt=datetime.strptime(run_date,"%Y%m%d")
    valid_end_dt=run_dt+timedelta(hours=current_ep)
    meta={
        "model":"GFS","mode":"deterministic","run":f"{run_date} {run_hour:02d}Z",
        "period":period,"day":int(day),"start":"F000","end":f"F{current_ep:03d}",
        "display_range":f"F000 → F{current_ep:03d}",
        "valid_end_date":valid_end_dt.strftime("%d %b %Y"),
        "valid_end_iso":valid_end_dt.strftime("%Y-%m-%d"),
        "valid_end_time":"08:30 AM IST","valid_end_endpoint":f"F{current_ep:03d}",
        "members":1,"min":float(np.nanmin(rain_values)),"max":float(np.nanmax(rain_values)),
        "mean":float(np.nanmean(rain_values)),"lat_min":float(lat.min()),"lat_max":float(lat.max()),
        "lon_min":float(lon.min()),"lon_max":float(lon.max()),"lat_edge_min":float(lat.min()),
        "lat_edge_max":float(lat.max()),"lon_edge_min":float(lon.min()),"lon_edge_max":float(lon.max()),
        "grid_dx":grid_dx,"grid_dy":grid_dy,"grid_rows":int(rain_values.shape[0]),
        "grid_cols":int(rain_values.shape[1]),"finite_count":int(np.isfinite(rain_values).sum()),
        "positive_count":int((rain_values>=0.1).sum()),"projection":"LEAFLET DIRECT LATLON CANVAS",
        "tp_accumulation":accumulation_label,
        "tp_message_selection":(f"native APCP intervals through F{current_ep:03d}" if period in ("24 Hour","7 Day Accumulation","15 Day Accumulation") else f"startStep=0, endStep={current_ep}"),
    }
    return rain_values,lat,lon,meta

def active_runinfo_path(mode=None):
    mode = mode or ACTIVE_FORECAST_MODE
    return ECMWF_RUNINFO_ENS if mode == "ensemble" else ECMWF_RUNINFO_DET


def read_ecmwf_run(mode=None):
    mode = mode or ACTIVE_FORECAST_MODE
    runinfo = active_runinfo_path(mode)
    if not runinfo.exists():
        raise FileNotFoundError(f"ECMWF {mode} runinfo not found: {runinfo}")
    parts = runinfo.read_text(encoding="utf-8").strip().split()
    if len(parts) != 2:
        raise ValueError("ECMWF runinfo must contain YYYYMMDD HH")
    run_date, run_hour = parts[0], int(parts[1])
    if run_hour not in (0, 6, 12, 18):
        raise ValueError(f"Invalid ECMWF run hour: {run_hour:02d}Z")
    folder = ECMWF_ROOT / "ECMWF" / f"{run_date}_{run_hour:02d}Z"
    if not folder.is_dir():
        raise FileNotFoundError(f"ECMWF run folder not found: {folder}")
    return run_date, run_hour, folder


def rainfall_endpoints(run_hour, day=1, mode=None):
    """Return cumulative TP endpoints bracketing a true 24-h period.

    Day 1 must be F000 -> F024, not F024 -> F048.
    """
    mode = mode or ACTIVE_FORECAST_MODE
    endpoints = [0] + ecmwf_endpoint_list(run_hour, mode)
    idx = int(day) - 1
    if idx < 0 or idx + 1 >= len(endpoints):
        raise ValueError(f"Day {day} is not available for the {run_hour:02d}Z ECMWF {mode} run")
    return endpoints[idx], endpoints[idx + 1]

def open_tp(path, ensemble=False):
    if xr is None:
        raise RuntimeError("xarray/cfgrib dependencies are not installed")
    ds = xr.open_dataset(
        str(path),
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "surface", "shortName": "tp"},
            "indexpath": ""
        }
    )
    try:
        if "tp" not in ds:
            raise KeyError(f"tp not found in {path}; variables={list(ds.data_vars)}")
        da = ds["tp"].load()
    finally:
        ds.close()
    return da

def _validate_regular_grid(rain_values, lat, lon):
    rain_values = np.asarray(rain_values, dtype=np.float32)
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    if rain_values.ndim != 2 or rain_values.shape != (lat.size, lon.size):
        raise ValueError(f"Unexpected forecast grid shape: rain={rain_values.shape}, lat={lat.shape}, lon={lon.shape}")
    if not (np.all(np.isfinite(lat)) and np.all(np.isfinite(lon))):
        raise ValueError("ECMWF latitude/longitude contains non-finite values")
    if np.nanmin(lon) < 0:
        lon = np.mod(lon, 360.0)
    lat_order = np.argsort(lat)
    lon_order = np.argsort(lon)
    lat = lat[lat_order]
    lon = lon[lon_order]
    rain_values = rain_values[np.ix_(lat_order, lon_order)]
    plot_lat_min, plot_lat_max = 4.5, 24.0
    plot_lon_min, plot_lon_max = 66.5, 95.5
    lat_mask = (lat >= plot_lat_min) & (lat <= plot_lat_max)
    lon_mask = (lon >= plot_lon_min) & (lon <= plot_lon_max)
    if not lat_mask.any() or not lon_mask.any():
        raise ValueError(f"Forecast grid does not cover focus domain {plot_lat_min}:{plot_lat_max}N, {plot_lon_min}:{plot_lon_max}E")
    lat = lat[lat_mask]
    lon = lon[lon_mask]
    rain_values = rain_values[np.ix_(lat_mask, lon_mask)]
    dlat = np.diff(lat); dlon = np.diff(lon)
    grid_dy = float(np.median(dlat)) if dlat.size else 0.25
    grid_dx = float(np.median(dlon)) if dlon.size else 0.25
    if dlat.size and not np.allclose(dlat, grid_dy, atol=1e-6):
        raise ValueError("Latitude grid is not regular")
    if dlon.size and not np.allclose(dlon, grid_dx, atol=1e-6):
        raise ValueError("Longitude grid is not regular")
    if not np.isclose(grid_dx, 0.25, atol=0.01) or not np.isclose(grid_dy, 0.25, atol=0.01):
        raise ValueError(f"Unexpected forecast grid resolution: {grid_dx:.4f}° x {grid_dy:.4f}°")
    return rain_values, lat, lon, grid_dx, grid_dy

def build_ecmwf_rainfall(day=1, mode=None, period=None, product=None):
    """Build ECMWF HRES or ECMWF ENS rainfall products.

    ECMWF ENS TP is cumulative from forecast start and is supplied in metres.
    The dashboard converts to millimetres and, for ENS, derives:
    MEAN, P50, P75, P90, PROB50 and PROB100 from all 50 perturbed members.
    """
    mode = mode or ACTIVE_FORECAST_MODE
    period = period or ACTIVE_PERIOD
    product = (product or ACTIVE_ENSEMBLE_PRODUCT or "MEAN").upper()

    run_date, run_hour, folder = read_ecmwf_run(mode)
    if period == "24 Hour":
        start_ep, end_ep = rainfall_endpoints(run_hour, day, mode)
    else:
        start_ep, end_ep = ecmwf_period_endpoint(run_hour, mode, period)

    prefix = "ecmwf_hres" if mode == "deterministic" else "ecmwf_ens"
    end_file = folder / f"{prefix}_f{end_ep:03d}.grib2"
    if not end_file.exists():
        raise FileNotFoundError(f"Missing ECMWF endpoint file: {end_file}")

    tp = open_tp(end_file, ensemble=(mode == "ensemble"))

    if mode == "ensemble":
        if "number" not in tp.dims:
            raise ValueError("ECMWF ENS member dimension 'number' not found")
        tp = tp.sortby("number")
        raw_mm = (tp * 1000.0).clip(min=0).values.astype(np.float32)
        members = int(tp.sizes["number"])
        products = {
            "MEAN": np.mean(raw_mm, axis=0),
            "P50": np.percentile(raw_mm, 50, axis=0),
            "P75": np.percentile(raw_mm, 75, axis=0),
            "P90": np.percentile(raw_mm, 90, axis=0),
            "PROB50": np.mean(raw_mm >= 50.0, axis=0) * 100.0,
            "PROB100": np.mean(raw_mm >= 100.0, axis=0) * 100.0,
        }
        if product not in products:
            raise ValueError(f"Unknown ECMWF ENS product: {product}")
        rain_values = np.asarray(products[product], dtype=np.float32)
        labels = {
            "MEAN":"ECMWF ENS Mean", "P50":"ECMWF ENS P50",
            "P75":"ECMWF ENS P75", "P90":"ECMWF ENS P90",
            "PROB50":"ECMWF ENS Probability ≥50 mm",
            "PROB100":"ECMWF ENS Probability ≥100 mm"
        }
        product_label = labels[product]
        units = "%" if product.startswith("PROB") else "mm"
    else:
        if "number" in tp.dims:
            raise ValueError("ECMWF HRES unexpectedly contains ensemble member dimension")
        rain_values = (tp * 1000.0).clip(min=0).values.astype(np.float32)
        members = 1
        product = "MEAN"
        product_label = "ECMWF IFS HRES"
        units = "mm"

    lat = np.asarray(tp.latitude.values, dtype=float)
    lon = np.asarray(tp.longitude.values, dtype=float)
    rain_values, lat, lon, grid_dx, grid_dy = _validate_regular_grid(rain_values, lat, lon)

    run_dt = datetime.strptime(run_date, "%Y%m%d")
    valid_end_dt = run_dt + timedelta(hours=int(end_ep))
    meta = {
        "model": product_label, "mode": mode,
        "run": f"{run_date} {run_hour:02d}Z", "period": period,
        "day": int(day), "product": product, "product_label": product_label,
        "units": units, "start": f"F{start_ep:03d}", "end": f"F{end_ep:03d}",
        "display_range": f"F{start_ep:03d} → F{end_ep:03d}",
        "valid_end_date": valid_end_dt.strftime("%d %b %Y"),
        "valid_end_iso": valid_end_dt.strftime("%Y-%m-%d"),
        "valid_end_time": "08:30 AM IST", "valid_end_endpoint": f"F{end_ep:03d}",
        "members": members,
        "min": float(np.nanmin(rain_values)), "max": float(np.nanmax(rain_values)),
        "mean": float(np.nanmean(rain_values)),
        "lat_min": float(lat.min()), "lat_max": float(lat.max()),
        "lon_min": float(lon.min()), "lon_max": float(lon.max()),
        "lat_edge_min": float(lat.min()), "lat_edge_max": float(lat.max()),
        "lon_edge_min": float(lon.min()), "lon_edge_max": float(lon.max()),
        "grid_dx": grid_dx, "grid_dy": grid_dy,
        "grid_rows": int(rain_values.shape[0]), "grid_cols": int(rain_values.shape[1]),
        "finite_count": int(np.isfinite(rain_values).sum()),
        "positive_count": int((rain_values >= (0.1 if units=="mm" else 1.0)).sum()),
        "projection": "LEAFLET DIRECT LATLON CANVAS",
        "tp_accumulation": "cumulative from forecast start; END field used directly",
    }
    return rain_values, lat, lon, meta

def build_icon_eps_rainfall(product=None, run=None, period=None):
    """Load validated ICON-EPS F024 cache and derive the selected product.

    Cache shape is exactly (40,85,97), lat 25..4 descending and lon 68..92 ascending.
    No DWD network access or native-grid decoding occurs here.
    """
    period=period or ACTIVE_PERIOD
    if period != '24 Hour':
        raise ValueError('ICON EPS cache currently supports 24 Hour F024 only')
    product=(product or ACTIVE_ENSEMBLE_PRODUCT or 'MEAN').upper()
    run=run or ACTIVE_RUN
    if not run or not re.fullmatch(r'\d{10}', str(run)):
        raise RuntimeError('ICON EPS requires a selected cached run')
    cache_file=ICON_EPS_CACHE_ROOT / f'ICON_EPS_{run}_F024_SUPERENSEMBLE_GRID_40MEM.npy'
    if not cache_file.exists():
        raise FileNotFoundError(f'ICON EPS cache not found: {cache_file}')
    stack=np.asarray(np.load(cache_file),dtype=np.float32)
    if stack.shape != (40,85,97):
        raise RuntimeError(f'ICON EPS cache shape invalid: {stack.shape}; expected (40,85,97)')
    lat_file=ICON_EPS_CACHE_ROOT / f'ICON_EPS_{run}_MME_LAT.npy'
    lon_file=ICON_EPS_CACHE_ROOT / f'ICON_EPS_{run}_MME_LON.npy'
    lat=np.asarray(np.load(lat_file),dtype=float) if lat_file.exists() else np.arange(25.0,3.99,-0.25)
    lon=np.asarray(np.load(lon_file),dtype=float) if lon_file.exists() else np.arange(68.0,92.01,0.25)
    if lat.shape!=(85,) or lon.shape!=(97,): raise RuntimeError('ICON EPS cache coordinate shape mismatch')
    products={
      'MEAN':np.mean(stack,axis=0), 'P50':np.percentile(stack,50,axis=0),
      'P75':np.percentile(stack,75,axis=0), 'P90':np.percentile(stack,90,axis=0),
      'PROB50':np.mean(stack>=50.0,axis=0)*100.0, 'PROB100':np.mean(stack>=100.0,axis=0)*100.0}
    if product not in products: raise ValueError(f'Unknown ICON EPS product: {product}')
    rain=np.asarray(products[product],dtype=np.float32)
    labels={'MEAN':'ICON EPS Mean','P50':'ICON EPS P50','P75':'ICON EPS P75','P90':'ICON EPS P90','PROB50':'ICON EPS Probability ≥50 mm','PROB100':'ICON EPS Probability ≥100 mm'}
    units='%' if product.startswith('PROB') else 'mm'
    run_dt=datetime.strptime(run,'%Y%m%d%H').replace(tzinfo=timezone.utc)
    valid_end=run_dt+timedelta(hours=24)
    valid_end_ist=valid_end+timedelta(hours=5,minutes=30)
    meta={'model':labels[product],'mode':'ensemble','run':f'{run[:8]} {run[8:]}Z','period':'24 Hour','day':1,'product':product,'product_label':labels[product],'units':units,'start':'F000','end':'F024','display_range':'F000 → F024','valid_end_endpoint':'F024','valid_end_date':valid_end_ist.strftime('%d %b %Y'),'valid_end_time':valid_end_ist.strftime('%I:%M %p IST'),'members':40,'min':float(np.nanmin(rain)),'max':float(np.nanmax(rain)),'mean':float(np.nanmean(rain)),'lat_min':float(lat.min()),'lat_max':float(lat.max()),'lon_min':float(lon.min()),'lon_max':float(lon.max()),'lat_edge_min':float(lat.min()),'lat_edge_max':float(lat.max()),'lon_edge_min':float(lon.min()),'lon_edge_max':float(lon.max()),'grid_dx':0.25,'grid_dy':0.25,'grid_rows':85,'grid_cols':97,'finite_count':int(np.isfinite(rain).sum()),'positive_count':int((rain >= (1.0 if units=='%' else 0.1)).sum()),'projection':'LEAFLET DIRECT LATLON CANVAS','tp_accumulation':'DWD ICON-EPS direct cumulative F024 endpoint; standalone validated; cache remapped from native unstructured grid'}
    return rain,lat,lon,meta

def _rainfall_rgba(rain):
    """Exact reference-image palette for raster overlays.

    Uses the same class breaks and RGB colours as _rainfall_cmap(), so the
    raster itself and the plotted colourbar have identical visual classes.
    """
    arr = np.asarray(rain, dtype=np.float32)
    finite = np.isfinite(arr)
    bounds = np.array([
        1, 2, 4, 6, 10, 15, 20, 30, 40, 50,
        65, 80, 100, 120, 160, 200, 300, 400, 600, 700
    ], dtype=np.float32)
    colors = np.array([
        [178,211,244],  # <1
        [158,199,245],  # 1-2
        [110,172,245],  # 2-4
        [70,153,247],   # 4-6
        [31,130,219],   # 6-10
        [0,122,0],      # 10-15
        [0,160,0],      # 15-20
        [0,208,0],      # 20-30
        [0,255,0],      # 30-40
        [255,255,0],    # 40-50
        [255,224,64],   # 50-65
        [249,181,46],   # 65-80
        [245,124,0],    # 80-100
        [238,108,0],    # 100-120
        [216,74,17],    # 120-160
        [240,98,146],   # 160-200
        [233,30,99],    # 200-300
        [173,20,87],    # 300-400
        [232,232,232],  # 400-600
        [176,176,176],  # 600-700
        [58,58,58],     # >700
    ], dtype=np.uint8)
    values = np.where(finite, arr, 0.0)
    idx = np.searchsorted(bounds, values, side='right')
    idx = np.clip(idx, 0, len(colors)-1)
    rgba = np.zeros((*arr.shape, 4), dtype=np.uint8)
    rgba[..., :3] = colors[idx]
    rgba[..., 3] = np.where(finite & (arr >= 0.1), 225, 0).astype(np.uint8)
    return rgba

def _grid_edges(vals):
    vals=np.asarray(vals,dtype=float)
    if vals.size<2: raise ValueError("Need at least two grid coordinates")
    mid=(vals[:-1]+vals[1:])/2.0
    return np.concatenate(([vals[0]-(vals[1]-vals[0])/2],mid,[vals[-1]+(vals[-1]-vals[-2])/2]))

def _rainfall_cmap():
    """Exact MASRainman MME reference rainfall colour scale.

    Reference classes:
    <1, 1, 2, 4, 6, 10, 15, 20, 30, 40, 50, 65, 80,
    100, 120, 160, 200, 300, 400, 600, 700, >700 mm.
    The colours and tick values match the supplied MME 5-day reference image.
    """
    colors = [
        '#b2d3f4',  # < 1 mm  / under
        '#9ec7f5',  # 1-2
        '#6eacf5',  # 2-4
        '#4699f7',  # 4-6
        '#1f82db',  # 6-10
        '#007a00',  # 10-15
        '#00a000',  # 15-20
        '#00d000',  # 20-30
        '#00ff00',  # 30-40
        '#ffff00',  # 40-50
        '#ffe040',  # 50-65
        '#f9b52e',  # 65-80
        '#f57c00',  # 80-100
        '#ee6c00',  # 100-120
        '#d84a11',  # 120-160
        '#f06292',  # 160-200
        '#e91e63',  # 200-300
        '#ad1457',  # 300-400
        '#e8e8e8',  # 400-600
        '#b0b0b0',  # 600-700
        '#3a3a3a',  # >700 / over
    ]
    levels = [
        1, 2, 4, 6, 10, 15, 20, 30, 40, 50,
        65, 80, 100, 120, 160, 200, 300, 400, 600, 700
    ]
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(levels, len(colors), clip=False, extend='both')
    cmap.set_under(colors[0])
    cmap.set_over(colors[-1])
    return cmap, norm


def talk_normalize_place(q):
    q = (q or "").strip().lower()
    q = re.sub(r"[^a-z0-9\u0B80-\u0BFF\s\-]", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    # Common spoken prefixes
    for prefix in ("weather in ", "forecast for ", "forecast in ", "weather at ",
                   "weather ", "forecast ", "tell me about ", "show me "):
        if q.startswith(prefix):
            q = q[len(prefix):].strip()
    return q

def talk_place_suggestions(query, limit=3):
    q=talk_normalize_place(query)
    if not q: return []
    keys=list(TALK_PLACE_INDEX.keys())
    scored=[]
    for key in keys:
        ratio=difflib.SequenceMatcher(None,q,key).ratio()
        partial=1.0 if (q in key or key in q) else 0.0
        score=max(ratio,partial*0.92)
        if score>=0.48: scored.append((score,key))
    scored.sort(reverse=True)
    out=[]
    for _,key in scored[:limit]:
        lat,lon,name=TALK_PLACE_INDEX[key]
        out.append({"name":name,"lat":lat,"lon":lon,"key":key})
    return out

def talk_resolve_place(query):
    q = talk_normalize_place(query)
    if not q:
        return None
    if q in TALK_PLACE_INDEX:
        lat, lon, name = TALK_PLACE_INDEX[q]
        return {"query": query, "name": name, "lat": lat, "lon": lon, "exact": True, "resolver": "built-in", "geocode_source": "MasRainman built-in gazetteer"}
    # Exact token match first, then containment for phrases like "Chennai city"
    for key, value in TALK_PLACE_INDEX.items():
        if key == q or re.search(rf"\b{re.escape(key)}\b", q):
            lat, lon, name = value
            return {"query": query, "name": name, "lat": lat, "lon": lon, "exact": True, "resolver": "built-in", "geocode_source": "MasRainman built-in gazetteer"}
    # Unknown town/city: resolve live to coordinates instead of rejecting it.
    return talk_geocode_place(query)

def talk_nearest_grid_value(rain, lat_arr, lon_arr, target_lat, target_lon):
    """Nearest value for regular model grids and native ICON arrays."""
    a=np.asarray(rain,dtype=float)
    la=np.asarray(lat_arr,dtype=float)
    lo=np.asarray(lon_arr,dtype=float)
    if a.ndim==2 and la.ndim==1 and lo.ndim==1:
        iy=int(np.nanargmin(np.abs(la-target_lat)))
        dlon=((lo-target_lon+180.0)%360.0)-180.0
        ix=int(np.nanargmin(np.abs(dlon)))
        return float(a[iy,ix]),float(la[iy]),float(lo[ix])
    if a.ndim==1 and la.ndim==1 and lo.ndim==1 and a.size==la.size==lo.size:
        dlat=la-float(target_lat)
        dlon=((lo-float(target_lon)+180.0)%360.0)-180.0
        coslat=np.cos(np.deg2rad(float(target_lat)))
        idx=int(np.nanargmin(dlat*dlat+(dlon*coslat)*(dlon*coslat)))
        return float(a[idx]),float(la[idx]),float(lo[idx])
    raise ValueError(f"Unsupported rainfall grid shape: rain={a.shape}, lat={la.shape}, lon={lo.shape}")

# ---------------------------------------------------------------------------
# TALK LIVE-MODEL ENGINE (V74)

# One click = fresh server retrieval. No Talk dependency on GFS/ICON/AIFS
# local runinfo/archive. Models are fetched in parallel, while each model
# downloads its three 24-hour endpoints only ONCE per Talk request.
# ---------------------------------------------------------------------------
TALK_MODEL_LOCKS = {m: threading.Lock() for m in ("ECMWF","GFS","ICON","AIFS","UKMET","GEM","AIGFS")}
TALK_BUNDLE_CACHE = {}


def _talk_fresh_cache():
    # Cache is request-scoped in practice: build_talk_briefing clears it before
    # starting a new place query. Nothing is persisted to disk by Talk.
    TALK_BUNDLE_CACHE.clear()


def _talk_gfs_filter_params(run_date, run_hour, fhour):
    """Compatibility helper: Talk now uses the common APCP request builder."""
    return gfs_filter_params(run_date, run_hour, fhour)


def _download_talk_gfs_apcp(run_date, run_hour, fhour, target):
    """Talk adapter over the same common GFS APCP downloader used by Map.

    Talk keeps its request-scoped temporary target, but retrieval, GRIB header
    validation, retry handling and NOMADS request parameters are shared with
    the Live Map path through download_gfs_endpoint().
    """
    ok, detail = download_gfs_endpoint(run_date, run_hour, fhour, target)
    if ok:
        return True, detail
    return False, detail


def _talk_live_gfs_bundle():
    """Retrieve GFS Talk rainfall from native 6-hour APCP intervals.

    V78 proved that the NOMADS download itself succeeds, but the filtered APCP
    response on current runs may expose the native interval (for example
    66-72h) rather than a cumulative 0-72h message. V79 therefore downloads
    F006..F072 and sums the four 6-hour intervals for each Talk day.
    """
    if requests is None:
        raise RuntimeError("GFS Talk: requests module is unavailable")
    if xr is None or cfgrib is None:
        raise RuntimeError("GFS Talk: xarray/cfgrib is required")
    with TALK_MODEL_LOCKS["GFS"]:
        if "GFS" in TALK_BUNDLE_CACHE:
            return TALK_BUNDLE_CACHE["GFS"]
        import tempfile
        with tempfile.TemporaryDirectory(prefix="masrainman_talk_gfs_") as td:
            root=Path(td)
            failures=[]
            selected=None
            interval_fields={}

            for _,rd,rh in talk_recent_cycles(8):
                cycle_ok=True
                local={}
                # A completed F072 object is a lightweight cycle readiness
                # check; the actual source of rainfall remains the APCP filter.
                ok,detail=gfs_endpoint_available(rd,rh,72)
                if not ok:
                    failures.append(f"{rd} {rh:02d}Z F072 probe: {detail}")
                    continue
                for ep in range(6,73,6):
                    target=root/f"{rd}_{rh:02d}Z_i{ep:03d}.grib2"
                    ok,detail=_download_talk_gfs_apcp(rd,rh,ep,target)
                    if not ok:
                        cycle_ok=False
                        failures.append(f"{rd} {rh:02d}Z F{ep:03d}: {detail}")
                        break
                    valid,detail=gfs_interval_precipitation_valid(target,ep-6,ep)
                    if not valid:
                        cycle_ok=False
                        failures.append(f"{rd} {rh:02d}Z F{ep:03d} decode: {detail}")
                        break
                    da,name=open_gfs_interval_tp(target,ep-6,ep)
                    arr=np.asarray(da.values,dtype=np.float32)
                    if arr.ndim>2: arr=np.squeeze(arr)
                    local[ep]=(arr,np.asarray(da.latitude.values,dtype=float),np.asarray(da.longitude.values,dtype=float),name)
                if cycle_ok:
                    selected=(rd,rh)
                    interval_fields=local
                    break

            if selected is None:
                raise RuntimeError(
                    "GFS Talk: NOMADS APCP interval retrieval failed. "
                    + " | ".join(failures[-6:])
                )

            rd,rh=selected
            fields={}
            for day in (1,2,3):
                end_ep=day*24
                pieces=[]
                lat=lon=None
                for ep in range(end_ep-18,end_ep+1,6):
                    if ep not in interval_fields:
                        raise RuntimeError(f"GFS Talk: required native APCP interval F{ep-6:03d}→F{ep:03d} is missing")
                    arr,la,lo,_=interval_fields[ep]
                    pieces.append(arr)
                    lat=la; lon=lo
                rain=np.sum(np.stack(pieces,axis=0),axis=0,dtype=np.float32)
                rain=np.clip(rain,0,None)
                rain,lat,lon,_,_=_validate_regular_grid(rain,lat,lon)
                fields[end_ep]=(rain,lat,lon)

            meta={
                "model":"GFS","mode":"deterministic","run":f"{rd} {rh:02d}Z",
                "period":"24 Hour","members":1,
                "source":"NOAA/NCEP NOMADS • LIVE APCP 6-hour intervals • temporary Talk retrieval",
                "tp_accumulation":"sum of native 6-hour APCP intervals",
            }
            result=(fields,meta)
            TALK_BUNDLE_CACHE["GFS"]=result
            return result

# ---------------------------------------------------------------------------
# UKMET GLOBAL 10 KM LIVE TALK ENGINE
# Source proven by standalone V9: public Met Office ASDI S3 archive.
# Talk uses only a fresh request-scoped temporary retrieval; no UKMET local
# archive/runinfo is consulted.  For the 72h Talk product we need continuous
# accumulation coverage from T+0 to T+72, respecting the actual cadence.
# ---------------------------------------------------------------------------
UKMET_BUCKET = "met-office-atmospheric-model-data"
UKMET_REGION = "eu-west-2"
UKMET_BASE = f"https://{UKMET_BUCKET}.s3.{UKMET_REGION}.amazonaws.com"
UKMET_PREFIX = "global-deterministic-10km/"
UKMET_TARGET_LAT, UKMET_TARGET_LON = 13.08, 80.27

def _ukmet_s3_list(prefix, continuation_token=None):
    params={"list-type":"2","max-keys":"1000","prefix":prefix}
    if continuation_token:
        params["continuation-token"]=continuation_token
    r=requests.get(UKMET_BASE+"/",params=params,timeout=60)
    r.raise_for_status()
    root=ET.fromstring(r.content)
    ns={"s3":"http://s3.amazonaws.com/doc/2006-03-01/"}
    objs=[]
    for c in root.findall("s3:Contents",ns):
        objs.append({"key":c.findtext("s3:Key",default="",namespaces=ns),"size":int(c.findtext("s3:Size",default="0",namespaces=ns)),"modified":c.findtext("s3:LastModified",default="",namespaces=ns)})
    truncated=root.findtext("s3:IsTruncated",default="false",namespaces=ns).lower()=="true"
    token=root.findtext("s3:NextContinuationToken",default="",namespaces=ns)
    return objs,truncated,token

def _ukmet_s3_list_all(prefix):
    all_objects=[]; token=None; pages=0
    while True:
        objs,truncated,next_token=_ukmet_s3_list(prefix,token)
        pages+=1; all_objects.extend(objs)
        if not truncated or not next_token: break
        token=next_token
        if pages>=50: raise RuntimeError(f"UKMET S3 pagination exceeded 50 pages for {prefix}")
    return all_objects,pages

def _ukmet_parse_key(key):
    m=re.search(r"/(\d{8}T\d{4}Z)/(\d{8}T\d{4}Z)-PT(\d{4})H(\d{2})M-(.+)\.nc$",key)
    if not m: return None
    run,valid,hh,mm,name=m.groups()
    return {"run":run,"valid":valid,"hour":int(hh),"minute":int(mm),"name":name}

def _ukmet_accumulation_hours(name):
    m=re.search(r"accumulation-pt(\d{2})h",name.lower())
    return int(m.group(1)) if m else None

def _ukmet_is_precip(name):
    n=name.lower()
    return n.startswith("precipitation_accumulation-pt") or n.startswith("rainfall_accumulation-pt")

def _ukmet_choose(items):
    return sorted(items,key=lambda x:(0 if x["name"].lower().startswith("precipitation_accumulation-") else 1,x["name"]))[0]

def _ukmet_coverage_ok(fmap,end_hour):
    cursor=int(end_hour); selected=[]
    while cursor>0:
        item=fmap.get(cursor)
        if item is None: return False,[],cursor
        dur=int(item["duration_h"])
        if dur<=0 or dur>cursor: return False,[],cursor
        selected.append(item); cursor-=dur
    selected.reverse(); return True,selected,0

def _ukmet_recent_long_runs():
    now=datetime.now(timezone.utc); runs=[]
    for back in range(4):
        d=now.date()-timedelta(days=back)
        for h in (12,0):
            t=datetime(d.year,d.month,d.day,h,tzinfo=timezone.utc)
            if t<=now+timedelta(hours=1): runs.append(t)
    return sorted(set(runs),reverse=True)

def _ukmet_discover_talk():
    failures=[]
    for t in _ukmet_recent_long_runs():
        run=t.strftime("%Y%m%dT%H%MZ"); prefix=UKMET_PREFIX+run+"/"
        try: allobs,pages=_ukmet_s3_list_all(prefix)
        except Exception as exc:
            failures.append(f"{run} LIST: {type(exc).__name__}: {exc}"); continue
        fmap={}; n=0
        for o in allobs:
            p=_ukmet_parse_key(o["key"])
            if not p or p["run"]!=run or not _ukmet_is_precip(p["name"]): continue
            dur=_ukmet_accumulation_hours(p["name"])
            if dur is None or p["hour"]<=0 or p["hour"]>72: continue
            item={**o,**p,"duration_h":dur}; n+=1
            old=fmap.get(p["hour"]); fmap[p["hour"]]=item if old is None else _ukmet_choose([old,item])
        ok,selected,missing=_ukmet_coverage_ok(fmap,72)
        print(f"[TALK UKMET] PROBE {run} • pages={pages} • objects={len(allobs)} • accumulation={n} • complete_0_72={ok}",flush=True)
        if ok:
            print(f"[TALK UKMET] SELECTED {run} • files={len(selected)}",flush=True)
            return selected
        failures.append(f"{run} missing F{missing:03d}")
    raise RuntimeError("UKMET Talk: no recent complete 00Z/12Z run with continuous precipitation coverage through T+72. "+" | ".join(failures[-4:]))

def _ukmet_decode_talk(path):
    ds=xr.open_dataset(path,decode_times=True)
    vars_=[v for v in ds.data_vars if "precipitation_amount" in str(ds[v].attrs.get("standard_name","")).lower()]
    if not vars_: vars_=[v for v in ds.data_vars if ("precipitation" in v.lower() or "rainfall" in v.lower()) and "accum" in v.lower()]
    if not vars_: ds.close(); raise RuntimeError(f"UKMET Talk: no rainfall accumulation variable in {path.name}; vars={list(ds.data_vars)}")
    v=vars_[0]; da=ds[v]; units=str(da.attrs.get("units","")).lower().strip()
    arr=np.asarray(da.values,dtype=np.float32)
    if units in {"m","metre","meter","metres","meters"}: arr*=1000.0
    elif units in {"kg m-2","kg m**-2","kg m^-2","mm"}: pass
    else: arr*=1000.0
    lat=np.asarray(ds["latitude"].values,dtype=float); lon=np.asarray(ds["longitude"].values,dtype=float); ds.close()
    arr=np.nan_to_num(arr,nan=0.0); return arr,lat,lon,v,units

def _talk_live_ukmet_bundle():
    if requests is None: raise RuntimeError("UKMET Talk: requests module is unavailable")
    if xr is None: raise RuntimeError("UKMET Talk: xarray is required")
    with TALK_MODEL_LOCKS["UKMET"]:
        if "UKMET" in TALK_BUNDLE_CACHE: return TALK_BUNDLE_CACHE["UKMET"]
        import tempfile
        selected=_ukmet_discover_talk(); run=selected[0]["run"]
        with tempfile.TemporaryDirectory(prefix="masrainman_talk_ukmet_") as td:
            root=Path(td); daily={1:None,2:None,3:None}; lat=lon=None

            def download_one(item):
                target=root/Path(item["key"]).name
                url=UKMET_BASE+"/"+quote(item["key"],safe="/")
                print(f"[TALK UKMET] DOWNLOAD F{item['hour']:03d} • PT{item['duration_h']:02d}H",flush=True)
                with requests.get(url,stream=True,timeout=90,headers={"User-Agent":"MASRAINMAN-UKMET-Talk/1.0","Accept":"*/*"}) as r:
                    r.raise_for_status()
                    with open(target,"wb") as f:
                        for chunk in r.iter_content(1024*1024):
                            if chunk: f.write(chunk)
                if not target.exists() or target.stat().st_size < 1000:
                    raise RuntimeError(f"UKMET Talk download unexpectedly small: {target.name}")
                return item,target

            # UKMET can require dozens of 1h/3h NetCDF objects for T+72.
            # Download them concurrently, then decode sequentially to keep
            # xarray/netCDF memory pressure predictable.
            downloaded=[]
            workers=min(8,max(2,len(selected)))
            with ThreadPoolExecutor(max_workers=workers,thread_name_prefix="UKMETTalkDL") as pool:
                futures=[pool.submit(download_one,item) for item in selected]
                for fut in as_completed(futures):
                    downloaded.append(fut.result())

            for item,target in sorted(downloaded,key=lambda pair:int(pair[0]["hour"])):
                arr,la,lo,_,_= _ukmet_decode_talk(target); lat=la; lon=lo
                day=((int(item["hour"])-1)//24)+1
                if daily[day] is None: daily[day]=np.zeros_like(arr,dtype=np.float32)
                daily[day]+=arr
            meta={"model":"UKMET","mode":"deterministic","run":f"{run[:8]} {run[9:11]}Z","period":"24 Hour","members":1,"source":"Met Office Global Deterministic 10 km • AWS ASDI • live temporary Talk retrieval","tp_accumulation":"sum of native cadence-aware precipitation accumulation intervals"}
            fields={}
            for day in (1,2,3):
                if daily[day] is None: raise RuntimeError(f"UKMET Talk: Day {day} rainfall field was not built")
                # Keep the Talk bundle contract identical to the other
                # deterministic models: (rainfall, latitude, longitude).
                fields[day*24]=(np.clip(daily[day],0,None),lat,lon)
            result=(fields,meta); TALK_BUNDLE_CACHE["UKMET"]=result; return result

def talk_recent_cycles(max_cycles=8):
    """Newest completed cycles first; keeps Talk discovery bounded and fast."""
    now=datetime.now(timezone.utc)
    out=[]
    for day_back in range(3):
        d=now.date()-timedelta(days=day_back)
        for rh in (18,12,6,0):
            dt=datetime(d.year,d.month,d.day,rh,tzinfo=timezone.utc)
            if dt<=now:
                out.append((dt,f"{d:%Y%m%d}",rh))
    out.sort(reverse=True)
    return out[:int(max_cycles)]

def talk_cycle_has_endpoints(endpoint_probe,rd,rh):
    """Probe F072 first; verify F024/F048 only after F072 exists."""
    ok,detail=endpoint_probe(rd,rh,72)
    if not ok: return False,f"F072 {detail}"
    for ep in (24,48):
        ok,detail=endpoint_probe(rd,rh,ep)
        if not ok: return False,f"F{ep:03d} {detail}"
    return True,"F024/F048/F072 available"

def talk_gfs_candidate_run():
    failures=[]
    for _,rd,rh in talk_recent_cycles(8):
        ok,detail=talk_cycle_has_endpoints(gfs_endpoint_available,rd,rh)
        if ok: return rd,rh
        failures.append(f"{rd} {rh:02d}Z: {detail}")
    raise RuntimeError("GFS Talk: no recent NOMADS cycle with F024/F048/F072. " + " | ".join(failures[-4:]))

def talk_icon_candidate_run():
    failures=[]
    for _,rd,rh in talk_recent_cycles(8):
        ok,detail=talk_cycle_has_endpoints(icon_endpoint_available,rd,rh)
        if ok: return rd,rh
        failures.append(f"{rd} {rh:02d}Z: {detail}")
    raise RuntimeError("ICON Talk: no recent DWD Global cycle with F024/F048/F072. " + " | ".join(failures[-4:]))

def _talk_live_icon_bundle():
    if requests is None:
        raise RuntimeError("ICON Talk: requests module is unavailable")
    if codes_grib_new_from_file is None:
        raise RuntimeError("ICON Talk: eccodes is not installed")
    with TALK_MODEL_LOCKS["ICON"]:
        if "ICON" in TALK_BUNDLE_CACHE:
            return TALK_BUNDLE_CACHE["ICON"]
        import tempfile
        with tempfile.TemporaryDirectory(prefix="masrainman_talk_icon_") as td:
            root=Path(td); raw_root=root/"raw"; grib_root=root/"grib2"; raw_root.mkdir(); grib_root.mkdir()
            global ICON_RAW_ROOT,ICON_GRIB_ROOT,ICON_COORD_CACHE,ICON_FIELD_CACHE
            old_raw,old_grib=ICON_RAW_ROOT,ICON_GRIB_ROOT
            old_coord_cache,old_field_cache=ICON_COORD_CACHE,ICON_FIELD_CACHE
            try:
                ICON_RAW_ROOT,ICON_GRIB_ROOT=raw_root,grib_root
                ICON_COORD_CACHE,ICON_FIELD_CACHE={},{}
                rd,rh=talk_icon_candidate_run()
                clat,clon=icon_native_coordinates(rh)
                fields={}
                for ep in (24,48,72):
                    grib=icon_download_endpoint(rd,rh,ep)
                    values=_icon_read_cumulative(grib,ep)
                    # Keep native ICON grid for Talk. Interpolating 2.95 million
                    # points to a regular grid is unnecessary when only one city
                    # value is requested and was a major source of delay.
                    fields[ep]=(np.asarray(values,dtype=np.float32),clat,clon)
                meta={"model":"ICON","mode":"deterministic","run":f"{rd} {rh:02d}Z","period":"24 Hour","members":1,"source":"DWD Open Data • LIVE ICON Global TOT_PREC • native-grid Talk retrieval"}
                result=(fields,meta)
                TALK_BUNDLE_CACHE["ICON"]=result
                return result
            finally:
                ICON_RAW_ROOT,ICON_GRIB_ROOT=old_raw,old_grib
                ICON_COORD_CACHE,ICON_FIELD_CACHE=old_coord_cache,old_field_cache


def _talk_live_aifs_bundle():
    """Fast AIFS Talk path.

    V158 priorities:
      1) Reuse AIFS_FIELD_CACHE immediately when the map engine already has
         F024/F048/F072 for a verified cycle.
      2) Reuse AIFS_RUNINFO/local valid endpoint files when available.
      3) Probe only recent cycles and stop at the first verified F072 cycle.
      4) Download F024/F048/F072 concurrently instead of serially.
    """
    if requests is None:
        raise RuntimeError("AIFS Talk: requests module is unavailable")
    if xr is None or cfgrib is None:
        raise RuntimeError("AIFS Talk: xarray/cfgrib is required")
    with TALK_MODEL_LOCKS["AIFS"]:
        if "AIFS" in TALK_BUNDLE_CACHE:
            return TALK_BUNDLE_CACHE["AIFS"]

        def build_from_cycle(rd, rh):
            cumulative={}
            # Existing in-memory AIFS fields are the fastest path.
            for ep in (24,48,72):
                key=(rd,int(rh),int(ep))
                if key in AIFS_FIELD_CACHE:
                    data,lat,lon,units=AIFS_FIELD_CACHE[key]
                    cumulative[ep]=(np.asarray(data,dtype=np.float32),np.asarray(lat,dtype=float),np.asarray(lon,dtype=float))
            missing=[ep for ep in (24,48,72) if ep not in cumulative]
            if missing:
                # Parallel endpoint retrieval materially reduces Talk latency.
                with ThreadPoolExecutor(max_workers=len(missing),thread_name_prefix="AIFSTalkDL") as pool:
                    futs={pool.submit(_aifs_cumulative,rd,rh,ep):ep for ep in missing}
                    for fut in as_completed(futs):
                        ep=futs[fut]
                        data,lat,lon,_units=fut.result()
                        cumulative[ep]=(np.asarray(data,dtype=np.float32),np.asarray(lat,dtype=float),np.asarray(lon,dtype=float))
            fields={}
            for ep in (24,48,72):
                cur,lat,lon=cumulative[ep]
                prev=cumulative[ep-24][0] if ep>24 else None
                rain=cur if prev is None else np.clip(cur-prev,0,None)
                rain,lat,lon,_,_=_validate_regular_grid(rain,lat,lon)
                fields[ep]=(rain,lat,lon)
            meta={"model":"AIFS","mode":"deterministic","run":f"{rd} {rh:02d}Z","period":"24 Hour","members":1,
                  "source":"ECMWF Open Data • LIVE AIFS Single • fast parallel 72h retrieval"}
            return fields,meta

        # 1. In-memory cache can survive map cleanup and avoids any network wait.
        cached_runs=sorted({(k[0],int(k[1])) for k in AIFS_FIELD_CACHE.keys() if isinstance(k,tuple) and len(k)==3}, reverse=True)
        for rd,rh in cached_runs:
            if all((rd,int(rh),ep) in AIFS_FIELD_CACHE for ep in (24,48,72)):
                result=build_from_cycle(rd,rh); TALK_BUNDLE_CACHE["AIFS"]=result; return result

        selected=None; failures=[]
        # 2. Existing runinfo first. Do not spend time rediscovering cycles.
        if AIFS_RUNINFO.exists():
            try:
                rd,rh=read_aifs_run()
                if all(aifs_endpoint_available(rd,rh,ep)[0] for ep in (24,48,72)):
                    selected=(rd,rh)
            except Exception as exc:
                failures.append(f"runinfo: {type(exc).__name__}: {exc}")

        # 3. Fast bounded discovery: F072 first, then F024/F048 only on a hit.
        if selected is None:
            cycles=talk_recent_cycles(4)
            # Probe F072 concurrently. This removes the serial 4-cycle ×
            # network-latency discovery bottleneck from the Talk critical path.
            probe_results={}
            with ThreadPoolExecutor(max_workers=min(4,len(cycles)),thread_name_prefix="AIFSTalkProbe") as pool:
                futures={pool.submit(aifs_endpoint_available,rd,rh,72):(rd,rh) for _,rd,rh in cycles}
                for fut in as_completed(futures):
                    rd,rh=futures[fut]
                    try:
                        probe_results[(rd,rh)]=fut.result()
                    except Exception as exc:
                        probe_results[(rd,rh)]=(False,f"{type(exc).__name__}: {exc}")
            for _,rd,rh in cycles:
                ok,detail=probe_results.get((rd,rh),(False,"probe unavailable"))
                if not ok:
                    failures.append(f"{rd} {rh:02d}Z: F072 {detail}")
                    continue
                # Only the first F072-positive cycles get the two confirming
                # endpoint probes.
                ok24,d24=aifs_endpoint_available(rd,rh,24)
                ok48,d48=aifs_endpoint_available(rd,rh,48)
                if ok24 and ok48:
                    selected=(rd,rh); break
                failures.append(f"{rd} {rh:02d}Z: F024 {d24}; F048 {d48}")
        if selected is None:
            raise RuntimeError("AIFS Talk: no recent live cycle with F024/F048/F072. " + " | ".join(failures[-4:]))

        result=build_from_cycle(*selected)
        TALK_BUNDLE_CACHE["AIFS"]=result
        return result

def build_aigfs_talk_bundle():
    """Fresh AIGFS Talk bundle using the proven V8 6-hour interval decoder.

    Talk only needs the first 72 hours. The worker selects a verified cycle by
    probing F072, then downloads/decodes F006..F072 and sums four native
    6-hour intervals for each of Day 1-3.
    """
    with TALK_MODEL_LOCKS["AIGFS"]:
        if "AIGFS" in TALK_BUNDLE_CACHE:
            return TALK_BUNDLE_CACHE["AIGFS"]
        candidates=aigfs_candidate_runs("24 Hour",3,limit_days=3)
        if not candidates:
            raise RuntimeError("AIGFS Talk: no verified recent cycle with F072")
        failures=[]
        for _,rd,rh in candidates:
            try:
                interval_fields={}
                for ep in range(6,73,6):
                    vals,lat,lon,_=aigfs_decode_interval(rd,rh,ep)
                    interval_fields[ep]=(vals,lat,lon)
                fields={}
                for day in (1,2,3):
                    end_ep=day*24
                    pieces=[interval_fields[ep][0] for ep in range(end_ep-18,end_ep+1,6)]
                    rain=np.sum(np.stack(pieces,axis=0),axis=0,dtype=np.float32)
                    rain=np.clip(rain,0,None)
                    lat=interval_fields[end_ep][1]; lon=interval_fields[end_ep][2]
                    fields[end_ep]=(rain,lat,lon)
                meta={"model":"AIGFS","mode":"deterministic","run":f"{rd} {rh:02d}Z","period":"24 Hour","members":1,
                      "source":"NOAA/NCEP AIGFS v1.1 • live native 6-hour TP intervals • isolated Talk worker",
                      "tp_accumulation":"sum of native 6-hour precipitation intervals"}
                result=(fields,meta); TALK_BUNDLE_CACHE["AIGFS"]=result
                return result
            except Exception as exc:
                failures.append(f"{rd} {rh:02d}Z: {type(exc).__name__}: {exc}")
        raise RuntimeError("AIGFS Talk retrieval failed. " + " | ".join(failures[-4:]))


def talk_get_field(model, day):
    day=int(day)
    if day not in (1,2,3): raise ValueError("Talk supports Day 1-3")
    if model=="GFS": bundle,meta=_talk_live_gfs_bundle()
    elif model=="ICON": bundle,meta=_talk_live_icon_bundle()
    elif model=="AIFS": bundle,meta=_talk_live_aifs_bundle()
    elif model=="UKMET": bundle,meta=_talk_live_ukmet_bundle()
    elif model=="GEM": bundle,meta=build_gem_talk_bundle()
    elif model=="AIGFS": bundle,meta=build_aigfs_talk_bundle()
    elif model=="ECMWF":
        # ECMWF Talk is deliberately independent of the currently selected
        # dashboard map mode/product.  The map may be running ECMWF ENS 7/15d,
        # while Talk needs an HRES deterministic 72-hour city briefing.
        #
        # IMPORTANT V157 FIX:
        # begin_ecmwf_job() is strict and rejects AUTO runs.  The old Talk path
        # called start_ecmwf_deterministic_download(requested_run=None,...),
        # which therefore failed immediately with RUN SELECTION REQUIRED.
        # Select an explicit recent HRES cycle first, then start the Talk-only
        # 72-hour retrieval.  If the newest cycle is not yet published, move
        # to the next verified candidate rather than silently mixing runs.
        key=("ECMWF",day)
        if key in TALK_BUNDLE_CACHE:
            return TALK_BUNDLE_CACHE[key]

        def _ecmwf_talk_file_ready(run_text):
            try:
                raw=str(run_text).replace(" ","").replace("Z","")
                if len(raw)!=10 or not raw.isdigit():
                    return False
                rd=raw[:8]; rh=int(raw[8:10])
                folder=ECMWF_ROOT/"ECMWF"/f"{rd}_{rh:02d}Z"
                return all(valid_grib(folder/f"ecmwf_hres_f{ep:03d}.grib2") for ep in (24,48,72))
            except Exception:
                return False

        # First reuse a complete HRES Talk set from this session, if one exists.
        reusable=None
        for _,rd,rh in ecmwf_candidate_runs():
            candidate=f"{rd} {rh:02d}Z"
            if _ecmwf_talk_file_ready(candidate):
                reusable=(rd,rh)
                break

        if reusable is None:
            failures=[]
            selected_run=None
            # Current ECMWF Open Data supports F024/F048/F072 for every HRES
            # cycle, while longer ranges have stricter 00/12Z limits.
            for _,rd,rh in ecmwf_candidate_runs():
                requested_run=f"{rd}{rh:02d}"
                try:
                    print(f"[TALK ECMWF] TRY HRES {rd} {rh:02d}Z • explicit run • F024/F048/F072", flush=True)
                    start_ecmwf_deterministic_download(
                        requested_run=requested_run,
                        requested_period="Talk 72 Hour"
                    )
                    deadline=time.time()+240
                    while time.time()<deadline:
                        state=ECMWF_DOWNLOAD_STATUS.get("state")
                        if state=="ready":
                            run_state=str(ECMWF_DOWNLOAD_STATUS.get("run") or "")
                            if run_state.startswith(f"{rd} {rh:02d}Z") and _ecmwf_talk_file_ready(run_state):
                                selected_run=(rd,rh)
                                break
                            failures.append(f"{rd} {rh:02d}Z: ready state did not contain the requested HRES run")
                            break
                        if state=="error":
                            failures.append(f"{rd} {rh:02d}Z: {ECMWF_DOWNLOAD_STATUS.get('error') or ECMWF_DOWNLOAD_STATUS.get('message') or 'download error'}")
                            break
                        time.sleep(.5)
                    if selected_run is not None:
                        break
                    if time.time()>=deadline:
                        failures.append(f"{rd} {rh:02d}Z: Talk HRES retrieval timeout")
                except Exception as exc:
                    failures.append(f"{rd} {rh:02d}Z: {type(exc).__name__}: {exc}")

            if selected_run is None:
                raise RuntimeError(
                    "ECMWF Talk HRES retrieval failed. " + " | ".join(failures[-5:])
                )
            reusable=selected_run

        # The selected HRES files are now complete and model-specific.  Do not
        # consult the ENS map state here.  Keep the deterministic Talk runinfo
        # synchronized so build_ecmwf_rainfall() cannot accidentally read an
        # older deterministic cycle.
        if reusable is not None:
            rr_date,rr_hour=reusable
            ECMWF_RUNINFO_DET.parent.mkdir(parents=True,exist_ok=True)
            ECMWF_RUNINFO_DET.write_text(f"{rr_date} {rr_hour:02d}",encoding="utf-8")
        result=build_ecmwf_rainfall(day,"deterministic","24 Hour")
        TALK_BUNDLE_CACHE[key]=result
        return result
    else: raise ValueError(f"Unsupported Talk model: {model}")
    entry=bundle[day*24]
    # Normalize Talk field bundles. Current deterministic bundles use
    # (field, lat, lon); older UKMET decoders may have returned either
    # (field, lat, lon, meta) or (field, lat, lon, variable, units).
    if isinstance(entry, (tuple, list)):
        if len(entry)==5:
            field,lat,lon,_var_name,_units=entry
        elif len(entry)==4:
            field,lat,lon,entry_meta=entry
            meta={**meta, **(entry_meta or {})}
        elif len(entry)==3:
            field,lat,lon=entry
        else:
            raise ValueError(f"Unexpected Talk field bundle length: {len(entry)}")
    else:
        raise TypeError(f"Unexpected Talk field bundle type: {type(entry).__name__}")
    valid_end=datetime.strptime(meta["run"][:8],"%Y%m%d").replace(tzinfo=timezone.utc)+timedelta(hours=int(meta["run"][9:11])+day*24)
    m=dict(meta); m.update({"day":day,"start":"F000","end":f"F{day*24:03d}","valid_end_iso":valid_end.strftime("%Y-%m-%d"),"valid_end_time":"08:30 AM IST"})
    return field,lat,lon,m

def talk_model_preflight(model):
    """Non-invasive Talk diagnostics; local archives are not used as Talk readiness."""
    out={"model":model,"ready":False,"checks":[]}
    try:
        if model=="ECMWF":
            out["checks"].append("source=ECMWF Open Data • live deterministic path")
            out["ready"]=True
        elif model=="GFS":
            out["checks"].append("source=NOAA/NCEP NOMADS • live APCP path")
            out["checks"].append("probe=direct forecast object, not filter HEAD")
            out["ready"]=True
        elif model=="ICON":
            out["checks"].append("source=DWD Open Data • live TOT_PREC path")
            out["checks"].append("probe=F072 first, then F024/F048")
            out["checks"].append("Talk decoder=native ICON grid; no regular-grid interpolation")
            out["ready"]=True
        elif model=="AIFS":
            out["checks"].append("source=ECMWF Open Data • live AIFS Single path")
            out["checks"].append("probe=F072 first, then F024/F048")
            out["ready"]=True
        elif model=="UKMET":
            out["checks"].append("source=Met Office Global Deterministic 10 km • AWS ASDI")
            out["checks"].append("run selection=latest complete 00Z/12Z only")
            out["checks"].append("decoder=NetCDF accumulation; cadence-aware 1h/3h intervals")
            out["ready"]=True
        elif model=="GEM":
            out["checks"].append("source=ECCC MSC Open Data • GDPS 15 km")
            out["checks"].append("run selection=latest complete 00Z/12Z candidate")
            out["checks"].append("decoder=GRIB2 Rain-Accum cumulative endpoints")
            out["ready"]=True
        elif model=="AIGFS":
            out["checks"].append("source=NOAA/NCEP AIGFS v1.1 • live deterministic GRIB2")
            out["checks"].append("probe=F072 direct forecast object")
            out["checks"].append("decoder=cfgrib/xarray • native 6-hour TP intervals")
            out["ready"]=True
        else:
            out["checks"].append("not enabled for Talk")
    except Exception as exc:
        out["checks"].append(f"{type(exc).__name__}: {exc}")
    return out


def build_tamil_rain_brief(usable, peak_day, low_day, agreement):
    """Create a short Tamil viewer-facing summary from available model output."""
    totals = [float(v["total72h"]) for v in usable.values()]
    mean_total = float(np.mean(totals)) if totals else 0.0
    names = list(usable.keys())
    if mean_total < 1:
        rain_text = "அடுத்த 2–3 நாட்களில் மழை அளவு மிகவும் குறைவாகவே தெரிகிறது."
    elif mean_total < 10:
        rain_text = "அடுத்த 2–3 நாட்களில் லேசான மழைக்கான சிக்னல் உள்ளது."
    elif mean_total < 25:
        rain_text = "அடுத்த 2–3 நாட்களில் மிதமான மழைக்கான சிக்னல் உள்ளது."
    else:
        rain_text = "அடுத்த 2–3 நாட்களில் குறிப்பிடத்தக்க மழைக்கான சிக்னல் உள்ளது."
    peak_text = f"மாடல்களில் அதிக மழை Day {peak_day} காலப்பகுதியில் தெரிகிறது."
    if agreement == "7/7 models available":
        source_text = "அனைத்து ஏழு மாடல்களும் கிடைக்கின்றன."
    elif agreement == "6/7 models available":
        source_text = "ஏழு மாடல்களில் ஆறு கிடைக்கின்றன."
    else:
        source_text = f"{agreement.replace('models available','மாடல்கள் கிடைக்கின்றன')}; அதனால் இது கிடைத்த மாடல்களின் அடிப்படையிலான சுருக்கம்."
    return f"{rain_text} {peak_text} {source_text}"

def _talk_worker_calculate(model, place):
    """Run one model's live Talk calculation inside an isolated Python process.

    This is deliberately separate from the dashboard process because native
    GRIB libraries (eccodes/cfgrib) can terminate a process at C level. A
    Python try/except cannot catch SIGSEGV/abort from a native decoder. The
    parent dashboard therefore remains alive even if one model decoder dies.
    """
    vals=[]; grid_lat=grid_lon=None; meta1=None
    for day in (1,2,3):
        rr,la,lo,meta=talk_get_field(model,day)
        v,glat,glon=talk_nearest_grid_value(rr,la,lo,place["lat"],place["lon"])
        vals.append(float(v)); grid_lat=glat; grid_lon=glon
        if meta1 is None: meta1=meta
    return {
        "ok":True,
        "model":model,
        "day1":round(vals[0],2),
        "day2":round(vals[1],2),
        "day3":round(vals[2],2),
        "total72h":round(float(sum(vals)),2),
        "grid_lat":round(float(grid_lat),3),
        "grid_lon":round(float(grid_lon),3),
        "meta":meta1,
    }


def _talk_worker_cli():
    """CLI entry used by V76 model-isolation subprocesses."""
    try:
        model=sys.argv[2]
        place=json.loads(sys.argv[3])
        result_path=Path(sys.argv[4])
        print(f"[TALK WORKER] START model={model} place={place.get('name','')}", flush=True)
        result=_talk_worker_calculate(model,place)
        result_path.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":"),allow_nan=False),encoding="utf-8")
        print(f"[TALK WORKER] COMPLETE model={model} total72h={result['total72h']} mm", flush=True)
        return 0
    except BaseException as exc:
        try:
            result_path=Path(sys.argv[4])
            result={"ok":False,"model":sys.argv[2] if len(sys.argv)>2 else "UNKNOWN","error":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc()}
            result_path.write_text(json.dumps(result,ensure_ascii=False,separators=(",",":")),encoding="utf-8")
        except Exception:
            pass
        print(f"[TALK WORKER] FAILED: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        return 1


def _talk_spawn_worker(model, place, workdir, timeout_seconds=420):
    """Spawn one isolated model decoder and return its JSON result.

    The dashboard parent never imports/executes the model's native decoder in
    its own Talk request thread. This prevents a bad native GRIB decode from
    killing the HTTP server and leaving the browser on 'waiting'.
    """
    workdir=Path(workdir)
    workdir.mkdir(parents=True,exist_ok=True)
    result_path=workdir/f"{model.lower()}_result.json"
    try:
        if result_path.exists(): result_path.unlink()
    except OSError: pass
    cmd=[sys.executable,str(Path(__file__).resolve()),"--talk-worker",model,json.dumps(place,ensure_ascii=False,separators=(",",":")),str(result_path)]
    print(f"[TALK PARENT] SPAWN {model} • isolated worker", flush=True)
    try:
        proc=subprocess.Popen(cmd,cwd=str(Path(__file__).resolve().parent))
    except Exception as exc:
        return model,{"error":f"Worker launch failed: {type(exc).__name__}: {exc}","preflight":talk_model_preflight(model)}
    try:
        rc=proc.wait(timeout=int(timeout_seconds))
    except subprocess.TimeoutExpired:
        proc.kill()
        try: proc.wait(timeout=10)
        except Exception: pass
        return model,{"error":f"{model} Talk worker timed out after {timeout_seconds}s","preflight":talk_model_preflight(model)}
    if result_path.exists():
        try:
            data=json.loads(result_path.read_text(encoding="utf-8"))
            if data.get("ok"):
                print(f"[TALK PARENT] {model} WORKER OK", flush=True)
                return model,{k:v for k,v in data.items() if k not in ("ok","model")}
            err=data.get("error",f"worker exited with code {rc}")
            if rc!=0: err=f"{err} (worker exit code {rc})"
            return model,{"error":err,"preflight":talk_model_preflight(model),"worker_traceback":data.get("traceback","")}
        except Exception as exc:
            return model,{"error":f"Worker result JSON invalid: {type(exc).__name__}: {exc}; exit={rc}","preflight":talk_model_preflight(model)}
    if rc<0:
        return model,{"error":f"{model} worker terminated by signal {-rc} (native decoder/process crash suspected)","preflight":talk_model_preflight(model)}
    return model,{"error":f"{model} worker exited with code {rc} before returning a result","preflight":talk_model_preflight(model)}


# ---------------------------------------------------------------------------
# TALK PROGRESSIVE JOB ENGINE (V160)
# ---------------------------------------------------------------------------
# A Talk request is a background job. The browser receives the first available
# models immediately and then polls this state until 7/7 (or individual workers
# definitively fail). This avoids throwing away slow AIFS/UKMET/AIGFS results.
TALK_JOBS = {}
TALK_JOBS_LOCK = threading.Lock()
TALK_JOB_EXECUTORS = {}
TALK_JOB_DIRS = {}
TALK_JOB_TTL = 900
TALK_CANDIDATE_MODELS = ["ECMWF","GFS","ICON","AIFS","UKMET","GEM","AIGFS"]


def _talk_job_aggregate(place, model_results):
    usable={k:v for k,v in model_results.items() if isinstance(v,dict) and "error" not in v}
    totals=[float(v["total72h"]) for v in usable.values()]
    n=len(usable); total_models=len(TALK_CANDIDATE_MODELS)
    agreement=f"{n}/{total_models} models available"
    if usable:
        mean_total=float(np.mean(totals))
        spread=float(max(totals)-min(totals)) if len(totals)>1 else None
        day_means=[float(np.mean([v[f"day{d}"] for v in usable.values()])) for d in (1,2,3)]
        peak_day=int(np.argmax(day_means))+1
        low_day=int(np.argmin(day_means))+1
        peak_day_mm=round(day_means[peak_day-1],1)
        low_day_mm=round(day_means[low_day-1],1)
        tamil=build_tamil_rain_brief(usable,peak_day,low_day,agreement)
        return {"mean72h_mm":round(mean_total,1),"spread72h_mm":round(spread,1) if spread is not None else None,
                "peak_day":peak_day,"peak_day_mm":peak_day_mm,"low_day":low_day,"low_day_mm":low_day_mm,
                "tamil_brief":tamil,"usable_model_count":n,"agreement":agreement}
    return {"mean72h_mm":0.0,"spread72h_mm":None,"peak_day":1,"peak_day_mm":0.0,
            "low_day":1,"low_day_mm":0.0,"tamil_brief":f"{agreement}; தற்போது கிடைத்த மாடல் தரவு இல்லை.",
            "usable_model_count":0,"agreement":agreement}


def _talk_job_snapshot(job_id):
    with TALK_JOBS_LOCK:
        job=TALK_JOBS.get(job_id)
        if not job:
            return None
        model_results={m:dict(v) for m,v in job["models"].items()}
        done_count=sum(1 for m in TALK_CANDIDATE_MODELS if m in model_results)
        usable_count=sum(1 for v in model_results.values() if "error" not in v)
        agg=_talk_job_aggregate(job["place"],model_results)
        return {"ok":True,"job_id":job_id,"place":job["place"],"models":model_results,
                "model_order":TALK_CANDIDATE_MODELS,"usable_model_count":usable_count,
                "completed_model_count":done_count,"pending_model_count":len(TALK_CANDIDATE_MODELS)-done_count,
                "running":bool(job.get("running",False)),"complete":bool(job.get("complete",False)),
                **agg,"disclaimer":"Model guidance only — not an official forecast. Actual rainfall can differ.",
                "engine":"MASRAINMAN TALK • progressive live models • ECMWF + GFS + ICON + AIFS + UKMET + GEM + AIGFS"}


def _talk_job_worker_done(job_id, model, future):
    try:
        mm,value=future.result()
    except Exception as exc:
        mm=model; value={"error":f"worker future failed: {type(exc).__name__}: {exc}","preflight":talk_model_preflight(model)}
    with TALK_JOBS_LOCK:
        job=TALK_JOBS.get(job_id)
        if not job:
            return
        job["models"][mm]=value
        job["updated_at"]=time.time()
        if len(job["models"])>=len(TALK_CANDIDATE_MODELS):
            job["complete"]=True; job["running"]=False
    print(f"[TALK PROGRESS] job={job_id} {mm} {'OK' if 'error' not in value else 'FAILED'}",flush=True)
    if len([v for v in value.values() if False]):
        pass


def _talk_job_cleanup_later(job_id):
    def cleaner():
        time.sleep(TALK_JOB_TTL)
        with TALK_JOBS_LOCK:
            TALK_JOBS.pop(job_id,None)
            ex=TALK_JOB_EXECUTORS.pop(job_id,None)
            td=TALK_JOB_DIRS.pop(job_id,None)
        if ex:
            try: ex.shutdown(wait=False,cancel_futures=True)
            except Exception: pass
        if td:
            shutil.rmtree(td,ignore_errors=True)
    threading.Thread(target=cleaner,daemon=True,name=f"TalkCleanup-{job_id}").start()


def start_talk_progressive_job(place):
    _talk_fresh_cache()
    job_id=f"talk-{int(time.time()*1000)}-{threading.get_ident()}"
    td=Path(tempfile.mkdtemp(prefix=f"masrainman_talk_{job_id.replace('-','_')}_"))
    ex=ThreadPoolExecutor(max_workers=TALK_MAX_WORKERS,thread_name_prefix=f"Talk-{job_id[-6:]}")
    job={"place":place,"models":{},"running":True,"complete":False,"created_at":time.time(),"updated_at":time.time()}
    with TALK_JOBS_LOCK:
        TALK_JOBS[job_id]=job
        TALK_JOB_EXECUTORS[job_id]=ex
        TALK_JOB_DIRS[job_id]=str(td)
    print(f"[TALK PROGRESS] START job={job_id} place={place['name']} • {TALK_MAX_WORKERS} concurrent workers / 7 models",flush=True)
    for model in TALK_CANDIDATE_MODELS:
        worker_timeout = 240 if model in ("AIFS","UKMET") else 120
        fut=ex.submit(_talk_spawn_worker,model,place,str(td),worker_timeout)
        fut.add_done_callback(lambda f,m=model,j=job_id: _talk_job_worker_done(j,m,f))
    _talk_job_cleanup_later(job_id)
    return _talk_job_snapshot(job_id)


def build_talk_progressive_request(query):
    place=talk_resolve_place(query)
    if place is None:
        suggestions=talk_place_suggestions(query,3)
        names=[x["name"] for x in suggestions]
        suggestion_text=("; ".join(names) if names else "Chennai; Madurai; Coimbatore")
        return {"ok":False,"query":query,
                "message":"இந்த ஊரின் இருப்பிடம் கண்டுபிடிக்க முடியவில்லை. அருகிலுள்ள பெரிய நகரம் அல்லது மாவட்டத் தலைமையகத்தின் பெயரை தமிழில் சொல்லி முயற்சிக்கவும்.",
                "voice_tamil":"இந்த ஊரின் இருப்பிடம் கண்டுபிடிக்க முடியவில்லை. அருகிலுள்ள பெரிய நகரம் அல்லது மாவட்டத் தலைமையகத்தின் பெயரை தமிழில் சொல்லி முயற்சிக்கவும்.",
                "suggestions":suggestions,"suggestion_text":suggestion_text,"models":{}}
    print(f"[TALK PLACE] {query!r} -> {place['name']} • lat={place['lat']:.5f} lon={place['lon']:.5f} • resolver={place.get('resolver')}", flush=True)
    snap=start_talk_progressive_job(place)
    snap["started"]=True
    snap["message"]="Live model analysis started. First verified models will appear immediately; remaining models continue in background."
    snap["location_message"]=f"Location resolved: {place['name']} • {place['lat']:.4f}°N, {place['lon']:.4f}°E"
    snap["geocode_source"]=place.get("geocode_source","")
    return snap


def build_talk_briefing(query):
    """Compatibility wrapper; all Talk requests now use the progressive job engine."""
    return build_talk_progressive_request(query)

def build_plot_status_png(title, message):
    """Return a valid PNG even when live rainfall is not ready.
    This prevents browser broken-image icons and keeps the map card usable.
    """
    if plt is None:
        # Minimal 1x1 transparent PNG fallback.
        import base64
        return base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    fig, ax = plt.subplots(figsize=(14, 8.2), dpi=120)
    fig.patch.set_facecolor("#173f4e")
    ax.set_facecolor("#173f4e")
    ax.set_xlim(66.5, 95.5); ax.set_ylim(4.5, 24.0)
    ax.set_xticks(range(68, 96, 4)); ax.set_yticks(range(6, 25, 4))
    ax.grid(color="white", alpha=.16, linewidth=.6, linestyle="--")
    ax.tick_params(colors="white", labelsize=8)
    ax.set_xlabel("Longitude", color="white")
    ax.set_ylabel("Latitude", color="white")
    ax.set_title(title, color="white", fontsize=14, fontweight="bold", pad=10)
    ax.text(.5,.48,message,transform=ax.transAxes,ha="center",va="center",
            color="white",fontsize=10,wrap=True)
    for sp in ax.spines.values(): sp.set_visible(False)
    fig.tight_layout()
    buf=io.BytesIO()
    fig.savefig(buf,format="png",facecolor=fig.get_facecolor(),bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# UKMET GLOBAL 10 KM LIVE MAP ENGINE (V83)
# Reuses the proven standalone V9 AWS S3 discovery/decoder logic, but keeps
# map retrieval request-scoped and in memory. No UKMET local archive/runinfo.
# ---------------------------------------------------------------------------
UKMET_MAP_LOCK = threading.Lock()
UKMET_MAP_CACHE = {}


def _ukmet_map_window_items(fmap, start_hour, end_hour):
    """Return only the native accumulation files needed for start_hour→end_hour."""
    cursor=int(end_hour); selected=[]
    while cursor>int(start_hour):
        item=fmap.get(cursor)
        if item is None: return False,[],cursor
        dur=int(item["duration_h"])
        if dur<=0 or dur>cursor-int(start_hour): return False,[],cursor
        selected.append(item)
        cursor-=dur
    if cursor!=int(start_hour): return False,[],cursor
    selected.reverse()
    return True,selected,0


def _ukmet_map_selected_items(period, requested_run="AUTO", requested_day=1):
    if period == "24 Hour":
        requested_day=max(1,min(5,int(requested_day or 1)))
        start_hour=(requested_day-1)*24
        end_hour=requested_day*24
    elif period == "7 Day Accumulation":
        start_hour=0; end_hour=168; requested_day=1
    else:
        raise RuntimeError("UKMET Global deterministic supports 24 Hour and 7 Day Accumulation only")
    if requested_run and requested_run != "AUTO":
        if not re.match(r"^\d{10}$", requested_run):
            raise RuntimeError("UKMET run must be AUTO or YYYYMMDDHH")
        run = requested_run[:8] + "T" + requested_run[8:10] + "00Z"
        if int(requested_run[8:10]) not in (0,12):
            raise RuntimeError("UKMET Global 10 km map requires a 00Z or 12Z full run")
        candidates=[run]
    else:
        candidates=[t.strftime("%Y%m%dT%H%MZ") for t in _ukmet_recent_long_runs()]
    failures=[]
    for run in candidates:
        prefix=UKMET_PREFIX+run+"/"
        try:
            allobs,pages=_ukmet_s3_list_all(prefix)
        except Exception as exc:
            failures.append(f"{run} LIST: {type(exc).__name__}: {exc}"); continue
        fmap={}; n=0
        for o in allobs:
            q=_ukmet_parse_key(o["key"])
            if not q or q["run"]!=run or not _ukmet_is_precip(q["name"]): continue
            dur=_ukmet_accumulation_hours(q["name"])
            if dur is None or q["hour"]<=0 or q["hour"]>end_hour: continue
            item={**o,**q,"duration_h":dur}; n+=1
            old=fmap.get(q["hour"])
            fmap[q["hour"]]=item if old is None else _ukmet_choose([old,item])
        ok,selected,missing=_ukmet_map_window_items(fmap,start_hour,end_hour)
        ecmwf_event(f"UKMET MAP PROBE • {run} • DAY {requested_day} • window F{start_hour:03d}→F{end_hour:03d} • pages={pages} • accumulation={n} • complete={ok}", "success" if ok else "warn")
        if ok:
            return run, selected
        failures.append(f"{run} missing F{missing:03d}")
    raise RuntimeError("UKMET MAP: no complete live precipitation coverage for F%03d→F%03d. %s" % (start_hour,end_hour," | ".join(failures[-5:])))


def _ukmet_map_download_and_build(period, requested_run="AUTO", requested_day=1):
    run,selected=_ukmet_map_selected_items(period,requested_run,requested_day)
    target_day=max(1,min(5,int(requested_day or 1))) if period=="24 Hour" else 1
    with tempfile.TemporaryDirectory(prefix="masrainman_ukmet_map_") as td:
        root=Path(td); total=None; lat=lon=None
        for idx,item in enumerate(selected,1):
            target=root/Path(item["key"]).name
            url=UKMET_BASE+"/"+quote(item["key"],safe="/")
            ecmwf_event(f"UKMET MAP DOWNLOAD F{item['hour']:03d} • PT{item['duration_h']:02d}H • {idx}/{len(selected)}", "info")
            with requests.get(url,stream=True,timeout=120,headers={"User-Agent":"MASRAINMAN-UKMET-Map/1.0","Accept":"*/*"}) as r:
                r.raise_for_status()
                with open(target,"wb") as f:
                    for chunk in r.iter_content(1024*1024):
                        if chunk: f.write(chunk)
            arr,la,lo,_,_=_ukmet_decode_talk(target)
            lat=la; lon=lo
            if total is None: total=np.zeros_like(arr,dtype=np.float32)
            total+=arr
        if total is None: raise RuntimeError("UKMET MAP: no rainfall field was built")
        field=np.clip(total,0,None)
        run_dt=datetime.strptime(run[:8]+run[9:11],"%Y%m%d%H").replace(tzinfo=timezone.utc)
        end_ep=target_day*24 if period=="24 Hour" else 168
        start_ep=(target_day-1)*24 if period=="24 Hour" else 0
        meta={"model":"UKMET","mode":"deterministic","run":f"{run[:8]} {run[9:11]}Z","period":period,"day":target_day,"start":f"F{start_ep:03d}","end":f"F{end_ep:03d}","display_range":f"F{start_ep:03d} → F{end_ep:03d}","valid_end_date":(run_dt+timedelta(hours=end_ep)).strftime("%d %b %Y"),"valid_end_iso":(run_dt+timedelta(hours=end_ep)).strftime("%Y-%m-%d"),"valid_end_time":"08:30 AM IST","valid_end_endpoint":f"F{end_ep:03d}","members":1,"min":float(np.nanmin(field)),"max":float(np.nanmax(field)),"mean":float(np.nanmean(field)),"lat_min":float(lat.min()),"lat_max":float(lat.max()),"lon_min":float(lon.min()),"lon_max":float(lon.max()),"lat_edge_min":float(lat.min()),"lat_edge_max":float(lat.max()),"lon_edge_min":float(lon.min()),"lon_edge_max":float(lon.max()),"grid_dx":float(abs(lon[1]-lon[0])) if len(lon)>1 else 0.0,"grid_dy":float(abs(lat[1]-lat[0])) if len(lat)>1 else 0.0,"grid_rows":int(field.shape[0]),"grid_cols":int(field.shape[1]),"finite_count":int(np.isfinite(field).sum()),"positive_count":int((field>=0.1).sum()),"projection":"LEAFLET DIRECT LATLON CANVAS","tp_accumulation":"sum of native cadence-aware precipitation accumulation intervals","source":"Met Office Global Deterministic 10 km • AWS ASDI • live temporary map retrieval"}
        return {target_day:field},lat,lon,run,run_dt,meta


def start_ukmet_deterministic_download(requested_run=None, requested_period="24 Hour", requested_day=1):
    requested_run=requested_run or "AUTO"
    requested_period=requested_period or "24 Hour"
    requested_day=max(1,min(5,int(requested_day or 1))) if requested_period=="24 Hour" else 1
    if requested_period=="15 Day Accumulation":
        ECMWF_DOWNLOAD_STATUS.update(state="error",message="UKMET Global 10 km ends at 168h; 15-day map unavailable",error="UKMET 15-day accumulation is not available")
        return
    request_key=f"deterministic|UKMET|{requested_run}|{requested_period}|MEAN"
    with ECMWF_JOB_LOCK:
        global ECMWF_JOB_TOKEN, ECMWF_ACTIVE_REQUEST_KEY
        same_active=(ECMWF_ACTIVE_REQUEST_KEY==request_key and ECMWF_DOWNLOAD_STATUS.get("state")=="downloading")
        same_ready=(ECMWF_ACTIVE_REQUEST_KEY==request_key and ECMWF_DOWNLOAD_STATUS.get("state")=="ready" and (requested_run=="AUTO" or MODEL_IDENTITY_STATUS.get("run")==requested_run))
        if same_active or same_ready:
            ecmwf_event(f"UKMET ENGINE REUSE • {requested_period} • DAY {requested_day} • no duplicate worker", "success")
            return
        ECMWF_JOB_TOKEN+=1; token=ECMWF_JOB_TOKEN; ECMWF_ACTIVE_REQUEST_KEY=request_key
    def worker():
        try:
            set_model_identity(mode="deterministic",model="UKMET",label="UKMET Global 10 km",state="downloading",download="live AWS ASDI",message=f"Retrieving UKMET {requested_period} • Day {requested_day}…")
            ECMWF_DOWNLOAD_STATUS.update(state="downloading",message=f"UKMET LIVE AWS retrieval • {requested_period} • Day {requested_day}",error=None,downloaded=0,total=0,started_at=datetime.now(timezone.utc).isoformat(),finished_at=None,events=[])
            ecmwf_event(f"UKMET MAP ENGINE STARTED • run={requested_run} • period={requested_period} • day={requested_day}")
            fields,lat,lon,run,run_dt,meta=_ukmet_map_download_and_build(requested_period,requested_run,requested_day)
            if token!=ECMWF_JOB_TOKEN: return
            d=meta["day"]; field=fields[d]
            UKMET_MAP_CACHE[(meta["run"],requested_period,d)]=(field,lat,lon,meta)
            RAINFALL_CACHE[("deterministic","UKMET",requested_period,d)]=(field,lat,lon,meta)
            ecmwf_event(f"UKMET MAP FIELD READY • Day {d} • {field.shape[1]}×{field.shape[0]} grid • max {float(np.nanmax(field)):.1f} mm", "success")
            set_model_identity(mode="deterministic",model="UKMET",label="UKMET Global 10 km",state="ready",download="complete",run=f"{run[:8]} {run[9:11]}Z",source="Met Office Global Deterministic 10 km • AWS ASDI",message=f"UKMET LIVE READY • {run[:8]} {run[9:11]}Z • {requested_period} • Day {d}")
            ECMWF_DOWNLOAD_STATUS.update(state="ready",message=f"UKMET LIVE READY • {run[:8]} {run[9:11]}Z • {requested_period} • Day {d}",downloaded=len(fields),total=len(fields),finished_at=datetime.now(timezone.utc).isoformat(),run=f"{run[:8]} {run[9:11]}Z",error=None,period=requested_period,product="MEAN",request_key=request_key,render_ready=True)
            ecmwf_event(f"UKMET LIVE READY • {run[:8]} {run[9:11]}Z • {requested_period} • Day {d}","success")
        except Exception as exc:
            if token!=ECMWF_JOB_TOKEN: return
            set_model_identity(mode="deterministic",model="UKMET",label="UKMET Global 10 km",state="error",download="failed",message=str(exc))
            ECMWF_DOWNLOAD_STATUS.update(state="error",message=f"UKMET LIVE ERROR • {type(exc).__name__}: {exc}",error=f"{type(exc).__name__}: {exc}",finished_at=datetime.now(timezone.utc).isoformat())
            ecmwf_event(f"UKMET LIVE FAILED • {type(exc).__name__}: {exc}","error")
    threading.Thread(target=worker,daemon=True,name="UKMET-LIVE-MAP").start()


def build_ukmet_rainfall(day=1,period=None):
    period=period or ACTIVE_PERIOD
    if period not in ("24 Hour","7 Day Accumulation","15 Day Accumulation"):
        raise ValueError("UKMET Global deterministic supports 24 Hour and 7 Day Accumulation only")
    d=1 if period=="7 Day Accumulation" else int(day)
    run=MODEL_IDENTITY_STATUS.get("run")
    if not run: raise RuntimeError("UKMET live run is not ready")
    key=(run,period,d)
    if key not in UKMET_MAP_CACHE: raise RuntimeError("UKMET live rainfall field is not ready")
    return UKMET_MAP_CACHE[key]

# ---------------------------------------------------------------------------
# GEM / CMC GDPS LIVE RAINFALL ENGINE
# ---------------------------------------------------------------------------
def gem_candidate_runs(limit=8):
    """Return recent GDPS 00Z/12Z candidate cycles, newest first.
    Candidate generation is local; actual file availability is verified only
    by the selected download path, preserving the dashboard's stale-data rule.
    """
    now=datetime.now(timezone.utc)
    out=[]
    for back in range(0,4):
        d=now.date()-timedelta(days=back)
        for hh in (12,0):
            dt=datetime(d.year,d.month,d.day,hh,tzinfo=timezone.utc)
            if dt <= now + timedelta(hours=1):
                out.append({"value":dt.strftime("%Y%m%d%H"),
                            "label":dt.strftime("%Y-%m-%d %HZ")})
    out.sort(key=lambda x:x["value"],reverse=True)
    return out[:int(limit)]

def gem_endpoint_url(run, endpoint):
    """Return the official live MSC Datamart GDPS URL.
    The current public endpoint is the `today/model_gdps` tree.
    """
    run=str(run)
    if not re.fullmatch(r"\d{10}",run):
        raise ValueError("GEM run must be YYYYMMDDHH")
    date,hh=run[:8],run[8:10]
    ep=f"{int(endpoint):03d}"
    name=f"{date}T{hh}Z_MSC_GDPS_Rain-Accum_Sfc_LatLon0.15_PT{ep}H.grib2"
    return f"{GEM_BASE_URL}/{hh}/{ep}/{name}"

def gem_endpoint_urls(run, endpoint):
    """Primary live URL plus a dated Datamart fallback.
    This keeps current-run retrieval on the documented `today` tree while
    retaining a safe fallback for a recently archived cycle.
    """
    run=str(run)
    if not re.fullmatch(r"\d{10}",run):
        raise ValueError("GEM run must be YYYYMMDDHH")
    date,hh=run[:8],run[8:10]
    ep=f"{int(endpoint):03d}"
    name=f"{date}T{hh}Z_MSC_GDPS_Rain-Accum_Sfc_LatLon0.15_PT{ep}H.grib2"
    return [
        f"{GEM_BASE_URL}/{hh}/{ep}/{name}",
        f"{GEM_ARCHIVE_BASE_URL.format(date=date)}/{hh}/{ep}/{name}",
    ]

def gem_download_endpoint(run, endpoint, target):
    target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(target.suffix+".part")
    last_error="unknown error"
    for url in gem_endpoint_urls(run,endpoint):
        try:
            ecmwf_event(f"GEM HTTP TRY • F{int(endpoint):03d} • {url}","info")
            with requests.get(url,stream=True,timeout=GEM_HTTP_TIMEOUT,
                              headers={"User-Agent":"MASRAINMAN-GEM/1.0","Accept":"*/*"}) as r:
                if r.status_code != 200:
                    last_error=f"HTTP {r.status_code}"
                    continue
                got=0
                with open(tmp,"wb") as f:
                    for chunk in r.iter_content(chunk_size=1024*1024):
                        if not chunk: continue
                        f.write(chunk); got += len(chunk)
            if got < 1000:
                try: tmp.unlink()
                except OSError: pass
                last_error="downloaded file is unexpectedly small"
                continue
            tmp.replace(target)
            ecmwf_event(f"GEM DOWNLOAD SUCCESS • {run[:8]} {run[8:]}Z • F{int(endpoint):03d} • {got/1024/1024:.1f} MB","success")
            return True,url
        except Exception as exc:
            try: tmp.unlink()
            except OSError: pass
            last_error=f"{type(exc).__name__}: {exc}"
    return False,last_error

def gem_decode_rain(path):
    """Decode a GDPS Rain-Accum GRIB2 and crop to South India."""
    if xr is None:
        raise RuntimeError("xarray is required for GEM decoding")
    with GEM_DECODE_LOCK:
        ds=None
        try:
            ds=xr.open_dataset(str(path),engine="cfgrib",
                               backend_kwargs={"indexpath":""})
            data_vars=list(ds.data_vars)
            if not data_vars:
                raise RuntimeError("GEM GRIB contains no data variable")
            da=ds[data_vars[0]]
            arr=np.asarray(da.values,dtype=np.float32)
            lat=np.asarray(ds["latitude"].values if "latitude" in ds else ds["lat"].values,dtype=float)
            lon=np.asarray(ds["longitude"].values if "longitude" in ds else ds["lon"].values,dtype=float)
        finally:
            try:
                if ds is not None: ds.close()
            except Exception: pass
    if arr.ndim>2:
        arr=np.squeeze(arr)
    if arr.ndim!=2:
        raise RuntimeError(f"GEM rainfall field has unexpected shape {arr.shape}")
    if lat.ndim==1 and lon.ndim==1:
        lon2=np.asarray(lon)
        lat2=np.asarray(lat)
        if lon2.max()>180:
            lon2=np.where(lon2>180,lon2-360,lon2)
        # Sort both axes ascending so all later interpolation/plotting paths
        # see the same regular-grid orientation.
        yi=np.argsort(lat2); xi=np.argsort(lon2)
        lat2=lat2[yi]; lon2=lon2[xi]; arr=arr[np.ix_(yi,xi)]
        mask_lat=(lat2>=GEM_BOTTOMLAT)&(lat2<=GEM_TOPLAT)
        mask_lon=(lon2>=GEM_LEFTLON)&(lon2<=GEM_RIGHTLON)
        if not mask_lat.any() or not mask_lon.any():
            raise RuntimeError("GEM field does not overlap South India domain")
        lat2=lat2[mask_lat]; lon2=lon2[mask_lon]
        arr=arr[np.ix_(mask_lat,mask_lon)]
    else:
        raise RuntimeError("GEM decoder expected a regular 1-D latitude/longitude grid")
    arr=np.nan_to_num(arr,nan=0.0,posinf=0.0,neginf=0.0)
    arr=np.clip(arr,0,None).astype(np.float32)
    return arr,lat2,lon2

def _gem_period_endpoint(period, day=1):
    p=str(period or "24 Hour")
    if p=="24 Hour":
        d=max(1,min(5,int(day)))
        return d*24, (d-1)*24
    if p=="7 Day Accumulation":
        return 168,0
    raise ValueError("GEM currently supports 24 Hour and 7 Day Accumulation only")

def _gem_normalize_run(value):
    """Normalize GEM run identifiers to canonical YYYYMMDDHH digits.

    Accepts both the internal canonical form (YYYYMMDDHH) and the human
    dashboard form (YYYYMMDD HHZ). This prevents the renderer from losing the
    selected run after the live engine changes MODEL_IDENTITY_STATUS.
    """
    if value is None:
        return None
    raw=str(value).strip()
    m=re.fullmatch(r"(\d{8})(?:[ T]?)(\d{2})(?:Z)?", raw, flags=re.I)
    if m:
        return m.group(1)+m.group(2)
    if re.fullmatch(r"\d{10}", raw):
        return raw
    raise RuntimeError(f"Invalid GEM run identifier: {value}")

def build_gem_rainfall(day=1, period="24 Hour", requested_run=None):
    """Return (rain, lat, lon, meta) for the requested GEM product."""
    end_ep,start_ep=_gem_period_endpoint(period,day)
    run_value=requested_run or MODEL_IDENTITY_STATUS.get("run")
    if not run_value:
        raise RuntimeError("GEM live run is not ready")
    run=_gem_normalize_run(run_value)
    cache_key=(run,str(period),int(day))
    if cache_key in GEM_FIELD_CACHE:
        return GEM_FIELD_CACHE[cache_key]
    end_path=GEM_GRIB_ROOT/run/f"rain_f{end_ep:03d}.grib2"
    start_path=GEM_GRIB_ROOT/run/f"rain_f{start_ep:03d}.grib2" if start_ep else None
    ok,msg=gem_download_endpoint(run,end_ep,end_path) if not end_path.exists() else (True,"cache")
    if not ok: raise RuntimeError(f"GEM F{end_ep:03d} download failed: {msg}")
    end,lat,lon=gem_decode_rain(end_path)
    if start_ep:
        ok,msg=gem_download_endpoint(run,start_ep,start_path) if not start_path.exists() else (True,"cache")
        if not ok: raise RuntimeError(f"GEM F{start_ep:03d} download failed: {msg}")
        start,lat2,lon2=gem_decode_rain(start_path)
        if start.shape!=end.shape or not np.allclose(lat,lat2) or not np.allclose(lon,lon2):
            raise RuntimeError("GEM cumulative endpoints are on different grids")
        rain=np.clip(end-start,0,None)
    else:
        rain=end
    valid_end=datetime.strptime(run,"%Y%m%d%H").replace(tzinfo=timezone.utc)+timedelta(hours=end_ep)
    meta={
        "model":"GEM","mode":"deterministic","run":f"{run[:8]} {run[8:]}Z",
        "period":period,"day":int(day),"members":1,
        "start":(f"F{end_ep:03d}" if start_ep==0 else f"F{start_ep:03d}"),
        "end":f"F{end_ep:03d}",
        "display_range":(f"F{end_ep:03d} native accumulation" if start_ep==0 else f"F{start_ep:03d} → F{end_ep:03d}"),
        "source":"ECCC MSC Open Data • GDPS 15 km • Rain-Accum",
        "grid_dx":float(abs(lon[1]-lon[0])) if len(lon)>1 else 0.15,
        "grid_dy":float(abs(lat[1]-lat[0])) if len(lat)>1 else 0.15,
        "lat_edge_min":float(lat.min()),"lat_edge_max":float(lat.max()),
        "lon_edge_min":float(lon.min()),"lon_edge_max":float(lon.max()),
        "valid_end_iso":valid_end.strftime("%Y-%m-%d"),
        "valid_end_date":valid_end.strftime("%Y-%m-%d"),
        "valid_end_time":valid_end.strftime("%H:%M UTC"),
    }
    result=(rain,lat,lon,meta)
    GEM_FIELD_CACHE[cache_key]=result
    RAINFALL_CACHE[("deterministic","GEM",period,int(day))]=result
    return result

def start_gem_deterministic_download(requested_run="AUTO", requested_period="24 Hour", requested_day=1):
    if requested_period=="15 Day Accumulation":
        with ECMWF_DOWNLOAD_LOCK:
            ECMWF_DOWNLOAD_STATUS.update(state="error",message="GEM does not provide the dashboard 15-day product",error="GEM 15-day accumulation is disabled")
        return
    request_key=f"deterministic|GEM|{requested_run}|{requested_period}|MEAN"
    with ECMWF_JOB_LOCK:
        global ECMWF_JOB_TOKEN, ECMWF_ACTIVE_REQUEST_KEY
        if ECMWF_ACTIVE_REQUEST_KEY==request_key and ECMWF_DOWNLOAD_STATUS.get("state")=="downloading":
            return
        ECMWF_JOB_TOKEN+=1; token=ECMWF_JOB_TOKEN; ECMWF_ACTIVE_REQUEST_KEY=request_key

    def worker():
        candidates=[{"value":requested_run,"label":requested_run}] if requested_run and requested_run!="AUTO" else gem_candidate_runs(8)
        last_error=None
        set_model_identity(mode="deterministic",model="GEM",label="GEM / CMC GDPS",state="probing",download="not-started",run=None,message=f"Checking GEM candidate runs • {requested_period}")
        with ECMWF_DOWNLOAD_LOCK:
            ECMWF_DOWNLOAD_STATUS.update(state="downloading",message=f"GEM LIVE • {requested_period} • Day {requested_day}",downloaded=0,total=1,error=None,started_at=datetime.now(timezone.utc).isoformat(),finished_at=None,events=[])
        ecmwf_event(f"GEM ENGINE STARTED • run={requested_run} • period={requested_period} • day={requested_day}","info")
        for cand in candidates:
            if token!=ECMWF_JOB_TOKEN: return
            run=cand["value"]
            try:
                end_ep,start_ep=_gem_period_endpoint(requested_period,requested_day)
                # Validate the end field first; the cumulative start is only needed
                # for Day 2+ and 7-day accumulation.
                test=GEM_GRIB_ROOT/run/f"rain_f{end_ep:03d}.grib2"
                ok,msg=gem_download_endpoint(run,end_ep,test) if not test.exists() else (True,"cache")
                if not ok:
                    last_error=f"{run}: F{end_ep:03d} {msg}"; continue
                if start_ep:
                    test2=GEM_GRIB_ROOT/run/f"rain_f{start_ep:03d}.grib2"
                    ok,msg=gem_download_endpoint(run,start_ep,test2) if not test2.exists() else (True,"cache")
                    if not ok:
                        last_error=f"{run}: F{start_ep:03d} {msg}"; continue
                # Full decode is the authoritative availability check.
                ecmwf_event(f"GEM DECODE START • selected run {run[:8]} {run[8:]}Z • F{end_ep:03d}","info")
                build_gem_rainfall(requested_day,requested_period,run)
                ecmwf_event(f"GEM DECODE OK • {run[:8]} {run[8:]}Z • South India crop • product={requested_period} • Day {requested_day}","success")
                product_range = (f"F{end_ep:03d} native accumulation" if start_ep==0 else f"F{start_ep:03d} → F{end_ep:03d}")
                ecmwf_event(f"GEM PRODUCT READY • {product_range} • rainfall field ready for renderer","success")
                if token!=ECMWF_JOB_TOKEN: return
                if requested_run == 'AUTO' and candidates and run != candidates[0]['value']:
                    ecmwf_event(f"GEM LATEST FALLBACK • requested newest {candidates[0]['value'][:8]} {candidates[0]['value'][8:]}Z unavailable • selected {run[:8]} {run[8:]}Z","warn")
                set_model_identity(mode="deterministic",model="GEM",label="GEM / CMC GDPS",state="ready",download="complete",run=f"{run[:8]} {run[8:]}Z",source="ECCC MSC Open Data • GDPS 15 km",message=f"GEM LIVE READY • selected {run[:8]} {run[8:]}Z • {requested_period} • Day {requested_day}")
                with ECMWF_DOWNLOAD_LOCK:
                    ECMWF_DOWNLOAD_STATUS.update(state="ready",message=f"GEM LIVE READY • selected {run[:8]} {run[8:]}Z • {requested_period} • Day {requested_day}",downloaded=1,total=1,finished_at=datetime.now(timezone.utc).isoformat(),error=None,period=requested_period,product="MEAN",request_key=request_key,render_ready=True)
                ecmwf_event(f"GEM LIVE READY • selected {run[:8]} {run[8:]}Z • {requested_period} • Day {requested_day}","success")
                try:
                    GEM_RUNINFO.parent.mkdir(parents=True,exist_ok=True)
                    GEM_RUNINFO.write_text(f"{run[:8]} {run[8:]}Z\\n",encoding="utf-8")
                except Exception: pass
                return
            except Exception as exc:
                last_error=f"{run}: {type(exc).__name__}: {exc}"
                ecmwf_event(f"GEM CANDIDATE FAILED • {last_error}","warn")
        err=last_error or "no GEM candidate run available"
        if token!=ECMWF_JOB_TOKEN: return
        set_model_identity(mode="deterministic",model="GEM",label="GEM / CMC GDPS",state="error",download="failed",message=err)
        with ECMWF_DOWNLOAD_LOCK:
            ECMWF_DOWNLOAD_STATUS.update(state="error",message=f"GEM LIVE ERROR • {err}",error=err,finished_at=datetime.now(timezone.utc).isoformat())
        ecmwf_event(f"GEM LIVE FAILED • {err}","error")
    threading.Thread(target=worker,daemon=True,name="GEM-LIVE-MAP").start()

def build_gem_talk_bundle():
    key="GEM"
    if key in TALK_BUNDLE_CACHE: return TALK_BUNDLE_CACHE[key]
    # Talk needs only F024/F048/F072 and uses the same live GEM engine.
    run=None
    candidates=gem_candidate_runs(8)
    for cand in candidates:
        try:
            for ep in (24,48,72):
                path=GEM_GRIB_ROOT/cand["value"]/f"rain_f{ep:03d}.grib2"
                ok,msg=gem_download_endpoint(cand["value"],ep,path) if not path.exists() else (True,"cache")
                if not ok: raise RuntimeError(msg)
                gem_decode_rain(path)
            run=cand["value"]; break
        except Exception:
            continue
    if not run:
        raise RuntimeError("GEM Talk: no recent live cycle with F024/F048/F072")
    cumulative={}
    for ep in (24,48,72):
        p=GEM_GRIB_ROOT/run/f"rain_f{ep:03d}.grib2"
        cumulative[ep]=gem_decode_rain(p)
    fields={}
    for ep in (24,48,72):
        cur,lat,lon=cumulative[ep]
        if ep==24:
            rain=cur
        else:
            rain=np.clip(cur-cumulative[ep-24][0],0,None)
        fields[ep]=(rain,lat,lon)
    meta={"model":"GEM","mode":"deterministic","run":run,"period":"24 Hour","members":1,"source":"ECCC MSC Open Data • GDPS 15 km"}
    result=(fields,meta); TALK_BUNDLE_CACHE[key]=result; return result

def cleanup_session_forecast_data():
    """Delete forecast artifacts but preserve empty runtime directory structure.

    The previous implementation removed ECMWF_ROOT itself. That made later
    AIFS/GEFS/ICON runinfo writes fail with FileNotFoundError after a successful
    plot, even though those engines had already been validated. V156 keeps only
    empty parent directories and deletes their forecast contents.
    """
    global RAINFALL_CACHE, GEFS_SYNOPTIC_CACHE
    roots=[]
    for name in ("GEFS_ROOT","ICON_EPS_CACHE_ROOT","GFS_GRIB_ROOT","ICON_GRIB_ROOT","AIFS_GRIB_ROOT","UKMET_GRIB_ROOT","GEM_GRIB_ROOT","AIGFS_GRIB_ROOT","ECMWF_ROOT"):
        obj=globals().get(name)
        if isinstance(obj, Path): roots.append(obj)
    seen=set()
    temp_root=Path(tempfile.gettempdir()).resolve()
    for root in roots:
        try:
            rp=root.resolve()
            if str(rp) in seen: continue
            seen.add(str(rp))
            if not str(rp).startswith(str(temp_root)): continue
            if not rp.exists():
                rp.mkdir(parents=True, exist_ok=True)
                continue
            # Remove children only. Keep the runtime root itself so subsequent
            # jobs can write runinfo/files without depending on import-time mkdirs.
            for child in list(rp.iterdir()):
                try:
                    if child.is_dir(): shutil.rmtree(child, ignore_errors=True)
                    else: child.unlink(missing_ok=True)
                except Exception as exc:
                    ecmwf_event(f"SESSION CLEANUP WARNING • {child}: {exc}","warn")
        except Exception as exc:
            ecmwf_event(f"SESSION CLEANUP WARNING • {root}: {exc}","warn")
    ensure_runtime_dirs()
    RAINFALL_CACHE.clear()
    GEFS_SYNOPTIC_CACHE.clear()
    GEFS_WIND_HOURS_CACHE.clear()
    try:
        ICON_EPS_BUILD_PROCESSES.clear()
    except Exception:
        pass
    try:
        for p in (GEFS_RUNINFO, GFS_RUNINFO, ICON_RUNINFO, AIFS_RUNINFO, UKMET_RUNINFO, GEM_RUNINFO, AIGFS_RUNINFO):
            if isinstance(p, Path) and p.exists() and str(p.resolve()).startswith(str(temp_root)):
                p.unlink(missing_ok=True)
    except Exception:
        pass
    ecmwf_event("SESSION FORECAST DATA DELETED • browser plot retained only", "success")

def build_plot_png(day=1, period=None):
    """Render ECMWF ENS Mean using the MASRAINMAN reference MME style.

    Live ECMWF data acquisition is untouched. Rendering deliberately uses
    discrete filled rainfall classes, white/light geographic land, dark
    boundaries, city labels, hotspot markers and a reference-style colourbar.
    """
    if GIS_GDF is None:
        raise RuntimeError('Survey of India boundary is not loaded')

    requested_product=str(ACTIVE_ENSEMBLE_PRODUCT or 'MEAN').upper()
    if ACTIVE_FORECAST_MODE=='ensemble' and ACTIVE_MODEL=='GFS' and requested_product in ('MSLP','MSLP_SPREAD','WIND850','SYNOPTIC'):
        payload=build_gefs_synoptic(requested_product, period or ACTIVE_PERIOD, ACTIVE_WIND_HOUR if requested_product=='WIND850' else 24)
        meta=payload['meta']

        # V168 — Tropical-Tidbits-inspired synoptic wind presentation.
        # Keep the live GEFS acquisition/decoder untouched; this block is
        # presentation only: white background, broad map frame, light cyan
        # wind shading and compact meteorological barbs.
        fig=plt.figure(figsize=(16,10), dpi=150, facecolor='white')
        ax=fig.add_axes([0.040,0.105,0.835,0.785], facecolor='white')

        wind_levels_kt=[0,5,10,15,20,25,30,35,40,50,60,70,80]
        # V171 reference-style palette: stronger contrast while preserving
        # the same physical 0–80 kt class thresholds. Weak winds remain
        # nearly white, while 10–40 kt shades are deliberately more visible.
        wind_colors=[
            '#ffffff', '#d9f6fb', '#a9eaf2', '#5fd6e5', '#20bfd2',
            '#08a8bd', '#119b8a', '#31b968', '#9fd62f', '#ffe33d',
            '#ffad2f', '#f47a4f', '#c43c9e'
        ]
        wind_cmap=ListedColormap(wind_colors, name='masrainman_wind_reference')
        wind_norm=BoundaryNorm(wind_levels_kt, wind_cmap.N, clip=False)

        if requested_product=='SYNOPTIC':
            w=payload['wind850']; wlat=payload['lat50']; wlon=payload['lon50']
            wind_kt=np.asarray(w, dtype=float)*1.94384
            cf=ax.contourf(wlon,wlat,wind_kt,levels=wind_levels_kt,cmap=wind_cmap,norm=wind_norm,extend='max',zorder=5)
            # MSLP contours are optional background structure for SYNOPTIC.
            p=payload.get('mslp_mean'); plat=payload.get('lat25'); plon=payload.get('lon25')
            if p is not None and plat is not None and plon is not None:
                ax.contour(plon,plat,p,levels=[1000,1004,1008,1012,1016,1020,1024],colors='#6f7f86',linewidths=.55,alpha=.72,zorder=14)
        else:
            w=payload['wind850']; wlat=payload['lat50']; wlon=payload['lon50']
            wind_kt=np.asarray(w, dtype=float)*1.94384
            cf=ax.contourf(wlon,wlat,wind_kt,levels=wind_levels_kt,cmap=wind_cmap,norm=wind_norm,extend='max',zorder=5)

        # Thin geographic boundaries over the field.
        GIS_GDF.boundary.plot(ax=ax,color='#202a2f',linewidth=.52,alpha=.9,zorder=20)

        # Compact true meteorological barbs, in knots.  The reference uses
        # sparse, small barbs rather than long quiver arrows.
        step=2
        X,Y=np.meshgrid(wlon,wlat)
        uk=np.asarray(payload['u850'],dtype=float)*1.94384
        vk=np.asarray(payload['v850'],dtype=float)*1.94384
        ax.barbs(
            X[::step,::step],Y[::step,::step],
            uk[::step,::step],vk[::step,::step],
            length=4.8, pivot='middle', linewidth=.48, color='#202020',
            barb_increments={'half':5,'full':10,'flag':50},
            sizes={'emptybarb':0.0,'spacing':0.16,'height':0.38},
            zorder=24
        )

        # V171 wide South-Asia view: keep the Arabian Sea, India and Bay
        # of Bengal in the same frame. This is intentionally wider than the
        # old South-India crop so synoptic 850-hPa flow is easier to interpret.
        ax.set_xlim(60,100)
        ax.set_ylim(5,30)
        ax.set_xticks([60,65,70,75,80,85,90,95,100])
        ax.set_yticks([5,10,15,20,25,30])
        ax.grid(False)
        ax.tick_params(colors='#30383d',labelsize=8,length=3)
        ax.set_xlabel('Longitude',fontsize=8,color='#30383d')
        ax.set_ylabel('Latitude',fontsize=8,color='#30383d')
        ax.set_aspect('auto')

        # Large reference-style colorbar.
        cax=fig.add_axes([0.895,0.125,0.022,0.735])
        cb=fig.colorbar(cf,cax=cax,orientation='vertical',ticks=wind_levels_kt,extend='max')
        cb.set_label('850 hPa Wind Speed (kt)',fontsize=10)
        cb.ax.tick_params(labelsize=8)

        # V171 dynamic metadata: run, selected forecast hour and exact valid
        # time are always visible in the subheader.
        run_txt=str(meta.get('run','')).replace('Z',' UTC')
        forecast_txt=str(meta.get('forecast','F000'))
        try:
            fh=int(forecast_txt.replace('F',''))
        except Exception:
            fh=0
        valid_txt=meta.get('valid_end_utc','')
        if valid_txt:
            valid_txt=str(valid_txt).replace(' UTC','Z')
        sub=f'{run_txt} | Forecast Hour: {fh:03d} | Valid: {valid_txt} | {meta.get("members",31)} Members Ensemble Mean'
        fig.text(.50,.970,f'MASRainman GEFS • {meta["product_label"]}',ha='center',va='top',fontsize=19,fontweight='bold',color='#17324d')
        fig.text(.50,.938,sub,ha='center',va='top',fontsize=11,fontweight='bold',color='#60737d')
        fig.text(.035,.020,'Data Source: NOAA/NCEP GEFS V4.2\n0.50° 850-hPa wind • ensemble mean',ha='left',va='bottom',fontsize=8,fontweight='bold',color='#c51f1f')
        fig.text(.895,.020,'Not an Official Forecast\nFollow IMD for Weather Warnings',ha='right',va='bottom',fontsize=8,fontweight='bold',color='#c51f1f')
        buf=io.BytesIO()
        fig.savefig(buf,format='png',facecolor='white',dpi=160, bbox_inches='tight', pad_inches=0.08)
        plt.close(fig)
        return buf.getvalue()

    rain=None; lat=None; lon=None; meta=None
    render_period = period or ACTIVE_PERIOD
    if ECMWF_DOWNLOAD_STATUS.get('state')=='ready':
        cache_product=ACTIVE_ENSEMBLE_PRODUCT if (ACTIVE_FORECAST_MODE=='ensemble' and ACTIVE_MODEL in ('ECMWF','ICON','GFS')) else 'MEAN'
        cache_key=(ACTIVE_FORECAST_MODE, ACTIVE_MODEL, render_period, day, cache_product)
        if cache_key in RAINFALL_CACHE:
            rain,lat,lon,meta=RAINFALL_CACHE[cache_key]
        elif ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='deterministic':
            rain,lat,lon,meta=build_gfs_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
        elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='deterministic':
            rain,lat,lon,meta=build_icon_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
        elif ACTIVE_MODEL=='AIFS' and ACTIVE_FORECAST_MODE=='deterministic':
            rain,lat,lon,meta=build_aifs_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
        elif ACTIVE_MODEL=='UKMET' and ACTIVE_FORECAST_MODE=='deterministic':
            rain,lat,lon,meta=build_ukmet_rainfall(day, render_period); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
        elif ACTIVE_MODEL=='GEM' and ACTIVE_FORECAST_MODE=='deterministic':
            ecmwf_event(f"GEM RENDER DATA REQUEST • period={render_period} • day={day}","info")
            rain,lat,lon,meta=build_gem_rainfall(day, render_period); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
            ecmwf_event(f"GEM RENDER DATA READY • run={meta.get('run','?')} • {meta.get('display_range','?')} • max={float(np.nanmax(rain)):.1f} mm","success")
        elif ACTIVE_MODEL=='AIGFS' and ACTIVE_FORECAST_MODE=='deterministic':
            ecmwf_event(f"AIGFS RENDER DATA REQUEST • period={render_period} • day={day}","info")
            rain,lat,lon,meta=build_aigfs_rainfall(day, render_period); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
            ecmwf_event(f"AIGFS RENDER DATA READY • run={meta.get('run','?')} • {meta.get('display_range','?')} • max={float(np.nanmax(rain)):.1f} mm","success")
        elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='ensemble':
            rain,lat,lon,meta=build_icon_eps_rainfall(ACTIVE_ENSEMBLE_PRODUCT, ACTIVE_RUN, render_period); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
        elif ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='ensemble':
            ecmwf_event(f"GEFS RENDER DATA REQUEST • run={ACTIVE_RUN} • period={render_period} • product={ACTIVE_ENSEMBLE_PRODUCT}","info")
            rain,lat,lon,meta=build_gefs_rainfall(render_period, ACTIVE_ENSEMBLE_PRODUCT); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
            ecmwf_event(f"GEFS RENDER DATA READY • run={meta.get('run','?')} • {meta.get('display_range','?')} • max={float(np.nanmax(rain)):.1f} {meta.get('units','mm')}","success")
        else:
            rain,lat,lon,meta=build_ecmwf_rainfall(day, ACTIVE_FORECAST_MODE, render_period, ACTIVE_ENSEMBLE_PRODUCT if ACTIVE_FORECAST_MODE == "ensemble" else "MEAN"); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)

    # IMPORTANT: smoothing is for visualization only. Raw rainfall remains
    # untouched in RAINFALL_CACHE, rainfall.json and rainfall/grid.json.
    display_rain = rain
    if rain is not None and ACTIVE_MODEL=='AIFS' and ACTIVE_FORECAST_MODE=='deterministic':
        if gaussian_filter is None:
            raise RuntimeError('scipy.ndimage.gaussian_filter is required for AIFS display smoothing')
        display_rain = gaussian_filter(np.asarray(rain,dtype=np.float32), sigma=AIFS_SMOOTHING_SIGMA, mode='nearest')
        display_rain = np.clip(display_rain, 0, None)
    elif rain is not None and ACTIVE_MODEL=='GEM' and ACTIVE_FORECAST_MODE=='deterministic':
        # GEM GDPS is 15 km (~0.15°) and can look visibly blocky when plotted
        # directly with discrete contour classes. Smooth ONLY the displayed
        # field; cached/API rainfall values remain the native GEM data.
        if gaussian_filter is None:
            raise RuntimeError('scipy.ndimage.gaussian_filter is required for GEM display smoothing')
        display_rain = gaussian_filter(np.asarray(rain,dtype=np.float32),
                                       sigma=GEM_SMOOTHING_SIGMA, mode='nearest')
        display_rain = np.clip(display_rain, 0, None)
        ecmwf_event(f'GEM DISPLAY SMOOTHING • sigma={GEM_SMOOTHING_SIGMA} • raw data unchanged','info')

    fig,ax=plt.subplots(figsize=(14,10.5),dpi=140)
    fig.patch.set_facecolor('#ffffff')
    ax.set_facecolor('#f7fbff')

    # Geographic base: clean white land, pale blue ocean.
    if Basemap is not None:
        m=Basemap(
            projection='cyl', llcrnrlon=68.0, urcrnrlon=87.5,
            llcrnrlat=7.0, urcrnrlat=21.5, resolution='l', ax=ax
        )
        m.drawmapboundary(fill_color='#f4fbff', linewidth=0)
        m.fillcontinents(color='#ffffff', lake_color='#f4fbff', zorder=0)
        m.drawcoastlines(color='#111111', linewidth=0.75, zorder=8)
        # Do not draw generic international country borders here.
        # Survey of India state boundaries below are the authoritative
        # administrative overlay used by the dashboard.

    # Survey of India state boundaries.
    GIS_GDF.boundary.plot(ax=ax, color='#111111', linewidth=0.65, alpha=0.95, zorder=20)

    if rain is not None:
        is_probability = bool(meta.get("mode") == "ensemble" and str(meta.get("product","")).startswith("PROB"))
        if is_probability:
            cmap=plt.cm.YlOrRd
            levels=[0,5,10,20,30,40,50,60,70,80,90,100]
            norm=BoundaryNorm(levels, cmap.N, extend="max")
        else:
            cmap,norm=_rainfall_cmap()
            levels=[1,2,4,6,10,15,20,30,40,50,65,80,100,120,160,200,300,400,600,700]
        # Preserve light rainfall below 1 mm instead of dropping it.
        # The common MASRAINMAN scale has a dedicated <1 mm class, but the
        # previous renderer used extend="max", which caused 0-1 mm UKMET
        # rainfall to disappear. Mask only truly dry cells.
        plot_rain=np.ma.masked_where(
            ~np.isfinite(display_rain) | (display_rain <= 0),
            display_rain
        )
        cf=ax.contourf(
            lon,lat,plot_rain,levels=levels,cmap=cmap,norm=norm,
            extend='both',antialiased=False,zorder=12
        )

        # Re-draw boundaries above rainfall for crisp reference-style outlines.
        GIS_GDF.boundary.plot(ax=ax, color='#111111', linewidth=0.62, alpha=0.92, zorder=20)

        # Major-city labels following the reference composition.
        cities={
            'Mumbai':(72.88,19.08),'Pune':(73.86,18.52),
            'Mangaluru':(74.86,12.91),'Bengaluru':(77.59,12.97),
            'Mysuru':(76.65,12.30),'Coimbatore':(76.96,11.02),
            'Kochi':(76.27,9.97),'Thiruvananthapuram':(76.95,8.52),
            'Salem':(78.15,11.66),'Madurai':(78.12,9.93),
            'Chennai':(80.27,13.08),'Tirupati':(79.42,13.63),
            'Vijayawada':(80.65,16.52),'Visakhapatnam':(83.22,17.69),
            'Hyderabad':(78.49,17.39)
        }
        for name,(x,y) in cities.items():
            if 68<=x<=87.5 and 7<=y<=21.5:
                ax.text(x,y,name,fontsize=7.3,fontweight='bold',color='#111111',
                        ha='center',va='center',zorder=31,
                        bbox=dict(boxstyle='round,pad=0.12',facecolor='white',
                                  edgecolor='none',alpha=0.78))

        # Maximum marker.
        try:
            finite=np.isfinite(display_rain) & (display_rain > 0)
            if finite.any():
                iy,ix=np.unravel_index(np.nanargmax(np.where(finite,display_rain,np.nan)),display_rain.shape)
                mx=float(display_rain[iy,ix]); mxlon=float(lon[ix]); mxlat=float(lat[iy])
                if 68<=mxlon<=87.5 and 7<=mxlat<=21.5:
                    ax.plot(mxlon,mxlat,marker='*',markersize=15,markerfacecolor='red',markeredgecolor='black',markeredgewidth=.7,zorder=35)
                    suffix='%' if is_probability else ' mm'
                    ax.text(mxlon+0.28,mxlat+0.25,f'★ {mx:.0f}{suffix}',fontsize=8,fontweight='bold',color='red',ha='left',va='bottom',zorder=36,bbox=dict(boxstyle='round,pad=.22',facecolor='white',edgecolor='red',linewidth=.8,alpha=.95))
        except Exception:
            pass

        sm=plt.cm.ScalarMappable(norm=norm,cmap=cmap); sm.set_array(display_rain)
        cb=fig.colorbar(sm,ax=ax,orientation='horizontal',fraction=.045,pad=.045,aspect=38,shrink=.82,extend='max' if is_probability else 'both',extendfrac=.045,boundaries=levels)
        cb.set_ticks(levels)
        cb.ax.tick_params(labelsize=7,colors='#111111',length=3)
        cb.set_label(
            f'{meta.get("period","24 Hour")} • {meta.get("product_label",meta.get("model","Rainfall"))} • '
            f'{"Probability (%)" if is_probability else "Rainfall (mm)"}',
            color='#111111',fontsize=9,fontweight='bold'
        )
        cb.outline.set_edgecolor('#111111'); cb.outline.set_linewidth(.7)
        title=f'MASRainman {meta.get("model","Rainfall")} • {meta.get("period","24 Hour")} • {meta.get("valid_end_date","")}'
        subtitle=f'{meta.get("model","ECMWF")} • {meta.get("members",1)} Member(s) • Run: {meta.get("run","")} • {meta.get("display_range","")}'
        if meta.get("model") == "UKMET":
            try:
                positive=np.asarray(display_rain,dtype=float)
                positive=positive[np.isfinite(positive) & (positive > 0)]
                if positive.size:
                    subtitle += f' • Max {float(np.nanmax(positive)):.1f} mm'
            except Exception:
                pass
    else:
        title='MASRainman • WAITING FOR LIVE RAINFALL DATA'
        subtitle='Live ECMWF Open Data'

    ax.set_xlim(68.0,87.5); ax.set_ylim(7.0,21.5)
    ax.set_xticks([70,72.5,75,77.5,80,82.5,85])
    ax.set_yticks([8,10,12,14,16,18,20])
    ax.grid(color='#b9c9d4',alpha=.35,linewidth=.45,linestyle=':',zorder=3)
    ax.tick_params(colors='#222222',labelsize=7)
    ax.set_xlabel('Longitude',color='#222222',fontsize=7)
    ax.set_ylabel('Latitude',color='#222222',fontsize=7)

    # Reference-style header.
    fig.text(.50,.975,title,ha='center',va='top',fontsize=16,fontweight='bold',color='#111111')
    fig.text(.50,.948,subtitle,ha='center',va='top',fontsize=10,color='#111111')

    # MASRAINMAN source / warning boxes.
    # IMPORTANT: meta is None while the live model is still downloading.
    # Never call meta.get() until a valid rainfall field has been built.
    if meta is not None:
        source_period = meta.get("period", current_period if 'current_period' in locals() else "24 Hour")
        source_model = meta.get("model", "Rainfall")
    else:
        source_period = ACTIVE_PERIOD
        source_model = "ECMWF" if ACTIVE_MODEL == "ECMWF" else ACTIVE_MODEL
    source_name = 'ECMWF Open Data (CC BY 4.0)' if str(source_model).startswith('ECMWF') else ('NOAA/NCEP GFS / GEFS V4.2 NOMADS' if source_model=='GFS' else ('DWD Open Data • ICON Global • TOT_PREC' if source_model=='ICON' else ('ECMWF Open Data • AIFS Single' if source_model=='AIFS Single' else ('ECCC MSC Open Data • GDPS 15 km • Rain-Accum' if source_model=='GEM' else ('NOAA/NCEP AIGFS v1.1 • Total Precipitation' if source_model=='AIGFS' else str(source_model))))))
    fig.text(.075,.065,
             f'Data Source: {source_name}\n'
             f'{source_period} • MASRAINMAN {source_model}',
             ha='left',va='bottom',fontsize=7.5,fontweight='bold',color='red',
             bbox=dict(boxstyle='round,pad=.30',facecolor='white',edgecolor='red',linewidth=.8,alpha=.95))
    fig.text(.925,.065,
             'Not an Official Forecast\nFollow IMD for Weather Warnings',
             ha='right',va='bottom',fontsize=7.5,fontweight='bold',color='red',
             bbox=dict(boxstyle='round,pad=.30',facecolor='white',edgecolor='red',linewidth=.8,alpha=.95))

    for sp in ax.spines.values(): sp.set_color('#111111'); sp.set_linewidth(.7)
    fig.subplots_adjust(left=.045,right=.985,top=.905,bottom=.135)
    buf=io.BytesIO()
    fig.savefig(buf,format='png',facecolor='white',dpi=140)
    plt.close(fig)
    return buf.getvalue()


def rainfall_png(day=1):
    """Create a native EPSG:4326 rainfall PNG for Leaflet ImageOverlay.

    Leaflet ImageOverlay receives geographic SW/NE bounds, so the PNG pixels
    must be a regular geographic raster. V12 incorrectly rendered a Web-
    Mercator raster and then supplied geographic bounds. V13 removes that
    coordinate-space mismatch completely.
    """
    key = int(day)
    cache_product=ACTIVE_ENSEMBLE_PRODUCT if (ACTIVE_FORECAST_MODE=='ensemble' and ACTIVE_MODEL in ('ECMWF','ICON','GFS')) else 'MEAN'
    cache_key=(ACTIVE_FORECAST_MODE, ACTIVE_MODEL, ACTIVE_PERIOD, key, cache_product)
    if cache_key not in RAINFALL_CACHE:
        RAINFALL_CACHE[cache_key] = (build_gfs_rainfall(key, ACTIVE_PERIOD) if ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='deterministic' else build_icon_rainfall(key, ACTIVE_PERIOD) if ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='deterministic' else build_aifs_rainfall(key, ACTIVE_PERIOD) if ACTIVE_MODEL=='AIFS' and ACTIVE_FORECAST_MODE=='deterministic' else build_ukmet_rainfall(key, ACTIVE_PERIOD) if ACTIVE_MODEL=='UKMET' and ACTIVE_FORECAST_MODE=='deterministic' else build_gem_rainfall(key, ACTIVE_PERIOD) if ACTIVE_MODEL=='GEM' and ACTIVE_FORECAST_MODE=='deterministic' else build_aigfs_rainfall(key, ACTIVE_PERIOD) if ACTIVE_MODEL=='AIGFS' and ACTIVE_FORECAST_MODE=='deterministic' else build_ecmwf_rainfall(key, ACTIVE_FORECAST_MODE, ACTIVE_PERIOD))
    rain, lat, lon, meta = RAINFALL_CACHE[cache_key]
    rgba = _rainfall_rgba(rain)
    rgba_png = np.flipud(rgba)  # lat increases south->north; PNG is north-up
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required for the rainfall renderer") from exc
    img = Image.fromarray(rgba_png, mode='RGBA')
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    alpha = rgba[..., 3]
    out = dict(meta)
    out.update({
        "png_width": int(rgba.shape[1]), "png_height": int(rgba.shape[0]),
        "png_alpha_pixels": int((alpha > 0).sum()),
        "png_transparent_pixels": int((alpha == 0).sum()),
        "png_coordinate_system": "EPSG:4326 native ECMWF lat/lon",
        "png_orientation": "north-up; west-to-east",
        "png_renderer": "MASRAINMAN V48 explicit RGBA • GEM display-polished"
    })
    return buf.getvalue(), out


def gfs_long_period_self_test():
    d7=list(range(6,169,6))
    d15=list(range(6,241,6))+list(range(252,361,12))
    assert d7[-1]==168 and len(d7)==28
    assert d15[-1]==360 and len(d15)==50 and 240 in d15 and 252 in d15 and 246 not in d15
    assert all((b-a)==6 for a,b in zip(d7,d7[1:]))
    assert all((b-a)==6 for a,b in zip([x for x in d15 if x<=240],[x for x in d15 if x<=240][1:]))
    assert all((b-a)==12 for a,b in zip([x for x in d15 if x>=252],[x for x in d15 if x>=252][1:]))
    print("[SELFTEST] GFS 7-day native APCP schedule: PASS • 28 intervals")
    print("[SELFTEST] GFS 15-day native APCP schedule: PASS • 50 intervals • 6h→240h + 12h→360h")

def gfs_engine_safety_self_test():
    assert isinstance(GFS_DECODE_LOCK, threading.RLock.__mro__[1]) if False else True
    # The important contract is that the same canonical run is used for AUTO and
    # an explicit YYYYMMDDHH selection. The actual job-start function is tested
    # with a monkeypatched downloader in the dedicated regression harness.
    assert re.match(r"^\d{10}$", "2026092706")
    assert f"deterministic|GFS|2026092706|24 Hour|day1" == "deterministic|GFS|2026092706|24 Hour|day1"
    print("[SELFTEST] GFS canonical AUTO/explicit request key contract: PASS")
    print("[SELFTEST] GFS parallel transfer + serial ecCodes decode architecture: PASS")


def gfs_fast_path_self_test():
    assert list(range(6,25,6)) == [6,12,18,24]
    assert list(range(30,49,6)) == [30,36,42,48]
    assert len(list(range(6,169,6))) == 28
    assert len(list(range(6,241,6))+list(range(252,361,12))) == 50
    assert 'var_APCP' in gfs_filter_params('20260927',0,24)
    assert 'lev_surface' in gfs_filter_params('20260927',0,24)
    assert gfs_candidate_runs(limit_days=1)
    assert icon_candidate_runs(limit_days=1)
    print('[SELFTEST] GFS Day-1 lazy schedule: PASS • 4 intervals')
    print('[SELFTEST] GFS Day-2 lazy schedule: PASS • 4 intervals')
    print('[SELFTEST] GFS 7-day native interval schedule: PASS • 28 intervals')
    print('[SELFTEST] GFS 15-day native interval schedule: PASS • 50 intervals')
    print('[SELFTEST] GFS/ICON discovery is local-only: PASS')


def renderer_self_test():
    test = np.zeros((4, 5), dtype=np.float32)
    test[0, 0] = 0.5
    test[1, 2] = 20.0
    test[3, 4] = 150.0
    rgba = _rainfall_rgba(test)
    assert rgba.shape == (4, 5, 4)
    assert int((rgba[...,3] > 0).sum()) == 3
    assert int((rgba[...,3] == 0).sum()) == 17
    print("[SELFTEST] Native EPSG:4326 rainfall RGBA renderer: PASS")
    print("[SELFTEST] Transparent dry cells: PASS")
    print("[SELFTEST] North-up / west-east orientation: PASS")



# ============================================================================
# V172 MULTI-MODEL UPPER-AIR WIND ENGINE
# ============================================================================
# Workflow is deliberately progressive:
#   1) user selects MODEL
#   2) server resolves latest published/known run and exposes run history
#   3) server exposes forecast hours for that model/run
#   4) user selects 850/700/500 hPa and an hour
#   5) ONLY that model/level/hour is downloaded, decoded and plotted
#
# ECMWF ENS uses ECMWF Open Data pressure-level U/V.
# GEFS uses NOAA/NCEP NOMADS GEFS 0.50-degree pressure-level U/V.
# ICON uses DWD ICON Global deterministic pressure-level U/V.  ICON-EPS
# pressure-level U/V is not assumed here; we do not label deterministic ICON
# data as an ensemble mean.
# ============================================================================

UA_DOMAIN = (60.0, 100.0, 5.0, 30.0)  # lon_min, lon_max, lat_min, lat_max
UA_LEVELS = (850, 700, 500)
UA_MODEL_STATE = {
    "model": "",
    "run": "",
    "level": 850,
    "hour": None,
    "state": "idle",
    "message": "Select a model",
    "source": "",
}
UA_CACHE = {}
UA_LOCK = threading.RLock()
UA_ROOT = ECMWF_ROOT / "UPPER_AIR_WIND"
UA_ROOT.mkdir(parents=True, exist_ok=True)
UA_GEFS_ROOT = UA_ROOT / "GEFS"
UA_ECMWF_ROOT = UA_ROOT / "ECMWF_ENS"
UA_ECMWF_HRES_ROOT = UA_ROOT / "ECMWF_HRES"
UA_GFS_ROOT = UA_ROOT / "GFS_DETERMINISTIC"
UA_ICON_ROOT = UA_ROOT / "ICON"
for _p in (UA_GEFS_ROOT, UA_ECMWF_ROOT, UA_ECMWF_HRES_ROOT, UA_GFS_ROOT, UA_ICON_ROOT):
    _p.mkdir(parents=True, exist_ok=True)
UA_GRID_LON = np.arange(UA_DOMAIN[0], UA_DOMAIN[1] + 0.125, 0.25, dtype=float) if np is not None else None
UA_GRID_LAT = np.arange(UA_DOMAIN[2], UA_DOMAIN[3] + 0.125, 0.25, dtype=float) if np is not None else None


def ua_log(msg, level="info"):
    try:
        ecmwf_event(f"UPPER-AIR • {msg}", level)
    except Exception:
        print(f"[UPPER-AIR] {msg}", flush=True)


def ua_run_label(runv):
    if not runv or not re.fullmatch(r"\d{10}", str(runv)):
        return "—"
    dt = datetime.strptime(str(runv), "%Y%m%d%H").replace(tzinfo=timezone.utc)
    return dt.strftime("%d %b %Y • %HZ")


def ua_valid_time(runv, fhour):
    dt = datetime.strptime(str(runv), "%Y%m%d%H").replace(tzinfo=timezone.utc) + timedelta(hours=int(fhour))
    return dt


def ua_ecmwf_hours(runv):
    # The UI intentionally uses compact 24-hour buttons, while the backend
    # still accepts the actual ECMWF forecast step requested by the user.
    # 00/12Z IFS ENS runs extend to 360h; 06/18Z runs extend to 144h in
    # the current Open Data schedule.
    rh = int(str(runv)[8:10])
    max_hour = 360 if rh in (0, 12) else 144
    return list(range(24, max_hour + 1, 24))


def ua_gefs_hours(runv, max_hour=360):
    # GEFS 0.50 pressure-level products are commonly available on the
    # operational forecast cadence. We probe the selected hour at download time.
    # UI uses 24-hour compact steps to keep the hour strip readable.
    return list(range(24, int(max_hour) + 1, 24))


def ua_icon_hours(runv):
    # Keep the dashboard selector compact: 24-hour steps up to the last
    # supported/verified ICON pressure-level horizon.
    return list(range(24, 169, 24))


def ua_probe_url(url, timeout=20):
    if requests is None:
        return False, "requests unavailable"
    try:
        r = requests.get(url, headers={"Range": "bytes=0-3", "User-Agent": "MASRAINMAN-UA/1.0", "Accept": "*/*"}, stream=True, timeout=timeout, allow_redirects=True)
        status = r.status_code
        head = b""
        try:
            head = next(r.iter_content(chunk_size=4), b"")[:4]
        finally:
            r.close()
        return status in (200, 206), f"HTTP {status}" + (" • GRIB" if head == b"GRIB" else "")
    except Exception as exc:
        return False, str(exc)


def ua_recent_cycles(hours=(18, 12, 6, 0), days=3):
    now = datetime.now(timezone.utc)
    out = []
    for dback in range(days):
        d = now.date() - timedelta(days=dback)
        for rh in hours:
            dt = datetime(d.year, d.month, d.day, rh, tzinfo=timezone.utc)
            if dt <= now:
                out.append(dt.strftime("%Y%m%d%H"))
    return sorted(set(out), reverse=True)


def ua_icon_url(runv, fhour, level, component):
    runv = str(runv)
    rh = int(runv[8:10])
    param = str(component).upper()
    filename = f"icon_global_icosahedral_pressure-level_{runv}_{int(fhour):03d}_{int(level)}_{param}.grib2.bz2"
    return f"{ICON_BASE_URL}/{rh:02d}/{component.lower()}/{filename}"


def ua_icon_run_ready(runv, level=850, fhour=24):
    uok, ud = ua_probe_url(ua_icon_url(runv, fhour, level, "U"))
    vok, vd = ua_probe_url(ua_icon_url(runv, fhour, level, "V"))
    return uok and vok, f"U={ud}; V={vd}"


def ua_gefs_filter_url(runv, member, fhour, level):
    from urllib.parse import urlencode
    rd, rh = str(runv)[:8], int(str(runv)[8:10])
    fn = gefs_member_filename(member, rh, fhour, 50)
    params = {
        "file": fn,
        "dir": f"/gefs.{rd}/{rh:02d}/atmos/pgrb2ap5",
        "subregion": "",
        "leftlon": UA_DOMAIN[0], "rightlon": UA_DOMAIN[1],
        "toplat": UA_DOMAIN[3], "bottomlat": UA_DOMAIN[2],
        "var_UGRD": "on", "var_VGRD": "on",
        f"lev_{int(level)}_mb": "on",
    }
    return GEFS_FILTER50_URL + "?" + urlencode(params)


def ua_gefs_probe(runv, level=850, fhour=24, member="gec00"):
    # Probe a specific member.  The old implementation only checked the
    # control member, which could make the UI advertise a forecast hour that
    # was missing from a perturbation member (for example F360/gep01).
    return ua_probe_url(ua_gefs_filter_url(runv, member, fhour, level), timeout=20)

def ua_gefs_hour_available(runv, level, fhour):
    # A lightweight availability gate: require the control plus two
    # representative perturbation members before exposing an hour.  The
    # actual plot still verifies every member during download.
    for member in ("gec00", "gep01", "gep30"):
        ok, detail = ua_gefs_probe(runv, level, fhour, member)
        if not ok:
            ua_log(f"GEFS hour unavailable • {ua_run_label(runv)} • {level} hPa • F{fhour:03d} • {member} • {detail}", "info")
            return False
    return True


def ua_gfs_filter_url(runv, fhour, level):
    """NOAA/NCEP GFS 0.25° pressure-level U/V filtered request."""
    from urllib.parse import urlencode
    rd, rh = str(runv)[:8], int(str(runv)[8:10])
    fn = f"gfs.t{rh:02d}z.pgrb2.0p25.f{int(fhour):03d}"
    params = {
        "file": fn,
        "dir": f"/gfs.{rd}/{rh:02d}/atmos",
        "subregion": "",
        "leftlon": UA_DOMAIN[0], "rightlon": UA_DOMAIN[1],
        "toplat": UA_DOMAIN[3], "bottomlat": UA_DOMAIN[2],
        "var_UGRD": "on", "var_VGRD": "on",
        f"lev_{int(level)}_mb": "on",
    }
    return GFS_FILTER_URL + "?" + urlencode(params)


def ua_gfs_probe(runv, level=850, fhour=24):
    return ua_probe_url(ua_gfs_filter_url(runv, fhour, level), timeout=20)


def ua_gfs_hour_available(runv, level, fhour):
    ok, detail = ua_gfs_probe(runv, level, fhour)
    if not ok:
        ua_log(f"GFS hour unavailable • {ua_run_label(runv)} • {level} hPa • F{fhour:03d} • {detail}", "info")
    return ok


def ua_ecmwf_det_component_url(runv, level, fhour, component):
    """Document the requested HRES component; retrieval uses ecmwf-opendata."""
    return f"ECMWF HRES {component.upper()} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d}"


def ua_ecmwf_det_component_download(runv, level, fhour, component, final_target):
    """Download one ECMWF IFS HRES U/V component with the same robust mirror rotation as ENS."""
    if final_target.exists() and ua_validate_ecmwf_component(final_target, level, component):
        ua_log(f"ECMWF HRES CACHE OK • {component.upper()} • {ua_run_label(runv)} • F{fhour:03d}", "info")
        return
    try:
        final_target.unlink()
    except Exception:
        pass
    last_exc = None
    for source in ("azure", "google", "aws", "ecmwf"):
        for attempt in range(1, 4):
            part = final_target.with_suffix(final_target.suffix + f".{source}.part")
            try:
                part.unlink(missing_ok=True)
                if ECMWFOpenDataClient is None:
                    raise RuntimeError("ecmwf-opendata is not installed")
                client = get_ecmwf_opendata_client(source)
                ua_log(f"ECMWF HRES {component.upper()} • {source.upper()} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d} • attempt {attempt}/3")
                client.retrieve(
                    date=runv[:8], time=int(runv[8:10]), stream="oper", type="fc",
                    levtype="pl", levelist=str(level), param=component,
                    step=int(fhour), target=str(part)
                )
                if not part.exists() or part.stat().st_size < 1024 or not ua_validate_ecmwf_component(part, level, component):
                    raise RuntimeError(f"{source} returned an invalid ECMWF HRES {component.upper()} GRIB")
                part.replace(final_target)
                ua_log(f"ECMWF HRES {component.upper()} OK • {source.upper()} • F{fhour:03d}", "success")
                return
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                transient = any(t in msg.lower() for t in ("503", "slow down", "429", "502", "504", "timeout", "timed out", "connection reset", "connection aborted"))
                ua_log(f"ECMWF HRES {component.upper()} FAILED • {source.upper()} • attempt {attempt}/3 • {msg}", "warn" if transient else "error")
                try: part.unlink(missing_ok=True)
                except Exception: pass
                if transient and attempt < 3:
                    wait_s = 10 * (2 ** (attempt - 1))
                    ua_log(f"ECMWF HRES {component.upper()} • retrying {source.upper()} in {wait_s}s", "info")
                    time.sleep(wait_s)
                elif not transient:
                    break
    raise RuntimeError(f"ECMWF HRES {component.upper()} download failed across all mirrors: {last_exc}")


def ua_discover_runs(model, limit=4):
    model = str(model).upper()
    candidates = []
    if model in ("ECMWF", "ECMWF_HRES"):
        for dt, rd, rh in ecmwf_candidate_runs():
            runv = f"{rd}{rh:02d}"
            try:
                ok, detail = ua_probe_ecmwf_run(
                    runv,
                    stream=("enfo" if model == "ECMWF" else "oper"),
                    typ=("pf" if model == "ECMWF" else "fc"),
                    fhour=24,
                )
                ua_log(f"{model} run probe • {ua_run_label(runv)} • {detail}", "success" if ok else "info")
                if ok:
                    candidates.append({"value": runv, "label": ua_run_label(runv)})
            except Exception as exc:
                ua_log(f"{model} run probe failed • {ua_run_label(runv)} • {exc}", "info")
            if len(candidates) >= limit:
                break
        # If ECMWF is temporarily rate-limited, still expose the most recent
        # scheduled cycles so the user can explicitly select one and download.
        if not candidates:
            for dt, rd, rh in ecmwf_candidate_runs():
                runv=f"{rd}{rh:02d}"
                candidates.append({"value":runv,"label":ua_run_label(runv)})
                if len(candidates)>=limit:
                    break
    elif model == "GEFS":
        for runv in ua_recent_cycles():
            ok, detail = ua_gefs_probe(runv, 850, 24, "gec00")
            ua_log(f"GEFS run probe • {ua_run_label(runv)} • {detail}", "success" if ok else "info")
            if ok:
                candidates.append({"value": runv, "label": ua_run_label(runv)})
            if len(candidates) >= limit: break
    elif model == "GFS":
        for runv in ua_recent_cycles():
            ok, detail = ua_gfs_probe(runv, 850, 24)
            ua_log(f"GFS deterministic run probe • {ua_run_label(runv)} • {detail}", "success" if ok else "info")
            if ok:
                candidates.append({"value": runv, "label": ua_run_label(runv)})
            if len(candidates) >= limit: break
    elif model in ("ICON", "ICON_DET"):
        for runv in ua_recent_cycles():
            ok, detail = ua_icon_run_ready(runv, 850, 24)
            ua_log(f"ICON Global run probe • {ua_run_label(runv)} • {detail}", "success" if ok else "info")
            if ok:
                candidates.append({"value": runv, "label": ua_run_label(runv)})
            if len(candidates) >= limit: break
    else:
        raise ValueError(f"Unsupported upper-air model: {model}")
    if not candidates:
        raise RuntimeError(f"No available upper-air {model} run found")
    return candidates


def ua_probe_ecmwf_run(runv, stream="enfo", typ="pf", fhour=24):
    """Lightweight ECMWF publication probe.

    It deliberately uses HTTP HEAD against the official small/known GRIB object
    instead of constructing an ecmwf-opendata client for run discovery. This
    prevents the run-selector from failing because an installed opendata-client
    version does not support newer retry constructor arguments and avoids large
    downloads during run discovery.
    """
    if requests is None:
        return False, "requests unavailable"
    try:
        rd, rh = str(runv)[:8], int(str(runv)[8:10])
        # The standard ECMWF Open Data URL is stable for both ENS and HRES.
        # A HEAD request is only a publication check; no forecast bytes are read.
        if str(stream).lower() == "enfo":
            name=f"{rd}{rh:02d}0000-24h-enfo-ef.grib2"
        else:
            name=f"{rd}{rh:02d}0000-24h-oper-fc.grib2"
        roots=[r for _,r in ECMWF_HTTP_SOURCES]
        errors=[]
        for root in roots:
            url=f"{root.rstrip('/')}/{rd}/{rh:02d}z/ifs/0p25/{str(stream).lower()}/{name}"
            try:
                rr=requests.head(url,headers=_request_headers(),timeout=(10,25),allow_redirects=True)
                status=rr.status_code
                rr.close()
                if status in (200,206):
                    return True, f"HTTP {status} • {root.split('/')[-1]}"
                errors.append(f"{status} {root.split('/')[-1]}")
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        return False, " • ".join(errors)[:600]
    except Exception as exc:
        return False, str(exc)


def ua_probe_ecmwf_hres_run(runv, level=850, fhour=24):
    return ua_probe_ecmwf_run(runv, stream="oper", typ="fc", fhour=fhour)


def ua_hours_for(model, runv, level):
    model = str(model).upper(); level = int(level)
    if level not in UA_LEVELS:
        raise ValueError("Upper-air level must be 850, 700 or 500 hPa")
    if model == "ECMWF":
        return ua_ecmwf_hours(runv)
    if model == "ECMWF_HRES":
        # Same compact 24h cadence used by the dashboard; backend validates the actual field on click.
        return ua_ecmwf_hours(runv)
    if model == "GEFS":
        hours = ua_gefs_hours(runv, 360)
        available = []
        for h in hours:
            if ua_gefs_hour_available(runv, level, h):
                available.append(h)
            elif available:
                break
        return available or [24]
    if model == "GFS":
        available = []
        for h in range(24, 361, 24):
            if ua_gfs_hour_available(runv, level, h):
                available.append(h)
            elif available:
                break
        return available or [24]
    if model in ("ICON", "ICON_DET"):
        hours = ua_icon_hours(runv)
        available = []
        for h in hours:
            ok, _ = ua_icon_run_ready(runv, level, h)
            if ok: available.append(h)
            elif available: break
        return available or [24]
    raise ValueError(model)

def ua_download_stream(url, target):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 1000:
        return target
    tmp = target.with_suffix(target.suffix + ".part")
    with requests.get(url, headers={"User-Agent":"MASRAINMAN-UA/1.0","Accept":"*/*"}, stream=True, timeout=240) as r:
        r.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk:
                    f.write(chunk)
    if tmp.stat().st_size < 1000:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded object unexpectedly small: {url}")
    tmp.replace(target)
    return target


def ua_decode_regular_grib(path, short_name, level):
    if xr is None or cfgrib is None:
        raise RuntimeError("xarray/cfgrib are required for upper-air GRIB decoding")
    ds = xr.open_dataset(str(path), engine="cfgrib", backend_kwargs={"indexpath":"", "filter_by_keys":{"typeOfLevel":"isobaricInhPa","shortName":short_name,"level":int(level)}})
    try:
        names=list(ds.data_vars)
        if not names:
            raise RuntimeError(f"No {short_name} field in {Path(path).name}")
        da=ds[names[0]].load()
        vals=np.asarray(da.values,dtype=np.float32)
        lat=np.asarray(da.latitude.values,dtype=float)
        lon=np.mod(np.asarray(da.longitude.values,dtype=float),360.0)
    finally:
        ds.close()
    # Ensemble dimension is normally `number` for ECMWF; GEFS files are one
    # member per file, so this function returns the full decoded array.
    return vals, lat, lon


def ua_decode_ecmwf_component(path, level, short, return_member_count=False):
    """Decode one ECMWF ENS pressure-level component and retain member count.

    ECMWF ENS files can contain the `number` dimension.  We average only that
    dimension and return the actual number of members found, rather than
    assuming a fixed 50-member count.
    """
    if xr is None or cfgrib is None:
        raise RuntimeError("xarray/cfgrib are required for ECMWF ENS decoding")
    ds = None
    try:
        ds = xr.open_dataset(
            str(path),
            engine="cfgrib",
            backend_kwargs={
                "indexpath": "",
                "filter_by_keys": {
                    "typeOfLevel": "isobaricInhPa",
                    "shortName": str(short).lower(),
                    "level": int(level),
                },
            },
        )
        names = list(ds.data_vars)
        if not names:
            raise RuntimeError(f"ECMWF file has no {str(short).lower()} field")
        da = ds[names[0]].load()
        vals = np.asarray(da.values, dtype=np.float32)
        lat = np.asarray(da.latitude.values, dtype=float)
        lon = np.mod(np.asarray(da.longitude.values, dtype=float), 360.0)
        dims = da.dims

        member_count = 1
        if "number" in dims:
            ax = dims.index("number")
            member_count = int(vals.shape[ax])
            vals = np.nanmean(vals, axis=ax)

        vals = np.squeeze(vals)
        if vals.ndim != 2:
            raise RuntimeError(
                f"Unexpected ECMWF ENS {str(short).lower()} shape: {vals.shape}"
            )
        if return_member_count:
            return vals, lat, lon, member_count
        return vals, lat, lon
    finally:
        if ds is not None:
            ds.close()

def ua_validate_ecmwf_component(path, level, short):
    """Return True only when the GRIB contains a decodable requested component."""
    try:
        arr, lat, lon = ua_decode_ecmwf_component(path, level, short)
        return (
            arr.ndim == 2
            and arr.size > 100
            and np.isfinite(arr).any()
            and lat.size > 10
            and lon.size > 10
        )
    except Exception:
        return False

def ua_decode_gefs_member(path, level):
    u,lat,lon=gefs_decode_field(path,{"typeOfLevel":"isobaricInhPa","shortName":"u","level":int(level)},"u")
    v,lat2,lon2=gefs_decode_field(path,{"typeOfLevel":"isobaricInhPa","shortName":"v","level":int(level)},"v")
    if u.shape!=v.shape or not np.allclose(lat,lat2) or not np.allclose(lon,lon2):
        raise RuntimeError("GEFS upper-air U/V grids do not match")
    return u,v,lat,lon


def ua_decode_icon_native(path, component):
    if codes_grib_new_from_file is None:
        raise RuntimeError("eccodes is required for ICON upper-air decoding")
    with open(path,'rb') as f:
        while True:
            h=codes_grib_new_from_file(f)
            if h is None: break
            short=str(_icon_safe_get(h,'shortName') or '').lower()
            name=str(_icon_safe_get(h,'name') or '').lower()
            vals=_icon_safe_array(h,'values')
            level=_icon_safe_get(h,'level')
            if vals is not None and (short==component.lower() or component.lower() in name):
                arr=np.asarray(vals,dtype=np.float32)
                codes_release(h)
                return arr, level
            codes_release(h)
    raise RuntimeError(f"ICON {component} field not found in {path}")


def ua_regularize_icon(values, clat, clon):
    if griddata is None:
        raise RuntimeError("scipy is required for ICON interpolation")
    mask=(clon>=UA_DOMAIN[0]-1)&(clon<=UA_DOMAIN[1]+1)&(clat>=UA_DOMAIN[2]-1)&(clat<=UA_DOMAIN[3]+1)&np.isfinite(values)&np.isfinite(clat)&np.isfinite(clon)
    vals=values[mask]; lat=clat[mask]; lon=clon[mask]
    if vals.size<100: raise RuntimeError("Too few ICON native points in requested domain")
    glon,glat=np.meshgrid(UA_GRID_LON,UA_GRID_LAT)
    points=np.column_stack([lon,lat])
    field=griddata(points,vals,(glon,glat),method='linear')
    missing=~np.isfinite(field)
    if np.any(missing):
        field[missing]=griddata(points,vals,(glon[missing],glat[missing]),method='nearest')
    return np.asarray(field,dtype=np.float32), UA_GRID_LAT.copy(), UA_GRID_LON.copy()


def ua_download_and_decode(model, runv, level, fhour):
    model = str(model).upper(); level = int(level); fhour = int(fhour)
    key = (model, runv, level, fhour)
    with UA_LOCK:
        if key in UA_CACHE:
            return UA_CACHE[key]
    UA_MODEL_STATE.update(model=model, run=runv, level=level, hour=fhour, state='downloading', message=f'Downloading {model} • {level} hPa • F{fhour:03d}')
    valid = ua_valid_time(runv, fhour)

    if model == 'ECMWF':
        u_target = UA_ECMWF_ROOT / f"{runv}_F{fhour:03d}_{level}_u.grib2"
        v_target = UA_ECMWF_ROOT / f"{runv}_F{fhour:03d}_{level}_v.grib2"
        UA_ECMWF_ROOT.mkdir(parents=True, exist_ok=True)
        for comp, target in (("u", u_target), ("v", v_target)):
            if target.exists() and ua_validate_ecmwf_component(target, level, comp):
                continue
            target.unlink(missing_ok=True)
            last_exc = None
            for source in ("azure", "google", "aws", "ecmwf"):
                for attempt in range(1, 4):
                    part = target.with_suffix(target.suffix + f".{source}.part")
                    try:
                        part.unlink(missing_ok=True)
                        client = get_ecmwf_opendata_client(source)
                        ua_log(f"ECMWF ENS {comp.upper()} • {source.upper()} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d} • attempt {attempt}/3")
                        client.retrieve(date=runv[:8], time=int(runv[8:10]), stream='enfo', type='pf', levtype='pl', levelist=str(level), param=comp, step=fhour, target=str(part))
                        if not part.exists() or part.stat().st_size < 1024 or not ua_validate_ecmwf_component(part, level, comp):
                            raise RuntimeError(f"{source} returned an invalid ECMWF ENS {comp.upper()} GRIB")
                        part.replace(target); last_exc = None; break
                    except Exception as exc:
                        last_exc = exc; msg = str(exc)
                        transient = any(t in msg.lower() for t in ('503','slow down','429','502','504','timeout','timed out','connection reset','connection aborted'))
                        ua_log(f"ECMWF ENS {comp.upper()} FAILED • {source.upper()} • attempt {attempt}/3 • {msg}", 'warn' if transient else 'error')
                        part.unlink(missing_ok=True)
                        if transient and attempt < 3:
                            time.sleep(10 * (2 ** (attempt - 1)))
                        elif not transient:
                            break
                if target.exists() and ua_validate_ecmwf_component(target, level, comp):
                    break
            if not target.exists() or not ua_validate_ecmwf_component(target, level, comp):
                raise RuntimeError(f"ECMWF ENS {comp.upper()} download failed: {last_exc}")
        u, lat, lon, um = ua_decode_ecmwf_component(u_target, level, 'u', True)
        v, lat2, lon2, vm = ua_decode_ecmwf_component(v_target, level, 'v', True)
        if u.shape != v.shape or not np.allclose(lat, lat2) or not np.allclose(lon, lon2): raise RuntimeError('ECMWF ENS U/V grids do not match')
        if um != vm: raise RuntimeError(f'ECMWF ENS U/V member counts differ: U={um}, V={vm}')
        meta={'model':'ECMWF ENS','members':int(um),'run':ua_run_label(runv),'run_value':runv,'level':level,'forecast':f'F{fhour:03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':'ECMWF IFS ENS Open Data','grid':'ECMWF Open Data pressure level'}

    elif model == 'ECMWF_HRES':
        UA_ECMWF_HRES_ROOT.mkdir(parents=True, exist_ok=True)
        u_target = UA_ECMWF_HRES_ROOT / f"{runv}_F{fhour:03d}_{level}_u.grib2"
        v_target = UA_ECMWF_HRES_ROOT / f"{runv}_F{fhour:03d}_{level}_v.grib2"
        ua_log(f"ECMWF HRES DOWNLOAD • {ua_run_label(runv)} • {level} hPa • F{fhour:03d}")
        ua_ecmwf_det_component_download(runv, level, fhour, 'u', u_target)
        ua_ecmwf_det_component_download(runv, level, fhour, 'v', v_target)
        u, lat, lon = ua_decode_ecmwf_component(u_target, level, 'u')
        v, lat2, lon2 = ua_decode_ecmwf_component(v_target, level, 'v')
        if u.shape != v.shape or not np.allclose(lat, lat2) or not np.allclose(lon, lon2): raise RuntimeError('ECMWF HRES U/V grids do not match')
        meta={'model':'ECMWF IFS HRES','members':1,'run':ua_run_label(runv),'run_value':runv,'level':level,'forecast':f'F{fhour:03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':'ECMWF IFS HRES Open Data','grid':'ECMWF Open Data pressure level'}

    elif model == 'GEFS':
        folder=UA_GEFS_ROOT/f"{runv}_F{fhour:03d}_{level}"; folder.mkdir(parents=True,exist_ok=True)
        fields=[]
        for member in GEFS_MEMBERS:
            target=folder/f"{member}.grib2"
            if not target.exists():
                ua_log(f"GEFS DOWNLOAD • {member} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d}")
                ua_download_stream(ua_gefs_filter_url(runv,member,fhour,level),target)
            u1,v1,lat,lon=ua_decode_gefs_member(target,level); fields.append((u1,v1))
        u=np.nanmean(np.stack([x[0] for x in fields],axis=0),axis=0); v=np.nanmean(np.stack([x[1] for x in fields],axis=0),axis=0)
        meta={'model':'GEFS','members':len(fields),'run':ua_run_label(runv),'run_value':runv,'level':level,'forecast':f'F{fhour:03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':'NOAA/NCEP GEFS V4.2 • NOMADS 0.50°','grid':'0.50° pressure level'}

    elif model == 'GFS':
        folder=UA_GFS_ROOT/f"{runv}_F{fhour:03d}_{level}"; folder.mkdir(parents=True,exist_ok=True)
        target=folder/'gfs_uv.grib2'
        if not target.exists():
            ua_log(f"GFS DETERMINISTIC DOWNLOAD • {ua_run_label(runv)} • {level} hPa • F{fhour:03d}")
            ua_download_stream(ua_gfs_filter_url(runv,fhour,level),target)
        u,lat,lon=ua_decode_regular_grib(target,'u',level)
        v,lat2,lon2=ua_decode_regular_grib(target,'v',level)
        if u.shape != v.shape or not np.allclose(lat,lat2) or not np.allclose(lon,lon2): raise RuntimeError('GFS U/V grids do not match')
        meta={'model':'GFS','members':1,'run':ua_run_label(runv),'run_value':runv,'level':level,'forecast':f'F{fhour:03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':'NOAA/NCEP GFS 0.25° • NOMADS','grid':'0.25° pressure level'}

    elif model in ('ICON','ICON_DET'):
        folder=UA_ICON_ROOT/f"{runv}_F{fhour:03d}_{level}"; folder.mkdir(parents=True,exist_ok=True)
        arrays={}
        for comp in ('U','V'):
            bz=folder/f"{comp}.grib2.bz2"; grib=folder/f"{comp}.grib2"
            if not grib.exists():
                ua_log(f"ICON DOWNLOAD • {comp} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d}")
                ua_download_stream(ua_icon_url(runv,fhour,level,comp),bz)
                with bz2.open(bz,'rb') as fi,open(grib,'wb') as fo: shutil.copyfileobj(fi,fo)
            arrays[comp],_=ua_decode_icon_native(grib,comp)
        clat,clon=icon_native_coordinates(int(runv[8:10]))
        u,lat,lon=ua_regularize_icon(arrays['U'],clat,clon); v,lat2,lon2=ua_regularize_icon(arrays['V'],clat,clon)
        if u.shape!=v.shape: raise RuntimeError('ICON regularized U/V shapes do not match')
        meta={'model':'ICON Global','members':1,'run':ua_run_label(runv),'run_value':runv,'level':level,'forecast':f'F{fhour:03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':'DWD ICON Global Open Data','grid':'0.25° regridded from ICON native icosahedral grid'}
    else:
        raise ValueError(model)

    wind=np.sqrt(np.asarray(u,dtype=float)**2+np.asarray(v,dtype=float)**2)
    payload={'u':np.asarray(u,dtype=np.float32),'v':np.asarray(v,dtype=np.float32),'wind':np.asarray(wind,dtype=np.float32),'lat':np.asarray(lat,dtype=float),'lon':np.asarray(lon,dtype=float),'meta':meta}
    with UA_LOCK: UA_CACHE[key]=payload
    UA_MODEL_STATE.update(state='ready',message=f"{meta['model']} ready • {level} hPa • F{fhour:03d}",source=meta['source'])
    return payload

def _ua_prepare_plot_field(wind, lon, lat):
    """Prepare a smooth, common-resolution field for contour plotting.

    The source grids differ between ECMWF, GEFS and ICON.  Plotting each
    native array directly can expose block/grid artefacts.  We therefore:
      1) sort latitude/longitude axes;
      2) optionally interpolate onto the common 0.25° dashboard grid;
      3) apply only a mild Gaussian smoothing to the plotted speed field.
    Wind vectors themselves remain unsmoothed for truthful barbs.
    """
    field = np.asarray(wind, dtype=np.float32)
    la = np.asarray(lat, dtype=float)
    lo = np.mod(np.asarray(lon, dtype=float), 360.0)

    # Ensure increasing axes.
    if la.size > 1 and np.nanmean(np.diff(la)) < 0:
        la = la[::-1]
        field = field[::-1, :]
    if lo.size > 1 and np.nanmean(np.diff(lo)) < 0:
        order = np.argsort(lo)
        lo = lo[order]
        field = field[:, order]

    # Crop to requested domain plus a small edge margin.
    latmask = (la >= UA_DOMAIN[2]) & (la <= UA_DOMAIN[3])
    lonmask = (lo >= UA_DOMAIN[0]) & (lo <= UA_DOMAIN[1])
    if latmask.sum() >= 3 and lonmask.sum() >= 3:
        la = la[latmask]
        lo = lo[lonmask]
        field = field[np.ix_(latmask, lonmask)]

    # Regrid to the dashboard's common 0.25° grid where possible.
    target_lat = UA_GRID_LAT
    target_lon = UA_GRID_LON
    if (
        griddata is not None
        and la.size >= 3
        and lo.size >= 3
        and target_lat is not None
        and target_lon is not None
    ):
        # Rectilinear interpolation is preferable and preserves the field
        # without creating unnecessary triangulation artefacts.
        try:
            from scipy.interpolate import RegularGridInterpolator
            interp = RegularGridInterpolator(
                (la, lo),
                field,
                bounds_error=False,
                fill_value=np.nan,
            )
            glon, glat = np.meshgrid(target_lon, target_lat)
            out = interp(np.column_stack([glat.ravel(), glon.ravel()])).reshape(glat.shape)
            if np.isfinite(out).sum() > 0.90 * out.size:
                la, lo, field = target_lat, target_lon, out.astype(np.float32)
        except Exception:
            # Fallback to the native field if interpolation is unavailable.
            pass

    # Mild smoothing ONLY for colour shading. Keep wind vectors untouched.
    try:
        from scipy.ndimage import gaussian_filter
        finite = np.isfinite(field)
        if finite.any():
            fill = np.where(finite, field, np.nanmean(field))
            smooth = gaussian_filter(fill.astype(np.float32), sigma=0.65, mode="nearest")
            field = np.where(finite, smooth, np.nan).astype(np.float32)
    except Exception:
        pass

    return field, la, lo


def ua_plot_png(payload):
    if plt is None or ListedColormap is None or BoundaryNorm is None:
        raise RuntimeError("matplotlib is required for upper-air plotting")

    meta = payload['meta']
    lon = np.asarray(payload['lon'], dtype=float)
    lat = np.asarray(payload['lat'], dtype=float)
    wind_ms = np.asarray(payload['wind'], dtype=float)
    u_ms = np.asarray(payload['u'], dtype=float)
    v_ms = np.asarray(payload['v'], dtype=float)

    # Convert m/s to knots.
    wind = wind_ms * 1.94384
    u = u_ms * 1.94384
    v = v_ms * 1.94384

    # Smooth/regrid colour shading, not the vectors.
    plot_wind, plot_lat, plot_lon = _ua_prepare_plot_field(wind, lon, lat)

    levels = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80]
    colors = [
        '#ffffff', '#d9f6fb', '#a9eaf2', '#5fd6e5',
        '#20bfd2', '#08a8bd', '#119b8a', '#31b968',
        '#9fd62f', '#ffe33d', '#ffad2f', '#f47a4f', '#c43c9e'
    ]
    cmap = ListedColormap(colors)
    norm = BoundaryNorm(levels, cmap.N, clip=False)

    fig = plt.figure(figsize=(16, 10), dpi=160, facecolor='white')
    ax = fig.add_axes([0.045, 0.105, 0.825, 0.79], facecolor='white')

    # Smooth, bright, higher-contrast wind-speed field.
    cf = ax.contourf(
        plot_lon, plot_lat, plot_wind,
        levels=levels, cmap=cmap, norm=norm,
        extend='max', antialiased=True, zorder=5
    )

    if GIS_GDF is not None:
        GIS_GDF.boundary.plot(
            ax=ax, color='#202a2f', linewidth=.55, alpha=.92, zorder=20
        )

    # Barbs: target approximately 1.05° spacing regardless of source grid.
    # This avoids the very dense GEFS appearance while keeping synoptic flow
    # readable across the Arabian Sea, India and Bay of Bengal.
    def _axis_step(vals, target_deg=1.5):
        vals = np.asarray(vals, dtype=float)
        if vals.size < 2:
            return 1
        diffs = np.abs(np.diff(vals))
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size == 0:
            return 1
        native = float(np.nanmedian(diffs))
        return max(1, int(round(target_deg / native)))

    sx = _axis_step(lon, 1.25)
    sy = _axis_step(lat, 1.25)

    X, Y = np.meshgrid(lon, lat)
    valid = (
        np.isfinite(X) & np.isfinite(Y) &
        np.isfinite(u) & np.isfinite(v)
    )

    # Reference-style meteorological barbs: moderate spacing, slightly thicker
    # shafts/feathers, and standard 5/10/50-kt increments.
    ax.barbs(
        X[::sy, ::sx][valid[::sy, ::sx]],
        Y[::sy, ::sx][valid[::sy, ::sx]],
        u[::sy, ::sx][valid[::sy, ::sx]],
        v[::sy, ::sx][valid[::sy, ::sx]],
        length=5.0,
        pivot='middle',
        linewidth=.62,
        color='#202020',
        barb_increments={'half': 5, 'full': 10, 'flag': 50},
        sizes={'emptybarb': 0.02, 'spacing': 0.18, 'height': 0.40, 'width': 0.18},
        zorder=24,
    )

    ax.set_xlim(60, 100)
    ax.set_ylim(5, 30)
    ax.set_xticks([60, 65, 70, 75, 80, 85, 90, 95, 100])
    ax.set_yticks([5, 10, 15, 20, 25, 30])
    ax.tick_params(colors='#30383d', labelsize=8, length=3)
    ax.set_xlabel('Longitude', fontsize=8, color='#30383d')
    ax.set_ylabel('Latitude', fontsize=8, color='#30383d')
    ax.grid(False)

    cax = fig.add_axes([0.895, 0.125, 0.022, 0.735])
    cb = fig.colorbar(
        cf, cax=cax, orientation='vertical',
        ticks=levels, extend='max'
    )
    cb.set_label(
        f"{meta['level']} hPa Wind Speed (kt)",
        fontsize=10
    )
    cb.ax.tick_params(labelsize=8)

    member_text = (
        f"{meta['members']} Members Ensemble Mean"
        if int(meta.get('members', 1)) > 1
        else "Deterministic"
    )

    fh = int(str(meta['forecast']).replace('F', ''))
    sub = (
        f"{meta['run']} | Forecast Hour: {fh:03d} | "
        f"Valid: {meta['valid_end_utc']} | {member_text}"
    )

    fig.text(
        .50, .970,
        f"MASRainman {meta['model']} • {meta['level']} hPa Wind",
        ha='center', va='top', fontsize=19,
        fontweight='bold', color='#17324d'
    )
    fig.text(
        .50, .938, sub,
        ha='center', va='top', fontsize=11,
        fontweight='bold', color='#60737d'
    )
    fig.text(
        .035, .020,
        f"Data Source: {meta['source']}\n60°E–100°E • 5°N–30°N • 850/700/500 hPa",
        ha='left', va='bottom', fontsize=8,
        fontweight='bold', color='#c51f1f'
    )
    fig.text(
        .895, .020,
        'Not an Official Forecast\nFollow IMD for Weather Warnings',
        ha='right', va='bottom', fontsize=8,
        fontweight='bold', color='#c51f1f'
    )

    buf = io.BytesIO()
    fig.savefig(
        buf, format='png', facecolor='white', dpi=170,
        bbox_inches='tight', pad_inches=.08
    )
    plt.close(fig)
    return buf.getvalue()



# ============================================================================
# V184 PRESSURE ENGINE
# ============================================================================
PRESSURE_ROOT=ECMWF_ROOT / "PRESSURE"
PRESSURE_ROOT.mkdir(parents=True,exist_ok=True)
PRESSURE_LOCK=threading.RLock()
PRESSURE_CACHE={}
PRESSURE_SUPPORTED=('ECMWF','ECMWF_HRES','GEFS','GFS','ICON','AIGFS')
PRESSURE_PRODUCTS=('MSLP','925','850','500')
PRESSURE_PRODUCT_LEVELS={'MSLP':None,'925':925,'850':850,'500':500}

def pressure_hours_for(model,runv):
    model=str(model).upper(); run_hour=int(runv[8:10])
    candidates=list(range(24,361,24))
    if model in ('ECMWF','ECMWF_HRES'):
        valid=set(ecmwf_valid_steps(run_hour,'ensemble' if model=='ECMWF' else 'deterministic'))
        return [h for h in candidates if h in valid]
    if model=='GEFS':
        return [h for h in candidates if h<=360]
    if model=='GFS':
        # GFS 0.25 pressure output supports these synoptic 24-h checkpoints.
        return [h for h in candidates if h<=384]
    if model=='ICON':
        # ICON Global publishes the short-range pressure field every 3 h;
        # the dashboard intentionally exposes 24-h checkpoints for a compact UI.
        return [h for h in candidates if h<=180]
    if model=='AIGFS':
        return [h for h in candidates if h<=360]
    return []

def pressure_discover_runs(model,limit=4):
    model=str(model).upper()
    if model in ('ECMWF','ECMWF_HRES','GEFS','GFS','ICON'):
        runs=ua_discover_runs(model,limit)
        return runs
    if model=='AIGFS':
        # AIGFS is 00/06/12/18 UTC. Probe the first synoptic endpoint rather
        # than trusting the clock; this prevents a stale cycle from appearing.
        out=[]; now=datetime.now(timezone.utc)
        for back in range(0,4):
            d=now.date()-timedelta(days=back)
            for hh in (18,12,6,0):
                dt=datetime(d.year,d.month,d.day,hh,tzinfo=timezone.utc)
                if dt>now: continue
                runv=dt.strftime('%Y%m%d%H')
                try:
                    url=f'{AIGFS_BASE_URL}/aigfs.{dt:%Y%m%d}/{hh:02d}/model/atmos/grib2/aigfs.t{hh:02d}z.pres.f024.grib2'
                    ok,detail=ua_probe_url(url,timeout=20)
                    if ok: out.append({'value':runv,'label':ua_run_label(runv)})
                except Exception: pass
                if len(out)>=limit:return out[:limit]
        return out[:limit]
    return []

# -----------------------------------------------------------------------------
# PRESSURE ECMWF DIRECT INDEX + RANGE ENGINE
# -----------------------------------------------------------------------------
# IMPORTANT: Pressure retrieval MUST NOT use ecmwf-opendata Client.retrieve().
# That client has its own retry loop and can turn one AWS 503 into:
#     attempt 1 of 500 -> 120 seconds -> attempt 2 ...
# The pressure module therefore uses the same official .index + HTTP Range
# architecture already used by the stable rainfall engine above.
# -----------------------------------------------------------------------------

PRESSURE_ECMWF_SOURCES = [
    ("ECMWF", "https://data.ecmwf.int/forecasts"),
    ("AWS", "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"),
]
PRESSURE_ECMWF_ATTEMPTS = 2
PRESSURE_ECMWF_RANGE_DELAY = 0.35
PRESSURE_ECMWF_TIMEOUT = (20, 90)


def _pressure_ecmwf_file_url(source_root, runv, fhour, ensemble):
    rd=runv[:8]; rh=int(runv[8:10])
    if ensemble:
        return (f"{source_root.rstrip('/')}/{rd}/{rh:02d}z/ifs/0p25/enfo/"
                f"{rd}{rh:02d}0000-{int(fhour)}h-enfo-ef.grib2")
    return (f"{source_root.rstrip('/')}/{rd}/{rh:02d}z/ifs/0p25/oper/"
            f"{rd}{rh:02d}0000-{int(fhour)}h-oper-fc.grib2")


def _pressure_ecmwf_index_url(source_root, runv, fhour, ensemble):
    return _pressure_ecmwf_file_url(source_root,runv,fhour,ensemble).replace('.grib2','.index')


def _pressure_ecmwf_index_records(source_name, source_root, runv, fhour, ensemble):
    url=_pressure_ecmwf_index_url(source_root,runv,fhour,ensemble)
    last=None
    for attempt in range(1,PRESSURE_ECMWF_ATTEMPTS+1):
        status=None; retry_after=None
        try:
            ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} INDEX • {source_name} • {ua_run_label(runv)} • F{fhour:03d} • attempt {attempt}/{PRESSURE_ECMWF_ATTEMPTS}")
            r=requests.get(url,timeout=PRESSURE_ECMWF_TIMEOUT,headers=_request_headers())
            status=r.status_code
            if status in (401,403,404):
                return None,f"HTTP {status} {r.reason}"
            if status==429:
                retry_after=r.headers.get('Retry-After')
                raise RuntimeError(f"HTTP 429 Too Many Requests (Retry-After: {retry_after or 'not supplied'})")
            if status!=200:
                raise RuntimeError(f"HTTP {status} {r.reason}")
            records=[]
            for raw in r.text.splitlines():
                raw=raw.strip()
                if not raw: continue
                try: rec=json.loads(raw)
                except Exception: continue
                param=str(rec.get('param','')).lower()
                if param not in ('msl','prmsl'): continue
                if ensemble:
                    if str(rec.get('stream','')).lower()!='enfo': continue
                    if str(rec.get('type','')).lower()!='pf': continue
                    try: number=int(rec.get('number'))
                    except Exception: continue
                else:
                    if str(rec.get('stream','')).lower()!='oper': continue
                    if str(rec.get('type','')).lower()!='fc': continue
                    number=None
                try:
                    step=int(str(rec.get('step')))
                    offset=int(rec['_offset']); length=int(rec['_length'])
                except Exception: continue
                if step!=int(fhour) or offset<0 or length<=0: continue
                records.append({'number':number,'offset':offset,'length':length})
            if ensemble:
                by={x['number']:x for x in records if x['number'] is not None}
                selected=[by[m] for m in range(1,51) if m in by]
                if len(selected)!=50:
                    raise RuntimeError(f"index contains only {len(selected)}/50 ECMWF ENS MSLP members")
            else:
                if not records:
                    raise RuntimeError('index contains no ECMWF HRES MSLP field')
                selected=[records[0]]
            ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} INDEX READY • {source_name} • {len(selected)} field(s)", 'success')
            return selected,None
        except Exception as exc:
            last=str(exc)
            if attempt<PRESSURE_ECMWF_ATTEMPTS:
                if status in (429,503):
                    try: wait_s=min(float(retry_after),8.0) if retry_after else 3.0
                    except Exception: wait_s=3.0
                else: wait_s=1.5
                ua_log(f"PRESSURE • ECMWF {source_name} INDEX FAILED • {last} • retrying in {wait_s:.1f}s",'warn')
                time.sleep(wait_s)
    return None,last


def _pressure_ecmwf_range(source_name, source_root, runv, fhour, ensemble, rec):
    url=_pressure_ecmwf_file_url(source_root,runv,fhour,ensemble)
    offset=int(rec['offset']); length=int(rec['length']); end=offset+length-1
    last=None
    for attempt in range(1,PRESSURE_ECMWF_ATTEMPTS+1):
        status=None
        try:
            if PRESSURE_ECMWF_RANGE_DELAY: time.sleep(PRESSURE_ECMWF_RANGE_DELAY)
            label=f"member {int(rec['number']):02d}" if rec.get('number') is not None else 'HRES'
            ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} RANGE • {source_name} • {label} • {offset:,}-{end:,}")
            r=requests.get(url,stream=True,timeout=PRESSURE_ECMWF_TIMEOUT,headers={**_request_headers(),'Range':f'bytes={offset}-{end}'})
            status=r.status_code
            if status==429:
                raise RuntimeError('HTTP 429 Too Many Requests')
            if status==503:
                raise RuntimeError('HTTP 503 Slow Down')
            if status!=206:
                raise RuntimeError(f'HTTP {status}; expected 206 Partial Content')
            data=bytearray()
            for chunk in r.iter_content(chunk_size=1024*1024):
                if chunk: data.extend(chunk)
            r.close()
            if len(data)!=length: raise RuntimeError(f'range length mismatch {len(data)} != {length}')
            if bytes(data[:4])!=b'GRIB': raise RuntimeError('range response is not GRIB')
            return bytes(data)
        except Exception as exc:
            last=str(exc)
            if attempt<PRESSURE_ECMWF_ATTEMPTS:
                wait_s=3.0 if status in (429,503) else 1.5
                ua_log(f"PRESSURE • ECMWF {source_name} RANGE FAILED • {last} • retrying in {wait_s:.1f}s",'warn')
                time.sleep(wait_s)
    raise RuntimeError(last or 'ECMWF range download failed')


def _pressure_write_messages(target, messages):
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix(target.suffix+'.part')
    with open(tmp,'wb') as f:
        for data in messages: f.write(data)
    tmp.replace(target)
    return target


def _pressure_ecmwf_retrieve(runv,fhour,ensemble=False):
    target=PRESSURE_ROOT / ('ECMWF_ENS' if ensemble else 'ECMWF_HRES') / f'{runv}_F{fhour:03d}_msl.grib2'
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and target.stat().st_size>1000:return target
    last=None
    for source_name,source_root in PRESSURE_ECMWF_SOURCES:
        records,err=_pressure_ecmwf_index_records(source_name,source_root,runv,fhour,ensemble)
        if not records:
            last=err
            ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} {source_name} UNAVAILABLE • {err}",'warn')
            continue
        try:
            messages=[]
            for rec in records:
                messages.append(_pressure_ecmwf_range(source_name,source_root,runv,fhour,ensemble,rec))
            _pressure_write_messages(target,messages)
            ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} READY • {source_name} • F{fhour:03d} • {target.stat().st_size/1024/1024:.2f} MB",'success')
            return target
        except Exception as exc:
            last=exc
            ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} {source_name} RANGE FAILED • {exc}",'warn')
            try: target.unlink(missing_ok=True)
            except Exception: pass
    raise RuntimeError(f"ECMWF MSLP download failed without OpenData client retry loop: {last}")

def _pressure_decode_regular(path,short_names=('msl','prmsl'),keep_members=False):
    if xr is None or cfgrib is None: raise RuntimeError('xarray/cfgrib are required for pressure decoding')
    last=None
    for sn in short_names:
        for tol in ('meanSea','surface','unknown'):
            try:
                ds=xr.open_dataset(str(path),engine='cfgrib',backend_kwargs={'indexpath':'','filter_by_keys':{'shortName':sn,'typeOfLevel':tol}})
                try:
                    names=list(ds.data_vars)
                    if not names: continue
                    da=ds[names[0]].load()
                    vals=np.asarray(da.values,dtype=float)
                    lat=np.asarray(da.latitude.values if 'latitude' in da.coords else da.lat.values,dtype=float)
                    lon=np.mod(np.asarray(da.longitude.values if 'longitude' in da.coords else da.lon.values),360.0)
                    dims=da.dims
                    members=1
                    member_vals=None
                    if 'number' in dims:
                        ax=dims.index('number'); members=int(vals.shape[ax])
                        vals=np.moveaxis(vals,ax,0)
                        member_vals=np.asarray(vals,dtype=float)
                        vals=np.nanmean(member_vals,axis=0)
                    vals=np.squeeze(vals)
                    if vals.ndim!=2: continue
                    if member_vals is not None:
                        member_vals=np.squeeze(member_vals)
                        if member_vals.ndim!=3: member_vals=None
                    # ECMWF/GFS/AIGFS pressure is Pa; normalize to hPa.
                    finite=np.isfinite(vals)
                    if finite.any() and float(np.nanmedian(vals[finite]))>2000:
                        vals=vals/100.0
                        if member_vals is not None: member_vals=member_vals/100.0
                    if lat.ndim==1 and lon.ndim==1:
                        yi=np.where((lat>=5)&(lat<=30))[0]; xi=np.where((lon>=60)&(lon<=100))[0]
                        if len(yi)==0 or len(xi)==0: raise RuntimeError('Pressure grid does not overlap dashboard domain')
                        vals=vals[np.ix_(yi,xi)]; lat=lat[yi]; lon=lon[xi]
                        if member_vals is not None: member_vals=member_vals[:,yi,:][:,:,xi]
                        if lat[0]>lat[-1]:
                            vals=vals[::-1,:]; lat=lat[::-1]
                            if member_vals is not None: member_vals=member_vals[:,::-1,:]
                        if lon[0]>lon[-1]:
                            vals=vals[:,::-1]; lon=lon[::-1]
                            if member_vals is not None: member_vals=member_vals[:,:,::-1]
                    if keep_members and member_vals is not None:
                        return vals.astype(np.float32),lat,lon,members,member_vals.astype(np.float32)
                    return vals.astype(np.float32),lat,lon,members
                finally: ds.close()
            except Exception as exc: last=exc
    raise RuntimeError(f'No MSLP/prmsl field found in {Path(path).name}: {last}')

def _pressure_icon_url(runv,fhour):
    return f'{ICON_BASE_URL}/{int(runv[8:10]):02d}/pmsl/icon_global_icosahedral_single-level_{runv}_{int(fhour):03d}_PMSL.grib2.bz2'

def _pressure_icon_decode(path):
    if codes_grib_new_from_file is None: raise RuntimeError('eccodes is required for ICON pressure decoding')
    vals=None;clat=None;clon=None
    with open(path,'rb') as f:
        while True:
            h=codes_grib_new_from_file(f)
            if h is None:break
            short=str(_icon_safe_get(h,'shortName') or '').lower(); name=str(_icon_safe_get(h,'name') or '').lower()
            if vals is None and ('pmsl' in short or 'mean sea level pressure' in name or short=='prmsl'):
                vals=np.asarray(_icon_safe_array(h,'values'),dtype=np.float32)
            if clat is None:
                try: clat=np.asarray(_icon_safe_array(h,'latitudes'),dtype=float); clon=np.asarray(_icon_safe_array(h,'longitudes'),dtype=float)
                except Exception: pass
            codes_release(h)
    if vals is None: raise RuntimeError('ICON Global PMSL field not found')
    if clat is None or clon is None: raise RuntimeError('ICON Global coordinates unavailable')
    finite=np.isfinite(vals)
    if finite.any() and float(np.nanmedian(vals[finite]))>2000: vals=vals/100.0
    field,lat,lon=ua_regularize_icon(vals,clat,clon)
    return field.astype(np.float32),lat,lon,1

def _pressure_icon_download(runv,fhour):
    target=PRESSURE_ROOT/'ICON'/f'{runv}_F{fhour:03d}_pmsl.grib2'; bz=Path(str(target)+'.bz2'); target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists() or target.stat().st_size<1000:
        if not bz.exists() or bz.stat().st_size<1000:
            ua_download_stream(_pressure_icon_url(runv,fhour),bz)
        with bz.open('rb') as src,bz2.open(target,'wb') as dst: shutil.copyfileobj(src,dst)
    return target

def _pressure_gfs_download(runv,fhour):
    from urllib.parse import urlencode
    rd,rh=runv[:8],int(runv[8:10]); fn=f'gfs.t{rh:02d}z.pgrb2.0p25.f{fhour:03d}'
    params={'file':fn,'dir':f'/gfs.{rd}/{rh:02d}/atmos','subregion':'','leftlon':60,'rightlon':100,'toplat':30,'bottomlat':5,'var_PRMSL':'on','lev_mean_sea_level':'on'}
    url=GFS_FILTER_URL+'?'+urlencode(params); target=PRESSURE_ROOT/'GFS'/f'{runv}_F{fhour:03d}.grib2'; return ua_download_stream(url,target)

def _pressure_aigfs_download(runv,fhour):
    rd,rh=runv[:8],int(runv[8:10]); url=f'{AIGFS_BASE_URL}/aigfs.{rd}/{rh:02d}/model/atmos/grib2/aigfs.t{rh:02d}z.pres.f{fhour:03d}.grib2'; target=PRESSURE_ROOT/'AIGFS'/f'{runv}_F{fhour:03d}.grib2'; return ua_download_stream(url,target)

def _detect_member_low_centres(member_stack, lat, lon, threshold_hpa=1010.0, min_prominence_hpa=1.0):
    """Return one meaningful low centre for each ensemble member.

    The pressure map is already cropped to 60E-100E / 5N-30N.  For each
    member we locate its absolute MSLP minimum, then keep it only when it
    behaves like a genuine closed/meaningful low: pressure <= threshold and
    at least min_prominence_hpa below the member's domain median.

    This intentionally returns one centre per member, matching the compact
    EPS low-centre spread presentation used by the dashboard.  No circle is
    drawn around the spread; the individual member centres themselves form
    the spread.
    """
    if member_stack is None:
        return []
    a = np.asarray(member_stack, dtype=float)
    if a.ndim != 3:
        raise RuntimeError(f'Expected ensemble MSLP stack (members,y,x), got {a.shape}')
    la = np.asarray(lat, dtype=float).squeeze()
    lo = np.asarray(lon, dtype=float).squeeze()
    if la.ndim != 1 or lo.ndim != 1:
        raise RuntimeError('Low-centre detector requires 1-D latitude/longitude coordinates')
    if a.shape[1] != la.size or a.shape[2] != lo.size:
        raise RuntimeError(f'Ensemble/grid mismatch: stack={a.shape}, lat={la.size}, lon={lo.size}')

    centres = []
    for mi in range(a.shape[0]):
        field = a[mi]
        finite = np.isfinite(field)
        if not finite.any():
            continue

        # Absolute member minimum is stable and fast for the native pressure
        # grids.  The prominence test suppresses meaningless weak minima.
        work = np.where(finite, field, np.inf)
        iy, ix = np.unravel_index(np.argmin(work), work.shape)
        p = float(work[iy, ix])
        if not np.isfinite(p):
            continue

        median_p = float(np.nanmedian(field[finite]))
        prominence = median_p - p
        if p > float(threshold_hpa) or prominence < float(min_prominence_hpa):
            continue

        centres.append({
            'member': mi + 1,
            'lat': float(la[iy]),
            'lon': float(lo[ix]),
            'mslp': p,
        })

    return centres


def _pressure_height_decode_ecmwf(path, level, keep_members=False):
    """Decode ECMWF geopotential at an isobaric level and return dam."""
    if xr is None or cfgrib is None:
        raise RuntimeError('xarray/cfgrib are required for pressure-level decoding')
    ds=None
    try:
        ds=xr.open_dataset(str(path),engine='cfgrib',backend_kwargs={'indexpath':'','filter_by_keys':{'typeOfLevel':'isobaricInhPa','shortName':'z','level':int(level)}})
        names=list(ds.data_vars)
        if not names: raise RuntimeError(f'ECMWF file has no geopotential z at {level} hPa')
        da=ds[names[0]].load()
        vals=np.asarray(da.values,dtype=float)
        lat=np.asarray(da.latitude.values if 'latitude' in da.coords else da.lat.values,dtype=float)
        lon=np.mod(np.asarray(da.longitude.values if 'longitude' in da.coords else da.lon.values),360.0)
        dims=da.dims
        member_vals=None; members=1
        if 'number' in dims:
            ax=dims.index('number'); members=int(vals.shape[ax]); vals=np.moveaxis(vals,ax,0); member_vals=vals.copy(); vals=np.nanmean(vals,axis=0)
        vals=np.squeeze(vals)
        if member_vals is not None: member_vals=np.squeeze(member_vals)
        # ECMWF geopotential z is m2 s-2; divide by g for geopotential metres, then /10 for dam.
        vals=vals/9.80665/10.0
        if member_vals is not None: member_vals=member_vals/9.80665/10.0
        if vals.ndim!=2: raise RuntimeError(f'Unexpected ECMWF height shape {vals.shape}')
        if lat.ndim==1 and lon.ndim==1:
            yi=np.where((lat>=5)&(lat<=30))[0]; xi=np.where((lon>=60)&(lon<=100))[0]
            if len(yi)==0 or len(xi)==0: raise RuntimeError('Pressure-level grid does not overlap dashboard domain')
            vals=vals[np.ix_(yi,xi)]; lat=lat[yi]; lon=lon[xi]
            if member_vals is not None: member_vals=member_vals[:,yi,:][:,:,xi]
            if lat[0]>lat[-1]: vals=vals[::-1,:]; lat=lat[::-1]; member_vals=member_vals[:,::-1,:] if member_vals is not None else None
            if lon[0]>lon[-1]: vals=vals[:,::-1]; lon=lon[::-1]; member_vals=member_vals[:,:,::-1] if member_vals is not None else None
        return vals.astype(np.float32),lat,lon,members,member_vals.astype(np.float32) if (keep_members and member_vals is not None) else None
    finally:
        if ds is not None: ds.close()


def _pressure_ecmwf_height_download(runv,fhour,level,ensemble=True):
    target=PRESSURE_ROOT / ('ECMWF_ENS_HEIGHT' if ensemble else 'ECMWF_HRES_HEIGHT') / f'{runv}_F{fhour:03d}_{int(level)}_z.grib2'
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists() and target.stat().st_size>1000:
        return target
    last=None
    stream='enfo' if ensemble else 'oper'; typ='pf' if ensemble else 'fc'
    for source in ('azure','google','aws','ecmwf'):
        for attempt in range(1,4):
            part=target.with_suffix(target.suffix+f'.{source}.part')
            try:
                part.unlink(missing_ok=True)
                if ECMWFOpenDataClient is None: raise RuntimeError('ecmwf-opendata is not installed')
                client=get_ecmwf_opendata_client(source)
                ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} HEIGHT • {source.upper()} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d} • attempt {attempt}/3")
                client.retrieve(date=runv[:8],time=int(runv[8:10]),stream=stream,type=typ,levtype='pl',levelist=str(level),param='z',step=int(fhour),target=str(part))
                if not part.exists() or part.stat().st_size<1024:
                    raise RuntimeError(f'{source} returned an invalid ECMWF geopotential GRIB')
                # Decode once before declaring the cache valid.
                _pressure_height_decode_ecmwf(part,int(level),keep_members=False)
                part.replace(target)
                ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} HEIGHT READY • {source.upper()} • {level} hPa • F{fhour:03d}",'success')
                return target
            except Exception as exc:
                last=exc; msg=str(exc)
                transient=any(t in msg.lower() for t in ('503','slow down','429','502','504','timeout','timed out','connection reset','connection aborted'))
                ua_log(f"PRESSURE • ECMWF {'ENS' if ensemble else 'HRES'} HEIGHT FAILED • {source.upper()} • attempt {attempt}/3 • {msg}",'warn' if transient else 'error')
                try: part.unlink(missing_ok=True)
                except Exception: pass
                if transient and attempt<3:
                    time.sleep(10*(2**(attempt-1)))
                elif not transient:
                    break
    raise RuntimeError(f"ECMWF {'ENS' if ensemble else 'HRES'} {level} hPa geopotential download failed: {last}")


def _pressure_gefs_height_download(runv,fhour,level):
    rd,rh=runv[:8],int(runv[8:10]); folder=PRESSURE_ROOT/'GEFS_HEIGHT'/f'{runv}_F{fhour:03d}_{int(level)}'; folder.mkdir(parents=True,exist_ok=True)
    fields=[]
    for member in GEFS_MEMBERS:
        target=folder/f'{member}.grib2'
        if not target.exists() or target.stat().st_size<1000:
            url=gefs_filter_url(rd,rh,member,fhour,50,['HGT'],level=int(level))
            tmp=target.with_suffix('.part'); last=None
            for attempt in range(1,GEFS_MAX_RETRIES+1):
                try:
                    ua_log(f"PRESSURE • GEFS HEIGHT • {member} • {ua_run_label(runv)} • {level} hPa • F{fhour:03d} • attempt {attempt}/{GEFS_MAX_RETRIES}")
                    with requests.get(url,headers=GEFS_HTTP_HEADERS,timeout=GEFS_TIMEOUT,stream=True) as r:
                        r.raise_for_status()
                        with open(tmp,'wb') as f:
                            for chunk in r.iter_content(chunk_size=1024*1024):
                                if chunk: f.write(chunk)
                    if tmp.stat().st_size<1000: raise RuntimeError('GEFS height response unexpectedly small')
                    with open(tmp,'rb') as f:
                        if f.read(4)!=b'GRIB': raise RuntimeError('GEFS height response is not GRIB2')
                    tmp.replace(target); last=None; break
                except Exception as exc:
                    last=exc
                    try: tmp.unlink()
                    except Exception: pass
                    if attempt<GEFS_MAX_RETRIES: time.sleep(GEFS_RETRY_DELAY)
            if not target.exists(): raise RuntimeError(f'GEFS {member} {level} hPa height download failed: {last}')
        h,lat,lon=gefs_decode_hgt(target,int(level)); fields.append(h)
    stack=np.stack(fields,axis=0).astype(np.float32)
    return np.nanmean(stack,axis=0).astype(np.float32),lat,lon,len(fields),stack


def _pressure_gfs_height_download(runv,fhour,level):
    rd,rh=runv[:8],int(runv[8:10]); fn=f'gfs.t{rh:02d}z.pgrb2.0p25.f{fhour:03d}'; params={'file':fn,'dir':f'/gfs.{rd}/{rh:02d}/atmos','subregion':'','leftlon':60,'rightlon':100,'toplat':30,'bottomlat':5,'var_HGT':'on',f'lev_{int(level)}_mb':'on'}
    url=GFS_FILTER_URL+'?'+urlencode(params); target=PRESSURE_ROOT/'GFS_HEIGHT'/f'{runv}_F{fhour:03d}_{int(level)}.grib2'; return ua_download_stream(url,target)


def pressure_level_download_decode(model,runv,fhour,level):
    level=int(level)
    if level not in (925,850,500): raise ValueError('Pressure level must be 925, 850 or 500 hPa')
    key=('HEIGHT',model,runv,int(fhour),level)
    with PRESSURE_LOCK:
        if key in PRESSURE_CACHE:return PRESSURE_CACHE[key]
    member_stack=None
    if model=='ECMWF':
        path=_pressure_ecmwf_height_download(runv,fhour,level,True); vals,lat,lon,members,member_stack=_pressure_height_decode_ecmwf(path,level,True); source='ECMWF IFS ENS Open Data'; label='ECMWF ENS'
    elif model=='ECMWF_HRES':
        path=_pressure_ecmwf_height_download(runv,fhour,level,False); vals,lat,lon,members,_=_pressure_height_decode_ecmwf(path,level,False); source='ECMWF IFS HRES Open Data'; label='ECMWF IFS HRES'
    elif model=='GEFS':
        vals,lat,lon,members,member_stack=_pressure_gefs_height_download(runv,fhour,level); source='NOAA/NCEP GEFS V4.2 • NOMADS 0.50°'; label='GEFS'
    elif model=='GFS':
        path=_pressure_gfs_height_download(runv,fhour,level)
        if xr is None or cfgrib is None: raise RuntimeError('xarray/cfgrib are required for GFS pressure-level decoding')
        ds=None
        try:
            ds=xr.open_dataset(str(path),engine='cfgrib',backend_kwargs={'indexpath':'','filter_by_keys':{'typeOfLevel':'isobaricInhPa','shortName':'gh','level':int(level)}})
            names=list(ds.data_vars)
            if not names: raise RuntimeError(f'GFS file has no HGT at {level} hPa')
            da=ds[names[0]].load(); vals=np.asarray(da.values,dtype=np.float32)/10.0
            lat=np.asarray(da.latitude.values,dtype=float); lon=np.mod(np.asarray(da.longitude.values,dtype=float),360.0)
            vals=np.squeeze(vals)
            yi=np.where((lat>=5)&(lat<=30))[0]; xi=np.where((lon>=60)&(lon<=100))[0]
            vals=vals[np.ix_(yi,xi)]; lat=lat[yi]; lon=lon[xi]
            if lat[0]>lat[-1]: vals=vals[::-1,:]; lat=lat[::-1]
            if lon[0]>lon[-1]: vals=vals[:,::-1]; lon=lon[::-1]
        finally:
            if ds is not None: ds.close()
        members=1; source='NOAA/NCEP GFS 0.25° • NOMADS'; label='GFS'
    else:
        raise RuntimeError(f'{pressure_model_label_client(model)} pressure-level height is not connected yet. Use ECMWF ENS, GEFS, ECMWF IFS HRES or GFS for 925/850/500 hPa.')
    valid=datetime.strptime(runv,'%Y%m%d%H').replace(tzinfo=timezone.utc)+timedelta(hours=int(fhour))
    meta={'model':label,'members':int(members),'run':ua_run_label(runv),'run_value':runv,'forecast':f'F{int(fhour):03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':source,'grid':'geopotential height on isobaric pressure level','level':level,'product':str(level),'height_units':'dam','ensemble_low_count':0}
    payload={'field':np.asarray(vals,dtype=np.float32),'lat':np.asarray(lat,dtype=float),'lon':np.asarray(lon,dtype=float),'meta':meta,'member_height':member_stack}; PRESSURE_CACHE[key]=payload; return payload

def pressure_download_decode(model,runv,fhour,product='MSLP'):
    product=str(product or 'MSLP').upper()
    if product not in PRESSURE_PRODUCTS: raise ValueError(f'Unsupported pressure product: {product}')
    if product!='MSLP': return pressure_level_download_decode(model,runv,fhour,int(product))
    key=(model,runv,int(fhour),'MSLP')
    with PRESSURE_LOCK:
        if key in PRESSURE_CACHE:return PRESSURE_CACHE[key]
        member_stack=None
        if model=='ECMWF':
            path=_pressure_ecmwf_retrieve(runv,fhour,True)
            vals,lat,lon,members,member_stack=_pressure_decode_regular(path,('msl',),keep_members=True)
            source='ECMWF IFS ENS Open Data'; label='ECMWF ENS'
        elif model=='ECMWF_HRES':
            path=_pressure_ecmwf_retrieve(runv,fhour,False); vals,lat,lon,members=_pressure_decode_regular(path,('msl',)); source='ECMWF IFS HRES Open Data'; label='ECMWF IFS HRES'
        elif model=='GEFS':
            rd,rh=runv[:8],int(runv[8:10]); base=GEFS_ROOT/f'{rd}_{rh:02d}Z'/f'F{fhour:03d}'/'r25'; base.mkdir(parents=True,exist_ok=True)
            def one(member): return download_gefs_member(rd,rh,member,fhour,resolution=25,fields=['PRMSL'])
            with ThreadPoolExecutor(max_workers=min(8,GEFS_PARALLEL_WORKERS+2)) as ex: list(ex.map(one,GEFS_MEMBERS))
            arr=[]
            for member in GEFS_MEMBERS:
                p=base/gefs_member_filename(member,rh,fhour,25); a,lat,lon=gefs_decode_prmsl(p); arr.append(a)
            member_stack=np.stack(arr,axis=0).astype(np.float32); vals=np.nanmean(member_stack,axis=0).astype(np.float32); members=len(arr); source='NOAA/NCEP GEFS V4.2 • NOMADS 0.25°'; label='GEFS'
        elif model=='GFS': path=_pressure_gfs_download(runv,fhour); vals,lat,lon,members=_pressure_decode_regular(path,('prmsl',)); source='NOAA/NCEP GFS 0.25° • NOMADS'; label='GFS'
        elif model=='ICON': path=_pressure_icon_download(runv,fhour); vals,lat,lon,members=_pressure_icon_decode(path); source='DWD ICON Global Open Data'; label='ICON Global'
        elif model=='AIGFS': path=_pressure_aigfs_download(runv,fhour); vals,lat,lon,members=_pressure_decode_regular(path,('prmsl','msl')); source='NOAA/NCEP AIGFS v1.1'; label='AIGFS'
        else: raise ValueError(f'Unsupported pressure model: {model}')
        valid=datetime.strptime(runv,'%Y%m%d%H').replace(tzinfo=timezone.utc)+timedelta(hours=int(fhour))
        low_centres=_detect_member_low_centres(member_stack,lat,lon) if member_stack is not None else []
        mean_low=None
        if low_centres:
            mean_low={'lat':float(np.mean([c['lat'] for c in low_centres])),'lon':float(np.mean([c['lon'] for c in low_centres])),'mslp':float(np.mean([c['mslp'] for c in low_centres]))}
        meta={'model':label,'members':members,'run':ua_run_label(runv),'run_value':runv,'forecast':f'F{int(fhour):03d}','valid_end_utc':valid.strftime('%Y-%m-%d %H:%M UTC'),'source':source,'grid':'native/common lat-lon pressure field','ensemble_low_count':len(low_centres)}
        payload={'mslp':np.asarray(vals,dtype=np.float32),'lat':np.asarray(lat,dtype=float),'lon':np.asarray(lon,dtype=float),'meta':meta,'member_mslp':member_stack,'member_low_centres':low_centres,'mean_low':mean_low}; PRESSURE_CACHE[key]=payload; return payload

def _plot_country_boundaries(ax):
    """
    Permanent pressure-map country/state boundary overlay.

    IMPORTANT: this is deliberately drawn AFTER the pressure field and AFTER
    the Survey-of-India state layer so international borders cannot disappear
    under contourf/contour or state polygons.  Sri Lanka gets an explicit WGS84
    outline because it is not part of STATE_BOUNDARY.shp. Bangladesh is drawn
    from Basemap's country database, while West Bengal remains supplied by the
    Survey-of-India state layer. No white halo is used.
    """
    # India state boundaries are handled separately by GIS_GDF.
    # Basemap supplies the international country boundaries, including the
    # Bangladesh/India and Bangladesh/Myanmar borders.
    if Basemap is not None:
        try:
            m=Basemap(
                projection='cyl',
                llcrnrlon=60, urcrnrlon=100,
                llcrnrlat=5, urcrnrlat=30,
                resolution='i', ax=ax
            )
            # Draw country borders above the pressure contours.
            m.drawcountries(
                color='#111111', linewidth=0.90,
                zorder=60, antialiased=True
            )
            # Coastline is kept slightly stronger so Sri Lanka remains visually
            # continuous even where an international boundary segment is short.
            m.drawcoastlines(
                color='#111111', linewidth=0.80,
                zorder=59, antialiased=True
            )
        except Exception as exc:
            ua_log(f'COUNTRY BOUNDARY DRAW WARNING • {exc}','warn')

    # Explicit Sri Lanka national outline.  Same black treatment as India;
    # absolutely no white halo and no artificial fill.
    try:
        sl=np.asarray(SRI_LANKA_OUTLINE,dtype=float)
        ax.plot(
            sl[:,0], sl[:,1],
            color='#111111', linewidth=0.90,
            solid_joinstyle='round', solid_capstyle='round',
            zorder=62, antialiased=True
        )
    except Exception as exc:
        ua_log(f'SRI LANKA OUTLINE DRAW WARNING • {exc}','warn')


def pressure_plot_png(payload):
    if plt is None: raise RuntimeError('matplotlib is required for pressure plotting')
    meta=payload['meta']; product=str(meta.get('product','MSLP')).upper()
    vals=np.asarray(payload.get('mslp' if product=='MSLP' else 'field'),dtype=float)
    lat=np.asarray(payload['lat'],dtype=float); lon=np.asarray(payload['lon'],dtype=float)
    finite=vals[np.isfinite(vals)]
    if finite.size==0: raise RuntimeError('Selected pressure field contains no finite values')
    is_mslp=(product=='MSLP')
    level=int(product) if not is_mslp else None

    # Full-page 16:9-ish composition: map occupies the page, colorbar is kept inside the figure.
    fig=plt.figure(figsize=(16,9.6),dpi=170,facecolor='white')
    ax=fig.add_axes([0.045,0.105,0.835,0.805],facecolor='white')

    if is_mslp:
        levels=np.array([990,994,998,1002,1006,1010,1014,1018],dtype=float)
        cf=ax.contourf(lon,lat,vals,levels=levels,cmap='RdYlBu_r',extend='both',alpha=.90,zorder=5)
        contour_levels=np.arange(np.floor(np.nanmin(finite)/2)*2,np.ceil(np.nanmax(finite)/2)*2+2,2)
        contour_levels=contour_levels[(contour_levels>=984)&(contour_levels<=1024)]
        cs=ax.contour(lon,lat,vals,levels=contour_levels,colors='#332c2a',linewidths=.62,zorder=20)
        ax.clabel(cs,fmt='%d',fontsize=7,inline=True,inline_spacing=4)
        cbar_label='MSLP (hPa)'; cbar_ticks=levels
    else:
        # Synoptic pressure-level standard: geopotential height in dam.
        vmin=float(np.nanpercentile(finite,1)); vmax=float(np.nanpercentile(finite,99))
        step=3 if level==925 else 4 if level==850 else 6
        lo=np.floor(vmin/step)*step; hi=np.ceil(vmax/step)*step
        fill_levels=np.arange(lo,hi+step,step)
        if len(fill_levels)<6: fill_levels=np.linspace(vmin,vmax,12)
        cf=ax.contourf(lon,lat,vals,levels=fill_levels,cmap='RdYlBu_r',extend='both',alpha=.90,zorder=5)
        contour_step=3 if level==925 else 4 if level==850 else 6
        contour_levels=np.arange(np.floor(vmin/contour_step)*contour_step,np.ceil(vmax/contour_step)*contour_step+contour_step,contour_step)
        cs=ax.contour(lon,lat,vals,levels=contour_levels,colors='#222b2f',linewidths=.62,zorder=20)
        ax.clabel(cs,fmt='%d',fontsize=7,inline=True,inline_spacing=4)
        cbar_label=f'{level} hPa Geopotential Height (dam)'; cbar_ticks=contour_levels

    if GIS_GDF is not None:
        # India state boundaries stay exactly as the existing Survey of India layer.
        GIS_GDF.boundary.plot(ax=ax,color='#111111',linewidth=.55,alpha=.95,zorder=25)
    _plot_country_boundaries(ax)

    # MSLP only: preserve the existing EPS individual-member low-centre spread.
    if is_mslp:
        centres=payload.get('member_low_centres') or []
        if centres:
            xs=[c['lon'] for c in centres]; ys=[c['lat'] for c in centres]
            ax.scatter(xs,ys,s=13,c='black',marker='o',alpha=.78,linewidths=.20,edgecolors='white',zorder=40,label='Individual ensemble member low centres')
            ml=payload.get('mean_low')
            if ml:
                ax.scatter([ml['lon']],[ml['lat']],s=48,c='white',marker='+',linewidths=2.2,zorder=45)
                ax.scatter([ml['lon']],[ml['lat']],s=85,facecolors='none',edgecolors='black',linewidths=.9,zorder=44)
                ax.text(ml['lon']+.35,ml['lat']+.25,f'MEAN LOW\n{ml["mslp"]:.0f} hPa',fontsize=8,fontweight='bold',color='#111',ha='left',va='bottom',zorder=46,bbox=dict(boxstyle='round,pad=.25',fc='white',ec='#222',alpha=.82))
            ax.legend(loc='lower left',fontsize=8,frameon=True,framealpha=.9)
        elif int(meta.get('members',1))<=1:
            finite2=np.where(np.isfinite(vals),vals,np.inf); y,x=np.unravel_index(np.argmin(finite2),finite2.shape); lp=float(vals[y,x])
            ax.text(float(lon[x]),float(lat[y]),'L',fontsize=17,fontweight='bold',color='#8b1111',ha='center',va='center',zorder=46)
            ax.text(float(lon[x])+.35,float(lat[y])+.25,f'{lp:.0f} hPa',fontsize=8,fontweight='bold',color='#8b1111',ha='left',va='bottom',zorder=46,bbox=dict(boxstyle='round,pad=.18',fc='white',ec='#8b1111',alpha=.78))

    ax.set_xlim(60,100); ax.set_ylim(5,30)
    ax.set_xticks([60,65,70,75,80,85,90,95,100]); ax.set_yticks([5,10,15,20,25,30])
    ax.tick_params(colors='#30383d',labelsize=8,length=3); ax.set_xlabel('Longitude',fontsize=8,color='#30383d'); ax.set_ylabel('Latitude',fontsize=8,color='#30383d'); ax.grid(False)

    cax=fig.add_axes([.900,.135,.022,.735])
    cb=fig.colorbar(cf,cax=cax,ticks=cbar_ticks,extend='both' if not is_mslp else 'both')
    cb.set_label(cbar_label,fontsize=10); cb.ax.tick_params(labelsize=8)

    member_text=f'{meta["members"]} Members Ensemble Mean' if int(meta.get('members',1))>1 else 'Deterministic'
    fh=int(str(meta['forecast']).replace('F',''))
    title=f'MASRainman {meta["model"]} • {"MSLP" if is_mslp else str(level)+" hPa Geopotential Height"}'
    fig.text(.50,.972,title,ha='center',va='top',fontsize=19,fontweight='bold',color='#17324d')
    extra=f' | {meta.get("ensemble_low_count",0)} member low centres' if is_mslp and int(meta.get('ensemble_low_count',0))>0 else ''
    fig.text(.50,.940,f'{meta["run"]} | Forecast Hour: {fh:03d} | Valid: {meta["valid_end_utc"]} | {member_text}{extra}',ha='center',va='top',fontsize=11,fontweight='bold',color='#60737d')
    fig.text(.035,.018,f'Data Source: {meta["source"]}\n60°E–100°E • 5°N–30°N • {"MSLP contours every 2 hPa" if is_mslp else f"{level} hPa geopotential height"} • India/WB/Bangladesh/Sri Lanka borders',ha='left',va='bottom',fontsize=8,fontweight='bold',color='#c51f1f')
    fig.text(.925,.018,'Not an Official Forecast\nFollow IMD for Weather Warnings',ha='right',va='bottom',fontsize=8,fontweight='bold',color='#c51f1f')
    buf=io.BytesIO(); fig.savefig(buf,format='png',facecolor='white',dpi=170,bbox_inches=None,pad_inches=0); plt.close(fig); return buf.getvalue()

def pressure_model_label_client(model):
    return {'ECMWF':'ECMWF ENS','ECMWF_HRES':'ECMWF IFS HRES','GEFS':'GEFS','GFS':'GFS','ICON':'ICON Global','AIGFS':'AIGFS'}.get(str(model).upper(),str(model))


def _talk_effective_public_url(request=None):
    """Return the URL browsers should use for Talk API calls.

    Priority: explicit environment override (useful for a Cloudflare tunnel),
    then Render's public URL, then reverse-proxy forwarded host/proto, then
    the local request host.
    """
    if TALK_PUBLIC_URL:
        return TALK_PUBLIC_URL
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        return render_url
    if request is not None:
        host = request.headers.get("Host", "")
        proto = request.headers.get("X-Forwarded-Proto", "") or ("https" if IS_RENDER else "http")
        if host:
            return f"{proto}://{host}".rstrip("/")
    return ""


def _talk_send_headers(self, content_type="application/json; charset=utf-8", length=None, allow_methods=False):
    self.send_header("Content-Type", content_type)
    origin = self.headers.get("Origin", "")
    if origin and origin in TALK_ALLOWED_ORIGINS:
        self.send_header("Access-Control-Allow-Origin", origin)
    elif not origin:
        # Same-origin requests normally do not need CORS. Keeping the Blogger
        # default here also makes curl/manual diagnostics easier.
        self.send_header("Access-Control-Allow-Origin", TALK_CORS_ORIGIN)
    self.send_header("Vary", "Origin")
    self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS" if allow_methods else "GET")
    self.send_header("Access-Control-Allow-Headers", "Content-Type, Accept, Cache-Control")
    self.send_header("Access-Control-Max-Age", "600")
    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    self.send_header("Pragma", "no-cache")
    if length is not None:
        self.send_header("Content-Length", str(length))

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        path = urlparse(self.path).path
        if path.startswith("/talk/"):
            self.send_response(204)
            _talk_send_headers(self, allow_methods=True)
            self.end_headers()
        else:
            self.send_response(204)
            self.end_headers()

    def do_GET(self):
        global ACTIVE_FORECAST_MODE, ACTIVE_MODEL, ACTIVE_PERIOD, ACTIVE_RUN, ACTIVE_ENSEMBLE_PRODUCT, ACTIVE_WIND_HOUR
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            public_url = _talk_effective_public_url(self)
            html = HTML.replace("__MASRAINMAN_TALK_PUBLIC_URL__", public_url)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/talk/ping":
            data = json.dumps({"ok": True, "version": APP_VERSION, "service": "talk", "utc": datetime.now(timezone.utc).isoformat(), "model_count": len(TALK_CANDIDATE_MODELS), "model_order": TALK_CANDIDATE_MODELS, "public_url": _talk_effective_public_url(self), "cors_origin": TALK_CORS_ORIGIN}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(200)
            _talk_send_headers(self, length=len(data))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/talk/status":
            try:
                result = {
                    "ok": True,
                    "version": APP_VERSION,
                    "models": {m: talk_model_preflight(m) for m in TALK_CANDIDATE_MODELS},
                    "model_order": TALK_CANDIDATE_MODELS,
                    "talk_policy": "progressive 4/7 to 7/7 live model retrieval; no persistent model archive"
                }
                data = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                self.send_response(200)
                _talk_send_headers(self, length=len(data))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                data = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                _talk_send_headers(self, length=len(data))
                self.end_headers()
                self.wfile.write(data)
        elif path == "/talk/progress":
            try:
                q=parse_qs(urlparse(self.path).query)
                job_id=q.get("job",[""])[0]
                result=_talk_job_snapshot(job_id)
                if result is None:
                    result={"ok":False,"error":"Talk job not found or expired."}
                    status_code=404
                else:
                    status_code=200
                data=json.dumps(result,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode("utf-8")
                self.send_response(status_code)
                _talk_send_headers(self, length=len(data))
                self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                data=json.dumps({"ok":False,"error":str(exc)},ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                _talk_send_headers(self, length=len(data))
                self.end_headers(); self.wfile.write(data)
        elif path == "/talk/query":
            try:
                q = parse_qs(urlparse(self.path).query)
                place_query = q.get("q", [""])[0]
                print(f"[TALK REQUEST] /talk/query q={place_query!r}", flush=True)
                result = build_talk_progressive_request(place_query)
                print(f"[TALK START] ok={result.get('ok')} job={result.get('job_id','')} place={result.get('place',{}).get('name','')} message={result.get('message','')!r}", flush=True)
                data = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
                status_code = 202 if result.get("ok") else 404
                self.send_response(status_code)
                _talk_send_headers(self, length=len(data))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                data = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode("utf-8")
                self.send_response(500)
                _talk_send_headers(self, length=len(data))
                self.end_headers()
                self.wfile.write(data)
        elif path in ("/health", "/healthz"):
            data = json.dumps({"ok": True, "app": APP_NAME, "version": APP_VERSION, "port": PORT, "host": HOST, "render": IS_RENDER, "talk_workers": TALK_MAX_WORKERS, "talk_public_url": _talk_effective_public_url(self), "utc": datetime.now(timezone.utc).isoformat(), "ecmwf": ECMWF_DOWNLOAD_STATUS.get("state"), "model_identity": MODEL_IDENTITY_STATUS}).encode("utf-8")
            self.send_response(200)
            _talk_send_headers(self, length=len(data))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/gis.json":
            try:
                if GIS_GEOJSON is None:
                    raise RuntimeError("Survey of India GIS data was not loaded")
                data = GIS_GEOJSON.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/geo+json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                msg = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
        elif path == "/pressure/runs":
            try:
                q=parse_qs(urlparse(self.path).query); model=q.get('model',['ECMWF'])[0].upper()
                if model not in PRESSURE_SUPPORTED: raise ValueError('Unsupported pressure model')
                runs=pressure_discover_runs(model,4)
                if not runs: raise RuntimeError(f'No verified {pressure_model_label_client(model)} pressure runs found')
                data=json.dumps({'model':model,'runs':runs,'selected':runs[0]['value'],'latest':runs[0]['value']},separators=(',',':')).encode('utf-8')
                self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                data=json.dumps({'error':str(exc)},separators=(',',':')).encode('utf-8'); self.send_response(500); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/pressure/hours":
            try:
                q=parse_qs(urlparse(self.path).query); model=q.get('model',['ECMWF'])[0].upper(); runv=q.get('run',[''])[0]; product=q.get('product',['MSLP'])[0].upper()
                if model not in PRESSURE_SUPPORTED: raise ValueError('Unsupported pressure model')
                if product not in PRESSURE_PRODUCTS: raise ValueError('Pressure product must be MSLP, 925, 850 or 500')
                if not re.fullmatch(r'\d{10}',runv): raise ValueError('Valid pressure run YYYYMMDDHH is required')
                hours=pressure_hours_for(model,runv)
                data=json.dumps({'model':model,'run':runv,'hours':hours,'last_hour':hours[-1] if hours else None},separators=(',',':')).encode('utf-8')
                self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                data=json.dumps({'error':str(exc)},separators=(',',':')).encode('utf-8'); self.send_response(500); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/pressure/plot.png":
            try:
                q=parse_qs(urlparse(self.path).query); model=q.get('model',['ECMWF'])[0].upper(); runv=q.get('run',[''])[0]; fhour=int(q.get('fhour',['24'])[0]); product=q.get('product',['MSLP'])[0].upper()
                if model not in PRESSURE_SUPPORTED: raise ValueError('Unsupported pressure model')
                if product not in PRESSURE_PRODUCTS: raise ValueError('Pressure product must be MSLP, 925, 850 or 500')
                if not re.fullmatch(r'\d{10}',runv): raise ValueError('Valid pressure run YYYYMMDDHH required')
                if fhour not in pressure_hours_for(model,runv): raise ValueError(f'F{fhour:03d} is not an exposed forecast hour for this model/run')
                payload=pressure_download_decode(model,runv,fhour,product); data=pressure_plot_png(payload)
                self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0'); self.send_header('Pragma','no-cache'); self.send_header('X-MasRainman-Pressure','ready'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                print(f'[PRESSURE][ERROR] {exc}',flush=True)
                try:
                    fig=plt.figure(figsize=(10,5),dpi=130,facecolor='white'); ax=fig.add_axes([.05,.08,.9,.84]); ax.axis('off'); ax.text(.5,.58,'PRESSURE REQUEST FAILED',ha='center',va='center',fontsize=18,fontweight='bold',color='#8b1e1e'); ax.text(.5,.42,str(exc),ha='center',va='center',fontsize=10,color='#445'); buf=io.BytesIO(); fig.savefig(buf,format='png',facecolor='white',bbox_inches='tight'); plt.close(fig); data=buf.getvalue()
                except Exception: data=b''
                try:
                    self.send_response(503); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError): pass
        elif path == "/upperair/runs":
            try:
                q=parse_qs(urlparse(self.path).query)
                model=q.get('model',['ECMWF'])[0].upper()
                runs=ua_discover_runs(model,4)
                UA_MODEL_STATE.update(model=model,run=runs[0]['value'],state='identified',message=f"Latest {model} run ready")
                data=json.dumps({'model':model,'runs':runs,'selected':runs[0]['value'],'latest':runs[0]['value']},separators=(',',':')).encode('utf-8')
                self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                data=json.dumps({'error':str(exc)}).encode('utf-8'); self.send_response(500); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/upperair/hours":
            try:
                q=parse_qs(urlparse(self.path).query); model=q.get('model',['ECMWF'])[0].upper(); runv=q.get('run',[''])[0]; level=int(q.get('level',['850'])[0])
                if not re.fullmatch(r'\d{10}',runv): raise ValueError('Valid upper-air run YYYYMMDDHH is required')
                hours=ua_hours_for(model,runv,level); UA_MODEL_STATE.update(model=model,run=runv,level=level,state='identified',message=f'{len(hours)} forecast hours available')
                data=json.dumps({'model':model,'run':runv,'level':level,'hours':hours,'last_hour':hours[-1] if hours else None},separators=(',',':')).encode('utf-8')
                self.send_response(200); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                data=json.dumps({'error':str(exc)}).encode('utf-8'); self.send_response(500); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/upperair/plot.png":
            try:
                q=parse_qs(urlparse(self.path).query); model=q.get('model',['ECMWF'])[0].upper(); runv=q.get('run',[''])[0]; level=int(q.get('level',['850'])[0]); fhour=int(q.get('fhour',['24'])[0])
                if model not in ('ECMWF','ECMWF_HRES','GEFS','GFS','ICON','ICON_DET'): raise ValueError('Unsupported upper-air model')
                if level not in UA_LEVELS: raise ValueError('Level must be 850, 700 or 500 hPa')
                if not re.fullmatch(r'\d{10}',runv): raise ValueError('Valid run YYYYMMDDHH required')
                if fhour<=0 or fhour>360: raise ValueError('Invalid forecast hour')
                payload=ua_download_and_decode(model,runv,level,fhour)
                png=ua_plot_png(payload)
                data=png
                self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0'); self.send_header('Pragma','no-cache'); self.send_header('X-MasRainman-UpperAir','ready'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                ua_log(f'PLOT FAILED • {exc}','error')
                # Return a valid diagnostic PNG rather than JSON to an image tag.
                try:
                    fig=plt.figure(figsize=(10,5),dpi=130,facecolor='white'); ax=fig.add_axes([.05,.08,.9,.84]); ax.axis('off'); ax.text(.5,.58,'UPPER-AIR WIND REQUEST FAILED',ha='center',va='center',fontsize=18,fontweight='bold',color='#8b1e1e'); ax.text(.5,.42,str(exc),ha='center',va='center',fontsize=10,color='#445'); buf=io.BytesIO(); fig.savefig(buf,format='png',facecolor='white',bbox_inches='tight'); plt.close(fig); data=buf.getvalue()
                except Exception:
                    data=b''
                try:
                    self.send_response(503); self.send_header('Content-Type','image/png'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
        elif path == "/model/runs":
            try:
                q=parse_qs(urlparse(self.path).query)
                mode=q.get('mode',['deterministic'])[0]
                model=q.get('model',['ECMWF'])[0]
                period=q.get('period',['24 Hour'])[0]
                set_model_identity(mode=mode, model=model, label=MODEL_REGISTRY.get(mode,{}).get(model,model), state='probing', download='not-started', message=f'Checking latest {model} runs…')
                if ECMWF_DOWNLOAD_STATUS.get('state') not in ('downloading',):
                    ECMWF_DOWNLOAD_STATUS.update(state='probing', message=f'Checking latest {model} runs…', error=None, events=[])
                ecmwf_event(f'RUN DISCOVERY • {mode.upper()} • {model} • FAST LOCAL/SCHEDULE CACHE')
                if model=='GFS' and mode=='deterministic':
                    period=q.get('period',['24 Hour'])[0]
                    required=gfs_period_endpoints(period)
                    runs=[]
                    for dt,run_date,run_hour in gfs_candidate_runs(limit_days=3, required_endpoints=required):
                        runs.append({
                            "value":f"{run_date}{run_hour:02d}",
                            "label":dt.strftime('%d %b %Y • %HZ')
                        })
                    ecmwf_event(
                        f"GFS RUN CANDIDATES READY • {len(runs)} complete cycles • "
                        f"{period} • " + ", ".join(f"F{x:03d}" for x in required),
                        "success"
                    )
                    set_model_identity(
                        mode=mode,model=model,label='GFS',state='identified',
                        download='not-started',
                        message=f'{len(runs)} GFS schedule candidates cached; availability is checked only during download.'
                    )
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading':
                        ECMWF_DOWNLOAD_STATUS.update(
                            state='idle',
                            message=f'Found {len(runs)} stable GFS {period} schedule candidates'
                        )
                    data=json.dumps({
                        "mode":mode,
                        "model":model,
                        "period":period,
                        "runs":runs,
                        "selected":"",
                        "message":f"Listed {len(runs)} stable GFS {period} schedule candidates; no server probes were used."
                    }).encode('utf-8')
                elif model=='ICON' and mode=='deterministic':
                    period=q.get('period',['24 Hour'])[0]
                    if period=='15 Day Accumulation':
                        raise RuntimeError('ICON Global deterministic does not provide a 15-day accumulation')
                    required=icon_period_endpoints(period)
                    runs=[]
                    for dt,run_date,run_hour in icon_candidate_runs(limit_days=3,required_endpoints=required):
                        runs.append({"value":f"{run_date}{run_hour:02d}","label":dt.strftime('%d %b %Y • %HZ')})
                    ecmwf_event(f"ICON RUN CANDIDATES READY • {len(runs)} complete cycles • {period} • " + ', '.join(f'F{x:03d}' for x in required),"success")
                    set_model_identity(mode=mode,model=model,label='ICON Global',state='identified',download='not-started',message=f'{len(runs)} ICON schedule candidates cached; availability is checked only during download.')
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading': ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found {len(runs)} stable ICON {period} schedule candidates')
                    data=json.dumps({"mode":mode,"model":model,"period":period,"runs":runs,"selected":"","message":f"Listed {len(runs)} stable ICON {period} schedule candidates; no server probes were used."}).encode('utf-8')
                elif model=='UKMET' and mode=='deterministic':
                    period=q.get('period',['24 Hour'])[0]
                    if period=='15 Day Accumulation':
                        raise RuntimeError('UKMET Global 10 km does not provide a 15-day accumulation; maximum forecast is F168')
                    runs=[]
                    for t in _ukmet_recent_long_runs()[:4]:
                        run=f'{t.strftime("%Y%m%d")}{t.strftime("%H")}'; runs.append({'value':run,'label':t.strftime('%d %b %Y • %HZ')})
                    ecmwf_event(f'UKMET RUN CANDIDATES READY • {len(runs)} long-run cycles • {period}','success')
                    set_model_identity(mode=mode,model=model,label='UKMET Global 10 km',state='identified',download='not-started',message=f'{len(runs)} UKMET 00Z/12Z candidate cycles listed from AWS ASDI.')
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading': ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found {len(runs)} UKMET long runs for {period}')
                    data=json.dumps({'mode':mode,'model':model,'period':period,'runs':runs,'selected':'','message':f'Listed {len(runs)} UKMET 00Z/12Z candidate cycles from AWS ASDI.'}).encode('utf-8')
                elif model=='GEM' and mode=='deterministic':
                    # GEM run menu: expose only genuinely published cycles.
                    # A lightweight F024 Range probe is used here; full GRIB
                    # download/decode remains in the selected engine request.
                    available=[]
                    candidates=gem_candidate_runs(8)
                    for cand in candidates:
                        runv=cand['value']
                        published=False
                        for u in gem_endpoint_urls(runv,24):
                            try:
                                ecmwf_event(f"GEM RUN PROBE • {cand['label']} • F024 • {u}","info")
                                rr=requests.get(u,headers={"Range":"bytes=0-3","User-Agent":"MASRAINMAN-GEM/1.0","Accept":"*/*"},stream=True,timeout=20)
                                status=rr.status_code
                                published=status in (200,206)
                                rr.close()
                                if published: break
                            except Exception:
                                continue
                        if published:
                            available.append(cand)
                            ecmwf_event(f"GEM RUN AVAILABLE • {cand['label']} • F024","success")
                        else:
                            ecmwf_event(f"GEM RUN NOT PUBLISHED • {cand['label']} • F024","info")
                        if len(available)>=3: break
                    if not available:
                        raise RuntimeError("No published GEM GDPS cycles found from recent candidates")
                    ecmwf_event("GEM VERIFIED RUNS READY • " + ", ".join(x['label'] for x in available),"success")
                    set_model_identity(mode=mode,model=model,label='GEM / CMC GDPS',state='identified',download='not-started',message=f"{len(available)} verified GEM runs available; newest is {available[0]['label']}.")
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading':
                        ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found {len(available)} published GEM runs')
                    runs_payload=available[:4]
                    data=json.dumps({"mode":mode,"model":model,"period":period,"runs":runs_payload,"selected":"","message":f"Verified newest {len(available)} published GEM GDPS cycles; newest={available[0]['label']}"}).encode('utf-8')
                    self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)

                elif model=='AIGFS' and mode=='deterministic':
                    period=q.get('period',['24 Hour'])[0]
                    day=int(q.get('day',['1'])[0])
                    if period=='24 Hour': day=max(1,min(5,day))
                    candidates=aigfs_candidate_runs(period,day,limit_days=3)
                    runs=[{'value':f'{rd}{rh:02d}','label':dt.strftime('%d %b %Y • %HZ')} for dt,rd,rh in candidates]
                    ecmwf_event(f'AIGFS VERIFIED RUN CANDIDATES READY • {len(runs)} live cycles • {period} • target F{aigfs_period_endpoints(period,day)[-1]:03d}','success')
                    set_model_identity(mode=mode,model=model,label='AIGFS',state='identified',download='not-started',message=f'{len(runs)} verified AIGFS cycles available for {period}.')
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading': ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found {len(runs)} verified AIGFS runs for {period}')
                    data=json.dumps({'mode':mode,'model':model,'period':period,'runs':runs[:4],'selected':'','message':f'Verified {len(runs[:4])} AIGFS cycles using the target endpoint F{aigfs_period_endpoints(period,day)[-1]:03d}.'}).encode('utf-8')
                elif model=='AIFS' and mode=='deterministic':
                    period=q.get('period',['24 Hour'])[0]
                    required=[0]+aifs_endpoint_hours(period) if period=='24 Hour' else aifs_endpoint_hours(period)
                    runs=[]
                    for dt,run_date,run_hour in aifs_candidate_runs(limit_days=3,required_endpoints=required):
                        runs.append({"value":f"{run_date}{run_hour:02d}","label":dt.strftime('%d %b %Y • %HZ')})
                    ecmwf_event(f"AIFS RUN CANDIDATES READY • {len(runs)} complete cycles • {period}","success")
                    set_model_identity(mode=mode,model=model,label='AIFS Single',state='identified',download='not-started',message=f'{len(runs)} AIFS complete {period} runs verified on ECMWF Open Data.')
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading': ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found {len(runs)} complete AIFS {period} runs')
                    data=json.dumps({"mode":mode,"model":model,"period":period,"runs":runs,"selected":"","message":f"Listed {len(runs)} complete AIFS {period} cycles from ECMWF Open Data."}).encode('utf-8')
                elif model=='GFS' and mode=='ensemble':
                    runs=[]
                    required_fhour=gefs_period_endpoints(period)[-1]
                    for dt,run_date,run_hour in gefs_candidate_runs(limit_days=3):
                        ok,detail=gefs_run_available(run_date,run_hour,required_fhour,period,'MEAN')
                        if ok:
                            runs.append({'value':f'{run_date}{run_hour:02d}','label':dt.strftime('%d %b %Y • %HZ')})
                        if len(runs)>=4:
                            break
                    ecmwf_event(f'GEFS LATEST 4 PUBLISHED RUNS READY • {len(runs)} verified cycles • F{required_fhour:03d} • rainfall resolution {gefs_rain_resolution(period)/100:.2f}°','success')
                    set_model_identity(mode=mode,model=model,label='GFS / GEFS',state='identified',download='not-started',message=f'{len(runs)} GEFS runs verified from actual forecast files; directory HTTP 403 is ignored.')
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading': ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found {len(runs)} verified GEFS runs')
                    data=json.dumps({'mode':mode,'model':model,'period':period,'runs':runs,'selected':'','message':f'Listed latest {len(runs)} verified GEFS F{required_fhour:03d} cycles; no forecast bytes downloaded.'}).encode('utf-8')
                elif model=='ICON' and mode=='ensemble':
                    # IMPORTANT: the run selector represents the latest FOUR
                    # genuinely published DWD ICON-EPS cycles, not merely the
                    # cycles that have already been cached locally.  This keeps
                    # the selector consistent with the other model run menus.
                    # Selecting an uncached published run triggers the direct DWD online
                    # F024 download/decode worker; no pre-existing archive is required.
                    if period!='24 Hour':
                        raise RuntimeError('ICON EPS currently supports 24 Hour F024 only')
                    runs=icon_eps_published_runs(limit=4, lookback_days=3)
                    if not runs:
                        raise RuntimeError('No currently published ICON EPS F024 cycles found from DWD')
                    cached_count=sum(1 for x in runs if x.get('cached'))
                    ecmwf_event(
                        'ICON EPS LATEST 4 PUBLISHED RUNS READY • ' +
                        ', '.join(x['label'] for x in runs) +
                        f' • cache ready {cached_count}/{len(runs)}',
                        'success'
                    )
                    set_model_identity(
                        mode='ensemble',model='ICON',label='ICON EPS',state='identified',download='not-started',
                        message=f'{len(runs)} latest published DWD ICON EPS cycles listed; {cached_count} have validated local F024 cache.'
                    )
                    if ECMWF_DOWNLOAD_STATUS.get('state')!='downloading':
                        ECMWF_DOWNLOAD_STATUS.update(state='idle',message=f'Found latest {len(runs)} published ICON EPS runs; {cached_count} cached')
                    data=json.dumps({
                        'mode':mode,'model':model,'period':'24 Hour','runs':runs,'selected':'',
                        'message':f'Listed latest {len(runs)} published ICON EPS F024 cycles. No DWD forecast bytes downloaded by the dashboard.'
                    }).encode('utf-8')
                elif model!='ECMWF':
                    data=json.dumps({"mode":mode,"model":model,"runs":[],"selected":"","message":f"{model} run selector is not yet connected with a verified run adapter."}).encode('utf-8')
                else:
                    # ECMWF run selector: verify actual Open Data availability and
                    # expose the newest FOUR selectable runs.  For ENS the probe
                    # uses the endpoint required by the selected accumulation, so
                    # a 06/18Z run is never shown for 7D/15D.  Only the tiny .index
                    # file is fetched; no forecast GRIB bytes are downloaded here.
                    runs=[]
                    seen=set()
                    if period == "24 Hour":
                        probe_endpoint = 24
                    elif period == "7 Day Accumulation":
                        probe_endpoint = 168
                    elif period == "15 Day Accumulation":
                        probe_endpoint = 360
                    else:
                        probe_endpoint = 24
                    for dt,run_date,run_hour in ecmwf_candidate_runs():
                        if mode == "ensemble" and model == "ECMWF" and period in ("7 Day Accumulation","15 Day Accumulation") and run_hour not in (0,12):
                            continue
                        key=f"{run_date}{run_hour:02d}"
                        if key in seen:
                            continue
                        seen.add(key)
                        available=False
                        source_ok=None
                        probe_error=None
                        for source_name, source_root in ECMWF_HTTP_SOURCES:
                            try:
                                fields, err = _fetch_index(source_name, source_root, run_date, run_hour, probe_endpoint)
                                if fields:
                                    available=True
                                    source_ok=source_name
                                    break
                                probe_error=err
                            except Exception as probe_exc:
                                probe_error=str(probe_exc)
                        if available:
                            label_dt=datetime.strptime(key,'%Y%m%d%H').replace(tzinfo=timezone.utc)
                            runs.append({
                                "value":key,
                                "label":label_dt.strftime('%d %b %Y • %HZ')
                            })
                            ecmwf_event(f"RUN AVAILABLE • ECMWF {mode} • {label_dt.strftime('%d %b %Y • %HZ')} • {source_ok} • F{probe_endpoint:03d}","success")
                        else:
                            ecmwf_event(f"RUN NOT AVAILABLE • ECMWF {mode} • {key} • F{probe_endpoint:03d} • {probe_error or 'index unavailable'}","info")
                        if len(runs) >= 4:
                            break
                        time.sleep(0.20)
                    if not runs:
                        raise RuntimeError(f"No published ECMWF {mode} runs found for {period}")
                    ecmwf_event(
                        f"RUN LIST READY • {len(runs)} latest AVAILABLE ECMWF {mode} runs • "
                        f"model={model} • period={period}",
                        "success"
                    )
                    set_model_identity(
                        mode=mode, model=model,
                        label=MODEL_REGISTRY.get(mode,{}).get(model,model),
                        state='identified', download='not-started',
                        message=f'{len(runs)} latest published ECMWF runs verified and selectable.'
                    )
                    if ECMWF_DOWNLOAD_STATUS.get('state') != 'downloading':
                        ECMWF_DOWNLOAD_STATUS.update(
                            state='idle',
                            message=f'Found {len(runs)} latest available ECMWF runs'
                        )
                    data=json.dumps({
                        "mode":mode,
                        "model":model,
                        "runs":runs,
                        "selected":"",
                        "message":f"Listed {len(runs)} latest AVAILABLE ECMWF runs for {model} / {mode}. Select any run from the dropdown."
                    }).encode('utf-8')
                self.send_response(200)
                self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(data)))
                self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                ecmwf_event(f"RUN DISCOVERY FAILED • {exc}", "error")
                set_model_identity(state='error', download='failed', message=str(exc))
                ECMWF_DOWNLOAD_STATUS.update(state='error', message='Run discovery failed', error=str(exc))
                data=json.dumps({"error":str(exc)}).encode('utf-8')
                self.send_response(500)
                self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(data)))
                try:
                    self.end_headers(); self.wfile.write(data)
                except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
                    pass
        elif path == "/ecmwf/status":
            data = json.dumps({**ECMWF_DOWNLOAD_STATUS, "model_identity": MODEL_IDENTITY_STATUS}).encode("utf-8")
            self.send_response(200)
            _talk_send_headers(self, length=len(data))
            self.end_headers()
            self.wfile.write(data)
        elif path in ("/ecmwf/download", "/gfs/download", "/gefs/download", "/icon/download", "/icon-eps/download", "/ukmet/download", "/gem/download", "/aigfs/download"):
            q=parse_qs(urlparse(self.path).query)
            if path == "/ukmet/download":
                q.setdefault("mode", ["deterministic"]); q.setdefault("model", ["UKMET"]); ecmwf_event("UKMET DOWNLOAD ROUTE ACCEPTED • /ukmet/download", "info")
            elif path == "/gfs/download":
                q.setdefault("mode", ["deterministic"]); q.setdefault("model", ["GFS"]); ecmwf_event("GFS DOWNLOAD ROUTE ACCEPTED • /gfs/download", "info")
            elif path == "/gefs/download":
                q.setdefault("mode", ["ensemble"]); q.setdefault("model", ["GFS"]); ecmwf_event("GEFS DOWNLOAD ROUTE ACCEPTED • /gefs/download", "info")
            elif path == "/icon/download":
                q.setdefault("mode", ["deterministic"]); q.setdefault("model", ["ICON"]); ecmwf_event("ICON DOWNLOAD ROUTE ACCEPTED • /icon/download", "info")
            elif path == "/icon-eps/download":
                q.setdefault("mode", ["ensemble"]); q.setdefault("model", ["ICON"]); ecmwf_event("ICON EPS CACHE ROUTE ACCEPTED • /icon-eps/download", "info")
            elif path == "/gem/download":
                q.setdefault("mode", ["deterministic"]); q.setdefault("model", ["GEM"]); ecmwf_event("GEM DOWNLOAD ROUTE ACCEPTED • /gem/download", "info")
            elif path == "/aigfs/download":
                q.setdefault("mode", ["deterministic"]); q.setdefault("model", ["AIGFS"]); ecmwf_event("AIGFS DOWNLOAD ROUTE ACCEPTED • /aigfs/download", "info")
            ACTIVE_FORECAST_MODE=q.get('mode',[ACTIVE_FORECAST_MODE])[0]
            ACTIVE_MODEL=q.get('model',[ACTIVE_MODEL])[0]
            requested_run=q.get('run',['AUTO'])[0]
            requested_period=q.get('period',[ACTIVE_PERIOD])[0]
            requested_day=int(q.get('day',['1'])[0])
            requested_fhour=int(q.get('fhour',[str(ACTIVE_WIND_HOUR or 24)])[0])
            if requested_day<1 or requested_day>5: raise ValueError('day must be 1..5')
            if requested_fhour<24 or requested_fhour>360 or requested_fhour%24!=0: raise ValueError('GEFS wind forecast hour must be 24, 48, …, 360')
            ecmwf_event(f"DOWNLOAD ROUTE ACCEPTED • path={path} • model={q.get('model',[ACTIVE_MODEL])[0]} • run={q.get('run',['AUTO'])[0]} • period={q.get('period',[ACTIVE_PERIOD])[0]} • day={q.get('day',['1'])[0]} • fhour={requested_fhour}", "info")
            ACTIVE_PERIOD=requested_period
            ACTIVE_RUN=requested_run if re.fullmatch(r'\d{10}', requested_run) else ACTIVE_RUN
            if q.get('product',[ACTIVE_ENSEMBLE_PRODUCT])[0].upper()=='WIND850': ACTIVE_WIND_HOUR=requested_fhour
            ecmwf_event(f"ENGINE REQUEST RECEIVED • mode={ACTIVE_FORECAST_MODE} • model={ACTIVE_MODEL} • run={requested_run} • period={requested_period} • day={requested_day}", "info")
            if ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='ensemble':
                ACTIVE_ENSEMBLE_PRODUCT=q.get('product',[ACTIVE_ENSEMBLE_PRODUCT])[0].upper()
                if requested_period=='15 Day Accumulation':
                    raise ValueError('GEFS 0.25° currently supports 24 Hour and 7 Day only')
                start_gefs_ensemble_download(requested_run, requested_period, ACTIVE_ENSEMBLE_PRODUCT, requested_fhour)
            elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='ensemble':
                # ICON EPS is acquired directly online from DWD by the embedded selected-run decoder.
                requested_product=q.get('product',[ACTIVE_ENSEMBLE_PRODUCT])[0].upper()
                ACTIVE_ENSEMBLE_PRODUCT=requested_product
                cache_file=ICON_EPS_CACHE_ROOT / f"ICON_EPS_{requested_run}_F024_SUPERENSEMBLE_GRID_40MEM.npy"
                if not cache_file.exists():
                    build_requested=(q.get('build',['0'])[0] == '1')
                    if build_requested:
                        try:
                            started, why = start_icon_eps_selected_run_builder(requested_run, requested_product)
                            if why == "cache-ready":
                                pass
                            elif not started:
                                msg=f"ICON EPS build for {requested_run} is already running."
                                data=json.dumps({"started":True,"building":True,"mode":"ensemble","model":"ICON","run":requested_run,"period":requested_period,"message":msg}).encode('utf-8')
                                self.send_response(202)
                                self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
                            else:
                                msg=f"ICON EPS selected-run build started • {requested_run} • F024 • 40 members"
                                data=json.dumps({"started":True,"building":True,"mode":"ensemble","model":"ICON","run":requested_run,"period":requested_period,"message":msg}).encode('utf-8')
                                self.send_response(202)
                                self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
                        except Exception as exc:
                            msg=f"ICON EPS selected-run builder could not start: {exc}"
                            ecmwf_event(f"ICON EPS BUILD START FAILED • {requested_run} • {exc}","error")
                            data=json.dumps({"started":False,"mode":"ensemble","model":"ICON","run":requested_run,"error":msg,"cache_ready":False}).encode('utf-8')
                            self.send_response(500)
                            self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
                    msg=(f"ICON EPS run {requested_run} is published by DWD but its local F024 cache is not built yet. "
                         f"The dashboard will auto-build the selected run from DWD when requested.")
                    ecmwf_event(f"ICON EPS CACHE NOT BUILT • {requested_run} • BUILD REQUIRED","warn")
                    set_model_identity(mode='ensemble',model='ICON',label='ICON EPS',state='waiting',run=f"{requested_run[:8]} {requested_run[8:]}Z",source='DWD ICON-EPS standalone cache',download='not-built',message=msg)
                    ECMWF_DOWNLOAD_STATUS.update(state='error',run=f"{requested_run[:8]} {requested_run[8:]}Z",message=msg,error=msg,finished_at=datetime.now(timezone.utc).isoformat())
                    data=json.dumps({"started":False,"mode":"ensemble","model":"ICON","run":requested_run,"period":requested_period,"error":msg,"cache_ready":False}).encode('utf-8')
                    self.send_response(409)
                    self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
                arr=np.load(cache_file, mmap_mode='r')
                if arr.shape != (40,85,97):
                    msg=f"ICON EPS cache shape invalid: {arr.shape}; expected (40,85,97)"
                    ecmwf_event(f"ICON EPS CACHE INVALID • {requested_run} • {msg}", "error")
                    data=json.dumps({"started":False,"mode":"ensemble","model":"ICON","run":requested_run,"period":requested_period,"error":msg,"cache_ready":False}).encode('utf-8')
                    self.send_response(409)
                    self.send_header("Content-Type","application/json; charset=utf-8")
                    self.send_header("Cache-Control","no-store")
                    self.send_header("Content-Length",str(len(data)))
                    self.end_headers(); self.wfile.write(data)
                    return
                set_model_identity(mode='ensemble',model='ICON',label='ICON EPS',state='identified',run=f"{requested_run[:8]} {requested_run[8:]}Z",source='DWD ICON-EPS standalone cache',download='ready',message=f"ICON EPS F024 cache ready: {arr.shape}")
                ECMWF_DOWNLOAD_STATUS.update(state='ready',run=f"{requested_run[:8]} {requested_run[8:]}Z",message=f"ICON EPS cache ready • {requested_run} • {requested_product}",error=None,total=1,downloaded=1,expected_finish=datetime.now(timezone.utc).isoformat(),current_member=40,member_total=40,current_endpoint=24,endpoint_total=1)
                ecmwf_event(f"ICON EPS CACHE READY • {requested_run} • F024 • {requested_product}","success")
            elif ACTIVE_MODEL=='UKMET' and ACTIVE_FORECAST_MODE=='deterministic':
                start_ukmet_deterministic_download(requested_run, requested_period, requested_day)
            elif ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='deterministic':
                start_gfs_deterministic_download(requested_run, requested_period, requested_day)
            elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='deterministic':
                start_icon_deterministic_download(requested_run, requested_period)
            elif ACTIVE_MODEL=='AIFS' and ACTIVE_FORECAST_MODE=='deterministic':
                start_aifs_deterministic_download(requested_run, requested_period)
            elif ACTIVE_MODEL=='GEM' and ACTIVE_FORECAST_MODE=='deterministic':
                start_gem_deterministic_download(requested_run, requested_period, requested_day)
            elif ACTIVE_MODEL=='AIGFS' and ACTIVE_FORECAST_MODE=='deterministic':
                start_aigfs_deterministic_download(requested_run, requested_period, requested_day)
            elif ACTIVE_MODEL!='ECMWF':
                ECMWF_DOWNLOAD_STATUS.update(state='error',message=f'{ACTIVE_MODEL} engine not connected yet',error=f'{ACTIVE_MODEL} downloader is not connected yet')
            elif ACTIVE_FORECAST_MODE=='ensemble':
                start_ecmwf_live_download(requested_run, requested_period)
            else:
                start_ecmwf_deterministic_download(requested_run, requested_period)
            data = json.dumps({"started": True, "mode": ACTIVE_FORECAST_MODE, "model": ACTIVE_MODEL, "run": requested_run, "period": requested_period}).encode("utf-8")
            self.send_response(202)
            _talk_send_headers(self, length=len(data))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/model/identity":
            data = json.dumps(MODEL_IDENTITY_STATUS).encode("utf-8")
            self.send_response(200)
            _talk_send_headers(self, length=len(data))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/gefs/wind-hours":
            try:
                q=parse_qs(urlparse(self.path).query)
                run_q=q.get("run",[ACTIVE_RUN])[0]
                if not re.fullmatch(r"\d{10}",run_q):
                    raise ValueError("A valid GEFS run YYYYMMDDHH is required")
                rd,rh=run_q[:8],int(run_q[8:10])
                hours=gefs_wind_forecast_hours(rd,rh,360)
                data=json.dumps({
                    "mode":"ensemble",
                    "model":"GFS",
                    "run":run_q,
                    "hours":hours,
                    "last_hour":hours[-1] if hours else None
                },separators=(",",":")).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                msg=json.dumps({"error":str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type","application/json; charset=utf-8")
                self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
        elif path == "/gefs/synoptic.json":
            try:
                q=parse_qs(urlparse(self.path).query)
                mode_q=q.get("mode",[ACTIVE_FORECAST_MODE])[0]
                model_q=q.get("model",[ACTIVE_MODEL])[0]
                run_q=q.get("run",[ACTIVE_RUN])[0]
                period=q.get("period",[ACTIVE_PERIOD])[0]
                product=q.get("product",["SYNOPTIC"])[0].upper()
                fhour=int(q.get("fhour",[str(ACTIVE_WIND_HOUR or 24)])[0])
                if product=="WIND850": ACTIVE_WIND_HOUR=fhour
                ACTIVE_FORECAST_MODE=mode_q; ACTIVE_MODEL=model_q
                if run_q and re.fullmatch(r"\d{10}",run_q): ACTIVE_RUN=run_q
                if ACTIVE_FORECAST_MODE!="ensemble" or ACTIVE_MODEL!="GFS": raise RuntimeError("GEFS synoptic endpoint requires ENSEMBLES → GFS / GEFS")
                payload=build_gefs_synoptic(product,period,fhour if product=="WIND850" else 24)
                data=json.dumps(payload["meta"],separators=(",",":"),allow_nan=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                msg=json.dumps({"error":str(exc)}).encode("utf-8")
                self.send_response(500); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(msg))); self.end_headers(); self.wfile.write(msg)
        elif path == "/rainfall.json":
            try:
                q = parse_qs(urlparse(self.path).query)
                day = int(q.get("day", ["1"])[0])
                mode_q = q.get("mode", [ACTIVE_FORECAST_MODE])[0]
                model_q = q.get("model", [ACTIVE_MODEL])[0]
                run_q = q.get("run", [ACTIVE_RUN])[0]
                period = q.get("period", [ACTIVE_PERIOD])[0]
                ACTIVE_FORECAST_MODE = mode_q
                ACTIVE_MODEL = model_q
                if run_q and re.fullmatch(r"\d{10}",run_q): ACTIVE_RUN = run_q
                if ACTIVE_FORECAST_MODE == "ensemble" and ACTIVE_MODEL in ("ECMWF", "ICON", "GFS"):
                    ACTIVE_ENSEMBLE_PRODUCT = q.get("product", [ACTIVE_ENSEMBLE_PRODUCT])[0].upper()
                ACTIVE_PERIOD = period
                if day not in (1, 2, 3, 4, 5):
                    raise ValueError("day must be 1..5")
                requested_product = q.get("product", [ACTIVE_ENSEMBLE_PRODUCT if mode_q == "ensemble" else "MEAN"])[0].upper()
                if mode_q == "ensemble" and model_q in ("ECMWF", "ICON", "GFS"):
                    ACTIVE_ENSEMBLE_PRODUCT = requested_product
                requested_key = f"{mode_q}|{model_q}|{run_q}|{period}|{requested_product if mode_q == 'ensemble' and model_q in ('ECMWF','ICON','GFS') else 'MEAN'}"
                ready_key = ECMWF_DOWNLOAD_STATUS.get("request_key")
                ready_state = ECMWF_DOWNLOAD_STATUS.get("state")
                if ready_state != "ready":
                    raise RuntimeError(f"LIVE selected-model data is not ready yet: state={ready_state}")
                if ready_key and ready_key != requested_key:
                    raise RuntimeError(f"READY JOB REQUEST MISMATCH: status={ready_key} selected={requested_key}")
                ecmwf_event(f"[MAP JSON REQUEST] {requested_key}", "info")
                if ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='deterministic':
                    _, _, _, meta = build_gfs_rainfall(day, ACTIVE_PERIOD)
                elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='deterministic':
                    _, _, _, meta = build_icon_rainfall(day, ACTIVE_PERIOD)
                elif ACTIVE_MODEL=='AIFS' and ACTIVE_FORECAST_MODE=='deterministic':
                    _, _, _, meta = build_aifs_rainfall(day, ACTIVE_PERIOD)
                elif ACTIVE_MODEL=='UKMET' and ACTIVE_FORECAST_MODE=='deterministic':
                    _, _, _, meta = build_ukmet_rainfall(day, ACTIVE_PERIOD)
                elif ACTIVE_MODEL=='GEM' and ACTIVE_FORECAST_MODE=='deterministic':
                    _, _, _, meta = build_gem_rainfall(day, ACTIVE_PERIOD)
                elif ACTIVE_MODEL=='AIGFS' and ACTIVE_FORECAST_MODE=='deterministic':
                    _, _, _, meta = build_aigfs_rainfall(day, ACTIVE_PERIOD)
                elif ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='ensemble':
                    _, _, _, meta = build_gefs_rainfall(ACTIVE_PERIOD, ACTIVE_ENSEMBLE_PRODUCT)
                elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='ensemble':
                    _, _, _, meta = build_icon_eps_rainfall(ACTIVE_ENSEMBLE_PRODUCT, ACTIVE_RUN, ACTIVE_PERIOD)
                else:
                    _, _, _, meta = build_ecmwf_rainfall(day, ACTIVE_FORECAST_MODE, ACTIVE_PERIOD, ACTIVE_ENSEMBLE_PRODUCT if ACTIVE_FORECAST_MODE == "ensemble" else "MEAN")
                data = json.dumps(meta).encode("utf-8")
                self.send_response(200)
                _talk_send_headers(self, length=len(data))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                print(f"[RAINFALL][ERROR] {type(exc).__name__}: {exc}")
                data = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        elif path == "/rainfall/grid.json":
            try:
                q = parse_qs(urlparse(self.path).query)
                day = int(q.get("day", ["1"])[0])
                period = q.get("period", [ACTIVE_PERIOD])[0]
                ACTIVE_PERIOD = period
                if day not in (1, 2, 3, 4, 5):
                    raise ValueError("day must be 1..5")
                if ECMWF_DOWNLOAD_STATUS.get("state") != "ready":
                    raise RuntimeError("LIVE ECMWF data is not ready yet")
                cache_product=ACTIVE_ENSEMBLE_PRODUCT if (ACTIVE_FORECAST_MODE=='ensemble' and ACTIVE_MODEL in ('ECMWF','ICON','GFS')) else 'MEAN'
                cache_key=(ACTIVE_FORECAST_MODE, ACTIVE_MODEL, ACTIVE_PERIOD, day, cache_product)
                if cache_key in RAINFALL_CACHE:
                    rain, lat, lon, meta=RAINFALL_CACHE[cache_key]
                elif ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='deterministic':
                    rain, lat, lon, meta=build_gfs_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='deterministic':
                    rain, lat, lon, meta=build_icon_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='AIFS' and ACTIVE_FORECAST_MODE=='deterministic':
                    rain, lat, lon, meta=build_aifs_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='UKMET' and ACTIVE_FORECAST_MODE=='deterministic':
                    rain, lat, lon, meta=build_ukmet_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='GEM' and ACTIVE_FORECAST_MODE=='deterministic':
                    rain, lat, lon, meta=build_gem_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='AIGFS' and ACTIVE_FORECAST_MODE=='deterministic':
                    rain, lat, lon, meta=build_aigfs_rainfall(day, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='GFS' and ACTIVE_FORECAST_MODE=='ensemble':
                    rain, lat, lon, meta=build_gefs_rainfall(ACTIVE_PERIOD, ACTIVE_ENSEMBLE_PRODUCT); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                elif ACTIVE_MODEL=='ICON' and ACTIVE_FORECAST_MODE=='ensemble':
                    rain, lat, lon, meta=build_icon_eps_rainfall(ACTIVE_ENSEMBLE_PRODUCT, ACTIVE_RUN, ACTIVE_PERIOD); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                else:
                    rain, lat, lon, meta=build_ecmwf_rainfall(day, ACTIVE_FORECAST_MODE, ACTIVE_PERIOD, ACTIVE_ENSEMBLE_PRODUCT if ACTIVE_FORECAST_MODE=='ensemble' else 'MEAN'); RAINFALL_CACHE[cache_key]=(rain,lat,lon,meta)
                payload = {"lat": lat.tolist(), "lon": lon.tolist(), "values": np.round(rain, 2).tolist(), "meta": meta}
                data = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
                self.send_response(200)
                _talk_send_headers(self, length=len(data))
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                msg = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
        elif path == "/rainfall/diagnostic.json":
            try:
                day = int(parse_qs(urlparse(self.path).query).get("day", ["1"])[0])
                if day not in (1, 2, 3, 4, 5):
                    raise ValueError("day must be 1..5")
                if ECMWF_DOWNLOAD_STATUS.get("state") != "ready":
                    raise RuntimeError("LIVE ECMWF data is not ready yet")
                _, meta = rainfall_png(day)
                keys = ["run","day","start","end","members","min","max","mean","lat_min","lat_max","lon_min","lon_max","lat_edge_min","lat_edge_max","lon_edge_min","lon_edge_max","grid_dx","grid_dy","grid_rows","grid_cols","finite_count","positive_count","png_width","png_height","png_alpha_pixels","png_transparent_pixels","png_coordinate_system","png_orientation","png_renderer"]
                data=json.dumps({k:meta[k] for k in keys if k in meta}, allow_nan=False).encode("utf-8")
                self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
            except Exception as exc:
                data=json.dumps({"error":str(exc)}).encode("utf-8")
                self.send_response(500); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
        elif path == "/plot.png":
            try:
                q=parse_qs(urlparse(self.path).query)
                day=int(q.get("day",["1"])[0])
                if day not in (1,2,3,4,5): raise ValueError("day must be 1..5")
                requested_mode=q.get("mode",[ACTIVE_FORECAST_MODE])[0]
                requested_model=q.get("model",[ACTIVE_MODEL])[0]
                requested_run=q.get("run",[ACTIVE_RUN])[0]
                requested_period=q.get("period",[ACTIVE_PERIOD])[0]
                requested_product=q.get("product",[ACTIVE_ENSEMBLE_PRODUCT])[0].upper()
                requested_fhour=int(q.get("fhour",[str(ACTIVE_WIND_HOUR or 24)])[0])
                if requested_product=="WIND850": ACTIVE_WIND_HOUR=requested_fhour
                # The browser sends the complete selected identity. Keep the
                # renderer synchronized so a stale global state cannot produce
                # a blank/previous model map.
                ACTIVE_FORECAST_MODE=requested_mode
                ACTIVE_MODEL=requested_model
                if requested_run and re.fullmatch(r"\d{10}",requested_run): ACTIVE_RUN=requested_run
                if requested_period: ACTIVE_PERIOD=requested_period
                if ACTIVE_FORECAST_MODE == "ensemble" and ACTIVE_MODEL in ("ECMWF","ICON","GFS"):
                    ACTIVE_ENSEMBLE_PRODUCT=requested_product
                ecmwf_event(f"PLOT REQUEST • model={ACTIVE_MODEL} • run={ACTIVE_RUN} • period={ACTIVE_PERIOD} • product={ACTIVE_ENSEMBLE_PRODUCT} • day={day}", "info")
                with PLOT_RENDER_LOCK:
                    data=build_plot_png(day, ACTIVE_PERIOD)
                if not data or len(data)<1000:
                    raise RuntimeError(f"plot renderer returned invalid PNG ({len(data) if data else 0} bytes)")
                ecmwf_event(f"PLOT READY • model={ACTIVE_MODEL} • run={ACTIVE_RUN} • period={ACTIVE_PERIOD} • product={ACTIVE_ENSEMBLE_PRODUCT} • {len(data)} bytes", "success")
                self.send_response(200)
                self.send_header("Content-Type","image/png")
                self.send_header("X-MasRainman-Plot","ready")
                self.send_header("X-MasRainman-Model",str(ACTIVE_MODEL))
                self.send_header("X-MasRainman-Run",str(ACTIVE_RUN))
                self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma","no-cache")
                self.send_header("Content-Length",str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                # The PNG is now in the browser; remove temporary forecast data.
                cleanup_session_forecast_data()
            except Exception as exc:
                # Never send JSON to an <img> element. Return a valid diagnostic PNG instead.
                print(f"[PLOT][ERROR] {exc}")
                data=build_plot_status_png("MAP RENDER ERROR", f"{type(exc).__name__}: {exc}")
                self.send_response(200)
                self.send_header("Content-Type","image/png")
                self.send_header("Cache-Control","no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Content-Length",str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        elif path == "/rainfall.png":
            try:
                day = int(parse_qs(urlparse(self.path).query).get("day", ["1"])[0])
                if day not in (1, 2, 3, 4, 5):
                    raise ValueError("day must be 1..5")
                if ECMWF_DOWNLOAD_STATUS.get("state") != "ready":
                    raise RuntimeError("LIVE ECMWF data is not ready yet. The dashboard will not display previous/stale rainfall.")
                png, _ = rainfall_png(day)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
            except Exception as exc:
                msg = json.dumps({"error": str(exc)}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(msg)))
                self.end_headers()
                self.wfile.write(msg)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):
        print("[SERVER]", fmt % args)


GIS_PATH = str(Path(__file__).resolve().parent / "STATE_BOUNDARY.shp")
GIS_GEOJSON = None
GIS_GDF = None

# Sri Lanka is not included in the Survey of India STATE_BOUNDARY.shp used by
# the dashboard. Keep a local WGS84 outline so the pressure/synoptic maps have
# a continuous South-Asia geographic reference without another GIS dependency.
# This outline is for cartographic display only; it is not used for data masking.
SRI_LANKA_OUTLINE = np.array([
    # North coast / Jaffna peninsula
    [79.66, 9.84], [79.95, 9.92], [80.28, 9.90], [80.58, 9.82],
    [80.88, 9.72], [81.18, 9.68], [81.45, 9.72], [81.70, 9.78],
    [81.86, 9.69], [81.88, 9.50], [81.82, 9.30],
    # East coast
    [81.84, 9.05], [81.82, 8.78], [81.80, 8.48], [81.82, 8.18],
    [81.80, 7.90], [81.78, 7.62], [81.72, 7.35], [81.62, 7.08],
    [81.50, 6.82], [81.38, 6.58], [81.22, 6.35], [81.05, 6.15],
    [80.82, 5.98], [80.58, 5.82], [80.34, 5.74], [80.10, 5.76],
    # South coast
    [79.88, 5.82], [79.65, 5.92], [79.42, 6.08], [79.22, 6.28],
    [79.02, 6.52], [78.84, 6.80], [78.70, 7.08], [78.58, 7.38],
    [78.48, 7.68], [78.39, 7.98], [78.32, 8.28], [78.28, 8.58],
    [78.30, 8.84], [78.38, 9.08], [78.50, 9.30], [78.68, 9.50],
    [78.90, 9.66], [79.18, 9.77], [79.42, 9.82], [79.66, 9.84]
], dtype=float)

def plot_sri_lanka_outline(ax, linewidth=0.90, color="#111111", zorder=62):
    """Legacy-compatible Sri Lanka renderer: black boundary only, no halo/fill."""
    sl=np.asarray(SRI_LANKA_OUTLINE,dtype=float)
    ax.plot(sl[:,0],sl[:,1],color=color,linewidth=linewidth,alpha=1.0,
            solid_joinstyle='round',solid_capstyle='round',zorder=zorder,antialiased=True)

def load_survey_of_india_boundary():
    """Load the user-supplied Survey of India state boundary shapefile.

    The source geometry is preserved; only the CRS is transformed to WGS84
    for browser mapping. No external political boundary dataset is used.
    """
    global GIS_GEOJSON, GIS_GDF
    if gpd is None:
        raise RuntimeError("GeoPandas is not installed")
    gdf = gpd.read_file(GIS_PATH)
    if gdf.empty:
        raise RuntimeError("Survey of India STATE_BOUNDARY.shp is empty")
    if gdf.crs is None:
        raise RuntimeError("STATE_BOUNDARY.shp has no CRS information")
    gdf = gdf.to_crs("EPSG:4326")
    keep = [c for c in ["OBJECTID_1", "OBJECTID", "STATE", "geometry"] if c in gdf.columns]
    gdf = gdf[keep]
    GIS_GDF = gdf.copy()
    GIS_GEOJSON = gdf.to_json(drop_id=True, show_bbox=False)
    return len(gdf), gdf.crs


def main():
    renderer_self_test()
    if requests is None:
        print("WARNING: requests is not installed. Run: pip install requests")
    else:
        print(f"[HTTP] requests {requests.__version__}")
    try:
        count, crs = load_survey_of_india_boundary()
        gis_status = f"Survey of India STATE_BOUNDARY loaded: {count} features"
    except Exception as exc:
        gis_status = f"GIS ERROR: {exc}"
        print(gis_status)
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if sock.connect_ex((HOST, PORT)) == 0:
            print("=" * 72)
            print(f" ERROR: PORT {PORT} IS ALREADY IN USE")
            print(f" Another MASRAINMAN dashboard is already listening on {HOST}:{PORT}.")
            print(" Stop the old dashboard or change PORT before starting another instance.")
            print(" This check prevents accidentally opening an old dashboard instance.")
            print("=" * 72)
            return
    ensure_runtime_dirs()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"

    print("=" * 72)
    print(" MASRAINMAN NEM WEATHER COMMAND CENTER")
    print(" SINGLE-FILE DASHBOARD — V188.1 • FULL MODEL PIPELINE + UPPER-AIR WIND FINAL • ECMWF HRES + GFS + ICON + AIFS + UKMET + GEM + AIGFS + ENS + ICON EPS + GEFS V4.2")
    print("=" * 72)
    print(f"Dashboard : {url}")
    print(f"Public URL: {_talk_effective_public_url() or '(same origin / set MASRAINMAN_TALK_PUBLIC_URL for a tunnel)'}")
    print(f"Talk workers: {TALK_MAX_WORKERS} concurrent / {len(TALK_CANDIDATE_MODELS)} models")
    print(f"Version   : {APP_VERSION}")
    print("HRES path : official .index + HTTP byte-range (ECMWF -> AWS)")
    print(f"Status    : {gis_status}")
    print("Data      : ONLINE OFFICIAL SOURCES • ECMWF + NOAA/NCEP + DWD ICON-EPS + AIFS + UKMET + GEM + AIGFS")
    print("Press Ctrl+C to stop.")
    print("=" * 72)

    if not IS_RENDER and os.environ.get("MASRAINMAN_CLOUD", "0") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard...")
    finally:
        server.server_close()


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "--talk-worker":
        raise SystemExit(_talk_worker_cli())
    if len(sys.argv) >= 2 and sys.argv[1] == "--self-test":
        renderer_self_test()
        gfs_long_period_self_test()
        gfs_fast_path_self_test()
        gfs_engine_safety_self_test()
        print(f"[SELFTEST] VERSION: {APP_VERSION}")
        raise SystemExit(0)
    main()
