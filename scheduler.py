import time
import schedule
from app import check_prices_and_alert

# Define how often to run the checker
schedule.every(30).minutes.do(check_prices_and_alert)  # 🔁 You can change to 10, 60, etc.

print("🔄 Price tracker scheduler started...")

while True:
    schedule.run_pending()
    time.sleep(1)
