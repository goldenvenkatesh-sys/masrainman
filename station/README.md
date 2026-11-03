# MasRainman Weather Station dashboard

Live dashboard for an Ambient Weather station: rain rate (computed from bucket tips),
rainfall totals, temperature, wind, humidity, pressure, sun, 6/24/48 h charts and a daily table.

## How it works
- `.github/workflows/station.yml` runs every 5 minutes (GitHub may run it late).
- `scripts/fetch_ambient.js` pulls the last 24 h of 5-minute records from the Ambient API and
  runs `processRainLogic()` (in `scripts/rainLogic.js`) on every new record, using each record's own time.
- Results go to `data/latest.json`, `history.json` (48 h), `daily.json` (90 days), `state.json` (rain buffer).
- `index.html` only reads these files. Refreshing the page never changes the rain baseline —
  only the scheduled Action does (the `isCron` rule from the original code).

## Setup (one time)
1. Get keys at https://ambientweather.net/account → API Keys: an **API key** and an **Application key**.
2. Repo → Settings → Secrets and variables → Actions → **New repository secret**:
   - `AMBIENT_API_KEY`, `AMBIENT_APP_KEY`
   - `AMBIENT_MAC` (optional, only if the account has more than one station)
3. Optional **Variables**: `STATION_NAME`, `STATION_TZ` (default `Asia/Kolkata`; keep it the same as the
   station's time zone in the Ambient account so the daily reset lines up).
4. Actions tab → *Weather Station Update* → **Run workflow** once.
5. Open `https://goldenvenkatesh-sys.github.io/masrainman/station/`

Without the secrets the workflow just skips (no failures).

## Changes from the original processRainLogic
- First tip after a dry spell (>10 min) is spread over the record interval, not a fixed 60 s
  (60 s turned one 0.01" tip into a fake 0.6"/h ≈ 15 mm/h spike).
- Current rate drops to 0 after 15 min with no tip; peak rate (`bufRR`) resets at local midnight.
- Uses the record timestamp instead of `Date.now()`, so late GitHub runs don't distort rates.
