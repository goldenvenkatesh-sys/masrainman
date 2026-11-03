// MasRainman Weather Station - Ambient Weather fetcher (runs in GitHub Actions)
// Reads the station's 5-minute records, runs processRainLogic() on every new record,
// and writes station/data/latest.json, history.json, daily.json, state.json.
//
// Rain day = IMD convention: 08:30 IST to 08:30 IST, labelled by the date it ENDS on.
//
// Secrets (repo Settings -> Secrets and variables -> Actions):
//   AMBIENT_API_KEY, AMBIENT_APP_KEY   (required)
//   AMBIENT_MAC                        (optional, picks a station if you have several)
// Variables (optional): STATION_TZ (default Asia/Kolkata), STATION_NAME
// Env BACKFILL_DAYS: days of past data to load on a rebuild (default 35, max 400)

const fs = require("fs");
const path = require("path");
const { processRainLogic, emptyBuffer } = require("./rainLogic");

const API = "https://rt.ambientweather.net/v1";
const DATA_DIR = path.join(__dirname, "..", "data");
const TZ = process.env.STATION_TZ || "Asia/Kolkata";
const STATE_VERSION = 2;
const RAIN_DAY_START_MIN = 8 * 60 + 30;   // 08:30
const HISTORY_HOURS = 48;
const DAILY_KEEP = 400;
const BACKFILL_DAYS = Math.min(400, Math.max(1, parseInt(process.env.BACKFILL_DAYS || "35", 10) || 35));

const API_KEY = process.env.AMBIENT_API_KEY;
const APP_KEY = process.env.AMBIENT_APP_KEY;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const r1 = (v) => (v == null || isNaN(v) ? null : Math.round(v * 10) / 10);
const fToC = (f) => (f == null ? null : r1(((f - 32) * 5) / 9));
const mphToKmh = (m) => (m == null ? null : r1(m * 1.609344));
const inHgToHpa = (i) => (i == null ? null : r1(i * 33.8639));
const inToMm = (i) => (i == null ? null : r1(i * 25.4));

function readJson(file, fallback) {
  try { return JSON.parse(fs.readFileSync(path.join(DATA_DIR, file), "utf8")); }
  catch { return fallback; }
}
function writeJson(file, obj) {
  fs.writeFileSync(path.join(DATA_DIR, file), JSON.stringify(obj, null, 1));
}

