import sys
import os

# Add scraper directory to path
scraper_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(scraper_dir)

from email_notifier import send_calendar_alert

print("Sending test email alert...")
send_calendar_alert(
    page_name="TEST UNIVERSITY MAIN CAMPUS", 
    num_rows=15, 
    source_url="https://www.facebook.com/testpost"
)
print("Finished testing email script.")
