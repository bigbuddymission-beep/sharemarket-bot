import asyncio
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from telegram import Bot
from telegram.constants import ParseMode

# ── CONFIG ─────────────────────────────────────────
BOT_TOKEN  = os.environ.get("TELEGRAM_TOKEN") or "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = int(os.environ.get("CHANNEL_ID") or "-1003967766296")
IST        = pytz.timezone('Asia/Kolkata')

NSE_HOLIDAYS_2026 = [
    "2026-01-26", "2026-03-25", "2026-04-02", "2026-04-14",
    "2026-05-01", "2026-08-15", "2026-10-02", "2026-10-20",
    "2026-11-04", "2026-11-25", "2026-12-25",
]

# ── STOCK SYMBOLS ───────────────────────────────────
STOCK_SYMBOLS = {
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "INFY":       "INFY.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "SBIN":       "SBIN.NS",
    "MARUTI":     "MARUTI.NS",
    "ONGC":       "ONGC.NS",
}

# ── OPTION SYMBOLS ──────────────────────────────────
OPTION_SYMBOLS = {
    "NIFTY 50":  {"ticker": "^NSEI",    "lot": 75,  "step": 50},
    "BANKNIFTY": {"ticker": "^NSEBANK", "lot": 35,  "step": 100},
}

# ── MARKDOWNV2 ESCAPE ───────────────────────────────
def esc(text):
    special = r'\_*[]()~`>#+-=|{}.!'
    result  = str(text)
    for ch in special:
        result = result.replace(ch, f'\\{ch}')
    return result

