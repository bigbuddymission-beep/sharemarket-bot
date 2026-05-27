import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import os
import time
import requests
import logging
import sqlite3
import hashlib
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from telegram import Bot
from telegram.constants import ParseMode

# ══════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("SignalBharat")

# ══════════════════════════════════════════════════
#  CONFIG — ENV ONLY
# ══════════════════════════════════════════════════
BOT_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "0"))
IST        = pytz.timezone("Asia/Kolkata")

if not BOT_TOKEN:
    log.error("TELEGRAM_TOKEN not set!")
    exit(1)
if CHANNEL_ID == 0:
    log.error("CHANNEL_ID not set!")
    exit(1)

# ══════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════
SIGNAL_COOLDOWN_MIN = 15
MIN_RR              = 1.5
ADX_THRESHOLD       = 20
DB_PATH             = "signals.db"
MIN_OPTION_PREMIUM  = 20       # skip options below this premium
MIN_DTE             = 1        # skip options expiring today

NSE_HOLIDAYS_2026 = [
    "2026-01-26","2026-03-25","2026-04-02","2026-04-14",
    "2026-05-01","2026-08-15","2026-10-02","2026-10-20",
    "2026-11-04","2026-11-25","2026-12-25",
]

# ── TIME ZONES TO AVOID ──────────────────────────
# (start_h, start_m, end_h, end_m, reason)
AVOID_WINDOWS = [
    (9,  15, 9,  29, "Opening volatility"),
    (12, 0,  12, 29, "Lunch — low volume"),
    (15, 0,  15, 30, "Closing — erratic moves"),
]

STOCK_SYMBOLS = {
    "RELIANCE":  "RELIANCE.NS",
    "TCS":       "TCS.NS",
    "HDFCBANK":  "HDFCBANK.NS",
    "INFY":      "INFY.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBIN":      "SBIN.NS",
    "MARUTI":    "MARUTI.NS",
    "ONGC":      "ONGC.NS",
}

OPTION_SYMBOLS = {
    "NIFTY 50":  {"ticker": "^NSEI",    "lot": 75,  "step": 50},
    "BANKNIFTY": {"ticker": "^NSEBANK", "lot": 35,  "step": 100},
}

# ══════════════════════════════════════════════════
#  SQLITE
# ══════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id   TEXT UNIQUE,
            name        TEXT,
            type        TEXT,
            direction   TEXT,
            entry       REAL,
            sl          REAL,
            t1          REAL,
            t2          REAL,
            conf        INTEGER,
            timestamp   TEXT,
            date        TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_signal(data: dict):
    try:
        conn = sqlite3.connect(DB_PATH)
        c    = conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO signals
            (signal_id,name,type,direction,entry,sl,t1,t2,conf,timestamp,date)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            data["signal_id"], data["name"], data["type"],
            data["direction"], data["entry"], data["sl"],
            data["t1"], data["t2"], data["conf"],
            data["timestamp"], data["date"],
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"DB save error: {e}")

def was_signal_sent_recently(name: str, direction: str) -> bool:
    try:
        conn  = sqlite3.connect(DB_PATH)
        c     = conn.cursor()
        since = (datetime.now(IST) - timedelta(minutes=SIGNAL_COOLDOWN_MIN)
                 ).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("""
            SELECT COUNT(*) FROM signals
            WHERE name=? AND direction=? AND timestamp>=?
        """, (name, direction, since))
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        log.warning(f"DB cooldown check: {e}")
        return False

def get_daily_summary():
    try:
        conn  = sqlite3.connect(DB_PATH)
        c     = conn.cursor()
        today = datetime.now(IST).strftime("%Y-%m-%d")
        c.execute(
            "SELECT name,type,direction,entry,t1,conf FROM signals WHERE date=?",
            (today,)
        )
        rows  = c.fetchall()
        count = len(rows)
        conn.close()
        if not rows:
            return "No signals today.", 0
        lines = [
            f"• {r[0]} [{r[1]}] {r[2]} — Entry ₹{r[3]} | T1 ₹{r[4]} | {r[5]}%"
            for r in rows
        ]
        return "\n".join(lines), count
    except Exception as e:
        log.warning(f"Summary error: {e}")
        return "Summary unavailable.", 0

# ══════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════
def esc(text):
    special = r'\_*[]()~`>#+-=|{}.!'
    result  = str(text)
    for ch in special:
        result = result.replace(ch, f"\\{ch}")
    return result

def make_signal_id(name: str, direction: str) -> str:
    raw = f"{name}_{direction}_{datetime.now(IST).strftime('%Y%m%d_%H%M')}"
    return hashlib.md5(raw.encode()).hexdigest()[:10]

