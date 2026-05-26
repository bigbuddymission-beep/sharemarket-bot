import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import pytz
import os
from telegram import Bot
from telegram.constants import ParseMode

# ── CONFIG ─────────────────────────────────────────
BOT_TOKEN  = os.environ.get("TELEGRAM_TOKEN") or "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or "-1003967766296")
IST        = pytz.timezone('Asia/Kolkata')

NSE_HOLIDAYS_2026 = [
    "2026-01-26",
    "2026-03-25",
    "2026-04-02",
    "2026-04-14",
    "2026-05-01",
    "2026-08-15",
    "2026-10-02",
    "2026-10-20",
    "2026-11-04",
    "2026-11-25",
    "2026-12-25",
]

SYMBOLS = {
    "NIFTY 50":   "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "INFY":       "INFY.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "SBIN":       "SBIN.NS",
    "TATAMOTORS": "TATAMOTORS.NS",
    "ONGC":       "ONGC.NS",
}

# ── MARKET CHECK ────────────────────────────────────
def is_market_open():
    now       = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    print(f"Current IST time: {now.strftime('%A %d %b %Y %I:%M %p IST')}")

    if now.weekday() >= 5:
        print("Weekend — market closed.")
        return False

    if today_str in NSE_HOLIDAYS_2026:
        print(f"NSE Holiday ({today_str}) — market closed.")
        return False

    op = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    cl = now.replace(hour=15, minute=30, second=0, microsecond=0)

    if not (op <= now <= cl):
        print(f"Outside market hours (9:15 AM - 3:30 PM IST). Current: {now.strftime('%I:%M %p')}")
        return False

    return True

def get_job_type():
    now = datetime.now(IST)
    h, m = now.hour, now.minute
    if h == 9 and m < 16:        return "pre_market"
    if h == 9 and 16 <= m < 20:  return "market_open"
    if h == 15 and m >= 30:      return "market_close"
    return "scan"

# ── INDICATORS ──────────────────────────────────────
def get_rsi(close, period=14):
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def get_macd(close):
    ema12  = close.ewm(span=12, adjust=False).mean()
    ema26  = close.ewm(span=26, adjust=False).mean()
    macd   = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal

def get_ema(close, p):
    return close.ewm(span=p, adjust=False).mean()

# ── ANALYSIS ────────────────────────────────────────
def analyze(name, ticker):
    try:
        df = yf.download(ticker, period="30d", interval="15m",
                         progress=False, auto_adjust=True)
        if df is None or len(df) < 40:
            print(f"  {name}: Not enough data")
            return None

        close  = df["Close"].squeeze()
        volume = df["Volume"].squeeze()

        rsi_s       = get_rsi(close)
        macd_s, sig = get_macd(close)
        ema9        = get_ema(close, 9)
        ema21       = get_ema(close, 21)
        ema50       = get_ema(close, 50)

        cmp       = float(close.iloc[-1])
        prev      = float(close.iloc[-2])
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
        chg       = round(((cmp - prev) / prev) * 100, 2)

        score = 0
        if rsi < 35:            score += 3
        elif rsi < 45:          score += 1
        elif rsi > 65:          score -= 3
        elif rsi > 55:          score -= 1

        if macd_v > sig_v and hist > hist_prev:   score += 2
        elif macd_v < sig_v and hist < hist_prev: score -= 2

        if ema9_v > ema21_v > ema50_v:   score += 2
        elif ema9_v < ema21_v < ema50_v: score -= 2
        elif cmp > ema21_v:              score += 1
        else:                            score -= 1

        if vol_ratio > 1.5:
            score += 1 if score > 0 else -1

        if score >= 4:    signal = "BUY"
        elif score <= -4: signal = "SELL"
        else:
            print(f"  {name}: Score {score} — no strong signal")
            return None

        conf = min(94, abs(score) * 10 + 45)
        atr  = float((df["High"].squeeze() - df["Low"].squeeze())
                     .rolling(14).mean().iloc[-1])

        if signal == "BUY":
            entry = round(cmp, 2)
            sl    = round(cmp - atr * 1.5, 2)
            t1    = round(cmp + atr * 2.0, 2)
            t2    = round(cmp + atr * 3.0, 2)
            t3    = round(cmp + atr * 4.5, 2)
        else:
            entry = round(cmp, 2)
            sl    = round(cmp + atr * 1.5, 2)
            t1    = round(cmp - atr * 2.0, 2)
            t2    = round(cmp - atr * 3.0, 2)
            t3    = round(cmp - atr * 4.5, 2)

        rr = round(abs(t1 - entry) / (abs(sl - entry) + 0.01), 2)
        print(f"  {name}: {signal} signal! Score={score} Conf={conf}%")

        return dict(name=name, signal=signal, cmp=cmp, chg=chg,
                    entry=entry, sl=sl, t1=t1, t2=t2, t3=t3,
                    rr=rr, rsi=round(rsi, 1), conf=conf,
                    vol=vol_ratio, macd_bull=macd_v > sig_v,
                    ema_bull=ema9_v > ema21_v)
    except Exception as e:
        print(f"  Error {name}: {e}")
        return None

