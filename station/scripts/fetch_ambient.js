// MasRainman Weather Station - Ambient Weather fetcher (runs in GitHub Actions)
// Reads the station's 5-minute records, runs processRainLogic() on every new record,
// and writes station/data/latest.json, history.json, daily.json, state.json.
//
// Secrets (repo Settings -> Secrets and variables -> Actions):
//   AMBIENT_API_KEY, AMBIENT_APP_KEY   (required, from ambientweather.net/account)
//   AMBIENT_MAC                        (optional, picks a station if you have several)
// Variables (optional): STATION_TZ (default Asia/Kolkata), STATION_NAME

const fs = require("fs");
const path = require("path");
const { processRainLogic, emptyBuffer } = require("./rainLogic");

const API = "https://rt.ambientweather.net/v1";
const DATA_DIR = path.join(__dirname, "..", "data");
const TZ = process.env.STATION_TZ || "Asia/Kolkata";
const HISTORY_HOURS = 48;
const DAILY_KEEP = 90;

const API_KEY = process.env.AMBIENT_API_KEY;
const APP_KEY = process.env.AMBIENT_APP_KEY;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const r1 = (v) => (v == null || isNaN(v) ? null : Math.round(v * 10) / 10);
const r2 = (v) => (v == null || isNaN(v) ? null : Math.round(v * 100) / 100);
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
function dayKeyOf(ms) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
}

async function getJson(url) {
  for (let attempt = 1; attempt <= 3; attempt++) {
    const res = await fetch(url);
    if (res.ok) return res.json();
    if (res.status === 429) { await sleep(1500 * attempt); continue; } // Ambient allows 1 req/sec
    throw new Error(`Ambient API ${res.status}: ${(await res.text()).slice(0, 200)}`);
  }
  throw new Error("Ambient API rate-limited (429) three times");
}

async function main() {
  if (!API_KEY || !APP_KEY) {
    console.log("::notice::AMBIENT_API_KEY / AMBIENT_APP_KEY not set - skipping station update.");
    return;
  }
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const keys = `apiKey=${encodeURIComponent(API_KEY)}&applicationKey=${encodeURIComponent(APP_KEY)}`;

  // 1. station list
  const devices = await getJson(`${API}/devices?${keys}`);
  if (!Array.isArray(devices) || !devices.length) throw new Error("No stations on this Ambient account");
  const dev = devices.find((d) => d.macAddress === process.env.AMBIENT_MAC) || devices[0];
  await sleep(1100);

  // 2. last 24 h of 5-minute records (newest first from the API)
  const records = await getJson(`${API}/devices/${encodeURIComponent(dev.macAddress)}?${keys}&limit=288`);
  records.sort((a, b) => a.dateutc - b.dateutc);

  // 3. run the rain logic on each record we have not processed yet
  const state = readJson("state.json", { buf: emptyBuffer(), lastProcessed: 0 });
  let history = readJson("history.json", []);
  const daily = readJson("daily.json", {});
  let added = 0;

  for (const rec of records) {
    if (rec.dateutc <= state.lastProcessed) continue;
    const t = rec.dateutc;
    const dk = dayKeyOf(t);
    processRainLogic(state.buf, rec.dailyrainin, t, true, dk);
    state.lastProcessed = t;
    added++;

    const rateMm = inToMm(state.buf.currentRate);
    history.push({
      t,
      tempC: fToC(rec.tempf), hum: rec.humidity ?? null,
      dewC: fToC(rec.dewPoint), feelsC: fToC(rec.feelsLike),
      windKmh: mphToKmh(rec.windspeedmph), gustKmh: mphToKmh(rec.windgustmph), dir: rec.winddir ?? null,
      hPa: inHgToHpa(rec.baromrelin),
      rainDayMm: inToMm(rec.dailyrainin),
      rateMm,                                   // computed (friend's logic)
      stnRateMm: inToMm(rec.hourlyrainin),      // station's own rain rate
      solar: rec.solarradiation ?? null, uv: rec.uv ?? null
    });

    const d = (daily[dk] ||= { rainMm: 0, peakRateMm: 0, peakAt: null, tMax: null, tMin: null });
    d.rainMm = Math.max(d.rainMm, inToMm(rec.dailyrainin) || 0);
    if ((rateMm || 0) > d.peakRateMm) { d.peakRateMm = rateMm; d.peakAt = t; }
    const tc = fToC(rec.tempf);
    if (tc != null) {
      if (d.tMax == null || tc > d.tMax) d.tMax = tc;
      if (d.tMin == null || tc < d.tMin) d.tMin = tc;
    }
  }

  // trim
  const cutoff = Date.now() - HISTORY_HOURS * 3600e3;
  history = history.filter((p) => p.t >= cutoff);
  const dayKeys = Object.keys(daily).sort();
  for (const k of dayKeys.slice(0, Math.max(0, dayKeys.length - DAILY_KEEP))) delete daily[k];

  // 4. latest snapshot for the dashboard
  const last = records[records.length - 1] || dev.lastData;
  const today = daily[dayKeyOf(last.dateutc)] || {};
  const latest = {
    station: {
      name: process.env.STATION_NAME || dev.info?.name || "MasRainman Weather Station",
      location: dev.info?.coords?.location || dev.info?.location || "",
      lat: dev.info?.coords?.coords?.lat ?? null,
      lon: dev.info?.coords?.coords?.lon ?? null,
      tz: TZ
    },
    obsTime: last.dateutc,
    builtAt: Date.now(),
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
      peakRateMm: inToMm(state.buf.bufRR),
      peakAt: state.buf.tRR,
      dayMm: inToMm(last.dailyrainin),
      eventMm: inToMm(last.eventrainin),
      weekMm: inToMm(last.weeklyrainin),
      monthMm: inToMm(last.monthlyrainin),
      yearMm: inToMm(last.yearlyrainin),
      lastRain: last.lastRain ? Date.parse(last.lastRain) : null,
      lastTip: state.buf.lastRainTime
    },
    today: { tMax: today.tMax ?? null, tMin: today.tMin ?? null }
  };

  writeJson("state.json", state);
  writeJson("history.json", history);
  writeJson("daily.json", daily);
  writeJson("latest.json", latest);
  console.log(`Processed ${added} new record(s). Rate ${latest.rain.rateMm} mm/h, today ${latest.rain.dayMm} mm, peak ${latest.rain.peakRateMm} mm/h`);
}

main().catch((e) => { console.error(e.message); process.exit(1); });