def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
    })
    retry   = Retry(total=3, backoff_factor=2,
                    status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://",  adapter)
    return s

# ══════════════════════════════════════════════════
#  TIME FILTER
# ══════════════════════════════════════════════════
def is_safe_time() -> tuple[bool, str]:
    """Returns (is_safe, reason)"""
    now = datetime.now(IST)
    h, m = now.hour, now.minute
    for (sh, sm, eh, em, reason) in AVOID_WINDOWS:
        start = sh * 60 + sm
        end   = eh * 60 + em
        cur   = h  * 60 + m
        if start <= cur <= end:
            return False, reason
    return True, "OK"

# ══════════════════════════════════════════════════
#  MARKET CHECK
# ══════════════════════════════════════════════════
def is_market_open() -> bool:
    now       = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    log.info(f"IST: {now.strftime('%A %d %b %Y %I:%M %p')}")
    if now.weekday() >= 5:
        log.info("Weekend — closed.")
        return False
    if today_str in NSE_HOLIDAYS_2026:
        log.info("NSE Holiday — closed.")
        return False
    op = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    cl = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if not (op <= now <= cl):
        log.info(f"Outside hours: {now.strftime('%I:%M %p')}")
        return False
    return True

def get_job_type() -> str:
    now  = datetime.now(IST)
    h, m = now.hour, now.minute
    if h == 9  and m < 16:       return "pre_market"
    if h == 9  and 16 <= m < 20: return "market_open"
    if h == 15 and m >= 30:      return "market_close"
    return "scan"

def get_expiry(index_name: str):
    now        = datetime.now(IST)
    today      = now.date()
    target_day = 3 if index_name == "NIFTY 50" else 2
    days_ahead = (target_day - today.weekday()) % 7
    if days_ahead == 0 and now.hour >= 15:
        days_ahead = 7
    expiry = today + timedelta(days=days_ahead)
    while expiry.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        expiry -= timedelta(days=1)
    return expiry

def get_atm_strike(cmp, step):
    return round(cmp / step) * step

# ══════════════════════════════════════════════════
#  DATA CACHE
# ══════════════════════════════════════════════════
_data_cache: dict = {}

def fetch_data(ticker: str):
    now    = time.time()
    cached = _data_cache.get(ticker)
    if cached and (now - cached["ts"]) < 300:
        return cached["df"]
    try:
        df = yf.download(ticker, period="30d", interval="15m",
                         progress=False, auto_adjust=True,
                         session=make_session())
        if df is None or len(df) < 40:
            time.sleep(3)
            df = yf.download(ticker, period="30d", interval="15m",
                             progress=False, auto_adjust=True,
                             session=make_session())
        if df is not None and len(df) >= 40:
            _data_cache[ticker] = {"df": df, "ts": now}
            return df
        return None
    except Exception as e:
        log.warning(f"Fetch error {ticker}: {e}")
        return None

def fetch_5min_data(ticker: str):
    """5min data for entry confirmation"""
    try:
        df = yf.download(ticker, period="2d", interval="5m",
                         progress=False, auto_adjust=True,
                         session=make_session())
        return df if df is not None and len(df) >= 10 else None
    except Exception:
        return None

# ══════════════════════════════════════════════════
#  INDICATORS
# ══════════════════════════════════════════════════
def get_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / (loss + 1e-10)))

def get_macd(close):
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9,  adjust=False).mean()
    return macd, signal

def get_ema(close, p):
    return close.ewm(span=p, adjust=False).mean()

def get_adx(high, low, close, period=14):
    try:
        tr   = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr  = tr.rolling(period).mean()
        up   = high.diff()
        down = -low.diff()
        pdm  = up.where((up > down) & (up > 0), 0.0)
        ndm  = down.where((down > up) & (down > 0), 0.0)
        pdi  = 100 * pdm.rolling(period).mean() / (atr + 1e-10)
        ndi  = 100 * ndm.rolling(period).mean() / (atr + 1e-10)
        dx   = 100 * (pdi - ndi).abs() / (pdi + ndi + 1e-10)
        return float(dx.rolling(period).mean().iloc[-1])
    except Exception:
        return 25.0

# ══════════════════════════════════════════════════
#  SUPPORT & RESISTANCE (Pivot Points)
# ══════════════════════════════════════════════════
def get_pivot_levels(df) -> dict:
    """
    Classic daily pivot from last completed session.
    Returns: pivot, r1, r2, s1, s2, pdh, pdl
    """
    try:
        # Get daily data for pivot
        close = df["Close"].squeeze()
        high  = df["High"].squeeze()
        low   = df["Low"].squeeze()

        # Use last 20 candles to find prev day high/low
        # Group by date
        df2        = df.copy()
        df2.index  = pd.to_datetime(df2.index)
        daily      = df2.resample("1D").agg({
            "High":  "max",
            "Low":   "min",
            "Close": "last",
        }).dropna()

        if len(daily) < 2:
            return {}

        prev = daily.iloc[-2]
        ph   = float(prev["High"])
        pl   = float(prev["Low"])
        pc   = float(prev["Close"])

        pivot = round((ph + pl + pc) / 3, 2)
        r1    = round(2 * pivot - pl, 2)
        r2    = round(pivot + (ph - pl), 2)
        s1    = round(2 * pivot - ph, 2)
        s2    = round(pivot - (ph - pl), 2)

        return {"pivot": pivot, "r1": r1, "r2": r2,
                "s1": s1, "s2": s2, "pdh": ph, "pdl": pl}
    except Exception as e:
        log.warning(f"Pivot calc error: {e}")
        return {}

