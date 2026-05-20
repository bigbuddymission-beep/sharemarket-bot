import requests

BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
BOT_USERNAME = "Sharemarketdiscussions_bot"

def get_bot_info():
    """Get bot info"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            print("✅ Bot Info:")
            print(data)
        else:
            print(f"❌ Error: {response.status_code}")
    except Exception as e:
        print(f"❌ Exception: {e}")

def send_test_message(chat_id):
    """Send test message"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    
    message = """
🧪 <b>TEST MESSAGE - Bot Working!</b> 🧪

✅ Bot successfully connected!
✅ Messages are working perfectly!

🎯 Your Indian Stock Market Bot is ready to send trading signals.

📊 Bot will scan Indian stocks every 15 minutes
🚀 Automated signals will be sent here

Bot: @Sharemarketdiscussions_bot

#BotTest #StockMarket #TradingSignals
"""
    
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print("✅ TEST MESSAGE SENT SUCCESSFULLY!")
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Exception: {e}")
        return False

if __name__ == "__main__":
    print("🔍 Checking bot connection...\n")
    get_bot_info()
    
    print("\n📤 Sending test message...\n")
    print("Aapne jo channel ka ID diya tha, usme message bhejenge.")
    print("Agar message aaya to reply mein batna! 🎉")
