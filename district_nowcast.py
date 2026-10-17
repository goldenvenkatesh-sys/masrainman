"""
MasRainMan district-wise nowcast caption builder.

Takes the latest IMD radar reflectivity grid (dBZ) plus a motion vector
(the same one used for the arrows on the nowcast map) and produces the
district-wise post:

    ☔ இப்போது பெய்யும் மழை
    ⏰ அடுத்த 60 நிமிடத்தில்
    ☀️ இப்போது மழை இல்லை

Covers south / delta Tamil Nadu and south Andhra Pradesh.
Town and district lists are plain data at the top of this file: add,
remove or rename freely.

Usage
-----
    from district_nowcast import build_caption_ta, build_caption_en, analyse

    result = analyse(dbz, lats, lons, u_kmh, v_kmh)   # u = eastward, v = northward
    text_ta = build_caption_ta(result, valid_time)    # datetime (IST or tz-aware)
    text_en = build_caption_en(result, valid_time)

dbz      : 2-D numpy array, rows = latitude, cols = longitude (NaN / low = no echo)
lats     : 1-D array of row latitudes  (ascending or descending)
lons     : 1-D array of column longitudes
u_kmh, v_kmh : storm motion in km/h. Either single numbers (one motion for
               the whole radar) or 2-D arrays the same shape as dbz.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Towns tracked:  (english, tamil, lat, lon, is_district_hq)
# District key order = order districts appear in the post.
# ---------------------------------------------------------------------------
DISTRICTS = {
    # ------------------------- TAMIL NADU -------------------------
    "TN": {
        "Madurai": ("மதுரை", [
            ("Madurai", "மதுரை", 9.925, 78.120, True),
            ("Melur", "மேலூர்", 10.032, 78.338, False),
            ("Usilampatti", "உசிலம்பட்டி", 9.968, 77.789, False),
        ]),
        "Sivaganga": ("சிவகங்கை", [
            ("Sivaganga", "சிவகங்கை", 9.847, 78.481, True),
            ("Karaikudi", "காரைக்குடி", 10.073, 78.780, False),
            ("Tiruppattur", "திருப்பத்தூர்", 10.112, 78.598, False),
            ("Manamadurai", "மானாமதுரை", 9.696, 78.457, False),
        ]),
        "Ramanathapuram": ("ராமநாதபுரம்", [
            ("Ramanathapuram", "ராமநாதபுரம்", 9.371, 78.830, True),
            ("Paramakudi", "பரமக்குடி", 9.545, 78.590, False),
            ("Rameswaram", "ராமேஸ்வரம்", 9.288, 79.313, False),
            ("Kamuthi", "கமுதி", 9.405, 78.374, False),
        ]),
        "Virudhunagar": ("விருதுநகர்", [
            ("Virudhunagar", "விருதுநகர்", 9.585, 77.958, True),
            ("Sivakasi", "சிவகாசி", 9.453, 77.802, False),
            ("Aruppukottai", "அருப்புக்கோட்டை", 9.509, 78.096, False),
            ("Rajapalayam", "ராஜபாளையம்", 9.452, 77.553, False),
            ("Srivilliputhur", "ஸ்ரீவில்லிபுத்தூர்", 9.512, 77.633, False),
        ]),
        "Theni": ("தேனி", [
            ("Theni", "தேனி", 10.010, 77.477, True),
            ("Bodinayakanur", "போடி", 10.011, 77.350, False),
            ("Cumbum", "கம்பம்", 9.738, 77.282, False),
        ]),
        "Dindigul": ("திண்டுக்கல்", [
            ("Dindigul", "திண்டுக்கல்", 10.362, 77.980, True),
            ("Palani", "பழனி", 10.450, 77.520, False),
            ("Kodaikanal", "கொடைக்கானல்", 10.238, 77.489, False),
            ("Oddanchatram", "ஒட்டன்சத்திரம்", 10.485, 77.750, False),
        ]),
        "Pudukkottai": ("புதுக்கோட்டை", [
            ("Pudukkottai", "புதுக்கோட்டை", 10.381, 78.821, True),
            ("Aranthangi", "அறந்தாங்கி", 10.170, 78.995, False),
        ]),
        "Thanjavur": ("தஞ்சாவூர்", [
            ("Thanjavur", "தஞ்சாவூர்", 10.787, 79.138, True),
            ("Pattukkottai", "பட்டுக்கோட்டை", 10.425, 79.320, False),
            ("Kumbakonam", "கும்பகோணம்", 10.960, 79.385, False),
        ]),
        "Tiruvarur": ("திருவாரூர்", [
            ("Tiruvarur", "திருவாரூர்", 10.773, 79.637, True),
            ("Mannargudi", "மன்னார்குடி", 10.665, 79.452, False),
            ("Thiruthuraipoondi", "திருத்துறைப்பூண்டி", 10.530, 79.637, False),
        ]),
        "Nagapattinam": ("நாகப்பட்டினம்", [
            ("Nagapattinam", "நாகப்பட்டினம்", 10.766, 79.843, True),
            ("Vedaranyam", "வேதாரண்யம்", 10.374, 79.850, False),
        ]),
        "Thoothukudi": ("தூத்துக்குடி", [
            ("Thoothukudi", "தூத்துக்குடி", 8.764, 78.134, True),
            ("Kovilpatti", "கோவில்பட்டி", 9.172, 77.868, False),
            ("Tiruchendur", "திருச்செந்தூர்", 8.496, 78.119, False),
        ]),
        "Tirunelveli": ("நெல்லை", [
            ("Tirunelveli", "திருநெல்வேலி", 8.714, 77.757, True),
            ("Ambasamudram", "அம்பாசமுத்திரம்", 8.704, 77.454, False),
            ("Nanguneri", "நாங்குநேரி", 8.492, 77.659, False),
        ]),
        "Tenkasi": ("தென்காசி", [
            ("Tenkasi", "தென்காசி", 8.959, 77.315, True),
            ("Sankarankovil", "சங்கரன்கோவில்", 9.172, 77.544, False),
            ("Kuttalam", "குற்றாலம்", 8.933, 77.269, False),
        ]),
        "Kanyakumari": ("குமரி", [
            ("Nagercoil", "நாகர்கோவில்", 8.178, 77.412, True),
            ("Kanyakumari", "கன்னியாகுமரி", 8.078, 77.541, False),
            ("Marthandam", "மார்த்தாண்டம்", 8.308, 77.222, False),
        ]),
    },
    # --------------------- SOUTH ANDHRA PRADESH ---------------------
    "AP": {
        "Nellore": ("நெல்லூர்", [
            ("Nellore", "நெல்லூர்", 14.443, 79.987, True),
            ("Kavali", "காவலி", 14.913, 79.993, False),
            ("Gudur", "கூடூர்", 14.150, 79.850, False),
            ("Atmakur", "ஆத்மகூர்", 14.618, 79.620, False),
        ]),
        "Tirupati": ("திருப்பதி", [
            ("Tirupati", "திருப்பதி", 13.628, 79.419, True),
            ("Srikalahasti", "காளஹஸ்தி", 13.750, 79.700, False),
            ("Sullurpeta", "சூளூர்பேட்டை", 13.700, 80.017, False),
            ("Naidupeta", "நாயுடுபேட்டை", 13.905, 79.897, False),
        ]),
        "Chittoor": ("சித்தூர்", [
            ("Chittoor", "சித்தூர்", 13.217, 79.100, True),
            ("Palamaner", "பலமனேர்", 13.200, 78.750, False),
            ("Kuppam", "குப்பம்", 12.749, 78.343, False),
            ("Nagari", "நகரி", 13.321, 79.585, False),
        ]),
        "Annamayya": ("அன்னமய்யா", [
            ("Rayachoti", "ராயச்சோட்டி", 14.058, 78.751, False),
            ("Madanapalle", "மதனப்பள்ளி", 13.550, 78.503, False),
            ("Rajampet", "ராஜம்பேட்டை", 14.190, 79.160, False),
        ]),
        "Kadapa": ("கடப்பா", [
            ("Kadapa", "கடப்பா", 14.467, 78.824, True),
            ("Proddatur", "புரொத்தூர்", 14.750, 78.548, False),
            ("Pulivendula", "புலிவெந்துலா", 14.422, 78.226, False),
        ]),
        "Prakasam": ("பிரகாசம்", [
            ("Ongole", "ஓங்கோல்", 15.503, 80.045, False),
            ("Kandukur", "கந்துகூர்", 15.215, 79.903, False),
            ("Markapur", "மார்க்காபூர்", 15.735, 79.270, False),
        ]),
    },
}

REGION_LABEL = {
    "TN": ("தமிழ்நாடு", "Tamil Nadu"),
    "AP": ("தெற்கு ஆந்திரா", "South Andhra"),
}

# ---------------------------------------------------------------------------
# Intensity classes (dBZ thresholds) – tune to match your colour bar
# ---------------------------------------------------------------------------
CLASSES = [  # (min_dBZ, emoji, tamil, english)
    (20, "🌦️", "சாரல்", "Light rain"),
    (30, "🌧️", "மிதமான மழை", "Moderate rain"),
    (40, "⛈️", "கனமழை", "Heavy rain"),
    (50, "⛈️", "கனமழை, இடி மின்னல்", "Heavy rain with thunder"),
]

SAMPLE_RADIUS_KM = 8      # area around a town that counts as "that town"
LEAD_TIMES_MIN = (10, 20, 30, 40, 50, 60)
MAX_TOWNS_SHOWN = 2       # towns in brackets per district


def dbz_class(dbz: float) -> int:
    """0 = no rain, 1..4 = index into CLASSES + 1."""
    c = 0
    if dbz is None or not np.isfinite(dbz):
        return 0
    for i, (thr, *_rest) in enumerate(CLASSES):
        if dbz >= thr:
            c = i + 1
    return c


# ---------------------------------------------------------------------------
# Radar sampling
# ---------------------------------------------------------------------------
class _Grid:
    def __init__(self, dbz, lats, lons):
        self.dbz = np.asarray(dbz, dtype=float)
        self.lats = np.asarray(lats, dtype=float)
        self.lons = np.asarray(lons, dtype=float)
        self.dlat = abs(self.lats[1] - self.lats[0])
        self.dlon = abs(self.lons[1] - self.lons[0])

    def _idx(self, arr, val):
        return int(np.abs(arr - val).argmin())

    def inside(self, lat, lon):
        return (min(self.lats) <= lat <= max(self.lats)
                and min(self.lons) <= lon <= max(self.lons))

    def sample(self, lat, lon, radius_km=SAMPLE_RADIUS_KM):
        """Robust max: 3rd-highest dBZ in the window, so one noisy pixel
        (clutter, speckle) can't trigger a post on its own."""
        if not self.inside(lat, lon):
            return np.nan
        rlat = radius_km / 111.0
        rlon = radius_km / (111.0 * math.cos(math.radians(lat)))
        i, j = self._idx(self.lats, lat), self._idx(self.lons, lon)
        di = max(1, int(round(rlat / self.dlat)))
        dj = max(1, int(round(rlon / self.dlon)))
        win = self.dbz[max(0, i - di): i + di + 1, max(0, j - dj): j + dj + 1]
        vals = win[np.isfinite(win)].ravel()
        if vals.size == 0:
            return np.nan
        vals.sort()
        return vals[-3] if vals.size >= 3 else vals[-1]

    def value_at(self, field_, lat, lon):
        if np.ndim(field_) == 0:
            return float(field_)
        return float(np.asarray(field_)[self._idx(self.lats, lat), self._idx(self.lons, lon)])


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
@dataclass
class TownResult:
    name_en: str
    name_ta: str
    hq: bool
    now: int                 # class now
    future: int              # max class in next 60 min
    arrival_min: int | None  # first lead time with rain (None if none / already raining)


