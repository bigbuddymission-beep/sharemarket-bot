# backtest.py — Alag file hai, bot.py se alag rakho
# Run: python backtest.py

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import pytz
import time
import requests
import sqlite3
import os
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

IST = pytz.timezone("Asia/Kolkata")

# ══════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════
BACKTEST_PERIOD   = "6mo"       # 6 months data
BACKTEST_INTERVAL = "15m"       # 15min candles
MIN_RR            = 1.5
ADX_THRESHOLD     = 20
SCORE_THRESHOLD   = 3
INITIAL_CAPITAL   = 100000      # ₹1 lakh
RISK_PER_TRADE    = 0.02        # 2% per trade
DB_PATH           = "backtest_results.db"

SYMBOLS = {
    "NIFTY 50":   "^NSEI",
    "BANKNIFTY":  "^NSEBANK",
    "RELIANCE":   "RELIANCE.NS",
    "TCS":        "TCS.NS",
    "HDFCBANK":   "HDFCBANK.NS",
    "INFY":       "INFY.NS",
    "ICICIBANK":  "ICICIBANK.NS",
    "SBIN":       "SBIN.NS",
    "MARUTI":     "MARUTI.NS",
    "ONGC":       "ONGC.NS",
}

# ══════════════════════════════════════════════════
#  SESSION
# ══════════════════════════════════════════════════
def make_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    })
    retry   = Retry(total=3, backoff_factor=2,
                    status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://",  adapter)
    return s

# ══════════════════════════════════════════════════
#  SQLITE — BACKTEST DB
# ══════════════════════════════════════════════════
def init_backtest_db():
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("DROP TABLE IF EXISTS trades")
    c.execute("""
        CREATE TABLE trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol      TEXT,
            signal      TEXT,
            entry_time  TEXT,
            exit_time   TEXT,
            entry       REAL,
            sl          REAL,
            t1          REAL,
            t2          REAL,
            t3          REAL,
            exit_price  REAL,
            exit_reason TEXT,
            pnl         REAL,
            pnl_pct     REAL,
            rr_achieved REAL,
            result      TEXT,
            score       INTEGER,
            adx         REAL,
            rsi         REAL
        )
    """)
    conn.commit()
    conn.close()

def save_trade(trade: dict):
    conn = sqlite3.connect(DB_PATH)
    c    = conn.cursor()
    c.execute("""
        INSERT INTO trades
        (symbol,signal,entry_time,exit_time,entry,sl,t1,t2,t3,
         exit_price,exit_reason,pnl,pnl_pct,rr_achieved,result,score,adx,rsi)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        trade["symbol"], trade["signal"],
        trade["entry_time"], trade["exit_time"],
        trade["entry"], trade["sl"],
        trade["t1"], trade["t2"], trade["t3"],
        trade["exit_price"], trade["exit_reason"],
        trade["pnl"], trade["pnl_pct"],
        trade["rr_achieved"], trade["result"],
        trade["score"], trade["adx"], trade["rsi"],
    ))
    conn.commit()
    conn.close()

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
    signal = macd.ewm(span=9, adjust=False).mean()
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
        return dx.rolling(period).mean()
    except Exception:
        return pd.Series([25.0] * len(close), index=close.index)

def get_pivot_sr(df_slice):
    """Get pivot S/R from previous day's data"""
    try:
        daily = df_slice.resample("1D").agg({
            "High": "max", "Low": "min", "Close": "last"
        }).dropna()
        if len(daily) < 2:
            return {}
        prev  = daily.iloc[-2]
        ph, pl, pc = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
        pivot = (ph + pl + pc) / 3
        return {
            "r1": 2 * pivot - pl,
            "r2": pivot + (ph - pl),
            "s1": 2 * pivot - ph,
            "s2": pivot - (ph - pl),
            "pivot": pivot, "pdh": ph, "pdl": pl,
        }
    except Exception:
        return {}

# ══════════════════════════════════════════════════
#  SIGNAL GENERATION (same logic as bot.py)
# ══════════════════════════════════════════════════
def generate_signal(i, close, high, low, volume, rsi_s,
                    macd_s, sig_s, ema9, ema21, ema50, adx_s):
    """Generate signal at candle i. Returns (signal, score, atr, rsi, adx)"""
    if i < 60:
        return None, 0, 0, 0, 0

    cmp       = float(close.iloc[i])
    rsi       = float(rsi_s.iloc[i])
    macd_v    = float(macd_s.iloc[i])
    sig_v     = float(sig_s.iloc[i])
    hist      = macd_v - sig_v
    hist_prev = float(macd_s.iloc[i-1]) - float(sig_s.iloc[i-1])
    ema9_v    = float(ema9.iloc[i])
    ema21_v   = float(ema21.iloc[i])
    ema50_v   = float(ema50.iloc[i])
    vol_avg   = float(volume.iloc[max(0,i-20):i].mean())
    vol_now   = float(volume.iloc[i])
    vol_ratio = round(vol_now / (vol_avg + 1), 2)
    adx       = float(adx_s.iloc[i])
    atr       = float((high.iloc[max(0,i-14):i] - low.iloc[max(0,i-14):i]).mean())

    # ADX filter
    if adx < ADX_THRESHOLD:
        return None, 0, atr, rsi, adx

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

    if vol_ratio >= 3.0:
        score += 2 if score > 0 else -2
    elif vol_ratio >= 1.5:
        score += 1 if score > 0 else -1
    elif vol_ratio < 0.7:
        score -= 2

    if score >= SCORE_THRESHOLD:    return "BUY",  score, atr, rsi, adx
    elif score <= -SCORE_THRESHOLD: return "SELL", score, atr, rsi, adx
    return None, score, atr, rsi, adx

# ══════════════════════════════════════════════════
#  TRADE SIMULATION
# ══════════════════════════════════════════════════
def simulate_trade(signal, entry, atr, entry_idx,
                   close, high, low, timestamps):
    """
    Simulate trade from entry_idx forward.
    Returns (exit_price, exit_reason, exit_time, rr_achieved)
    """
    max_sl_dist = entry * 0.03

    if signal == "BUY":
        sl = round(entry - min(atr * 1.5, max_sl_dist), 2)
        t1 = round(entry + atr * 2.0, 2)
        t2 = round(entry + atr * 3.0, 2)
        t3 = round(entry + atr * 4.5, 2)
    else:
        sl = round(entry + min(atr * 1.5, max_sl_dist), 2)
        t1 = round(entry - atr * 2.0, 2)
        t2 = round(entry - atr * 3.0, 2)
        t3 = round(entry - atr * 4.5, 2)

    rr1 = abs(t1 - entry) / (abs(sl - entry) + 0.01)
    if rr1 < MIN_RR:
        return None, "RR_SKIP", None, 0, sl, t1, t2, t3

    trailing_sl = sl
    max_candles = 26  # max hold = ~6.5 hours (26 x 15min)

    for j in range(entry_idx + 1, min(entry_idx + max_candles, len(close))):
        c_high  = float(high.iloc[j])
        c_low   = float(low.iloc[j])
        c_close = float(close.iloc[j])
        ts      = str(timestamps[j])

        if signal == "BUY":
            if c_low <= trailing_sl:
                pnl_pts   = trailing_sl - entry
                rr_achiev = pnl_pts / (abs(sl - entry) + 0.01)
                return trailing_sl, "SL_HIT", ts, rr_achiev, sl, t1, t2, t3

            if c_high >= t3:
                return t3, "T3_HIT", ts, abs(t3-entry)/abs(sl-entry+0.01), sl, t1, t2, t3
            elif c_high >= t2:
                trailing_sl = t1   # lock profit at T1
                if c_high >= t2:
                    return t2, "T2_HIT", ts, abs(t2-entry)/abs(sl-entry+0.01), sl, t1, t2, t3
            elif c_high >= t1:
                trailing_sl = entry  # move to breakeven
        else:  # SELL
            if c_high >= trailing_sl:
                pnl_pts   = entry - trailing_sl
                rr_achiev = pnl_pts / (abs(sl - entry) + 0.01)
                return trailing_sl, "SL_HIT", ts, rr_achiev, sl, t1, t2, t3

            if c_low <= t3:
                return t3, "T3_HIT", ts, abs(t3-entry)/abs(sl-entry+0.01), sl, t1, t2, t3
            elif c_low <= t2:
                trailing_sl = t1
                return t2, "T2_HIT", ts, abs(t2-entry)/abs(sl-entry+0.01), sl, t1, t2, t3
            elif c_low <= t1:
                trailing_sl = entry

    # Time exit
    exit_p = float(close.iloc[min(entry_idx + max_candles - 1, len(close)-1)])
    rr     = (exit_p - entry) / (abs(sl - entry) + 0.01)
    if signal == "SELL":
        rr = (entry - exit_p) / (abs(sl - entry) + 0.01)
    return exit_p, "TIME_EXIT", str(timestamps[min(entry_idx+max_candles-1, len(close)-1)]), rr, sl, t1, t2, t3

# ══════════════════════════════════════════════════
#  BACKTEST ONE SYMBOL
# ══════════════════════════════════════════════════
def backtest_symbol(name, ticker):
    print(f"\n{'='*50}")
    print(f"Backtesting: {name} ({ticker})")
    print(f"{'='*50}")

    try:
        df = yf.download(ticker, period=BACKTEST_PERIOD,
                         interval=BACKTEST_INTERVAL,
                         progress=False, auto_adjust=True,
                         session=make_session())
        if df is None or len(df) < 100:
            time.sleep(3)
            df = yf.download(ticker, period=BACKTEST_PERIOD,
                             interval=BACKTEST_INTERVAL,
                             progress=False, auto_adjust=True,
                             session=make_session())
        if df is None or len(df) < 100:
            print(f"  Not enough data for {name}")
            return []

        df.index = pd.to_datetime(df.index)
        close     = df["Close"].squeeze()
        high      = df["High"].squeeze()
        low       = df["Low"].squeeze()
        volume    = df["Volume"].squeeze()
        timestamps = df.index

        # Pre-compute indicators
        rsi_s  = get_rsi(close)
        macd_s, sig_s = get_macd(close)
        ema9   = get_ema(close, 9)
        ema21  = get_ema(close, 21)
        ema50  = get_ema(close, 50)
        adx_s  = get_adx(high, low, close)

        trades         = []
        last_signal_i  = -20  # cooldown: 20 candles = 5 hours

        for i in range(60, len(close) - 1):
            # Cooldown check (15 min = 1 candle, but use 20 for quality)
            if (i - last_signal_i) < 20:
                continue

            # Market hours filter (IST)
            ts  = pd.Timestamp(timestamps[i])
            try:
                ts_ist = ts.tz_convert(IST) if ts.tzinfo else ts.tz_localize("UTC").tz_convert(IST)
            except Exception:
                ts_ist = ts
            h, m = ts_ist.hour, ts_ist.minute

            # Skip unsafe windows
            cur_min = h * 60 + m
            if (9*60+15) <= cur_min <= (9*60+29):   continue  # opening
            if (12*60+0)  <= cur_min <= (12*60+29):  continue  # lunch
            if (15*60+0)  <= cur_min <= (15*60+30):  continue  # close
            if not ((9*60+15) <= cur_min <= (15*60+30)): continue  # outside hours

            signal, score, atr, rsi, adx = generate_signal(
                i, close, high, low, volume,
                rsi_s, macd_s, sig_s, ema9, ema21, ema50, adx_s
            )

            if signal is None:
                continue

            entry     = float(close.iloc[i])
            entry_time = str(timestamps[i])

            result = simulate_trade(
                signal, entry, atr, i,
                close, high, low, timestamps
            )
            exit_price, exit_reason, exit_time, rr_achieved, sl, t1, t2, t3 = result

            if exit_reason == "RR_SKIP":
                continue

            # PnL calculation
            if signal == "BUY":
                pnl     = exit_price - entry
                pnl_pct = (pnl / entry) * 100
            else:
                pnl     = entry - exit_price
                pnl_pct = (pnl / entry) * 100

            win = pnl > 0

            trade = {
                "symbol":       name,
                "signal":       signal,
                "entry_time":   entry_time,
                "exit_time":    exit_time or entry_time,
                "entry":        round(entry, 2),
                "sl":           round(sl, 2),
                "t1":           round(t1, 2),
                "t2":           round(t2, 2),
                "t3":           round(t3, 2),
                "exit_price":   round(exit_price, 2),
                "exit_reason":  exit_reason,
                "pnl":          round(pnl, 2),
                "pnl_pct":      round(pnl_pct, 2),
                "rr_achieved":  round(rr_achieved, 2),
                "result":       "WIN" if win else "LOSS",
                "score":        score,
                "adx":          round(adx, 1),
                "rsi":          round(rsi, 1),
            }
            trades.append(trade)
            save_trade(trade)
            last_signal_i = i

        print(f"  Total trades simulated: {len(trades)}")
        return trades

    except Exception as e:
        print(f"  Error {name}: {e}")
        return []

# ══════════════════════════════════════════════════
#  PERFORMANCE REPORT
# ══════════════════════════════════════════════════
def generate_report(all_trades: list):
    if not all_trades:
        print("\nNo trades to report.")
        return

    df = pd.DataFrame(all_trades)

    total       = len(df)
    wins        = len(df[df["result"] == "WIN"])
    losses      = len(df[df["result"] == "LOSS"])
    win_rate    = round((wins / total) * 100, 1) if total > 0 else 0

    avg_win     = round(df[df["result"]=="WIN"]["pnl_pct"].mean(), 2)  if wins   > 0 else 0
    avg_loss    = round(df[df["result"]=="LOSS"]["pnl_pct"].mean(), 2) if losses > 0 else 0
    avg_rr      = round(df["rr_achieved"].mean(), 2)

    total_pnl   = round(df["pnl_pct"].sum(), 2)
    best_trade  = round(df["pnl_pct"].max(), 2)
    worst_trade = round(df["pnl_pct"].min(), 2)

    # Exit reason breakdown
    exit_counts = df["exit_reason"].value_counts()

    # Signal breakdown
    buy_df     = df[df["signal"] == "BUY"]
    sell_df    = df[df["signal"] == "SELL"]
    buy_wr     = round(len(buy_df[buy_df["result"]=="WIN"]) / max(len(buy_df),1) * 100, 1)
    sell_wr    = round(len(sell_df[sell_df["result"]=="WIN"]) / max(len(sell_df),1) * 100, 1)

    # Per symbol stats
    sym_stats = df.groupby("symbol").agg(
        Trades=("result","count"),
        Wins=("result", lambda x: (x=="WIN").sum()),
        WinRate=("result", lambda x: round((x=="WIN").mean()*100,1)),
        AvgPnL=("pnl_pct", lambda x: round(x.mean(),2)),
        TotalPnL=("pnl_pct", lambda x: round(x.sum(),2)),
    ).sort_values("WinRate", ascending=False)

    # Consecutive losses (max drawdown indicator)
    results_list = df["result"].tolist()
    max_consec_loss = 0
    curr_loss       = 0
    for r in results_list:
        if r == "LOSS":
            curr_loss += 1
            max_consec_loss = max(max_consec_loss, curr_loss)
        else:
            curr_loss = 0

    # Capital simulation
    capital  = INITIAL_CAPITAL
    cap_hist = [capital]
    for _, row in df.iterrows():
        risk_amt  = capital * RISK_PER_TRADE
        pos_size  = risk_amt / max(abs(row["entry"] - row["sl"]), 0.01)
        trade_pnl = pos_size * row["pnl"]
        capital  += trade_pnl
        cap_hist.append(capital)

    peak        = max(cap_hist)
    drawdowns   = [(peak - c) / peak * 100 for c in cap_hist]
    max_dd      = round(max(drawdowns), 1)
    final_cap   = round(capital, 0)
    total_return = round(((final_cap - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100, 1)

    # Profit factor
    gross_profit = df[df["pnl_pct"]>0]["pnl_pct"].sum()
    gross_loss   = abs(df[df["pnl_pct"]<0]["pnl_pct"].sum())
    profit_factor = round(gross_profit / max(gross_loss, 0.01), 2)

    # ── PRINT REPORT ──
    sep = "═" * 55
    print(f"\n{sep}")
    print(f"  📊 BACKTEST REPORT — SignalBharat Bot")
    print(f"  Period: {BACKTEST_PERIOD} | Interval: {BACKTEST_INTERVAL}")
    print(sep)

    print(f"\n{'─'*55}")
    print(f"  OVERALL PERFORMANCE")
    print(f"{'─'*55}")
    print(f"  Total Trades      : {total}")
    print(f"  Wins              : {wins}  |  Losses: {losses}")
    print(f"  Win Rate          : {win_rate}%")
    print(f"  Profit Factor     : {profit_factor}  (>1.5 = good)")
    print(f"  Avg Win           : +{avg_win}%")
    print(f"  Avg Loss          : {avg_loss}%")
    print(f"  Avg RR Achieved   : {avg_rr}")
    print(f"  Best Trade        : +{best_trade}%")
    print(f"  Worst Trade       : {worst_trade}%")
    print(f"  Max Consec Losses : {max_consec_loss}")

    print(f"\n{'─'*55}")
    print(f"  CAPITAL SIMULATION (₹{INITIAL_CAPITAL:,} start, 2% risk)")
    print(f"{'─'*55}")
    print(f"  Final Capital     : ₹{final_cap:,}")
    print(f"  Total Return      : {total_return}%")
    print(f"  Max Drawdown      : -{max_dd}%")

    print(f"\n{'─'*55}")
    print(f"  BUY vs SELL")
    print(f"{'─'*55}")
    print(f"  BUY signals  : {len(buy_df)} trades | Win Rate: {buy_wr}%")
    print(f"  SELL signals : {len(sell_df)} trades | Win Rate: {sell_wr}%")

    print(f"\n{'─'*55}")
    print(f"  EXIT REASONS")
    print(f"{'─'*55}")
    for reason, cnt in exit_counts.items():
        pct = round(cnt/total*100, 1)
        print(f"  {reason:<15}: {cnt} ({pct}%)")

    print(f"\n{'─'*55}")
    print(f"  PER SYMBOL STATS")
    print(f"{'─'*55}")
    print(f"  {'Symbol':<14} {'Trades':>6} {'Wins':>5} {'WinRate':>8} {'AvgPnL':>8} {'TotalPnL':>9}")
    print(f"  {'─'*14} {'─'*6} {'─'*5} {'─'*8} {'─'*8} {'─'*9}")
    for sym, row in sym_stats.iterrows():
        wr_emoji = "✅" if row["WinRate"] >= 55 else "⚠️" if row["WinRate"] >= 45 else "❌"
        print(f"  {sym:<14} {int(row['Trades']):>6} {int(row['Wins']):>5} "
              f"{row['WinRate']:>7}% {row['AvgPnL']:>+8.2f}% "
              f"{row['TotalPnL']:>+9.2f}% {wr_emoji}")

    print(f"\n{'─'*55}")
    print(f"  VERDICT")
    print(f"{'─'*55}")

    if win_rate >= 55 and profit_factor >= 1.5 and max_dd <= 20:
        verdict = "✅ PROFITABLE — Safe to paper trade"
        note    = "Win rate and profit factor both good."
    elif win_rate >= 50 and profit_factor >= 1.2:
        verdict = "⚠️  MARGINAL — Needs more optimization"
        note    = "Borderline. Paper trade first, don't go live yet."
    else:
        verdict = "❌ NOT PROFITABLE — Strategy needs fixing"
        note    = "Win rate too low. Adjust score threshold or filters."

    print(f"  {verdict}")
    print(f"  {note}")
    print(f"\n  📁 Full trade log saved: {DB_PATH}")
    print(f"  Run: python backtest.py --csv  to export trades")
    print(sep)

    # Save summary CSV
    df.to_csv("backtest_trades.csv", index=False)
    print(f"  📄 CSV exported: backtest_trades.csv")

    return {
        "total": total, "win_rate": win_rate,
        "profit_factor": profit_factor,
        "total_return": total_return,
        "max_drawdown": max_dd,
    }

# ══════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════
def main():
    print("\n🔁 SignalBharat — Backtest Engine")
    print(f"   Symbols  : {len(SYMBOLS)}")
    print(f"   Period   : {BACKTEST_PERIOD}")
    print(f"   Interval : {BACKTEST_INTERVAL}")
    print(f"   Min RR   : {MIN_RR}")
    print(f"   Score    : {SCORE_THRESHOLD}+")
    print(f"   ADX      : {ADX_THRESHOLD}+")
    print()

    init_backtest_db()
    all_trades = []

    for name, ticker in SYMBOLS.items():
        trades = backtest_symbol(name, ticker)
        all_trades.extend(trades)
        time.sleep(2)  # rate limit

    generate_report(all_trades)

if __name__ == "__main__":
    import sys
    if "--csv" in sys.argv:
        # Just export existing DB to CSV
        conn = sqlite3.connect(DB_PATH)
        df   = pd.read_sql("SELECT * FROM trades", conn)
        conn.close()
        df.to_csv("backtest_trades.csv", index=False)
        print("Exported to backtest_trades.csv")
    else:
        main()
