// MasRainman live weather station - Cloudflare Worker
//
// Routes
//   GET  /api/latest            current conditions, rain, records, weekly/monthly totals (small, poll every 30 s)
//   GET  /api/history?h=48      readings for the last h hours (max 48)
//   GET  /api/daily             rain days (08:30-08:30 IST) for up to 400 days
//   GET|POST /upload/<TOKEN>    direct upload from the console ("Customized" server:
//                               Ambient / Wunderground / Ecowitt protocols)
//   cron every minute           reads Ambient's latest reading when no direct upload arrived recently
//
// Secrets: AMBIENT_API_KEY, AMBIENT_APP_KEY, UPLOAD_TOKEN, optional AMBIENT_MAC
// Vars:    STATION_TZ, SEED_URL, AMBIENT_BASE, TIP_IN, STATION_NAME

import { DurableObject } from "cloudflare:workers";
import { processRain, emptyBuffer } from "./rain.js";

const STATE_VERSION = 3;
const RAIN_DAY_START_MIN = 8 * 60 + 30;
const HISTORY_HOURS = 48;
const HISTORY_STEP_MS = 60e3;          // keep one history point per minute
const DAILY_KEEP = 400;
const DIRECT_FRESH_MS = 150e3;         // skip Ambient polling while direct uploads are arriving

const r1 = (v) => (v == null || isNaN(v) ? null : Math.round(v * 10) / 10);
const num = (v) => (v == null || v === "" ? null : isNaN(+v) ? null : +v);
const fToC = (f) => (f == null ? null : r1(((f - 32) * 5) / 9));
const mphToKmh = (m) => (m == null ? null : r1(m * 1.609344));
const inHgToHpa = (i) => (i == null ? null : r1(i * 33.8639));
const inToMm = (i) => (i == null ? null : r1(i * 25.4));

const CORS = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
const json = (obj, maxAge = 0, status = 200) =>
  new Response(JSON.stringify(obj), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": maxAge ? `public, max-age=${maxAge}` : "no-store", ...CORS }
  });

// ---------- time helpers ----------
function makeTime(tz) {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const parts = (ms) => {
    const p = Object.fromEntries(fmt.formatToParts(new Date(ms)).map((x) => [x.type, x.value]));
    return { date: `${p.year}-${p.month}-${p.day}`, min: (+p.hour % 24) * 60 + +p.minute };
  };
  const addDays = (d, n) => { const x = new Date(d + "T00:00:00Z"); x.setUTCDate(x.getUTCDate() + n); return x.toISOString().slice(0, 10); };
  const rainDayKey = (ms) => { const { date, min } = parts(ms); return min >= RAIN_DAY_START_MIN ? addDays(date, 1) : date; };
  const weekKey = (k) => addDays(k, -((new Date(k + "T00:00:00Z").getUTCDay() + 6) % 7));
  return { parts, addDays, rainDayKey, weekKey };
}

// ---------- normalise any upload protocol to Ambient field names ----------
function normalise(p) {
  const g = (...names) => { for (const n of names) { const v = num(p[n]); if (v != null) return v; } return null; };
  return {
    tempf: g("tempf"), humidity: g("humidity"), dewPoint: g("dewPoint", "dewptf"), feelsLike: g("feelsLike"),
    windspeedmph: g("windspeedmph"), windgustmph: g("windgustmph"), maxdailygust: g("maxdailygust"), winddir: g("winddir"),
    baromrelin: g("baromrelin", "baromin"), solarradiation: g("solarradiation"), uv: g("uv", "UV"),
    tempinf: g("tempinf", "indoortempf"), humidityin: g("humidityin", "indoorhumidity"),
    dailyrainin: g("dailyrainin"), eventrainin: g("eventrainin"), yearlyrainin: g("yearlyrainin"),
    rateIn: g("rainratein", "hourlyrainin", "rainin")
  };
}
// feels-like for direct uploads that do not send it (heat index above 27 C, else temperature)
function feelsLikeF(tf, rh) {
  if (tf == null || rh == null || tf < 80) return tf;
  return -42.379 + 2.04901523 * tf + 10.14333127 * rh - 0.22475541 * tf * rh - 6.83783e-3 * tf * tf
    - 5.481717e-2 * rh * rh + 1.22874e-3 * tf * tf * rh + 8.5282e-4 * tf * rh * rh - 1.99e-6 * tf * tf * rh * rh;
}
function dewPointF(tf, rh) {
  if (tf == null || !rh) return null;
  const tc = ((tf - 32) * 5) / 9, a = 17.27, b = 237.7, al = (a * tc) / (b + tc) + Math.log(rh / 100);
  return ((b * al) / (a - al)) * 9 / 5 + 32;
}

