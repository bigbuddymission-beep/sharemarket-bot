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

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ─── CONFIGURATION ────────────────────────────────
BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = -1003967766296
IST = pytz.timezone('Asia/Kolkata')

# High Volume Liquid Stocks & Indices for Real Accurate Signals
SYMBOLS = {
    "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "RELIANCE": "RELIANCE.NS",
    "TCS": "TCS.NS",
    "HDFCBANK": "HDFCBANK.NS",
    "ICICIBANK": "ICICIBANK.NS",
    "SBIN": "SBIN.NS",
    "INFY": "INFY.NS",
    "TATAMOTORS": "TATAMOTORS.NS"
}

# ─── MATHEMATICAL INDICATORS ──────────────────────
def calculate_atr(df, period=14):
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return atr

def calculate_supertrend(df, period=10, multiplier=3):
    """Generates accurate algorithmic trend signals"""
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    atr = calculate_atr(df, period)
    hl2 = (high + low) / 2
    
    final_upperband = hl2 + (multiplier * atr)
    final_lowerband = hl2 - (multiplier * atr)
    
    supertrend = np.zeros(len(df))
    direction = np.zeros(len(df))
    
    for i in range(1, len(df)):
        if close.iloc[i] > final_upperband.iloc[i-1]:
            direction[i] = 1
        elif close.iloc[i] < final_lowerband.iloc[i-1]:
            direction[i] = -1
        else:
            direction[i] = direction[i-1]
            if direction[i] == 1 and final_lowerband.iloc[i] < final_lowerband.iloc[i-1]:
                final_lowerband.iloc[i] = final_lowerband.iloc[i-1]
            if direction[i] == -1 and final_upperband.iloc[i] > final_upperband.iloc[i-1]:
                final_upperband.iloc[i] = final_upperband.iloc[i-1]
                
        if direction[i] == 1:
            supertrend[i] = final_lowerband.iloc[i]
        else:
            supertrend[i] = final_upperband.iloc[i]
            
    return pd.Series(supertrend, index=df.index), pd.Series(direction, index=df.index)

# ─── LIVE ALGORITHM ENGINE ────────────────────────
def generate_real_signal(name, ticker):
    try:
        # Fetching latest 15-minute multi-day candle structured interval data
        df = yf.download(ticker, period="10d", interval="15m", progress=False)
        
        if df is None or len(df) < 30:
            logger.warning(f"Insufficient historical data stream for {name}")
            return None
            
        df = df.dropna()
        
        # Calculate moving averages
        df['EMA_Fast'] = df['Close'].ewm(span=9, adjust=False).mean()
        df['EMA_Slow'] = df['Close'].ewm(span=21, adjust=False).mean()
        
        # Calculate Supertrend
        df['Supertrend'], df['Direction'] = calculate_supertrend(df, period=10, multiplier=3)
        
        # Extract last two candles for absolute mathematical confirmation
        current_candle = df.iloc[-1]
        prev_candle = df.iloc[-2]
        
        cmp = float(current_candle['Close'])
        prev_close = float(prev_candle['Close'])
        pct_change = ((cmp - prev_close) / prev_close) * 100
        
        signal = None
        
        # STRATEGY: EMA Crossover + Supertrend Alignment Matrix
        # Condition BUY: Fast EMA crosses above Slow EMA AND Supertrend turns bullish
        if (current_candle['EMA_Fast'] > current_candle['EMA_Slow']) and (current_candle['Direction'] == 1):
            if (prev_candle['EMA_Fast'] <= prev_candle['EMA_Slow']) or (prev_candle['Direction'] == -1):
                signal = "BUY"
                
        # Condition SELL: Fast EMA crosses below Slow EMA AND Supertrend turns bearish
        elif (current_candle['EMA_Fast'] < current_candle['EMA_Slow']) and (current_candle['Direction'] == -1):
            if (prev_candle['EMA_Fast'] >= prev_candle['EMA_Slow']) or (prev_candle['Direction'] == 1):
                signal = "SELL"
                
        if not signal:
            return None
            
        # Target / Stoploss Calculations using current volatility ATR
        atr_val = calculate_atr(df).iloc[-1]
        
        if signal == "BUY":
            sl = cmp - (atr_val * 1.5)
            t1 = cmp + (atr_val * 1.5)
            t2 = cmp + (atr_val * 3.0)
            action_emoji = "🟢 BULLISH BREAKOUT"
        else:
            sl = cmp + (atr_val * 1.5)
            t1 = cmp - (atr_val * 1.5)
            t2 = cmp - (atr_val * 3.0)
            action_emoji = "🔴 BEARISH BREAKDOWN"
            
        rr_ratio = abs(t1 - cmp) / abs(sl - cmp) if abs(sl - cmp) != 0 else 1.0
        
        return {
            "name": name,
            "signal": signal,
            "cmp": round(cmp, 2),
            "pct": round(pct_change, 2),
            "sl": round(sl, 2),
            "t1": round(t1, 2),
            "t2": round(t2, 2),
            "rr": round(rr_ratio, 2),
            "trend": action_emoji,
            "time": datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S')
        }
        
    except Exception as e:
        logger.error(f"Failed parsing mathematical array matrix for {name}: {e}")
        return None

