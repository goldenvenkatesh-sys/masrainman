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

## Rain day, totals and records
- Rain day = IMD convention, 08:30 IST to 08:30 IST, named by the date it ends on.
- Daily rain is accumulated from bucket tips (Ambient's midnight `dailyrainin` reset is handled).
- Weekly totals = Mon–Sun rain days; monthly totals by rain-day date; rainy day = 2.5 mm or more.
- Record highest rain rate and record wettest rain day are kept since logging started.
- To reload past data: Actions -> Weather Station Update -> Run workflow -> tick *Rebuild*, set days (max 400).

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

## Live mode: Cloudflare Worker (station/worker)
The dashboard reads live data from a Cloudflare Worker (URL in `station/config.json`, filled in automatically).
If the Worker can't be reached the page falls back to the `data/` files from the GitHub Action (now every 30 min).

- Every minute the Worker reads Ambient's latest reading (about 1-minute data).
- If the console uploads straight to the Worker ("Customized" server), readings arrive every 16-30 s and the
  minute polling pauses automatically.
- API: `/api/latest` (poll), `/api/history?h=48`, `/api/daily`, `/api/status`.
- On first start it copies records, daily totals and the rain buffer from `station/data/`, so nothing is lost.

### One-time setup
1. Cloudflare dashboard -> Workers & Pages (open it once so your workers.dev subdomain is created).
2. Profile -> API Tokens -> Create Token -> template **Edit Cloudflare Workers** -> copy the token.
3. Copy your **Account ID** (Workers & Pages overview, right side).
4. GitHub repo secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, and `UPLOAD_TOKEN`
   (make up 20+ random letters/digits; it protects the upload address).
5. Actions -> **Deploy Station Worker (Cloudflare)** -> Run workflow. It deploys, copies the Ambient keys
   into the Worker and writes the Worker URL into `station/config.json`.

### Direct upload from the console (optional, for ~30 s updates)
awnet app -> your console -> Customized: Enable, Protocol **Ambient Weather** (or Wunderground),
Server = `masrainman-station.<your-subdomain>.workers.dev`, Path = `/upload/<UPLOAD_TOKEN>?`,
Port = 80, Upload interval = 30 s (or the lowest allowed).
Check it works: `/api/status` shows `"source":"direct"`.