def check_sr_validity(cmp, signal, levels: dict) -> tuple[bool, str]:
    """
    Returns (is_valid, reason)
    BUY: check not too close to resistance
    SELL: check not too close to support
    """
    if not levels:
        return True, "No SR data"

    buffer_pct = 0.003  # 0.3% buffer

    if signal == "BUY":
        # Check nearest resistance
        resistances = [v for k, v in levels.items()
                       if k in ("r1", "r2", "pdh") and v > cmp]
        if resistances:
            nearest_r = min(resistances)
            gap_pct   = (nearest_r - cmp) / cmp
            if gap_pct < buffer_pct:
                return False, f"Too close to resistance ₹{nearest_r}"
        # Good — price near support is better
        supports = [v for k, v in levels.items()
                    if k in ("s1", "s2", "pivot") and v < cmp]
        if supports:
            nearest_s  = max(supports)
            support_gap = (cmp - nearest_s) / cmp
            if support_gap < 0.005:
                return True, f"Near support ₹{nearest_s} ✅"

    elif signal == "SELL":
        # Check nearest support
        supports = [v for k, v in levels.items()
                    if k in ("s1", "s2", "pdl") and v < cmp]
        if supports:
            nearest_s = max(supports)
            gap_pct   = (cmp - nearest_s) / cmp
            if gap_pct < buffer_pct:
                return False, f"Too close to support ₹{nearest_s}"
        # Good — price near resistance is better for sell
        resistances = [v for k, v in levels.items()
                       if k in ("r1", "r2", "pivot") and v > cmp]
        if resistances:
            nearest_r  = min(resistances)
            resist_gap = (nearest_r - cmp) / cmp
            if resist_gap < 0.005:
                return True, f"Near resistance ₹{nearest_r} ✅"

    return True, "SR OK"

# ══════════════════════════════════════════════════
#  5MIN ENTRY CONFIRMATION
# ══════════════════════════════════════════════════
def confirm_entry(ticker: str, signal: str) -> bool:
    """
    Check last 2 x 5min candles confirm direction.
    BUY: last 5min candle should be green (close > open)
    SELL: last 5min candle should be red (close < open)
    """
    try:
        df5 = fetch_5min_data(ticker)
        if df5 is None or len(df5) < 3:
            return True  # no data = don't block

        last_close = float(df5["Close"].squeeze().iloc[-1])
        last_open  = float(df5["Open"].squeeze().iloc[-1])
        prev_close = float(df5["Close"].squeeze().iloc[-2])
        prev_open  = float(df5["Open"].squeeze().iloc[-2])

        if signal == "BUY":
            # Both last 2 candles green = strong confirmation
            last_green = last_close > last_open
            prev_green = prev_close > prev_open
            confirmed  = last_green and prev_green
        else:
            last_red  = last_close < last_open
            prev_red  = prev_close < prev_open
            confirmed = last_red and prev_red

        if not confirmed:
            log.info(f"  {ticker}: 5min confirmation failed")
        return confirmed
    except Exception:
        return True  # fail open

# ══════════════════════════════════════════════════
#  VOLUME CONFIRMATION (improved)
# ══════════════════════════════════════════════════
def check_volume(vol_ratio: float, score: int) -> tuple[int, str]:
    """Returns (score_adjustment, volume_tag)"""
    if vol_ratio >= 3.0:
        adj = 2 if score > 0 else -2
        return adj, f"🚀 {vol_ratio}x SURGE"
    elif vol_ratio >= 1.5:
        adj = 1 if score > 0 else -1
        return adj, f"📊 {vol_ratio}x Above avg"
    elif vol_ratio < 0.7:
        return -2, f"⚠️ {vol_ratio}x LOW vol"
    return 0, f"📊 {vol_ratio}x Normal"

# ══════════════════════════════════════════════════
#  TRAILING SL LEVELS
# ══════════════════════════════════════════════════
def calc_trailing_sl(signal: str, entry, t1, t2, atr) -> dict:
    """
    Returns trailing SL levels after each target hit.
    """
    if signal == "BUY":
        return {
            "after_t1": round(entry, 2),           # Move SL to entry (free trade)
            "after_t2": round(t1, 2),              # Move SL to T1 (lock profit)
        }
    else:
        return {
            "after_t1": round(entry, 2),
            "after_t2": round(t1, 2),
        }

