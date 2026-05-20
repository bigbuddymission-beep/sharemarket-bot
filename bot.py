import requests
import time

BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"
CHANNEL_ID = -1003967766296

def send_signal(message):

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": CHANNEL_ID,
        "text": message
    }

    response = requests.post(url, data=data)

    print(response.text)

while True:

    signal = """
🔥 STOCK SIGNAL ALERT 🔥

📈 BUY: RELIANCE
🎯 TARGET: 2950
🛑 STOPLOSS: 2890

#stocks #trading
"""

    send_signal(signal)

    print("Signal Sent Successfully")

    time.sleep(3600)