// ================= Durable Object: one station, all state =================
export class Station extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.env = env;
    this.T = makeTime(env.STATION_TZ || "Asia/Kolkata");
    this.sql = ctx.storage.sql;
    this.sql.exec("CREATE TABLE IF NOT EXISTS history (t INTEGER PRIMARY KEY, j TEXT)");
    this.sql.exec("CREATE TABLE IF NOT EXISTS daily (k TEXT PRIMARY KEY, j TEXT)");
  }

  async state() {
    if (this._state) return this._state;
    let s = await this.ctx.storage.get("state");
    if (!s || s.v !== STATE_VERSION) s = await this.seed();
    this._state = s;
    return s;
  }

  // first start: continue from the GitHub data (records, daily totals, rain buffer)
  async seed() {
    const s = {
      v: STATE_VERSION, buf: emptyBuffer(), lastDailyIn: null, lastT: 0, lastHistT: 0,
      lastDirectAt: 0, source: null, obs: null, station: null,
      record: { rateMm: 0, rateAt: null, dayMm: 0, dayKey: null }
    };
    const base = this.env.SEED_URL;
    if (base) {
      const get = async (f) => { try { const r = await fetch(base + f + "?t=" + Date.now()); return r.ok ? r.json() : null; } catch { return null; } };
      const [st, daily, hist, latest] = await Promise.all([get("state.json"), get("daily.json"), get("history.json"), get("latest.json")]);
      if (st?.buf) {
        Object.assign(s.buf, {
          lastRainRaw: st.buf.lastRainRaw, lastRainTime: st.buf.lastRainTime,
          lastSampleTime: st.buf.lastSampleTime, lastCalculatedRate: st.buf.lastCalculatedRate || 0, currentRate: 0
        });
        s.lastDailyIn = st.lastDailyIn ?? null;
        s.lastT = st.lastProcessed || 0;
        if (st.record) s.record = st.record;
      }
      if (latest?.station) s.station = latest.station;
      if (daily) for (const [k, v] of Object.entries(daily)) this.sql.exec("INSERT OR REPLACE INTO daily (k, j) VALUES (?, ?)", k, JSON.stringify(v));
      if (Array.isArray(hist)) for (const p of hist) this.sql.exec("INSERT OR REPLACE INTO history (t, j) VALUES (?, ?)", p.t, JSON.stringify(p));
      if (hist?.length) s.lastHistT = hist[hist.length - 1].t;
    }
    await this.ctx.storage.put("state", s);
    return s;
  }

  getDay(k) {
    const row = this.sql.exec("SELECT j FROM daily WHERE k = ?", k).toArray()[0];
    return row ? JSON.parse(row.j) : { rainMm: 0, peakRateMm: 0, peakAt: null, tMax: null, tMin: null };
  }
  allDays() {
    return Object.fromEntries(this.sql.exec("SELECT k, j FROM daily ORDER BY k").toArray().map((r) => [r.k, JSON.parse(r.j)]));
  }

  // ---- one reading from any source ----
  async ingest(rec, t, source) {
    const s = await this.state();
    if (t <= s.lastT) return { ok: true, skipped: "old" };
    const T = this.T, dk = T.rainDayKey(t), { min } = T.parts(t);
    const tip = num(this.env.TIP_IN) || 0.01;

    const { incIn } = processRain(s.buf, rec.dailyrainin, t, min, tip);
    if (incIn !== null && rec.dailyrainin != null) s.lastDailyIn = rec.dailyrainin;
    s.lastT = t;
    s.source = source;
    if (source === "direct") s.lastDirectAt = t;

    const d = this.getDay(dk);
    d.rainMm = Math.round((d.rainMm + (incIn || 0) * 25.4) * 100) / 100;
    const rateMm = inToMm(s.buf.currentRate);
    if ((rateMm || 0) > (d.peakRateMm || 0)) { d.peakRateMm = rateMm; d.peakAt = t; }
    const tc = fToC(rec.tempf);
    if (tc != null) {
      if (d.tMax == null || tc > d.tMax) d.tMax = tc;
      if (d.tMin == null || tc < d.tMin) d.tMin = tc;
    }
    this.sql.exec("INSERT OR REPLACE INTO daily (k, j) VALUES (?, ?)", dk, JSON.stringify(d));

    if ((rateMm || 0) > (s.record.rateMm || 0)) { s.record.rateMm = rateMm; s.record.rateAt = t; }
    if (d.rainMm > (s.record.dayMm || 0)) { s.record.dayMm = r1(d.rainMm); s.record.dayKey = dk; }

    // fill in values some protocols leave out
    if (rec.dewPoint == null) rec.dewPoint = dewPointF(rec.tempf, rec.humidity);
    if (rec.feelsLike == null) rec.feelsLike = feelsLikeF(rec.tempf, rec.humidity);
    s.obs = { ...rec, t };

    if (t - s.lastHistT >= HISTORY_STEP_MS - 5e3) {
      s.lastHistT = t;
      this.sql.exec("INSERT OR REPLACE INTO history (t, j) VALUES (?, ?)", t, JSON.stringify({
        t, d: dk, tempC: tc, hum: rec.humidity ?? null, dewC: fToC(rec.dewPoint), feelsC: fToC(rec.feelsLike),
        windKmh: mphToKmh(rec.windspeedmph), gustKmh: mphToKmh(rec.windgustmph), dir: rec.winddir ?? null,
        hPa: inHgToHpa(rec.baromrelin), rainDayMm: r1(d.rainMm), rateMm, stnRateMm: inToMm(rec.rateIn),
        solar: rec.solarradiation ?? null, uv: rec.uv ?? null
      }));
      this.sql.exec("DELETE FROM history WHERE t < ?", t - HISTORY_HOURS * 3600e3);
      const keys = this.sql.exec("SELECT k FROM daily ORDER BY k DESC LIMIT -1 OFFSET ?", DAILY_KEEP).toArray();
      for (const r of keys) this.sql.exec("DELETE FROM daily WHERE k = ?", r.k);
    }

    await this.ctx.storage.put("state", s);
    await this.ctx.storage.put("snapshot", this.buildSnapshot(s));
    return { ok: true, rateMm, rainDayMm: r1(d.rainMm) };
  }

  buildSnapshot(s) {
    const T = this.T, o = s.obs || {}, last = o.t || s.lastT || Date.now();
    const daily = this.allDays();
    const todayKey = T.rainDayKey(last), today = daily[todayKey] || {};
    const sum = (fn) => r1(Object.entries(daily).filter(([k]) => fn(k)).reduce((a, [, v]) => a + (v.rainMm || 0), 0));
    const wk = T.weekKey(todayKey), mo = todayKey.slice(0, 7);
    const weeks = {}, months = {};
    for (const [k, v] of Object.entries(daily)) {
      const w = T.weekKey(k), m = k.slice(0, 7), mm = v.rainMm || 0;
      weeks[w] = r1((weeks[w] || 0) + mm);
      const M = (months[m] ||= { rainMm: 0, rainyDays: 0, maxDayMm: 0, maxDayKey: null, peakRateMm: 0, peakAt: null });
      M.rainMm = r1(M.rainMm + mm);
      if (mm >= 2.5) M.rainyDays++;
      if (mm > M.maxDayMm) { M.maxDayMm = r1(mm); M.maxDayKey = k; }
      if ((v.peakRateMm || 0) > M.peakRateMm) { M.peakRateMm = v.peakRateMm; M.peakAt = v.peakAt; }
    }
    const st = s.station || {};
    return {
      station: { name: this.env.STATION_NAME || st.name || "MasRainman Weather Station", location: st.location || "", lat: st.lat ?? null, lon: st.lon ?? null, tz: this.env.STATION_TZ || "Asia/Kolkata" },
      obsTime: o.t || null, builtAt: Date.now(), source: s.source,
      loggingSince: Object.keys(daily)[0] || todayKey,
      rainDay: { key: todayKey, startsAt: "08:30" },
      now: {
        tempC: fToC(o.tempf), feelsC: fToC(o.feelsLike), dewC: fToC(o.dewPoint), hum: o.humidity ?? null,
        windKmh: mphToKmh(o.windspeedmph), gustKmh: mphToKmh(o.windgustmph), maxGustKmh: mphToKmh(o.maxdailygust),
        dir: o.winddir ?? null, hPa: inHgToHpa(o.baromrelin), solar: o.solarradiation ?? null, uv: o.uv ?? null,
        inTempC: fToC(o.tempinf), inHum: o.humidityin ?? null
      },
      rain: {
        rateMm: inToMm(s.buf.currentRate), stnRateMm: inToMm(o.rateIn),
        peakRateMm: today.peakRateMm ?? 0, peakAt: today.peakAt ?? null,
        dayMm: r1(today.rainMm ?? 0), prevDayMm: daily[T.addDays(todayKey, -1)]?.rainMm ?? null,
        last7Mm: sum((k) => k > T.addDays(todayKey, -7) && k <= todayKey),
        weekMm: sum((k) => k >= wk && k <= todayKey), weekStart: wk,
        monthMm: sum((k) => k.startsWith(mo)), yearMm: sum((k) => k.startsWith(todayKey.slice(0, 4))),
        stationYearMm: inToMm(o.yearlyrainin), eventMm: inToMm(o.eventrainin),
        lastRain: o.lastRain ? Date.parse(o.lastRain) : s.buf.lastRainTime, lastTip: s.buf.lastRainTime
      },
      record: s.record,
      today: { tMax: today.tMax ?? null, tMin: today.tMin ?? null },
      weeks: Object.entries(weeks).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 8).map(([k, v]) => ({ start: k, rainMm: v })),
      months: Object.entries(months).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 13).map(([k, v]) => ({ month: k, ...v }))
    };
  }

  // ---- cron: Ambient REST latest reading ----
  async poll() {
    let result;
    try { result = await this.pollInner(); }
    catch (e) { result = { error: String(e && e.message || e) }; }
    const s = await this.state();
    s.lastPoll = { at: Date.now(), result };
    await this.ctx.storage.put("state", s);
    return result;
  }

  async pollInner() {
    const s = await this.state();
    if (Date.now() - s.lastDirectAt < DIRECT_FRESH_MS) return { skipped: "direct uploads active" };
    const { AMBIENT_API_KEY: k1, AMBIENT_APP_KEY: k2 } = this.env;
    if (!k1 || !k2) return { skipped: "no Ambient keys" };
    const base = this.env.AMBIENT_BASE || "https://rt.ambientweather.net/v1";
    const res = await fetch(`${base}/devices?apiKey=${encodeURIComponent(k1)}&applicationKey=${encodeURIComponent(k2)}`);
    const text = await res.text();
    if (!res.ok) return { error: `Ambient ${res.status}: ${text.slice(0, 150)}` };
    let devs;
    try { devs = JSON.parse(text); } catch { return { error: "Ambient sent non-JSON: " + text.slice(0, 150) }; }
    if (!Array.isArray(devs)) return { error: "Ambient: " + text.slice(0, 150) };
    const dev = devs.find((d) => d.macAddress === this.env.AMBIENT_MAC) || devs[0];
    if (!dev?.lastData) return { error: "no station data" };
    if (dev.info) {
      s.station = { ...(s.station || {}), name: dev.info.name, location: dev.info.coords?.location || dev.info.location || "",
        lat: dev.info.coords?.coords?.lat ?? null, lon: dev.info.coords?.coords?.lon ?? null };
    }
    const L = dev.lastData;
    const rec = { ...normalise(L), feelsLike: num(L.feelsLike), dewPoint: num(L.dewPoint), lastRain: L.lastRain };
    return this.ingest(rec, L.dateutc, "ambient");
  }

  async fetch(request) {
    const url = new URL(request.url);
    const p = url.pathname;
    if (p === "/ingest") {
      const params = await request.json();
      return json(await this.ingest(normalise(params), Date.now(), "direct"));
    }
    if (p === "/poll") return json(await this.poll());
    await this.state();
    if (p === "/api/latest") {
      const snap = (await this.ctx.storage.get("snapshot")) || this.buildSnapshot(this._state);
      return json(snap, 10);
    }
    if (p === "/api/history") {
      const h = Math.min(HISTORY_HOURS, Math.max(1, +url.searchParams.get("h") || HISTORY_HOURS));
      const rows = this.sql.exec("SELECT j FROM history WHERE t >= ? ORDER BY t", Date.now() - h * 3600e3).toArray();
      return new Response("[" + rows.map((r) => r.j).join(",") + "]", { headers: { "Content-Type": "application/json", "Cache-Control": "public, max-age=30", ...CORS } });
    }
    if (p === "/api/daily") return json(this.allDays(), 120);
    if (p === "/api/status") {
      const s = this._state;
      return json({ lastReading: s.lastT, lastReadingIST: s.lastT ? new Date(s.lastT).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : null,
        hasObs: !!s.obs, source: s.source, lastDirectAt: s.lastDirectAt || null, lastPoll: s.lastPoll || null,
        keys: { api: !!this.env.AMBIENT_API_KEY, app: !!this.env.AMBIENT_APP_KEY, upload: !!this.env.UPLOAD_TOKEN }, historyPoints: this.sql.exec("SELECT COUNT(*) AS n FROM history").one().n });
    }
    return json({ error: "not found" }, 0, 404);
  }
}

