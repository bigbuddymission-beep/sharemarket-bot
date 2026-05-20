import requests

BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = -1003967766296

def send_test_message():
    """Send test message to Telegram"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    message = """
🧪 <b>TEST MESSAGE - Bot Working!</b> 🧪

✅ Bot successfully connected to your Telegram channel!
✅ Messages are working perfectly!

🎯 Your Indian Stock Market Bot is ready to send trading signals.

📊 Bot will scan Indian stocks every 15 minutes
🚀 Automated signals will be sent here

#BotTest #StockMarket #TradingSignals
"""
    
    data = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print("✅ TEST MESSAGE SENT SUCCESSFULLY!")
            print(f"Response: {response.json()}")
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Exception: {e}")

if __name__ == "__main__":
    send_test_message()