@dataclass
class DistrictResult:
    region: str
    key: str
    name_ta: str
    towns: list = field(default_factory=list)

    @property
    def now(self):
        return max((t.now for t in self.towns), default=0)

    @property
    def future(self):
        return max((t.future for t in self.towns), default=0)

    @property
    def status(self):
        """'now', 'coming', 'continue', 'easing' or 'clear'."""
        if self.now and self.future:
            return "continue"
        if self.now:
            return "easing"
        if self.future:
            return "coming"
        return "clear"


def analyse(dbz, lats, lons, u_kmh, v_kmh, regions=("TN", "AP")):
    """Sample every tracked town now and along its upstream track.

    Backward (semi-Lagrangian) extrapolation: rain reaching a town in t
    minutes is the rain that is *now* at  town - motion * t.
    """
    g = _Grid(dbz, lats, lons)
    out = []
    for region in regions:
        for key, (name_ta, towns) in DISTRICTS[region].items():
            d = DistrictResult(region, key, name_ta)
            for en, ta, lat, lon, hq in towns:
                if not g.inside(lat, lon):
                    continue  # outside this radar's coverage
                now_c = dbz_class(g.sample(lat, lon))
                u = g.value_at(u_kmh, lat, lon)
                v = g.value_at(v_kmh, lat, lon)
                fut_c, arrival = 0, None
                for t in LEAD_TIMES_MIN:
                    hrs = t / 60.0
                    up_lat = lat - (v * hrs) / 111.0
                    up_lon = lon - (u * hrs) / (111.0 * math.cos(math.radians(lat)))
                    c = dbz_class(g.sample(up_lat, up_lon))
                    if c:
                        fut_c = max(fut_c, c)
                        if arrival is None:
                            arrival = t
                d.towns.append(TownResult(en, ta, hq, now_c, fut_c,
                                          None if now_c else arrival))
            if d.towns:
                out.append(d)
    return out