# ─── TELEGRAM MESSAGING PROTOCOL ──────────────────
async def broadcast_signal(bot, data):
    emoji = "🚀" if data["signal"] == "BUY" else "💥"
    
    message = f"""
{emoji} <b>ALGORITHMIC {data['signal']} SIGNAL</b>

📌 <b>Asset:</b> {data['name']}
💰 <b>Execution Price (CMP):</b> ₹{data['cmp']} ({data['pct']}%)
📊 <b>Market Structure:</b> {data['trend']}

━━━━━━━━━━━━━━━━━━
🛑 <b>Strict Stoploss:</b> ₹{data['sl']}
🎯 <b>Target 1 (Conservative):</b> ₹{data['t1']}
🎯 <b>Target 2 (Aggressive):</b> ₹{data['t2']}
⚖️ <b>Risk-Reward Ratio:</b> 1:{data['rr']}

🕒 <b>Timestamp (IST):</b> {data['time']}
⚠️ <i>System Generated Signal. For Educational Studies Only.</i>
"""
    try:
        await bot.send_message(chat_id=CHANNEL_ID, text=message, parse_mode=ParseMode.HTML)
        logger.info(f"Signal successfully transmitted for {data['name']}")
    except Exception as e:
        logger.error(f"Transmission loss on Telegram API: {e}")

# ─── MAIN ENGINE LIFECYCLE ────────────────────────
async def execution_cycle():
    # NSE Market Time validation constraint check
    now = datetime.now(IST)
    
    # Validation Check: Monday=0, Friday=4. Execution between 09:15 to 15:30 IST
    if now.weekday() > 4:
        logger.info("Market is closed (Weekend). Skipping scanner runtime.")
        return
        
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    if not (market_start <= now <= market_end):
        logger.info("Outside official Indian Market operating hours. Skipping scan.")
        return

    logger.info("Initiating Live Market Data Array Scan...")
    bot = Bot(token=BOT_TOKEN)
    
    for name, ticker in SYMBOLS.items():
        signal_data = generate_real_signal(name, ticker)
        if signal_data:
            await broadcast_signal(bot, signal_data)
            await asyncio.sleep(2)  # Avoid rate limit thresholds

def run_async_loop(coroutine):
    asyncio.run(coroutine)

# Real-time scan engine loop interval - Checks every 5 minutes for new candle closed data
schedule.every(5).minutes.do(lambda: run_async_loop(execution_cycle()))

if __name__ == "__main__":
    logger.info("HFT Real-Time Signal Bot active and listening to standard input streams...")
    
    # Script runtime initialization loop
    while True:
        schedule.run_pending()
        time.sleep(1)