# ══════════════════════════════════════════════════
#  SCORING
# ══════════════════════════════════════════════════
def calc_score(rsi, macd_v, sig_v, hist, hist_prev,
               ema9_v, ema21_v, ema50_v, cmp) -> int:
    score = 0
    if rsi < 35:         score += 3
    elif rsi < 45:       score += 1
    elif rsi > 65:       score -= 3
    elif rsi > 55:       score -= 1

    if macd_v > sig_v and hist > hist_prev:   score += 2
    elif macd_v < sig_v and hist < hist_prev: score -= 2

    if ema9_v > ema21_v > ema50_v:    score += 2
    elif ema9_v < ema21_v < ema50_v:  score -= 2
    elif cmp > ema21_v:               score += 1
    else:                             score -= 1
    return score

def calc_rr(entry, sl, target) -> float:
    risk   = abs(entry - sl)
    reward = abs(target - entry)
    if risk == 0:
        return 0.0
    return round(reward / risk, 2)

# ══════════════════════════════════════════════════
#  NIFTY TREND
# ══════════════════════════════════════════════════
def get_nifty_trend() -> str:
    try:
        df = fetch_data("^NSEI")
        if df is None or len(df) < 50:
            return "NEUTRAL"
        close  = df["Close"].squeeze()
        ema20  = get_ema(close, 20).iloc[-1]
        ema50  = get_ema(close, 50).iloc[-1]
        cmp    = float(close.iloc[-1])
        adx    = get_adx(df["High"].squeeze(), df["Low"].squeeze(), close)
        if adx < ADX_THRESHOLD:
            log.info(f"NIFTY ADX={adx:.1f} — SIDEWAYS")
            return "SIDEWAYS"
        if cmp > ema20 > ema50:
            log.info(f"NIFTY: BULLISH (ADX={adx:.1f})")
            return "BULLISH"
        if cmp < ema20 < ema50:
            log.info(f"NIFTY: BEARISH (ADX={adx:.1f})")
            return "BEARISH"
        return "NEUTRAL"
    except Exception as e:
        log.warning(f"Trend error: {e}")
        return "NEUTRAL"

