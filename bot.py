import requests
import yfinance as yf
import pandas as pd
import time
from datetime import datetime
import talib

BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = -1003967766296

# Indian stocks to monitor
STOCKS = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFC.NS", "ICICIBANK.NS", 
          "SBIN.NS", "LT.NS", "MARUTI.NS", "WIPRO.NS", "BAJAJFINSV.NS"]

def send_signal(message):
    """Send signal to Telegram channel"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print(f"✅ Signal sent successfully!")
            return True
        else:
            print(f"❌ Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending signal: {e}")
        return False

def calculate_indicators(stock_symbol):
    """Calculate technical indicators for stock"""
    try:
        # Fetch 100 days of data
        data = yf.download(stock_symbol, period="100d", progress=False)
        
        if len(data) < 20:
            return None
        
        # Calculate indicators
        data['RSI'] = talib.RSI(data['Close'], timeperiod=14)
        data['MACD'], data['Signal'], data['Hist'] = talib.MACD(data['Close'], fastperiod=12, slowperiod=26, signalperiod=9)
        data['SMA_20'] = talib.SMA(data['Close'], timeperiod=20)
        data['SMA_50'] = talib.SMA(data['Close'], timeperiod=50)
        
        return data
    except Exception as e:
        print(f"❌ Error fetching data for {stock_symbol}: {e}")
        return None

def generate_signals(stock_symbol):
    """Generate buy/sell signals based on indicators"""
    data = calculate_indicators(stock_symbol)
    
    if data is None or len(data) < 2:
        return None
    
    # Get latest values
    current_price = data['Close'].iloc[-1]
    prev_price = data['Close'].iloc[-2]
    
    rsi = data['RSI'].iloc[-1]
    macd = data['MACD'].iloc[-1]
    signal = data['Signal'].iloc[-1]
    sma_20 = data['SMA_20'].iloc[-1]
    sma_50 = data['SMA_50'].iloc[-1]
    
    stock_name = stock_symbol.split('.')[0]
    signal_type = None
    target = None
    stoploss = None
    confidence = 0
    
    # Buy Signal Logic
    if (rsi < 30 and macd > signal and current_price > sma_20):
        signal_type = "BUY"
        target = current_price * 1.03  # 3% target
        stoploss = current_price * 0.98  # 2% stoploss
        confidence = 65
    
    # Strong Buy Signal
    elif (rsi < 25 and macd > signal and current_price > sma_50 and sma_20 > sma_50):
        signal_type = "STRONG BUY"
        target = current_price * 1.05  # 5% target
        stoploss = current_price * 0.97  # 3% stoploss
        confidence = 85
    
    # Sell Signal Logic
    elif (rsi > 70 and macd < signal and current_price < sma_20):
        signal_type = "SELL"
        target = current_price * 0.97  # 3% downside
        stoploss = current_price * 1.02  # 2% stoploss
        confidence = 65
    
    # Strong Sell Signal
    elif (rsi > 75 and macd < signal and current_price < sma_50 and sma_20 < sma_50):
        signal_type = "STRONG SELL"
        target = current_price * 0.95  # 5% downside
        stoploss = current_price * 1.03  # 3% stoploss
        confidence = 85
    
    if signal_type:
        return {
            "stock": stock_name,
            "price": round(current_price, 2),
            "signal": signal_type,
            "target": round(target, 2),
            "stoploss": round(stoploss, 2),
            "confidence": confidence,
            "rsi": round(rsi, 2),
            "macd": round(macd, 4)
        }
    
    return None

def format_signal_message(signal_data):
    """Format signal data into Telegram message"""
    emoji = "🟢" if "BUY" in signal_data["signal"] else "🔴"
    
    message = f"""
{emoji} <b>{signal_data["signal"]} SIGNAL ALERT</b> {emoji}

📊 <b>Stock:</b> {signal_data["stock"]}
💰 <b>Current Price:</b> ₹{signal_data["price"]}

🎯 <b>Target:</b> ₹{signal_data["target"]}
🛑 <b>Stoploss:</b> ₹{signal_data["stoploss"]}

📈 <b>RSI:</b> {signal_data["rsi"]}
📊 <b>MACD:</b> {signal_data["macd"]}
💪 <b>Confidence:</b> {signal_data["confidence"]}%

⏰ <b>Time:</b> {datetime.now().strftime("%d-%m-%Y %H:%M:%S")}

#IndianStocks #NSE #TradingSignal #StockMarket
"""
    return message

def send_startup_message():
    """Send startup notification"""
    message = """
🚀 <b>STOCK SIGNAL BOT STARTED</b> 🚀

✅ Bot is now live and monitoring Indian stocks!
📊 Scanning 10 major NSE stocks every 15 minutes

Stocks being monitored:
• RELIANCE
• TCS
• INFY
• HDFC
• ICICIBANK
• SBIN
• LT
• MARUTI
• WIPRO
• BAJAJFINSV

🎯 You will receive BUY/SELL signals with targets and stoploss
💡 Using advanced technical indicators (RSI, MACD, SMA)

Channel: @Sharemarketdiscussions_bot

#TradingSignals #StockMarket #NSE #IndianStocks
"""
    send_signal(message)
    print("📢 Startup message sent!")

def main():
    """Main bot loop"""
    print("🚀 Indian Share Market Bot Started!")
    print(f"Monitoring stocks: {', '.join(STOCKS)}")
    print(f"Channel ID: {CHANNEL_ID}")
    print(f"Bot started at: {datetime.now()}\n")
    
    # Send startup message
    send_startup_message()
    time.sleep(2)
    
    last_signal_time = {}
    
    while True:
        try:
            print(f"\n🔄 Scanning stocks at {datetime.now().strftime('%H:%M:%S')}...")
            
            signals_found = 0
            for stock in STOCKS:
                stock_name = stock.split('.')[0]
                
                # Avoid duplicate signals (wait 1 hour between signals for same stock)
                if stock_name in last_signal_time:
                    time_diff = (datetime.now() - last_signal_time[stock_name]).total_seconds()
                    if time_diff < 3600:  # Less than 1 hour
                        continue
                
                signal_data = generate_signals(stock)
                
                if signal_data:
                    message = format_signal_message(signal_data)
                    if send_signal(message):
                        last_signal_time[stock_name] = datetime.now()
                        print(f"✅ {signal_data['signal']} signal sent for {stock_name}")
                        signals_found += 1
                        time.sleep(1)  # Prevent rate limiting
            
            if signals_found == 0:
                print("ℹ️ No trading signals found in this scan")
            else:
                print(f"📊 Total signals sent: {signals_found}")
            
            # Wait 15 minutes before next scan
            print(f"⏳ Next scan in 15 minutes... ({datetime.now().strftime('%H:%M:%S')})")
            time.sleep(900)  # 15 minutes
            
        except Exception as e:
            print(f"❌ Error in main loop: {e}")
            print("⏳ Retrying in 60 seconds...")
            time.sleep(60)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n❌ Bot stopped by user")
    except Exception as e:
        print(f"\n\n❌ Critical error: {e}")
