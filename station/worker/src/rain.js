// MasRainman live rain-rate logic for 30-60 s station data.
// Built on the friend's processRainLogic(): rate = rain since last tip / time since last tip.
// Adjusted for fast data:
//  - floor is 15 s (not 60 s), so two tips in 30 s are not capped at 15 mm/h
//  - first single tip after a dry spell (>10 min) is spread over 5 min instead of 60 s
//    (one 0.25 mm tip would otherwise show a fake 15 mm/h spike); several tips at once
//    after a dry spell use the real sample interval, so a sudden downpour still shows
//  - with no new tip the rate decays as tip / time-since-last-tip, and is 0 after 15 min
//  - a drop in the daily total is a midnight reset only near local midnight; any other
//    drop is a sensor glitch and is ignored

export const DRY_GAP_SEC = 600;
export const FIRST_TIP_WINDOW_SEC = 300;
export const FLOOR_SEC = 15;
export const DECAY_ZERO_SEC = 900;

export function emptyBuffer() {
  return { lastRainRaw: null, lastRainTime: null, lastSampleTime: null, lastCalculatedRate: 0, currentRate: 0 };
}

// returns { buf, incIn } where incIn = rain added by this reading (inches), null if glitch
export function processRain(buf, dailyIn, now, localMinute, tipIn = 0.01) {
  if (dailyIn == null || isNaN(dailyIn)) return { buf, incIn: 0 };
  const prevSample = buf.lastSampleTime;
  buf.lastSampleTime = now;
  const sampleSec = prevSample ? (now - prevSample) / 1000 : 60;

  if (buf.lastRainRaw === null) {
    buf.lastRainRaw = dailyIn;
    buf.lastRainTime = now - DRY_GAP_SEC * 2000; // unknown -> treat as dry
    return { buf, incIn: 0 };
  }

  if (dailyIn < buf.lastRainRaw) {
    const nearMidnight = localMinute <= 20 || localMinute >= 1430;
    if (nearMidnight) {                                  // midnight reset
      buf.lastRainRaw = dailyIn;
      buf.dropCount = 0;
      return { buf, incIn: dailyIn };
    }
    buf.dropCount = (buf.dropCount || 0) + 1;
    if (buf.dropCount < 3) return { buf, incIn: null };  // one-off glitch: ignore
    buf.lastRainRaw = dailyIn;                           // persistent drop (reboot/reset): new baseline
    buf.dropCount = 0;
    return { buf, incIn: 0 };
  }
  buf.dropCount = 0;

  const deltaRain = dailyIn - buf.lastRainRaw;
  let incIn = 0;

  if (deltaRain > 0.0001) {
    incIn = deltaRain;
    let tSec = (now - buf.lastRainTime) / 1000;
    if (tSec > DRY_GAP_SEC) {
      const singleTip = deltaRain <= tipIn * 1.5;
      tSec = singleTip ? Math.max(sampleSec, FIRST_TIP_WINDOW_SEC) : sampleSec;
    }
    let rate = deltaRain * (3600 / Math.max(tSec, FLOOR_SEC));
    // several tips in one reading: all but the first certainly fell within this reading
    if (deltaRain > tipIn * 1.5) rate = Math.max(rate, (deltaRain - tipIn) * (3600 / Math.max(sampleSec, FLOOR_SEC)));
    buf.lastCalculatedRate = rate;
    buf.lastInterval = Math.max(tSec, sampleSec);
    buf.currentRate = rate;
    buf.lastRainRaw = dailyIn;
    buf.lastRainTime = now;
  } else {
    const since = (now - buf.lastRainTime) / 1000;
    if (since > DECAY_ZERO_SEC) buf.currentRate = 0;
    else if (since <= (buf.lastInterval || 60) * 1.5) buf.currentRate = buf.lastCalculatedRate;  // next tip not overdue yet
    else buf.currentRate = Math.min(buf.lastCalculatedRate, (tipIn * 3600) / since);
  }
  return { buf, incIn };
}