# ══════════════════════════════════════════════════
#  STOCK ANALYSIS
# ══════════════════════════════════════════════════
def analyze_stock(name: str, ticker: str, market_trend: str) -> dict | None:
    try:
        df = fetch_data(ticker)
        if df is None or len(df) < 40:
            log.warning(f"  {name}: Not enough data")
            return None

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()
        volume = df["Volume"].squeeze()

        rsi_s       = get_rsi(close)
        macd_s, sig = get_macd(close)
        ema9        = get_ema(close, 9)
        ema21       = get_ema(close, 21)
        ema50       = get_ema(close, 50)

        cmp       = float(close.iloc[-1])
        rsi       = float(rsi_s.iloc[-1])
        macd_v    = float(macd_s.iloc[-1])
        sig_v     = float(sig.iloc[-1])
        hist      = macd_v - sig_v
        hist_prev = float(macd_s.iloc[-2]) - float(sig.iloc[-2])
        ema9_v    = float(ema9.iloc[-1])
        ema21_v   = float(ema21.iloc[-1])
        ema50_v   = float(ema50.iloc[-1])
        vol_avg   = float(volume.rolling(20).mean().iloc[-1])
        vol_now   = float(volume.iloc[-1])
        vol_ratio = round(vol_now / (vol_avg + 1), 2)
        atr       = float((high - low).rolling(14).mean().iloc[-1])
        adx       = get_adx(high, low, close)

        # ADX filter
        if adx < ADX_THRESHOLD:
            log.info(f"  {name}: ADX={adx:.1f} sideways — skip")
            return None

        score = calc_score(rsi, macd_v, sig_v, hist, hist_prev,
                           ema9_v, ema21_v, ema50_v, cmp)

        # Volume adjustment
        vol_adj, vol_tag = check_volume(vol_ratio, score)
        score += vol_adj

        if score >= 3:    signal = "BUY"
        elif score <= -3: signal = "SELL"
        else:
            log.info(f"  {name}: Score {score} — no signal")
            return None

        # Trend filter
        if market_trend == "BULLISH" and signal == "SELL":
            log.info(f"  {name}: SELL blocked — BULLISH market")
            return None
        if market_trend == "BEARISH" and signal == "BUY":
            log.info(f"  {name}: BUY blocked — BEARISH market")
            return None
        if market_trend == "SIDEWAYS":
            log.info(f"  {name}: Blocked — SIDEWAYS market")
            return None

        # Support/Resistance check
        levels   = get_pivot_levels(df)
        sr_valid, sr_reason = check_sr_validity(cmp, signal, levels)
        if not sr_valid:
            log.info(f"  {name}: SR blocked — {sr_reason}")
            return None

        # 5min entry confirmation
        if not confirm_entry(ticker, signal):
            log.info(f"  {name}: 5min candle not confirmed")
            return None

        # Cooldown
        if was_signal_sent_recently(name, signal):
            log.info(f"  {name}: Cooldown — skip")
            return None

        # SL with 3% cap
        max_sl_dist = cmp * 0.03
        if signal == "BUY":
            entry = round(cmp, 2)
            sl    = round(cmp - min(atr * 1.5, max_sl_dist), 2)
            t1    = round(cmp + atr * 2.0, 2)
            t2    = round(cmp + atr * 3.0, 2)
            t3    = round(cmp + atr * 4.5, 2)
        else:
            entry = round(cmp, 2)
            sl    = round(cmp + min(atr * 1.5, max_sl_dist), 2)
            t1    = round(cmp - atr * 2.0, 2)
            t2    = round(cmp - atr * 3.0, 2)
            t3    = round(cmp - atr * 4.5, 2)

        rr1 = calc_rr(entry, sl, t1)
        rr2 = calc_rr(entry, sl, t2)
        rr3 = calc_rr(entry, sl, t3)

        # RR filter
        if rr1 < MIN_RR:
            log.info(f"  {name}: RR {rr1} < {MIN_RR} — skip")
            return None

        trailing = calc_trailing_sl(signal, entry, t1, t2, atr)
        conf     = min(94, abs(score) * 10 + 45)
        sig_id   = make_signal_id(name, signal)

        # Nearest SR level to show
        nearest_sr = ""
        if levels:
            if signal == "BUY" and levels.get("s1"):
                nearest_sr = f"S1: ₹{levels['s1']}"
            elif signal == "SELL" and levels.get("r1"):
                nearest_sr = f"R1: ₹{levels['r1']}"

        log.info(f"  {name}: STOCK {signal} Score={score} RR={rr1} Conf={conf}%")
        return dict(
            name=name, signal=signal, type="STOCK",
            entry=entry, sl=sl, t1=t1, t2=t2, t3=t3,
            rr1=rr1, rr2=rr2, rr3=rr3,
            trailing_t1=trailing["after_t1"],
            trailing_t2=trailing["after_t2"],
            conf=conf, rsi=round(rsi, 1),
            vol_tag=vol_tag, adx=round(adx, 1),
            nearest_sr=nearest_sr,
            signal_id=sig_id,
            direction=signal,
            timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            date=datetime.now(IST).strftime("%Y-%m-%d"),
        )
    except Exception as e:
        log.error(f"  Error {name}: {e}")
        return None

