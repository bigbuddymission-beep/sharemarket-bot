import requests

BOT_TOKEN = "667814057:AAGiL1EB6Go3zbYmicm5tyxKucWdfCxRYCY"

def test_bot():
    """Test bot connection and get bot info"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    
    print("🔍 Testing bot connection...\n")
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            print("✅ Bot is connected successfully!")
            print(f"Bot Info: {data['result']}\n")
            return True
        else:
            print(f"❌ Connection failed: {response.status_code}")
            print(f"Error: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Exception: {e}")
        return False

def get_updates():
    """Get latest updates (messages) received by bot"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    
    print("📨 Checking for incoming messages...\n")
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            updates = data.get('result', [])
            
            if updates:
                print(f"✅ Found {len(updates)} updates:")
                for update in updates[-5:]:  # Last 5
                    print(f"  - {update}\n")
            else:
                print("ℹ️ No updates received yet")
            return True
        else:
            print(f"❌ Error: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Exception: {e}")
        return False

if __name__ == "__main__":
    test_bot()
    get_updates()