# ---------------------------------------------------------------------------
# Caption text
# ---------------------------------------------------------------------------
TA_MONTHS = ["ஜன.", "பிப்.", "மார்.", "ஏப்.", "மே", "ஜூன்",
             "ஜூலை", "ஆக.", "செப்.", "அக்.", "நவ.", "டிச."]


def _ta_daypart(h):
    if h < 4:
        return "இரவு"
    if h < 6:
        return "அதிகாலை"
    if h < 12:
        return "காலை"
    if h < 16:
        return "மதியம்"
    if h < 19:
        return "மாலை"
    return "இரவு"


def _to_ist(t: datetime) -> datetime:
    return t.replace(tzinfo=IST) if t.tzinfo is None else t.astimezone(IST)


def _clock(t: datetime) -> str:
    h = t.hour % 12 or 12
    return f"{h}:{t.minute:02d}"


def _towns_in_brackets(d: DistrictResult, attr: str, lang: str) -> str:
    """Rainy towns, strongest first; the HQ town is dropped when it
    shares the district's name (no 'மதுரை (மதுரை)')."""
    towns = [t for t in d.towns if getattr(t, attr)]
    if attr == "future":
        towns = [t for t in towns if not t.now] or towns
    towns.sort(key=lambda t: (-getattr(t, attr), t.arrival_min or 0))
    names = []
    for t in towns[:MAX_TOWNS_SHOWN]:
        n = t.name_ta if lang == "ta" else t.name_en
        dn = d.name_ta if lang == "ta" else d.key
        if not (t.hq and n == dn):
            names.append(n)
    return f" ({', '.join(names)})" if names else ""


