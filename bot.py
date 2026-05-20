import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta
import time
import pytz
from datetime import datetime

BOT_TOKEN = "YOUR_BOT_TOKEN"
CHANNEL_USERNAME = "@stocksignlas"

# Stocks + NIFTY
STOCKS = [
    "^NSEI",
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "LT.NS",
    "MARUTI.NS",
    "WIPRO.NS",
    "BAJAJFINSV.NS"
]

def send_signal(message):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": CHANNEL_USERNAME,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        response = requests.post(url, data=data, timeout=10)

        if response.status_code == 200:
            print("✅ Signal Sent")
            return True

        else:
            print(response.text)
            return False

    except Exception as e:
        print(e)
        return False

def calculate_indicators(stock_symbol):

    try:
        data = yf.download(
            stock_symbol,
            period="3mo",
            interval="15m",
            progress=False
        )

        if len(data) < 50:
            return None

        close = data["Close"]

        # RSI
        data["RSI"] = ta.rsi(close, length=14)

        # MACD
        macd = ta.macd(close)

        data["MACD"] = macd["MACD_12_26_9"]
        data["SIGNAL"] = macd["MACDs_12_26_9"]

        # SMA
        data["SMA20"] = ta.sma(close, length=20)
        data["SMA50"] = ta.sma(close, length=50)

        return data

    except Exception as e:
        print(f"{stock_symbol} Error: {e}")
        return None

def generate_signal(stock):

    data = calculate_indicators(stock)

    if data is None:
        return None

    latest = data.iloc[-1]

    price = float(latest["Close"])
    rsi = float(latest["RSI"])
    macd = float(latest["MACD"])
    signal = float(latest["SIGNAL"])
    sma20 = float(latest["SMA20"])

    signal_type = None

    # BUY
    if rsi < 30 and macd > signal and price > sma20:
        signal_type = "BUY"

    # SELL
    elif rsi > 70 and macd < signal and price < sma20:
        signal_type = "SELL"

    if signal_type is None:
        return None

    stock_name = stock.replace(".NS", "")

    # NIFTY OPTION SIGNAL
    option_signal = ""

    if stock == "^NSEI":

        stock_name = "NIFTY 50"

        nifty_strike = round(price / 50) * 50

        if signal_type == "BUY":
            option_signal = f"{nifty_strike} CE"

        else:
            option_signal = f"{nifty_strike} PE"

    target = round(price * 1.03, 2)
    stoploss = round(price * 0.98, 2)

    return {
        "stock": stock_name,
        "price": round(price, 2),
        "signal": signal_type,
        "target": target,
        "stoploss": stoploss,
        "rsi": round(rsi, 2),
        "option": option_signal
    }

def format_message(data):

    emoji = "🟢" if data["signal"] == "BUY" else "🔴"

    option_text = ""

    if data["option"]:
        option_text = f"\n📈 Option: {data['option']}"

    return f"""
{emoji} <b>{data['signal']} SIGNAL</b> {emoji}

📊 Stock: {data['stock']}
💰 Price: ₹{data['price']}
{option_text}

🎯 Target: ₹{data['target']}
🛑 Stoploss: ₹{data['stoploss']}

📈 RSI: {data['rsi']}

⏰ {datetime.now().strftime('%d-%m-%Y %H:%M')}

#NSE #NIFTY #StockMarket
"""

def main():

    print("🚀 Trading Bot Started")

    ist = pytz.timezone("Asia/Kolkata")

    while True:

        try:

            now = datetime.now(ist)

            current_time = now.time()
            current_day = now.weekday()

            market_open = current_time >= datetime.strptime("09:15", "%H:%M").time()
            market_close = current_time <= datetime.strptime("15:30", "%H:%M").time()

            # Monday-Friday only
            if current_day < 5 and market_open and market_close:

                print(f"\n🔄 Market Open - Scanning at {now.strftime('%H:%M')}")

                for stock in STOCKS:

                    signal = generate_signal(stock)

                    if signal:

                        msg = format_message(signal)

                        send_signal(msg)

                        print(f"✅ Signal Sent For {stock}")

                        time.sleep(2)

                print("⏳ Waiting 15 Minutes...")
                time.sleep(900)

            else:

                print("❌ Market Closed")
                time.sleep(300)

        except Exception as e:

            print(f"Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    main()
