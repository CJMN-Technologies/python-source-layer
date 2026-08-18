import sys
import os

# Add the current directory to sys.path so we can import email_notifier
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from email_notifier import send_cookie_alert

print("Testing Cookie Expiration Email Notifier...")

test_accounts = [
    {"account_label": "Test Account 1 (Primary)", "env_suffix": ""},
    {"account_label": "Test Account 2 (Backup)", "env_suffix": "_1"}
]

try:
    send_cookie_alert(expired_accounts=test_accounts, scraper_name="Test Script")
    print("Test finished. If your .env has SENDER_EMAIL and SENDER_PASSWORD, you should receive an email.")
except Exception as e:
    print(f"Error occurred: {e}")