def _cls(c, lang):
    emo, ta, en = CLASSES[c - 1][1:]
    return emo, (ta if lang == "ta" else en)


def _line(d, kind, lang):
    name = d.name_ta if lang == "ta" else d.key
    if kind == "now":
        emo, txt = _cls(d.now, lang)
        return f"📍 {name} – {emo} {txt}{_towns_in_brackets(d, 'now', lang)}"
    if kind == "coming":
        emo, txt = _cls(d.future, lang)
        word = "மழை வரும்" if lang == "ta" else "rain expected"
        if d.future >= 3:
            word = f"{txt} வரும்" if lang == "ta" else f"{txt.lower()} expected"
        return f"📍 {name} – {emo} {word}{_towns_in_brackets(d, 'future', lang)}"
    if kind == "continue":
        emo, txt = _cls(d.future, lang)
        word = "மழை தொடரும்" if lang == "ta" else "rain continues"
        if d.future >= 3 and d.future > d.now:  # getting heavier
            word = f"{txt} வரும்" if lang == "ta" else f"{txt.lower()} on the way"
        return f"📍 {name} – {emo} {word}"
    if kind == "easing":
        word = "மழை குறையும்" if lang == "ta" else "rain easing"
        return f"📍 {name} – 🌤️ {word}"
    raise ValueError(kind)