# ── SESSION ─────────────────────────────────────────
def make_session():
    session = requests.Session()
    session.headers.update({
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
    retry = Retry(total=3, backoff_factor=2,
                  status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    return session

# ── MARKET CHECK ────────────────────────────────────
def is_market_open():
    now       = datetime.now(IST)
    today_str = now.strftime("%Y-%m-%d")
    print(f"Current IST: {now.strftime('%A %d %b %Y %I:%M %p IST')}")

    if now.weekday() >= 5:
        print("Weekend — market closed.")
        return False
    if today_str in NSE_HOLIDAYS_2026:
        print(f"NSE Holiday — market closed.")
        return False

    op = now.replace(hour=9,  minute=15, second=0, microsecond=0)
    cl = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if not (op <= now <= cl):
        print(f"Outside market hours. Current: {now.strftime('%I:%M %p')}")
        return False
    return True

def get_job_type():
    now  = datetime.now(IST)
    h, m = now.hour, now.minute
    if h == 9  and m < 16:       return "pre_market"
    if h == 9  and 16 <= m < 20: return "market_open"
    if h == 15 and m >= 30:      return "market_close"
    return "scan"

# ── EXPIRY CALC ─────────────────────────────────────
def get_expiry(index_name):
    now       = datetime.now(IST)
    today     = now.date()
    target_day = 3 if index_name == "NIFTY 50" else 2  # Thu=3, Wed=2
    days_ahead = (target_day - today.weekday()) % 7
    if days_ahead == 0 and now.hour >= 15:
        days_ahead = 7
    expiry = today + timedelta(days=days_ahead)
    while expiry.strftime("%Y-%m-%d") in NSE_HOLIDAYS_2026:
        expiry -= timedelta(days=1)
    return expiry

def get_atm_strike(cmp, step):
    return round(cmp / step) * step

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

# ── COMMON DATA FETCHER ──────────────────────────────
def fetch_data(ticker):
    sess = make_session()
    df   = yf.download(ticker, period="30d", interval="15m",
                       progress=False, auto_adjust=True, session=sess)
    if df is None or len(df) < 40:
        time.sleep(3)
        df = yf.download(ticker, period="30d", interval="15m",
                         progress=False, auto_adjust=True,
                         session=make_session())
    return df

# ── COMMON SCORING ───────────────────────────────────
def calc_score(rsi, macd_v, sig_v, hist, hist_prev,
               ema9_v, ema21_v, ema50_v, cmp, vol_ratio):
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

    if vol_ratio > 1.5:
        score += 1 if score > 0 else -1
    return score

# ── STOCK ANALYSIS ───────────────────────────────────
def analyze_stock(name, ticker):
    try:
        df = fetch_data(ticker)
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
        atr       = float((df["High"].squeeze() - df["Low"].squeeze())
                          .rolling(14).mean().iloc[-1])

        score = calc_score(rsi, macd_v, sig_v, hist, hist_prev,
                           ema9_v, ema21_v, ema50_v, cmp, vol_ratio)

        if score >= 3:    signal = "BUY"
        elif score <= -3: signal = "SELL"
        else:
            print(f"  {name}: Score {score} — no signal")
            return None

        conf = min(94, abs(score) * 10 + 45)

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
        print(f"  {name}: STOCK {signal}! Score={score} Conf={conf}%")

        return dict(name=name, signal=signal, cmp=cmp, chg=chg,
                    entry=entry, sl=sl, t1=t1, t2=t2, t3=t3,
                    rr=rr, rsi=round(rsi, 1), conf=conf,
                    vol=vol_ratio, macd_bull=macd_v > sig_v,
                    ema_bull=ema9_v > ema21_v)
    except Exception as e:
        print(f"  Error {name}: {e}")
        return None

# ── OPTION ANALYSIS ──────────────────────────────────
def analyze_option(name, info):
    ticker = info["ticker"]
    step   = info["step"]
    lot    = info["lot"]

    try:
        df = fetch_data(ticker)
        if df is None or len(df) < 40:
            print(f"  {name}: Not enough data")
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
        atr       = float((high - low).rolling(14).mean().iloc[-1])

        score = calc_score(rsi, macd_v, sig_v, hist, hist_prev,
                           ema9_v, ema21_v, ema50_v, cmp, vol_ratio)

        if score >= 3:    direction = "BUY"
        elif score <= -3: direction = "SELL"
        else:
            print(f"  {name}: Score {score} — no option signal")
            return None

        option_type = "CE" if direction == "BUY" else "PE"
        atm  = get_atm_strike(cmp, step)
        itm  = atm - step if direction == "BUY" else atm + step
        otm  = atm + step if direction == "BUY" else atm - step

        expiry     = get_expiry(name)
        expiry_str = expiry.strftime("%d %b '%y").upper()
        dte        = (expiry - datetime.now(IST).date()).days

        dte_factor  = max(1, dte)
        atm_premium = round(atr * 0.4 * (dte_factor ** 0.5), 1)
        itm_premium = round(atm_premium * 1.6, 1)
        otm_premium = round(atm_premium * 0.5, 1)

        if direction == "BUY":
            entry_price = round(cmp, 0)
            sl_price    = round(cmp - atr * 1.5, 0)
            t1_price    = round(cmp + atr * 2.0, 0)
            t2_price    = round(cmp + atr * 3.5, 0)
        else:
            entry_price = round(cmp, 0)
            sl_price    = round(cmp + atr * 1.5, 0)
            t1_price    = round(cmp - atr * 2.0, 0)
            t2_price    = round(cmp - atr * 3.5, 0)

        move1        = abs(t1_price - entry_price) * 0.5
        move2        = abs(t2_price - entry_price) * 0.5
        prem_t1      = round(atm_premium + move1, 1)
        prem_t2      = round(atm_premium + move2, 1)
        prem_sl      = round(atm_premium * 0.4, 1)
        lot_profit_t1 = round((prem_t1 - atm_premium) * lot)
        lot_profit_t2 = round((prem_t2 - atm_premium) * lot)
        lot_loss      = round((atm_premium - prem_sl) * lot)

        conf = min(94, abs(score) * 10 + 45)
        print(f"  {name}: OPTION {direction} {option_type}! Score={score} Conf={conf}%")

        return dict(
            name=name, direction=direction, option_type=option_type,
            cmp=round(cmp, 0), chg=chg, score=score, conf=conf,
            atm=atm, itm=itm, otm=otm,
            expiry_str=expiry_str, dte=dte,
            atm_premium=atm_premium, itm_premium=itm_premium,
            otm_premium=otm_premium,
            entry_price=entry_price, sl_price=sl_price,
            t1_price=t1_price, t2_price=t2_price,
            prem_t1=prem_t1, prem_t2=prem_t2, prem_sl=prem_sl,
            lot=lot, lot_profit_t1=lot_profit_t1,
            lot_profit_t2=lot_profit_t2, lot_loss=lot_loss,
            rsi=round(rsi, 1), vol=vol_ratio,
            macd_bull=macd_v > sig_v, ema_bull=ema9_v > ema21_v,
            atr=round(atr, 1)
        )
    except Exception as e:
        print(f"  Error {name}: {e}")
        return None

# ── STOCK MESSAGE FORMAT ─────────────────────────────
def fmt_stock_signal(s):
    arrow   = "🟢" if s["signal"] == "BUY" else "🔴"
    header  = "📈 *BUY SIGNAL*" if s["signal"] == "BUY" else "📉 *SELL SIGNAL*"
    chg_str = f"+{s['chg']}%" if s["chg"] >= 0 else f"{s['chg']}%"
    stars   = "⭐" * min(5, max(1, s["conf"] // 20))
    now_str = datetime.now(IST).strftime("%I:%M %p IST")
    mk      = "Bullish 🟢" if s["macd_bull"] else "Bearish 🔴"
    ek      = "Bullish 🟢" if s["ema_bull"]  else "Bearish 🔴"
    rsi_tag = ("🔥 Oversold"    if s["rsi"] < 35
               else "❄️ Overbought" if s["rsi"] > 65
               else "✅ Normal")

    return (
        f"{arrow} {header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *{esc(s['name'])}* \\[STOCK\\]\n"
        f"💰 CMP: ₹{esc(s['cmp'])} \\({esc(chg_str)}\\)\n"
        f"🕐 Time: {esc(now_str)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *TRADE SETUP*\n"
        f"▶️ Entry:     ₹{esc(s['entry'])}\n"
        f"🛑 Stop Loss: ₹{esc(s['sl'])}\n"
        f"🎯 Target 1:  ₹{esc(s['t1'])}\n"
        f"🎯 Target 2:  ₹{esc(s['t2'])}\n"
        f"🎯 Target 3:  ₹{esc(s['t3'])}\n"
        f"⚖️ R:R Ratio: 1:{esc(s['rr'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 *INDICATORS*\n"
        f"• RSI: {esc(s['rsi'])} — {rsi_tag}\n"
        f"• MACD: {mk}\n"
        f"• EMA: {ek}\n"
        f"• Volume: {esc(s['vol'])}x {'🚀' if s['vol'] > 2 else '📊'}\n"
        f"• Confidence: {stars} {esc(s['conf'])}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _Educational only\\. Not SEBI advice\\._\n"
        f"🔔 @SignalBharat"
    )

# ── OPTION MESSAGE FORMAT ────────────────────────────
def fmt_option_signal(s):
    arrow     = "🟢" if s["direction"] == "BUY" else "🔴"
    opt_emoji = "📈" if s["option_type"] == "CE" else "📉"
    chg_str   = f"+{s['chg']}%" if s["chg"] >= 0 else f"{s['chg']}%"
    stars     = "⭐" * min(5, max(1, s["conf"] // 20))
    now_str   = datetime.now(IST).strftime("%I:%M %p IST")
    mk        = "Bullish 🟢" if s["macd_bull"] else "Bearish 🔴"
    ek        = "Bullish 🟢" if s["ema_bull"]  else "Bearish 🔴"
    rsi_tag   = ("🔥 Oversold"    if s["rsi"] < 35
                 else "❄️ Overbought" if s["rsi"] > 65
                 else "✅ Normal")
    action    = "BUY CE" if s["option_type"] == "CE" else "BUY PE"

    return (
        f"{arrow} {opt_emoji} *{esc(action)} SIGNAL*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *{esc(s['name'])}* \\[OPTIONS\\]\n"
        f"💹 Spot: ₹{esc(s['cmp'])} \\({esc(chg_str)}\\)\n"
        f"🕐 Time: {esc(now_str)}\n"
        f"📅 Expiry: {esc(s['expiry_str'])} \\({esc(s['dte'])} days\\)\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *OPTION STRIKES*\n"
        f"• ITM {esc(s['option_type'])}: {esc(s['itm'])} @ ₹{esc(s['itm_premium'])} est\\.\n"
        f"• ATM {esc(s['option_type'])}: {esc(s['atm'])} @ ₹{esc(s['atm_premium'])} est\\. ⭐\n"
        f"• OTM {esc(s['option_type'])}: {esc(s['otm'])} @ ₹{esc(s['otm_premium'])} est\\.\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *SPOT LEVELS*\n"
        f"▶️ Entry: ₹{esc(s['entry_price'])}\n"
        f"🛑 SL:    ₹{esc(s['sl_price'])}\n"
        f"🎯 T1:    ₹{esc(s['t1_price'])}\n"
        f"🎯 T2:    ₹{esc(s['t2_price'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *PREMIUM TARGETS \\(ATM\\)*\n"
        f"▶️ Buy Around: ₹{esc(s['atm_premium'])}\n"
        f"🛑 SL Below:   ₹{esc(s['prem_sl'])}\n"
        f"🎯 Target 1:   ₹{esc(s['prem_t1'])}\n"
        f"🎯 Target 2:   ₹{esc(s['prem_t2'])}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 *LOT SIZE: {esc(s['lot'])} qty*\n"
        f"✅ Profit T1: ₹{esc(s['lot_profit_t1'])} per lot\n"
        f"✅ Profit T2: ₹{esc(s['lot_profit_t2'])} per lot\n"
        f"❌ Max Loss:  ₹{esc(s['lot_loss'])} per lot\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📐 *INDICATORS*\n"
        f"• RSI: {esc(s['rsi'])} — {rsi_tag}\n"
        f"• MACD: {mk}\n"
        f"• EMA: {ek}\n"
        f"• ATR: {esc(s['atr'])} pts\n"
        f"• Volume: {esc(s['vol'])}x {'🚀' if s['vol'] > 2 else '📊'}\n"
        f"• Confidence: {stars} {esc(s['conf'])}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ _Premium estimated\\. Check live on NSE\\._\n"
        f"⚠️ _Educational only\\. Not SEBI advice\\._\n"
        f"🔔 @SignalBharat"
    )

# ── MAIN ────────────────────────────────────────────
async def main():
    now     = datetime.now(IST)
    job     = get_job_type()
    now_str = esc(now.strftime("%d %b %Y %I:%M %p IST"))

    print(f"Job type: {job}")
    print(f"Token: {'SET' if BOT_TOKEN else 'MISSING!'}")

    if not BOT_TOKEN or len(BOT_TOKEN) < 10:
        print("ERROR: BOT_TOKEN missing!")
        return

    bot = Bot(token=BOT_TOKEN)

    # ── PRE-MARKET ──
    if job == "pre_market":
        text = (
            "⏰ *PRE\\-MARKET ALERT*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now_str}\n"
            "🕘 Market opens in 15 minutes\\!\n\n"
            "📌 *TODAY SCAN*\n"
            "• NIFTY & BANKNIFTY Options CE/PE\n"
            "• RELIANCE, TCS, HDFC, INFY\n"
            "• ICICI, SBIN, MARUTI, ONGC\n\n"
            "🎯 Signals 9:15 AM se shuru\\!\n"
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
            "🔔 *MARKET OPEN — FULL SCAN*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"📅 {now_str}\n"
            "📊 NSE/BSE: *OPEN* 🟢\n\n"
            "🔍 Scanning:\n"
            "• 📈 Options: NIFTY \\| BANKNIFTY\n"
            "• 📊 Stocks: 8 symbols\n"
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
            "🔔 *MARKET CLOSED — EOD*\n"
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

    # ── FULL SCAN ───────────────────────────────────
    if not is_market_open():
        print("Market closed — exiting.")
        return

    sent = 0

    # ── STEP 1: OPTIONS FIRST ──
    print(f"\n── OPTIONS SCAN ──")
    for name, info in OPTION_SYMBOLS.items():
        print(f"Analyzing {name} options...")
        result = analyze_option(name, info)
        if result:
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=fmt_option_signal(result),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                sent += 1
                await asyncio.sleep(3)
            except Exception as e:
                print(f"  Send error {name}: {e}")
        await asyncio.sleep(1)

    # ── STEP 2: STOCKS ──
    print(f"\n── STOCKS SCAN ──")
    for name, ticker in STOCK_SYMBOLS.items():
        print(f"Analyzing {name}...")
        result = analyze_stock(name, ticker)
        if result:
            try:
                await bot.send_message(
                    chat_id=CHANNEL_ID,
                    text=fmt_stock_signal(result),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                sent += 1
                await asyncio.sleep(3)
            except Exception as e:
                print(f"  Send error {name}: {e}")
        await asyncio.sleep(1)

    print(f"\nScan complete. Total signals sent: {sent}")
    if sent == 0:
        print("No strong signals this scan.")

if __name__ == "__main__":
    asyncio.run(main())