// ---- local-time helpers ----
const partsFmt = new Intl.DateTimeFormat("en-CA", {
  timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23"
});
function localParts(ms) {
  const p = Object.fromEntries(partsFmt.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, min: (+p.hour % 24) * 60 + +p.minute };
}
function addDays(dateStr, n) {
  const d = new Date(dateStr + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
// IMD rain day: 08:30 on day D to 08:30 on D+1 is "rain day ending D+1"
function rainDayKey(ms) {
  const { date, min } = localParts(ms);
  return min >= RAIN_DAY_START_MIN ? addDays(date, 1) : date;
}
function weekKey(dayKey) {           // Monday of that rain day's week
  const dow = (new Date(dayKey + "T00:00:00Z").getUTCDay() + 6) % 7;
  return addDays(dayKey, -dow);
}

async function getJson(url) {
  for (let attempt = 1; attempt <= 4; attempt++) {
    const res = await fetch(url);
    if (res.ok) return res.json();
    if (res.status === 429) { await sleep(1500 * attempt); continue; } // Ambient allows 1 req/sec
    throw new Error(`Ambient API ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  throw new Error("Ambient API rate-limited (429) repeatedly");
}

// fetch records newer than `since` (ms), paging back with endDate; returns oldest-first
async function fetchRecords(mac, keys, since) {
  const out = [];
  let endDate = null;
  for (let page = 0; page < 450; page++) {
    const url = `${API}/devices/${encodeURIComponent(mac)}?${keys}&limit=288` + (endDate ? `&endDate=${endDate}` : "");
    const recs = await getJson(url);
    if (!Array.isArray(recs) || !recs.length) break;
    for (const r of recs) if (r.dateutc > since) out.push(r);
    const oldest = Math.min(...recs.map((r) => r.dateutc));
    if (oldest <= since || recs.length < 288) break;
    endDate = encodeURIComponent(new Date(oldest).toISOString());
    await sleep(1100);
  }
  const seen = new Set();
  return out.filter((r) => !seen.has(r.dateutc) && seen.add(r.dateutc)).sort((a, b) => a.dateutc - b.dateutc);
}

function newState() {
  return {
    v: STATE_VERSION, buf: emptyBuffer(), lastProcessed: 0, lastDailyIn: null,
    record: { rateMm: 0, rateAt: null, dayMm: 0, dayKey: null }
  };
}

async function main() {
  if (!API_KEY || !APP_KEY) {
    console.log("::notice::AMBIENT_API_KEY / AMBIENT_APP_KEY not set - skipping station update.");
    return;
  }
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const keys = `apiKey=${encodeURIComponent(API_KEY)}&applicationKey=${encodeURIComponent(APP_KEY)}`;

  const devices = await getJson(`${API}/devices?${keys}`);
  if (!Array.isArray(devices) || !devices.length) throw new Error("No stations on this Ambient account");
  const dev = devices.find((d) => d.macAddress === process.env.AMBIENT_MAC) || devices[0];
  await sleep(1100);

  // rebuild everything when the data format changes (or state is missing)
  let state = readJson("state.json", null);
  let history = readJson("history.json", []);
  let daily = readJson("daily.json", {});
  const rebuild = !state || state.v !== STATE_VERSION || process.env.FORCE_REBUILD === "true";
  if (rebuild) {
    state = newState();
    history = [];
    daily = {};
    console.log(`Rebuilding from the last ${BACKFILL_DAYS} day(s) of station data`);
  }
  const since = rebuild ? Date.now() - BACKFILL_DAYS * 86400e3 : state.lastProcessed;
  const records = await fetchRecords(dev.macAddress, keys, since);

  let added = 0;
  for (const rec of records) {
    const t = rec.dateutc;
    const dk = rainDayKey(t);

    // own accumulation from Ambient's midnight-reset daily total
    let incIn = 0;
    const di = rec.dailyrainin;
    if (di != null && !isNaN(di)) {
      if (state.lastDailyIn == null) incIn = 0;
      else if (di >= state.lastDailyIn) incIn = di - state.lastDailyIn;
      else {
        const { min } = localParts(t);
        if (min <= 20 || min >= 1430) incIn = di;   // genuine midnight reset
        else {
          state.dropCount = (state.dropCount || 0) + 1;
          incIn = state.dropCount >= 3 ? 0 : null;  // glitch: ignore; persistent drop: new baseline
        }
      }
      if (incIn !== null) { state.lastDailyIn = di; state.dropCount = 0; }
    }

    // friend's rain-rate logic; peak (bufRR) resets when the 08:30 rain day changes
    processRainLogic(state.buf, di, t, true, dk);
    state.lastProcessed = t;
    added++;

    const d = (daily[dk] ||= { rainMm: 0, peakRateMm: 0, peakAt: null, tMax: null, tMin: null });
    d.rainMm = Math.round((d.rainMm + (incIn || 0) * 25.4) * 100) / 100;
    const rateMm = inToMm(state.buf.currentRate);
    if ((rateMm || 0) > d.peakRateMm) { d.peakRateMm = rateMm; d.peakAt = t; }
    const tc = fToC(rec.tempf);
    if (tc != null) {
      if (d.tMax == null || tc > d.tMax) d.tMax = tc;
      if (d.tMin == null || tc < d.tMin) d.tMin = tc;
    }

    // all-time records (since logging started)
    if ((rateMm || 0) > state.record.rateMm) { state.record.rateMm = rateMm; state.record.rateAt = t; }
    if (d.rainMm > state.record.dayMm) { state.record.dayMm = r1(d.rainMm); state.record.dayKey = dk; }

    if (t >= Date.now() - HISTORY_HOURS * 3600e3) {
      history.push({
        t, d: dk,
        tempC: tc, hum: rec.humidity ?? null,
        dewC: fToC(rec.dewPoint), feelsC: fToC(rec.feelsLike),
        windKmh: mphToKmh(rec.windspeedmph), gustKmh: mphToKmh(rec.windgustmph), dir: rec.winddir ?? null,
        hPa: inHgToHpa(rec.baromrelin),
        rainDayMm: r1(d.rainMm),                  // rain since 08:30
        rateMm,                                   // computed (friend's logic)
        stnRateMm: inToMm(rec.hourlyrainin),      // station's own rain rate
        solar: rec.solarradiation ?? null, uv: rec.uv ?? null
      });
    }
  }

  // trim
  history = history.filter((p) => p.t >= Date.now() - HISTORY_HOURS * 3600e3);
  const dayKeys = Object.keys(daily).sort();
  for (const k of dayKeys.slice(0, Math.max(0, dayKeys.length - DAILY_KEEP))) delete daily[k];
  for (const k of Object.keys(daily)) daily[k].rainMm = r1(daily[k].rainMm);

  // totals by rain day
  const last = records[records.length - 1] || dev.lastData;
  const todayKey = rainDayKey(last.dateutc);
  const sumWhere = (fn) => r1(Object.entries(daily).filter(([k]) => fn(k)).reduce((s, [, v]) => s + (v.rainMm || 0), 0));
  const wk = weekKey(todayKey), mo = todayKey.slice(0, 7), yr = todayKey.slice(0, 4);
  const firstDay = Object.keys(daily).sort()[0] || todayKey;

  const weeks = {}, months = {};
  for (const [k, v] of Object.entries(daily)) {
    const w = weekKey(k), m = k.slice(0, 7);
    weeks[w] = r1((weeks[w] || 0) + (v.rainMm || 0));
    (months[m] ||= { rainMm: 0, rainyDays: 0, maxDayMm: 0, maxDayKey: null, peakRateMm: 0, peakAt: null });
    const M = months[m];
    M.rainMm = r1(M.rainMm + (v.rainMm || 0));
    if ((v.rainMm || 0) >= 2.5) M.rainyDays++;               // IMD rainy day: >= 2.5 mm
    if ((v.rainMm || 0) > M.maxDayMm) { M.maxDayMm = v.rainMm; M.maxDayKey = k; }
    if ((v.peakRateMm || 0) > M.peakRateMm) { M.peakRateMm = v.peakRateMm; M.peakAt = v.peakAt; }
  }

  const today = daily[todayKey] || {};
  const latest = {
    station: {
      name: process.env.STATION_NAME || dev.info?.name || "MasRainman Weather Station",
      location: dev.info?.coords?.location || dev.info?.location || "",
      tz: TZ
    },
    obsTime: last.dateutc,
    builtAt: Date.now(),
    loggingSince: firstDay,
    rainDay: { key: todayKey, startsAt: "08:30" },
    now: {
      tempC: fToC(last.tempf), feelsC: fToC(last.feelsLike), dewC: fToC(last.dewPoint), hum: last.humidity ?? null,
      windKmh: mphToKmh(last.windspeedmph), gustKmh: mphToKmh(last.windgustmph),
      maxGustKmh: mphToKmh(last.maxdailygust), dir: last.winddir ?? null,
      hPa: inHgToHpa(last.baromrelin), solar: last.solarradiation ?? null, uv: last.uv ?? null,
      inTempC: fToC(last.tempinf), inHum: last.humidityin ?? null
    },
    rain: {
      rateMm: inToMm(state.buf.currentRate),
      stnRateMm: inToMm(last.hourlyrainin),
      peakRateMm: today.peakRateMm ?? 0,
      peakAt: today.peakAt ?? null,
      dayMm: r1(today.rainMm ?? 0),                         // since 08:30
      prevDayMm: daily[addDays(todayKey, -1)]?.rainMm ?? null,
      last7Mm: sumWhere((k) => k > addDays(todayKey, -7) && k <= todayKey),
      weekMm: sumWhere((k) => k >= wk && k <= todayKey),
      weekStart: wk,
      monthMm: sumWhere((k) => k.startsWith(mo)),
      yearMm: sumWhere((k) => k.startsWith(yr)),
      stationYearMm: inToMm(last.yearlyrainin),
      eventMm: inToMm(last.eventrainin),
      lastRain: last.lastRain ? Date.parse(last.lastRain) : null,
      lastTip: state.buf.lastRainTime
    },
    record: state.record,
    today: { tMax: today.tMax ?? null, tMin: today.tMin ?? null },
    weeks: Object.entries(weeks).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 8).map(([k, v]) => ({ start: k, rainMm: v })),
    months: Object.entries(months).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 13).map(([k, v]) => ({ month: k, ...v }))
  };

  writeJson("state.json", state);
  writeJson("history.json", history);
  writeJson("daily.json", daily);
  writeJson("latest.json", latest);
  console.log(`Processed ${added} new record(s). Rain day ${todayKey}: ${latest.rain.dayMm} mm, week ${latest.rain.weekMm}, month ${latest.rain.monthMm}, record rate ${state.record.rateMm} mm/h`);
}

main().catch((e) => { console.error(e.message); process.exit(1); });