def _section(title, lines_by_region, lang):
    """Section with region sub-headings only when both regions appear."""
    regions = [r for r, ls in lines_by_region.items() if ls]
    if not regions:
        return []
    bar = "━━━━━━━━━━━━━━"
    out = [bar, title, bar]
    for r in regions:
        if len(regions) > 1:
            out.append(f"🔸 {REGION_LABEL[r][0 if lang == 'ta' else 1]}")
        out.extend(lines_by_region[r])
    out.append("")
    return out


def _build(result, valid_time, lang):
    t = _to_ist(valid_time)
    end = t + timedelta(minutes=60)
    regions = list(dict.fromkeys(d.region for d in result))

    def by_region(pred, kind):
        return {r: [_line(d, kind, lang) for d in result if d.region == r and pred(d)]
                for r in regions}

    now_lines = by_region(lambda d: d.now > 0, "now")
    next_lines = {r: [] for r in regions}
    for kind in ("coming", "continue", "easing"):  # coming first
        for r, ls in by_region(lambda d, k=kind: d.status == k, kind).items():
            next_lines[r].extend(ls)

    clear = {r: [d for d in result if d.region == r and d.status == "clear"] for r in regions}

    if lang == "ta":
        head = [
            "🌧️ MasRainMan | மாவட்ட வாரியாக மழை",
            f"🕒 {TA_MONTHS[t.month - 1]} {t.day} | {_ta_daypart(t.hour)} {_clock(t)} நிலவரம்",
            "",
        ]
        s_now, s_next, s_clear = ("☔ இப்போது பெய்யும் மழை",
                                  f"⏰ அடுத்த 60 நிமிடத்தில் ({_clock(end)} வரை)",
                                  "☀️ இப்போது மழை இல்லை")
        tail = ["☂️ வெளியே செல்பவர்கள் குடை எடுத்துச் செல்லுங்கள்",
                "⏱️ மழை நேரம் சற்று மாறலாம் | IMD எச்சரிக்கைகளைப் பின்பற்றவும்",
                "#MasRainMan #தமிழ்நாடு_மழை #ஆந்திரா_மழை"]
        quiet = "☀️ தற்போது தென் தமிழ்நாடு, தெற்கு ஆந்திராவில் குறிப்பிடத்தக்க மழை இல்லை."
    else:
        head = [
            "🌧️ MasRainMan | District-wise rain",
            f"🕒 {t:%b} {t.day} | {_clock(t)} {'AM' if t.hour < 12 else 'PM'} update",
            "",
        ]
        s_now, s_next, s_clear = ("☔ Raining now",
                                  f"⏰ Next 60 minutes (till {_clock(end)})",
                                  "☀️ No rain now")
        tail = ["☂️ Carry an umbrella if you are heading out",
                "⏱️ Timing may shift slightly | Follow official IMD warnings",
                "#MasRainMan #TamilNaduRains #AndhraRains"]
        quiet = "☀️ No significant rain over south Tamil Nadu and south Andhra right now."

    body = _section(s_now, now_lines, lang) + _section(s_next, next_lines, lang)
    if not body:
        return "\n".join(head + [quiet, ""] + tail)

    clear_lines = {}
    for r, ds in clear.items():
        if ds:
            names = ", ".join(d.name_ta if lang == "ta" else d.key for d in ds)
            clear_lines[r] = [f"📍 {names}"]
    body += _section(s_clear, clear_lines, lang)
    return "\n".join(head + body + tail)


def build_caption_ta(result, valid_time: datetime) -> str:
    return _build(result, valid_time, "ta")


def build_caption_en(result, valid_time: datetime) -> str:
    return _build(result, valid_time, "en")


def has_rain(result) -> bool:
    """Use this to skip posting when nothing is happening."""
    return any(d.now or d.future for d in result)


def tracked_storms(result):
    """Districts with heavy rain now or coming – handy for a separate
    'storm alert' push (e.g. only when an AP storm heads toward TN)."""
    return [(d.region, d.key, d.now, d.future, d.status)
            for d in result if max(d.now, d.future) >= 3]


def build_captions(result, valid_time: datetime) -> dict:
    """Both languages in one call:
    {'ta': Tamil post, 'en': English post, 'both': Tamil then English}"""
    ta = build_caption_ta(result, valid_time)
    en = build_caption_en(result, valid_time)
    return {"ta": ta, "en": en, "both": f"{ta}\n\n— English —\n\n{en}"}