# ══════════════════════════════════════════════════
#  OPTION ANALYSIS
# ══════════════════════════════════════════════════
def analyze_option(name: str, info: dict, market_trend: str) -> dict | None:
    ticker = info["ticker"]
    step   = info["step"]
    lot    = info["lot"]

    try:
        df = fetch_data(ticker)
        if df is None or len(df) < 40:
            log.warning(f"  {name}: Not enough data")
            return None

        close  = df["Close"].squeeze()
        high   = df["High"].squeeze()
        low    = df["Low"].squeeze()
        volume = df["Volume"].squeeze()

        rsi_s       = get_rsi(close)
        macd_s, sig = get_macd(close)
        ema9        = get_ema(close, 9)
        ema21       = get_ema(close, 21)
        ema50       = get_ema(close, 50)

        cmp       = float(close.iloc[-1])
        rsi       = float(rsi_s.iloc[-1])
        macd_v    = float(macd_s.iloc[-1])
        sig_v     = float(sig.iloc[-1])
        hist      = macd_v - sig_v
        hist_prev = float(macd_s.iloc[-2]) - float(sig.iloc[-2])
        ema9_v    = float(ema9.iloc[-1])
        ema21_v   = float(ema21.iloc[-1])
        ema50_v   = float(ema50.iloc[-1])
        vol_avg   = float(volume.rolling(20).mean().iloc[-1])
        vol_now   = float(volume.iloc[-1])
        vol_ratio = round(vol_now / (vol_avg + 1), 2)
        atr       = float((high - low).rolling(14).mean().iloc[-1])
        adx       = get_adx(high, low, close)

        if adx < ADX_THRESHOLD:
            log.info(f"  {name}: ADX={adx:.1f} sideways — skip")
            return None

        score = calc_score(rsi, macd_v, sig_v, hist, hist_prev,
                           ema9_v, ema21_v, ema50_v, cmp)

        vol_adj, vol_tag = check_volume(vol_ratio, score)
        score += vol_adj

        if score >= 3:    direction = "BUY"
        elif score <= -3: direction = "SELL"
        else:
            log.info(f"  {name}: Score {score} — no option signal")
            return None

        # Trend filter
        if market_trend == "BULLISH" and direction == "SELL":
            log.info(f"  {name}: PE blocked — BULLISH")
            return None
        if market_trend == "BEARISH" and direction == "BUY":
            log.info(f"  {name}: CE blocked — BEARISH")
            return None
        if market_trend == "SIDEWAYS":
            log.info(f"  {name}: Option blocked — SIDEWAYS")
            return None

        # 5min confirmation
        if not confirm_entry(ticker, direction):
            log.info(f"  {name}: 5min not confirmed")
            return None

        # Expiry check
        expiry = get_expiry(name)
        dte    = (expiry - datetime.now(IST).date()).days
        if dte < MIN_DTE:
            log.info(f"  {name}: DTE={dte} too low — skip")
            return None

        opt_type = "CE" if direction == "BUY" else "PE"

        # Cooldown
        cooldown_key = f"{name}_{opt_type}"
        if was_signal_sent_recently(cooldown_key, direction):
            log.info(f"  {name}: Cooldown — skip")
            return None

        # SR check for index
        levels = get_pivot_levels(df)
        sr_valid, sr_reason = check_sr_validity(cmp, direction, levels)
        if not sr_valid:
            log.info(f"  {name}: SR blocked — {sr_reason}")
            return None

        atm = get_atm_strike(cmp, step)
        itm = atm - step if direction == "BUY" else atm + step
        otm = atm + step if direction == "BUY" else atm - step

        expiry_str = expiry.strftime("%d %b '%y").upper()

        # Improved BS-approximation premium
        vol_factor  = max(0.01, atr / cmp)
        t_years     = max(1, dte) / 252
        atm_premium = round(max(
            cmp * vol_factor * (t_years ** 0.5) * 0.4 * 100,
            atr * 0.3
        ), 1)

        # Skip if premium too low (illiquid)
        if atm_premium < MIN_OPTION_PREMIUM:
            log.info(f"  {name}: Premium ₹{atm_premium} too low — skip")
            return None

        itm_premium = round(atm_premium * 1.6, 1)
        otm_premium = round(atm_premium * 0.45, 1)
        prem_sl     = round(atm_premium * 0.4, 1)

        if direction == "BUY":
            spot_entry = round(cmp, 0)
            spot_sl    = round(cmp - min(atr * 1.5, cmp * 0.025), 0)
            spot_t1    = round(cmp + atr * 2.0, 0)
            spot_t2    = round(cmp + atr * 3.5, 0)
        else:
            spot_entry = round(cmp, 0)
            spot_sl    = round(cmp + min(atr * 1.5, cmp * 0.025), 0)
            spot_t1    = round(cmp - atr * 2.0, 0)
            spot_t2    = round(cmp - atr * 3.5, 0)

        move1   = abs(spot_t1 - spot_entry) * 0.5
        move2   = abs(spot_t2 - spot_entry) * 0.5
        prem_t1 = round(atm_premium + move1, 1)
        prem_t2 = round(atm_premium + move2, 1)

        rr1 = calc_rr(atm_premium, prem_sl, prem_t1)
        rr2 = calc_rr(atm_premium, prem_sl, prem_t2)

        if rr1 < MIN_RR:
            log.info(f"  {name}: Option RR {rr1} < {MIN_RR} — skip")
            return None

        # Trailing SL for options
        trailing_t1 = round(atm_premium * 1.0, 1)   # move SL to cost
        trailing_t2 = round(prem_t1, 1)              # lock T1 profit

        lot_profit_t1 = round((prem_t1 - atm_premium) * lot)
        lot_profit_t2 = round((prem_t2 - atm_premium) * lot)
        lot_loss      = round((atm_premium - prem_sl)  * lot)

        conf   = min(94, abs(score) * 10 + 45)
        sig_id = make_signal_id(name, direction)

        log.info(f"  {name}: OPTION {direction} {opt_type} Score={score} RR={rr1} Conf={conf}%")
        return dict(
            name=name, direction=direction, option_type=opt_type,
            type="OPTION",
            cmp=round(cmp, 0), expiry_str=expiry_str, dte=dte,
            atm=atm, itm=itm, otm=otm,
            atm_premium=atm_premium, itm_premium=itm_premium,
            otm_premium=otm_premium, prem_sl=prem_sl,
            prem_t1=prem_t1, prem_t2=prem_t2,
            trailing_t1=trailing_t1, trailing_t2=trailing_t2,
            rr1=rr1, rr2=rr2,
            lot=lot, lot_profit_t1=lot_profit_t1,
            lot_profit_t2=lot_profit_t2, lot_loss=lot_loss,
            conf=conf, rsi=round(rsi, 1),
            vol_tag=vol_tag, adx=round(adx, 1),
            signal_id=sig_id,
            entry=atm_premium, sl=prem_sl,
            t1=prem_t1, t2=prem_t2,
            timestamp=datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            date=datetime.now(IST).strftime("%Y-%m-%d"),
        )
    except Exception as e:
        log.error(f"  Error {name}: {e}")
        return None