# ── MESSAGE FORMAT ───────────────────────────────────
def fmt_signal(s):
    e   = "🟢" if s["signal"] == "BUY" else "🔴"
    hd  = "📈 *BUY SIGNAL*" if s["signal"] == "BUY" else "📉 *SELL SIGNAL*"
    chg = f"+{s['chg']}%" if s["chg"] >= 0 else f"{s['chg']}%"
    st  = "⭐" * min(5, max(1, s["conf"] // 20))
    now = datetime.now(IST).strftime("%I:%M %p IST")
    mk  = "Bullish 🟢" if s["macd_bull"] else "Bearish 🔴"
    ek  = "Bullish 🟢" if s["ema_bull"]  else "Bearish 🔴"
    rsi_tag = "🔥 Oversold" if s["rsi"] < 35 else "❄️ Overbought" if s["rsi"] > 65 else "✅ Normal"

    chg_escaped = chg.replace("+", "\\+").replace("-", "\\-").replace("%", "\\%")

    return (
        f"{e} {hd}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *{s['name']}*\n"
        f"💰 CMP: ₹{s['cmp']} \\({chg_escaped}\\)\n"
        f"🕐 Time: {now}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *TRADE SETUP*\n"
        f"▶️ Entry:     ₹{s['entry']}\n"
        f"🛑 Stop Loss: ₹{s['sl']}\n"
        f"🎯 Target 1:  ₹{s['t1']}\n"
        f"🎯 Target 2:  ₹{s['t2']}\n"
        f"🎯 Target 3:  ₹{s['t3']}\n"
        f"⚖️ R:R Ratio: 1:{s['rr']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 *INDICATORS*\n"
        f"• RSI:    {s['rsi']} \\— {rsi_tag}\n"
        f"• MACD:   {mk}\n"
        f"• EMA:    {ek}\n"
        f"• Volume: {s['vol']}x {'🚀' if s['vol'] > 2 else '📊'}\n"
        f"• Confidence: {st} {s['conf']}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _Educational only\\. Not SEBI advice\\._\n"
        f"🔔 @SignalBharat"
    )

# ── MAIN ────────────────────────────────────────────
async def main():
    now     = datetime.now(IST)
    job     = get_job_type()
    now_str = now.strftime("%d %b %Y %I:%M %p IST")

    print(f"Job type: {job}")
    print(f"Token: {'SET' if BOT_TOKEN else 'MISSING!'}")

    if not BOT_TOKEN or len(BOT_TOKEN) < 10:
        print("ERROR: BOT_TOKEN missing! Add TELEGRAM_TOKEN in GitHub Secrets.")
        return

    bot = Bot(token=BOT_TOKEN)

    # ── PRE-MARKET ──
    if job == "pre_market":
        text = (
            "⏰ *PRE\\-MARKET ALERT*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now_str}\n"
            "🕘 Market opens in 15 minutes\\!\n\n"
            "📌 *WATCH LEVELS*\n"
            "• NIFTY: Support 22,200 \\| Resistance 22,800\n"
            "• BNKN: Support 48,500 \\| Resistance 49,500\n\n"
            "🎯 Signals 9:15 AM se shuru honge\\!\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ _Educational only\\. Not SEBI advice\\._\n"
            "🔔 @SignalBharat"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=text,
                               parse_mode=ParseMode.MARKDOWN_V2)
        return

    # ── MARKET OPEN ──
    if job == "market_open":
        text = (
            "🔔 *MARKET OPEN — SCANNING*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now_str}\n"
            "📊 NSE/BSE: *OPEN* 🟢\n\n"
            "🎯 AI scanning 10 symbols\\.\\.\\.\n"
            "📈 NIFTY \\| BANKNIFTY \\| F&O\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ _Educational only\\. Not SEBI advice\\._\n"
            "🔔 @SignalBharat"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=text,
                               parse_mode=ParseMode.MARKDOWN_V2)
        return

    # ── MARKET CLOSE ──
    if job == "market_close":
        text = (
            "🔔 *MARKET CLOSED — EOD SUMMARY*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now_str}\n"
            "⏰ NSE Closed at 3:30 PM IST\n\n"
            "💡 Kal ke signals ke liye ready raho\\!\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "⚠️ _Educational only\\. Not SEBI advice\\._\n"
            "🔔 @SignalBharat"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=text,
                               parse_mode=ParseMode.MARKDOWN_V2)
        return

    # ── SIGNAL SCAN ──
    if not is_market_open():
        print("Market closed — no scan needed. Exiting.")
        return

    print(f"Scanning {len(SYMBOLS)} symbols...")
    sent = 0
    for name, ticker in SYMBOLS.items():
        print(f"Analyzing {name}...")
        result = analyze(name, ticker)
        if result:
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=fmt_signal(result),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                sent += 1
                await asyncio.sleep(3)
            except Exception as e:
                print(f"  Send error {name}: {e}")

    print(f"Scan complete. Signals sent: {sent}")

    if sent == 0:
        print("No strong signals found this scan.")

if __name__ == "__main__":
    asyncio.run(main())
