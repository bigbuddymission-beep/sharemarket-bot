import requests
from datetime import datetime

BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_USERNAME = "@stocksignlas"

def send_test_message():
    """Send test message to public Telegram channel"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    message = f"""
🧪 <b>TEST MESSAGE - Bot Connection Successful!</b> 🧪

✅ Bot is successfully connected to your channel!
✅ All systems are operational!

🎯 Your Indian Stock Market Bot is now READY!

📊 <b>Features:</b>
• Real-time NSE stock monitoring
• Advanced technical indicators (RSI, MACD, SMA)
• Automated BUY/SELL signals
• Target & Stoploss calculation
• 15-minute scan interval

🚀 Bot will start sending trading signals soon!

Stocks monitored:
RELIANCE | TCS | INFY | HDFC | ICICIBANK | SBIN | LT | MARUTI | WIPRO | BAJAJFINSV

⏰ <b>Test Time:</b> {datetime.now().strftime("%d-%m-%Y %H:%M:%S")}

Bot: @Sharemarketdiscussions_bot
Channel: {CHANNEL_USERNAME}

#IndianStocks #TradingSignals #NSE #StockMarket
"""
    
    data = {
        "chat_id": CHANNEL_USERNAME,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print("✅ TEST MESSAGE SENT SUCCESSFULLY!")
            print(f"Message sent to Channel: {CHANNEL_USERNAME}")
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Exception occurred: {e}")
        return False

if __name__ == "__main__":
    print("🔄 Sending test message to Telegram channel...\n")
    send_test_message()
