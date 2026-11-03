// MasRainman Weather Station - rain-rate logic
// Based on the processRainLogic() function shared by a friend, with three fixes
// for running it from a scheduled GitHub Action over Ambient Weather 5-minute records:
//   1. "now" is the record's own timestamp, not the clock time of the Action run
//      (GitHub cron runs are often late, so wall-clock time would distort rates).
//   2. After a dry spell (> 10 min since last tip) the rate is spread over the
//      record interval (time since the previous record), not a fixed 60 s.
//      A fixed 60 s turns one 0.01" tip in a 5-min record into a fake 0.6"/hr spike.
//   3. The peak rate (bufRR) resets at local midnight, and the "current" rate
//      drops back to 0 when no tip has arrived for DECAY_SEC.

const DRY_GAP_SEC = 600;      // friend's 10-minute threshold
const MIN_INTERVAL_SEC = 60;  // friend's 60 s floor
const DECAY_SEC = 900;        // current rate falls to 0 after 15 min with no tip

function emptyBuffer() {
  return {
    lastRainRaw: null,        // last seen daily total (inches)
    lastRainTime: null,       // ms of last tip
    lastSampleTime: null,     // ms of last processed record
    lastCalculatedRate: 0,    // in/hr from the latest tip
    currentRate: 0,           // in/hr shown as "now" (decays to 0)
    bufRR: 0,                 // peak rate today (in/hr)
    tRR: null,                // ms when the peak happened
    dayKey: null              // local date the peak belongs to
  };
}

// Same signature as the original: processRainLogic(buf, newDailyInches, currentTimeStamp, isCron)
// currentTimeStamp = record time in ms. Only the cron (the GitHub Action) advances state.
function processRainLogic(buf, newDailyInches, currentTimeStamp, isCron = false, dayKey = null) {
  if (!isCron) return buf;                       // page refreshes never touch the baseline
  if (newDailyInches == null || isNaN(newDailyInches)) return buf;

  const now = currentTimeStamp;
  const prevSample = buf.lastSampleTime;
  buf.lastSampleTime = now;

  // new local day -> reset the daily peak
  if (dayKey && buf.dayKey !== dayKey) {
    buf.dayKey = dayKey;
    buf.bufRR = 0;
    buf.tRR = null;
  }

  if (buf.lastRainRaw === null) {
    buf.lastRainRaw = newDailyInches;
    buf.lastRainTime = now;
    return buf;
  }

  // --- MIDNIGHT RESET FIX (friend's) ---
  if (newDailyInches < buf.lastRainRaw) {
    buf.lastRainRaw = newDailyInches;
    buf.lastRainTime = now;
    buf.currentRate = 0;
    return buf;
  }

  const deltaRain = newDailyInches - buf.lastRainRaw;

  if (deltaRain > 0.0001) {
    let timeSinceLastTipSec = (now - buf.lastRainTime) / 1000;

    if (timeSinceLastTipSec > DRY_GAP_SEC) {
      // first tip after a dry spell: the rain fell within this record's interval
      timeSinceLastTipSec = prevSample ? (now - prevSample) / 1000 : MIN_INTERVAL_SEC;
    }

    const effectiveTime = Math.max(timeSinceLastTipSec, MIN_INTERVAL_SEC);

    buf.lastCalculatedRate = deltaRain * (3600 / effectiveTime);
    buf.currentRate = buf.lastCalculatedRate;

    buf.lastRainRaw = newDailyInches;
    buf.lastRainTime = now;
  } else if ((now - buf.lastRainTime) / 1000 > DECAY_SEC) {
    buf.currentRate = 0;
  }

  if (buf.currentRate > (buf.bufRR || 0)) {
    buf.bufRR = buf.currentRate;
    buf.tRR = now;
  }

  return buf;
}

module.exports = { processRainLogic, emptyBuffer, DRY_GAP_SEC, MIN_INTERVAL_SEC, DECAY_SEC };