# ══════════════════════════════════════════════════
#  MESSAGE FORMATS
# ══════════════════════════════════════════════════
def fmt_stock(s: dict) -> str:
    now_str   = datetime.now(IST).strftime("%I:%M %p IST")
    header    = "🟢 *BUY SIGNAL* 🟢"  if s["signal"] == "BUY" else "🔴 *SELL SIGNAL* 🔴"
    entry_ico = "▶️" if s["signal"] == "BUY" else "🔻"
    sr_line   = f"\n📍 Key Level: {esc(s['nearest_sr'])}" if s.get("nearest_sr") else ""

    msg = (
        f"{header}\n\n"
        f"📌 *{esc(s['name'])}*\n"
        f"💹 NSE STOCK\n"
        f"⏰ {esc(now_str)}\n"
        f"📶 ADX: {esc(s['adx'])} \\| {esc(s['vol_tag'])}"
        f"{sr_line}\n\n"
        f"{entry_ico} Entry: ₹{esc(s['entry'])}\n"
        f"🛑 Stop Loss: ₹{esc(s['sl'])}\n\n"
        f"🎯 Target 1: ₹{esc(s['t1'])}\n"
        f"🎯 Target 2: ₹{esc(s['t2'])}\n"
        f"🎯 Target 3: ₹{esc(s['t3'])}\n\n"
        f"⚖️ Risk : Reward\n"
        f"• T1 → 1 : {esc(s['rr1'])}\n"
        f"• T2 → 1 : {esc(s['rr2'])}\n"
        f"• T3 → 1 : {esc(s['rr3'])}\n\n"
        f"🔄 *Trailing SL*\n"
        f"• After T1 hit → SL to ₹{esc(s['trailing_t1'])} \\(free trade\\)\n"
        f"• After T2 hit → SL to ₹{esc(s['trailing_t2'])} \\(lock profit\\)\n\n"
        f"📊 Confidence: {esc(s['conf'])}%\n\n"
        f"⚠️ _Educational purpose only\\. Not financial advice\\._\n"
        f"🔔 @SignalBharat"
    )
    return msg[:4000]

def fmt_option(s: dict) -> str:
    now_str   = datetime.now(IST).strftime("%I:%M %p IST")
    header    = "🟢 *BUY CE SIGNAL* 🟢" if s["option_type"] == "CE" else "🔴 *BUY PE SIGNAL* 🔴"
    entry_ico = "▶️" if s["direction"] == "BUY" else "🔻"

    msg = (
        f"{header}\n\n"
        f"📌 *{esc(s['name'])}*\n"
        f"📅 Expiry: {esc(s['expiry_str'])} \\({esc(s['dte'])} days\\)\n"
        f"⏰ {esc(now_str)}\n"
        f"📶 ADX: {esc(s['adx'])} \\| {esc(s['vol_tag'])}\n\n"
        f"💹 Spot Price: ₹{esc(s['cmp'])}\n\n"
        f"🎯 *STRIKES*\n"
        f"• ITM {esc(s['option_type'])}: {esc(s['itm'])} @ ₹{esc(s['itm_premium'])}\n"
        f"• ATM {esc(s['option_type'])}: {esc(s['atm'])} @ ₹{esc(s['atm_premium'])} ⭐\n"
        f"• OTM {esc(s['option_type'])}: {esc(s['otm'])} @ ₹{esc(s['otm_premium'])}\n\n"
        f"{entry_ico} Buy Around: ₹{esc(s['atm_premium'])}\n"
        f"🛑 Stop Loss: ₹{esc(s['prem_sl'])}\n\n"
        f"🎯 Target 1: ₹{esc(s['prem_t1'])}\n"
        f"🎯 Target 2: ₹{esc(s['prem_t2'])}\n\n"
        f"⚖️ Risk : Reward\n"
        f"• T1 → 1 : {esc(s['rr1'])}\n"
        f"• T2 → 1 : {esc(s['rr2'])}\n\n"
        f"🔄 *Trailing SL*\n"
        f"• After T1 → SL to ₹{esc(s['trailing_t1'])} \\(cost cover\\)\n"
        f"• After T2 → SL to ₹{esc(s['trailing_t2'])} \\(lock profit\\)\n\n"
        f"📦 Lot Size: {esc(s['lot'])} qty\n"
        f"✅ Profit T1: ₹{esc(s['lot_profit_t1'])} per lot\n"
        f"✅ Profit T2: ₹{esc(s['lot_profit_t2'])} per lot\n"
        f"❌ Max Loss: ₹{esc(s['lot_loss'])} per lot\n\n"
        f"📊 Confidence: {esc(s['conf'])}%\n\n"
        f"⚠️ _Premium estimated\\. Verify on NSE\\._\n"
        f"⚠️ _Educational purpose only\\. Not financial advice\\._\n"
        f"🔔 @SignalBharat"
    )
    return msg[:4000]