// ================= Worker entry =================
const stub = (env) => env.STATION.get(env.STATION.idFromName("main"));

async function readParams(request, url) {
  const params = Object.fromEntries(url.searchParams);
  if (request.method === "POST") {
    const ct = request.headers.get("content-type") || "";
    const body = await request.text();
    if (ct.includes("json")) { try { Object.assign(params, JSON.parse(body)); } catch {} }
    else Object.assign(params, Object.fromEntries(new URLSearchParams(body)));
  }
  // Ambient custom upload puts everything after "?" in the path when the path ends with "?" or "&"
  const tail = url.pathname.split("/").slice(3).join("/");
  if (tail.includes("=")) Object.assign(params, Object.fromEntries(new URLSearchParams(tail.replace(/^[?&]/, ""))));
  return params;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") return new Response(null, { headers: CORS });

    // /upload/<TOKEN>[...]  (also accepts /upload/<TOKEN>&field=..., /upload/<TOKEN>?field=...)
    if (url.pathname.startsWith("/upload/")) {
      const token = url.pathname.split("/")[2]?.split(/[?&]/)[0];
      if (!env.UPLOAD_TOKEN || token !== env.UPLOAD_TOKEN) return json({ error: "bad token" }, 0, 403);
      const params = await readParams(request, url);
      if (env.AMBIENT_MAC && params.MAC && params.MAC.toUpperCase() !== env.AMBIENT_MAC.toUpperCase()) return json({ error: "unknown station" }, 0, 403);
      const res = await stub(env).fetch("https://do/ingest", { method: "POST", body: JSON.stringify(params) });
      return new Response("success", { status: res.ok ? 200 : 500, headers: CORS });  // consoles expect a plain 200
    }

    if (url.pathname.startsWith("/api/")) return stub(env).fetch(new Request("https://do" + url.pathname + url.search));
    if (url.pathname === "/") return json({ name: "MasRainman station API", endpoints: ["/api/latest", "/api/history", "/api/daily", "/api/status"] });
    return json({ error: "not found" }, 0, 404);
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(stub(env).fetch("https://do/poll").then((r) => r.text()).then((t) => console.log("poll:", t)));
  }
};
