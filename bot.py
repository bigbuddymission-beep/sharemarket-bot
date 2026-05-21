import asyncio
import schedule
import time
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
from telegram import Bot
from telegram.constants import ParseMode
import logging
import warnings
import nest_asyncio

nest_asyncio.apply()
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────
BOT_TOKEN  = 667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY
CHANNEL_ID = -1003967766296
IST        = pytz.timezone('Asia/Kolkata')

SYMBOLS = {
    "NIFTY 50":   "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "FINNIFTY":   "^NSEFIN",
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "INFY":       "INFY.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "SBIN":       "SBIN.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "ADANIENT":   "ADANIENT.NS",
    "WIPRO":      "WIPRO.NS",
    "ONGC":       "ONGC.NS",
    "BAJFINANCE": "BAJFINANCE.NS",
    "SUNPHARMA":  "SUNPHARMA.NS",
}

LAST_SIGNALS = {}

# ─── INDICATORS ────────────────────────────────────
def get_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def get_ema(close, period):
    return close.ewm(span=period, adjust=False).mean()

def get_bollinger(close, period=20, std=2):
    sma = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    upper = sma + std * sigma
    lower = sma - std * sigma
    return upper, sma, lower

def get_volume_signal(volume, period=20):
    vol_ma = volume.rolling(period).mean()
    return volume.iloc[-1] / vol_ma.iloc[-1]

# ─── ANALYSIS ──────────────────────────────────────
def analyze_symbol(name, ticker):
    try:
        df = yf.download(ticker, period="60d", interval="15m", progress=False)
        if df is None or len(df) < 50:
            return None

        df = df.dropna()

        close = df["Close"]
        volume = df["Volume"]

        rsi = get_rsi(close)
        macd, sig, hist = get_macd(close)

        ema9 = get_ema(close, 9)
        ema21 = get_ema(close, 21)
        ema50 = get_ema(close, 50)
        ema200 = get_ema(close, 200)

        bb_up, bb_mid, bb_low = get_bollinger(close)
        vol_ratio = get_volume_signal(volume)

        cmp = float(close.iloc[-1])
        rsi_val = float(rsi.iloc[-1])
        macd_val = float(macd.iloc[-1])
        sig_val = float(sig.iloc[-1])
        hist_val = float(hist.iloc[-1])
        hist_prev = float(hist.iloc[-2])

        ema9_v = float(ema9.iloc[-1])
        ema21_v = float(ema21.iloc[-1])
        ema50_v = float(ema50.iloc[-1])
        ema200_v = float(ema200.iloc[-1])

        bb_up_v = float(bb_up.iloc[-1])
        bb_low_v = float(bb_low.iloc[-1])

        prev_close = float(close.iloc[-2])
        chg_pct = ((cmp - prev_close) / prev_close) * 100

        score = 0

        # RSI
        if rsi_val < 35:
            score += 2
        elif rsi_val > 65:
            score -= 2

        # MACD
        if macd_val > sig_val and hist_val > hist_prev:
            score += 2
        elif macd_val < sig_val and hist_val < hist_prev:
            score -= 2

        # EMA
        if ema9_v > ema21_v > ema50_v:
            score += 2
        elif ema9_v < ema21_v < ema50_v:
            score -= 2

        # EMA200 filter
        if cmp > ema200_v:
            score += 1
        else:
            score -= 1

        # Bollinger
        if cmp <= bb_low_v:
            score += 2
        elif cmp >= bb_up_v:
            score -= 2

        # Volume
        if vol_ratio > 1.5:
            score += 1 if score > 0 else -1

        if score >= 3:
            signal = "BUY"
        elif score <= -3:
            signal = "SELL"
        else:
            return None

        if LAST_SIGNALS.get(name) == signal:
            return None

        LAST_SIGNALS[name] = signal

        confidence = min(95, abs(score) * 15)

        atr = float((df["High"] - df["Low"]).rolling(14).mean().iloc[-1])

        if signal == "BUY":
            sl = cmp - atr * 1.5
            t1 = cmp + atr * 2
            t2 = cmp + atr * 3
            trend = "BULLISH 🟢"
        else:
            sl = cmp + atr * 1.5
            t1 = cmp - atr * 2
            t2 = cmp - atr * 3
            trend = "BEARISH 🔴"

        rr = abs(t1 - cmp) / abs(sl - cmp)

        return {
            "name": name,
            "signal": signal,
            "cmp": round(cmp, 2),
            "chg_pct": round(chg_pct, 2),
            "sl": round(sl, 2),
            "t1": round(t1, 2),
            "t2": round(t2, 2),
            "rr": round(rr, 2),
            "confidence": confidence,
            "trend": trend,
            "rsi": round(rsi_val, 1),
            "vol_ratio": round(vol_ratio, 2),
        }

    except Exception as e:
        logger.error(f"{name} error: {e}")
        return None

# ─── TELEGRAM ──────────────────────────────────────
async def send_message(bot, text):
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def format_signal(s):
    emoji = "🟢" if s["signal"] == "BUY" else "🔴"

    return f"""
{emoji} <b>{s['signal']} SIGNAL</b>

📌 <b>{s['name']}</b>
💰 CMP: ₹{s['cmp']} ({s['chg_pct']}%)
🎯 {s['trend']}

━━━━━━━━━━━━━━
🛑 SL: ₹{s['sl']}
🎯 T1: ₹{s['t1']}
🎯 T2: ₹{s['t2']}
⚖️ RR: 1:{s['rr']}

📊 RSI: {s['rsi']}
📦 Volume: {s['vol_ratio']}x
⭐ Confidence: {s['confidence']}%

⚠️ Educational Only
"""

# ─── SCAN ──────────────────────────────────────────
async def job_scan():
    bot = Bot(token=BOT_TOKEN)

    for name, ticker in SYMBOLS.items():
        result = analyze_symbol(name, ticker)
        if result:
            await send_message(bot, format_signal(result))
            await asyncio.sleep(3)

# ─── RUNNER ────────────────────────────────────────
def run_async(coro):
    asyncio.run(coro)

def setup():
    schedule.every(30).minutes.do(lambda: run_async(job_scan()))
    logger.info("Scheduler started")

if __name__ == "__main__":
    logger.info("Bot starting...")

    setup()

    while True:
        schedule.run_pending()
        time.sleep(10)