def fmt_daily_summary(summary: str, count: int) -> str:
    now_str = esc(datetime.now(IST).strftime("%d %b %Y"))
    return (
        f"📊 *DAILY SUMMARY — {now_str}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Total Signals Today: *{esc(count)}*\n\n"
        f"{esc(summary)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _Educational only\\. Not financial advice\\._\n"
        f"🔔 @SignalBharat"
    )[:4000]

# ══════════════════════════════════════════════════
#  ASYNC WRAPPERS
# ══════════════════════════════════════════════════
async def scan_stock_async(name, ticker, trend):
    loop = asyncio.get_event_loop()
    return name, await loop.run_in_executor(
        None, analyze_stock, name, ticker, trend)

async def scan_option_async(name, info, trend):
    loop = asyncio.get_event_loop()
    return name, await loop.run_in_executor(
        None, analyze_option, name, info, trend)

# ══════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════
async def main():
    init_db()
    now     = datetime.now(IST)
    job     = get_job_type()
    now_str = esc(now.strftime("%d %b %Y %I:%M %p IST"))
    log.info(f"Job: {job}")

    bot = Bot(token=BOT_TOKEN)

    # ── PRE-MARKET ──
    if job == "pre_market":
        await bot.send_message(
            chat_id=CHANNEL_ID, parse_mode=ParseMode.MARKDOWN_V2,
            text=(
                "⏰ *PRE\\-MARKET ALERT*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                "🕘 Market opens in 15 minutes\\!\n\n"
                "📌 *TODAY SCAN*\n"
                "• NIFTY & BANKNIFTY CE/PE Options\n"
                "• RELIANCE, TCS, HDFC, INFY\n"
                "• ICICI, SBIN, MARUTI, ONGC\n\n"
                "✅ Filters Active:\n"
                "• Trend \\+ ADX \\+ SR \\+ Volume \\+ RR\n"
                "• 5min entry confirmation\n"
                "• Trailing SL levels\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ _Educational only\\. Not financial advice\\._\n"
                "🔔 @SignalBharat"
            )
        )
        return

    # ── MARKET OPEN ──
    if job == "market_open":
        await bot.send_message(
            chat_id=CHANNEL_ID, parse_mode=ParseMode.MARKDOWN_V2,
            text=(
                "🔔 *MARKET OPEN*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"📅 {now_str}\n"
                "📊 NSE/BSE: *OPEN* 🟢\n\n"
                "🔍 Scanning with full filters:\n"
                "📈 NIFTY & BANKNIFTY Options\n"
                "📊 Top 8 NSE Stocks\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "⚠️ _Educational only\\. Not financial advice\\._\n"
                "🔔 @SignalBharat"
            )
        )
        return

    # ── MARKET CLOSE + SUMMARY ──
    if job == "market_close":
        summary, count = get_daily_summary()
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=fmt_daily_summary(summary, count),
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    # ── SIGNAL SCAN ──
    if not is_market_open():
        log.info("Market closed — exiting.")
        return

    # Time window check
    safe, reason = is_safe_time()
    if not safe:
        log.info(f"Unsafe time window: {reason} — skip scan")
        return

    # Market trend
    log.info("Checking NIFTY trend...")
    market_trend = get_nifty_trend()
    log.info(f"Trend: {market_trend}")

    sent = 0

    # OPTIONS — parallel
    log.info("── OPTIONS SCAN ──")
    opt_tasks   = [scan_option_async(n, i, market_trend)
                   for n, i in OPTION_SYMBOLS.items()]
    opt_results = await asyncio.gather(*opt_tasks)

    for name, result in opt_results:
        if result:
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=fmt_option(result),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                save_signal(result)
                sent += 1
                await asyncio.sleep(3)
            except Exception as e:
                log.error(f"Send error {name}: {e}")

    # STOCKS — parallel
    log.info("── STOCKS SCAN ──")
    stk_tasks   = [scan_stock_async(n, t, market_trend)
                   for n, t in STOCK_SYMBOLS.items()]
    stk_results = await asyncio.gather(*stk_tasks)

    for name, result in stk_results:
        if result:
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=fmt_stock(result),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                save_signal(result)
                sent += 1
                await asyncio.sleep(3)
            except Exception as e:
                log.error(f"Send error {name}: {e}")

    log.info(f"Done. Signals sent: {sent}")

if __name__ == "__main__":
    asyncio.run(main())
