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
warnings.filterwarnings('ignore')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────
BOT_TOKEN  = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = -1003967766296
IST        = pytz.timezone('Asia/Kolkata')

SYMBOLS = {
    "NIFTY 50":   "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "FINNIFTY":   "^NSEMDCP50",
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

# ─── INDICATORS ────────────────────────────────────
def get_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.where(delta > 0, 0).rolling(period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))

def get_macd(close, fast=12, slow=26, signal=9):
    ema_fast   = close.ewm(span=fast, adjust=False).mean()
    ema_slow   = close.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line= macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram

def get_ema(close, period):
    return close.ewm(span=period, adjust=False).mean()

def get_bollinger(close, period=20, std=2):
    sma   = close.rolling(period).mean()
    sigma = close.rolling(period).std()
    upper = sma + std * sigma
    lower = sma - std * sigma
    return upper, sma, lower

def get_supertrend(df, period=10, multiplier=3):
    hl2 = (df['High'] + df['Low']) / 2
    atr = df['High'].combine(df['Close'].shift(), max) - \
          df['Low'].combine(df['Close'].shift(), min)
    atr = atr.rolling(period).mean()
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    supertrend = pd.Series(index=df.index, dtype=float)
    direction  = pd.Series(index=df.index, dtype=int)
    for i in range(1, len(df)):
        if df['Close'].iloc[i] > upper_band.iloc[i-1]:
            direction.iloc[i] = 1
        elif df['Close'].iloc[i] < lower_band.iloc[i-1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
    return direction

def get_volume_signal(volume, period=20):
    vol_ma = volume.rolling(period).mean()
    return volume.iloc[-1] / vol_ma.iloc[-1]

# ─── SIGNAL GENERATOR ──────────────────────────────
def analyze_symbol(name, ticker):
    try:
        df = yf.download(ticker, period="60d", interval="15m",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 50:
            return None

        close  = df['Close']
        volume = df['Volume']

        # Indicators
        rsi            = get_rsi(close)
        macd, sig, hist= get_macd(close)
        ema9           = get_ema(close, 9)
        ema21          = get_ema(close, 21)
        ema50          = get_ema(close, 50)
        ema200         = get_ema(close, 200)
        bb_up, bb_mid, bb_low = get_bollinger(close)
        vol_ratio      = get_volume_signal(volume)

        # Current values
        cmp      = float(close.iloc[-1])
        rsi_val  = float(rsi.iloc[-1])
        macd_val = float(macd.iloc[-1])
        sig_val  = float(sig.iloc[-1])
        hist_val = float(hist.iloc[-1])
        hist_prev= float(hist.iloc[-2])
        ema9_v   = float(ema9.iloc[-1])
        ema21_v  = float(ema21.iloc[-1])
        ema50_v  = float(ema50.iloc[-1])
        ema200_v = float(ema200.iloc[-1])
        bb_up_v  = float(bb_up.iloc[-1])
        bb_low_v = float(bb_low.iloc[-1])

        # Prev candle
        prev_close = float(close.iloc[-2])
        chg_pct    = ((cmp - prev_close) / prev_close) * 100

        # ── SCORING SYSTEM ──
        score = 0
        signals_list = []

        # RSI signals
        if rsi_val < 35:
            score += 2; signals_list.append("RSI Oversold 🟢")
        elif rsi_val < 45:
            score += 1; signals_list.append("RSI Low")
        elif rsi_val > 65:
            score -= 2; signals_list.append("RSI Overbought 🔴")
        elif rsi_val > 55:
            score -= 1

        # MACD signals
        if macd_val > sig_val and hist_val > 0 and hist_val > hist_prev:
            score += 2; signals_list.append("MACD Bullish Cross 🟢")
        elif macd_val < sig_val and hist_val < 0 and hist_val < hist_prev:
            score -= 2; signals_list.append("MACD Bearish Cross 🔴")

        # EMA signals
        if ema9_v > ema21_v > ema50_v:
            score += 2; signals_list.append("EMA Bullish Stack 🟢")
        elif ema9_v < ema21_v < ema50_v:
            score -= 2; signals_list.append("EMA Bearish Stack 🔴")

        # Price vs EMA200
        if cmp > ema200_v:
            score += 1; signals_list.append("Above EMA200 ✅")
        else:
            score -= 1; signals_list.append("Below EMA200 ⚠️")

        # Bollinger signals
        if cmp <= bb_low_v:
            score += 2; signals_list.append("BB Lower Touch 🟢")
        elif cmp >= bb_up_v:
            score -= 2; signals_list.append("BB Upper Touch 🔴")

        # Volume confirmation
        if vol_ratio > 1.5:
            if score > 0: score += 1
            elif score < 0: score -= 1
            signals_list.append(f"High Volume {vol_ratio:.1f}x 📊")

        # ── DETERMINE SIGNAL ──
        if score >= 3:
            signal = "BUY"
        elif score <= -3:
            signal = "SELL"
        else:
            return None  # Skip HOLD — only strong signals

        # Confidence score
        confidence = min(95, abs(score) * 12 + 40)

        # Entry / SL / Targets
        atr_val = float((df['High'] - df['Low']).rolling(14).mean().iloc[-1])

        if signal == "BUY":
            entry  = round(cmp, 2)
            sl     = round(cmp - atr_val * 1.5, 2)
            t1     = round(cmp + atr_val * 2, 2)
            t2     = round(cmp + atr_val * 3, 2)
            t3     = round(cmp + atr_val * 4.5, 2)
            trend  = "BULLISH 🟢"
        else:
            entry  = round(cmp, 2)
            sl     = round(cmp + atr_val * 1.5, 2)
            t1     = round(cmp - atr_val * 2, 2)
            t2     = round(cmp - atr_val * 3, 2)
            t3     = round(cmp - atr_val * 4.5, 2)
            trend  = "BEARISH 🔴"

        rr = round(abs(t1 - entry) / abs(sl - entry), 2)

        return {
            "name":       name,
            "ticker":     ticker,
            "signal":     signal,
            "cmp":        round(cmp, 2),
            "chg_pct":    round(chg_pct, 2),
            "entry":      entry,
            "sl":         sl,
            "t1":         t1,
            "t2":         t2,
            "t3":         t3,
            "rr":         rr,
            "rsi":        round(rsi_val, 1),
            "macd_trend": "Bullish" if macd_val > sig_val else "Bearish",
            "ema_trend":  "Bullish" if ema9_v > ema21_v else "Bearish",
            "confidence": confidence,
            "trend":      trend,
            "vol_ratio":  round(vol_ratio, 2),
            "signals":    signals_list,
            "score":      score,
        }

    except Exception as e:
        logger.error(f"Error analyzing {name}: {e}")
        return None

# ─── MESSAGE FORMATTER ─────────────────────────────
def format_signal(s):
    emoji   = "🟢" if s['signal'] == "BUY" else "🔴"
    action  = "📈 BUY SIGNAL" if s['signal'] == "BUY" else "📉 SELL SIGNAL"
    chg_str = f"+{s['chg_pct']}%" if s['chg_pct'] >= 0 else f"{s['chg_pct']}%"
    stars   = "⭐" * min(5, int(s['confidence'] / 20))

    return f"""
{emoji} *{action}*
━━━━━━━━━━━━━━━━━━━━━
📌 *{s['name']}*
💰 CMP: ₹`{s['cmp']}` ({chg_str})
🎯 Trend: {s['trend']}
━━━━━━━━━━━━━━━━━━━━━
📊 *TRADE DETAILS*
▶️ Entry: ₹`{s['entry']}`
🛑 Stop Loss: ₹`{s['sl']}`
🎯 Target 1: ₹`{s['t1']}`
🎯 Target 2: ₹`{s['t2']}`
🎯 Target 3: ₹`{s['t3']}`
⚖️ R:R Ratio: `1:{s['rr']}`
━━━━━━━━━━━━━━━━━━━━━
📐 *INDICATORS*
• RSI: `{s['rsi']}` {'🔥' if s['rsi'] < 35 else '❄️' if s['rsi'] > 65 else '✅'}
• MACD: `{s['macd_trend']}`
• EMA: `{s['ema_trend']}`
• Volume: `{s['vol_ratio']}x` {'🚀' if s['vol_ratio'] > 2 else '📊'}
• Confidence: {stars} `{s['confidence']}%`
━━━━━━━━━━━━━━━━━━━━━
⚠️ _Educational signals only. SEBI registered advisor nahi hain. Trade at your own risk._

🔔 @SignalBharat
"""

def format_market_open():
    now = datetime.now(IST)
    return f"""
🔔 *MARKET OPEN — SIGNALS STARTING*
━━━━━━━━━━━━━━━━━━━━━
📅 Date: {now.strftime('%d %B %Y, %A')}
⏰ Time: {now.strftime('%I:%M %p')} IST
📊 NSE/BSE: *OPEN*
━━━━━━━━━━━━━━━━━━━━━
🎯 SignalBharat AI scanning market...
📈 NIFTY | BANKNIFTY | F&O Stocks

⚠️ _Educational signals. Not SEBI advice._
🔔 @SignalBharat
"""

def format_market_close(all_results):
    now   = datetime.now(IST)
    total = len(all_results)
    buy_c = sum(1 for r in all_results if r['signal'] == 'BUY')
    sel_c = sum(1 for r in all_results if r['signal'] == 'SELL')

    return f"""
🔔 *MARKET CLOSED — EOD SUMMARY*
━━━━━━━━━━━━━━━━━━━━━
📅 {now.strftime('%d %B %Y')}
⏰ NSE Closed at 3:30 PM IST
━━━━━━━━━━━━━━━━━━━━━
📊 *TODAY'S SIGNALS*
✅ Total Signals: `{total}`
🟢 BUY Signals:  `{buy_c}`
🔴 SELL Signals: `{sel_c}`
━━━━━━━━━━━━━━━━━━━━━
💡 _Review all signals before tomorrow_
🔔 @SignalBharat
"""

def format_pre_market():
    now = datetime.now(IST)
    return f"""
⏰ *PRE-MARKET ALERT*
━━━━━━━━━━━━━━━━━━━━━
📅 {now.strftime('%d %B %Y, %A')}
🕘 Market opens in 15 minutes!

📌 *WATCH LEVELS TODAY*
• NIFTY 50: Support 22,200 | Resistance 22,800
• BANKNIFTY: Support 48,500 | Resistance 49,500

⚠️ Gap up/down check karo
📊 FII/DII data check karo
🎯 Signals 9:15 AM se aayenge!

⚠️ _Educational only. Not SEBI advice._
🔔 @SignalBharat
"""

# ─── MARKET HOURS ──────────────────────────────────
def is_market_open():
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Sat/Sun
        return False
    market_open  = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close

def is_trading_day():
    now = datetime.now(IST)
    return now.weekday() < 5  # Mon-Fri

# ─── MAIN BOT JOBS ─────────────────────────────────
async def send_message(bot, text):
    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN
        )
        await asyncio.sleep(1)
    except Exception as e:
        logger.error(f"Send error: {e}")

async def job_pre_market():
    if not is_trading_day():
        return
    logger.info("Sending pre-market alert...")
    bot = Bot(token=BOT_TOKEN)
    await send_message(bot, format_pre_market())

async def job_market_open():
    if not is_trading_day():
        return
    logger.info("Market open alert...")
    bot = Bot(token=BOT_TOKEN)
    await send_message(bot, format_market_open())

async def job_scan_signals():
    if not is_market_open():
        logger.info("Market closed — skipping scan")
        return

    logger.info("Scanning signals...")
    bot     = Bot(token=BOT_TOKEN)
    results = []

    for name, ticker in SYMBOLS.items():
        logger.info(f"Analyzing {name}...")
        result = analyze_symbol(name, ticker)
        if result:
            results.append(result)
            await send_message(bot, format_signal(result))
            await asyncio.sleep(3)

    if not results:
        logger.info("No strong signals found this scan.")
    else:
        logger.info(f"Sent {len(results)} signals.")

    return results

async def job_market_close():
    if not is_trading_day():
        return
    logger.info("Market close summary...")
    bot = Bot(token=BOT_TOKEN)
    # Final scan before close
    results = []
    for name, ticker in list(SYMBOLS.items())[:5]:
        result = analyze_symbol(name, ticker)
        if result:
            results.append(result)
    await send_message(bot, format_market_close(results))

# ─── SCHEDULER ─────────────────────────────────────
def run_async(coro):
    asyncio.get_event_loop().run_until_complete(coro)

def setup_schedule():
    # Pre-market: 9:00 AM
    schedule.every().day.at("09:00").do(lambda: run_async(job_pre_market()))

    # Market open: 9:16 AM
    schedule.every().day.at("09:16").do(lambda: run_async(job_market_open()))

    # Signal scans during market hours
    schedule.every().day.at("09:30").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("10:00").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("10:30").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("11:00").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("11:30").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("12:00").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("12:30").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("13:00").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("13:30").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("14:00").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("14:30").do(lambda: run_async(job_scan_signals()))
    schedule.every().day.at("15:00").do(lambda: run_async(job_scan_signals()))

    # Market close: 3:31 PM
    schedule.every().day.at("15:31").do(lambda: run_async(job_market_close()))

    logger.info("✅ Schedule set! Scans: every 30min (9:30AM - 3:00PM IST)")

# ─── MAIN ──────────────────────────────────────────
async def send_startup():
    bot = Bot(token=BOT_TOKEN)
    await send_message(bot, """
🚀 *SignalBharat AI Bot Started!*
━━━━━━━━━━━━━━━━━━━━━
✅ Bot is LIVE and scanning
📊 Coverage: 15 NSE symbols
⏰ Scan: Every 30 min (Market Hours)
🎯 Signals: BUY / SELL only (High confidence)
━━━━━━━━━━━━━━━━━━━━━
📌 Symbols Tracked:
NIFTY | BANKNIFTY | RELIANCE | TCS
HDFCBANK | INFY | ICICIBANK | SBIN
TATAMOTORS | ADANIENT | WIPRO | ONGC

⚠️ _Educational signals. Not SEBI advice._
🔔 @SignalBharat
""")

if __name__ == "__main__":
    logger.info("🚀 Starting SignalBharat Bot...")
    asyncio.get_event_loop().run_until_complete(send_startup())
    setup_schedule()
    logger.info("✅ Bot running! Press Ctrl+C to stop.")
    while True:
        schedule.run_pending()
        time.sleep(30)
