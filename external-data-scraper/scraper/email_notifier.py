import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

def mask_ci_text(val: str):
    """Emit GitHub Actions ::add-mask:: workflow command to scrub sensitive text from public runner logs."""
    if val and (os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"):
        for line in str(val).splitlines():
            clean = line.strip()
            if len(clean) >= 6:
                print(f"::add-mask::{clean}", flush=True)

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECEIVER_EMAILS = os.getenv("RECEIVER_EMAIL")

mask_ci_text(SENDER_EMAIL)
mask_ci_text(RECEIVER_EMAILS)

def _send_email(subject: str, html_body: str):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAILS:
        print("Warning: SENDER_EMAIL, SENDER_PASSWORD, or RECEIVER_EMAIL not set. Skipping Email notification.")
        return

    recipients = [email.strip() for email in RECEIVER_EMAILS.split(",") if email.strip()]
    if not recipients:
        return

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject

    msg.attach(MIMEText(html_body, 'html'))

    try:
        # Use Gmail SMTP server
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"Email alert sent successfully to {len(recipients)} recipients!")
    except Exception as e:
        print(f"Failed to send Email alert: {e}")

def send_calendar_alert(page_name: str, num_rows: int, source_url: str = None):
    subject = f"🚨 New Academic Calendar Extracted: {page_name}"
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #0056b3;">New Academic Calendar Found!</h2>
        <p>The automated scraper has successfully extracted a new academic calendar.</p>
        <ul>
          <li><b>University / Page:</b> {page_name}</li>
          <li><b>Rows Extracted:</b> {num_rows}</li>
        </ul>
    """
    
    if source_url:
        html += f'<p><b>Source Post:</b> <a href="{source_url}">View Facebook Post</a></p>'
        
    html += """
        <p><i>This data has been saved to academic_calendars.xlsx in the GitHub repository.</i></p>
      </body>
    </html>
    """
    
    _send_email(subject, html)

def send_cookie_alert(expired_accounts: list[dict] = None, scraper_name: str = "Scraper"):
    """Send an alert when Facebook cookies have expired.
    
    Args:
        expired_accounts: List of dicts with keys:
            - account_label: Human-readable label (e.g. "Account 1 (Primary)")
            - env_suffix: The env var suffix (e.g. "" for primary, "_1" for backup 1)
        scraper_name: Which scraper triggered the alert (e.g. "Events Pipeline", "Calendar Scraper")
    """
    if not expired_accounts:
        expired_accounts = [{"account_label": "All Accounts", "env_suffix": "unknown"}]
    
    num_expired = len(expired_accounts)
    subject = f"⚠️ ACTION REQUIRED: {num_expired} Facebook Cookie(s) Expired — {scraper_name}"
    
    # Build account rows
    account_rows = ""
    for acc in expired_accounts:
        suffix = acc.get("env_suffix", "")
        label = acc.get("account_label", "Unknown")
        
        if suffix == "":
            secrets_to_update = "<code>FB_C_USER</code>, <code>FB_XS</code>, <code>FB_DATR</code>, <code>FB_FR</code>, <code>FB_SB</code>"
        else:
            secrets_to_update = f"<code>FB_C_USER{suffix}</code>, <code>FB_XS{suffix}</code>, <code>FB_DATR{suffix}</code>, <code>FB_FR{suffix}</code>, <code>FB_SB{suffix}</code>"
        
        account_rows += f"""
              <tr style="border-bottom: 1px solid #e0e0e0;">
                <td style="padding: 12px 8px; font-weight: bold; color: #333;">{label}</td>
                <td style="padding: 12px 8px; font-family: monospace; font-size: 13px; color: #555;">{secrets_to_update}</td>
              </tr>
        """
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; margin: 0; padding: 20px; background-color: #f9f9f9;">
        <div style="max-width: 700px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); border-top: 5px solid #d32f2f;">
          <h2 style="color: #d32f2f; margin-top: 0; font-size: 24px;">🚨 Scraper Blocked — Cookies Expired</h2>
          <p style="font-size: 15px; line-height: 1.5; color: #555;">
            The <b>{scraper_name}</b> was blocked by Facebook's login wall. The following account(s) need their cookies refreshed:
          </p>
          
          <table border="0" cellpadding="10" cellspacing="0" style="border-collapse: collapse; width: 100%; margin-top: 15px; font-size: 14px;">
            <thead>
              <tr style="background-color: #fde8e8; text-align: left; border-bottom: 2px solid #d32f2f;">
                <th style="font-weight: bold; color: #d32f2f; padding: 12px 8px;">Account</th>
                <th style="font-weight: bold; color: #d32f2f; padding: 12px 8px;">GitHub Secrets to Update</th>
              </tr>
            </thead>
            <tbody>
              {account_rows}
            </tbody>
          </table>
          
          <div style="background-color: #fff8e1; border-left: 4px solid #f9a825; padding: 15px; margin-top: 20px; border-radius: 4px;">
            <p style="margin: 0; font-size: 14px; color: #555;"><b>How to fix:</b></p>
            <ol style="margin: 10px 0 0 0; padding-left: 20px; font-size: 14px; color: #555; line-height: 1.8;">
              <li>Log in to the Facebook account in your browser.</li>
              <li>Export fresh cookies using your browser extension.</li>
              <li>Go to <b>GitHub Repository Settings → Secrets and variables → Actions</b>.</li>
              <li>Update the secrets listed above with the new cookie values.</li>
              <li>Manually re-run the workflow in the Actions tab.</li>
            </ol>
          </div>
          
          <p style="font-size: 13px; color: #888; margin-top: 30px; border-top: 1px solid #eee; padding-top: 15px;">
            <i>This alert was triggered by the {scraper_name}. The scraper was unable to continue and has stopped.</i>
          </p>
        </div>
      </body>
    </html>
    """
    
    _send_email(subject, html)

def _format_event_date(date_str: str) -> str:
    if not date_str or date_str.lower() in ("n/a", "not specified", "null", "none"):
        return "Not specified"
    date_str = date_str.strip()
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%B %d, %Y")
    except Exception:
        pass
    
    # Try parsing range e.g. YYYY-MM-DD to YYYY-MM-DD
    import re
    range_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})$", date_str, re.IGNORECASE)
    if range_match:
        try:
            start_dt = datetime.strptime(range_match.group(1), "%Y-%m-%d")
            end_dt = datetime.strptime(range_match.group(2), "%Y-%m-%d")
            return f"{start_dt.strftime('%B %d, %Y')} to {end_dt.strftime('%B %d, %Y')}"
        except Exception:
            pass
    return date_str

def _format_scraped_at(scraped_at_str: str) -> str:
    if not scraped_at_str:
        return "N/A"
    try:
        # Standardize ISO timestamp format
        clean_str = scraped_at_str.split(".")[0]
        if clean_str.endswith("Z"):
            clean_str = clean_str[:-1]
        dt = datetime.fromisoformat(clean_str)
        return dt.strftime("%B %d, %Y, %I:%M %p")
    except Exception:
        return scraped_at_str

def _format_category(cat: str) -> str:
    if not cat:
        return "N/A"
    mapping = {
        "academic": "Academic",
        "lgu": "LGU",
        "pagasa": "PAGASA",
        "academic_calendar": "Academic Calendar"
    }
    return mapping.get(cat.lower(), cat.capitalize())

def send_pipeline_alert(new_events: list):
    subject = f"🔔 LRT-2 Scraper: {len(new_events)} New Events Found!"
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; margin: 0; padding: 20px; background-color: #f9f9f9;">
        <div style="max-width: 900px; margin: 0 auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 10px rgba(0,0,0,0.05); border-top: 5px solid #2e7d32;">
          <h2 style="color: #2e7d32; margin-top: 0; font-size: 24px;">New Events Extracted</h2>
          <p style="font-size: 15px; line-height: 1.5; color: #555;">The automated scraper has successfully extracted and classified new events into Supabase.</p>
          <table border="0" cellpadding="10" cellspacing="0" style="border-collapse: collapse; width: 100%; margin-top: 20px; font-size: 14px;">
            <thead>
              <tr style="background-color: #f2f5f3; text-align: left; border-bottom: 2px solid #2e7d32;">
                <th style="font-weight: bold; color: #2e7d32; padding: 12px 8px;">University/LGU</th>
                <th style="font-weight: bold; color: #2e7d32; padding: 12px 8px;">Category</th>
                <th style="font-weight: bold; color: #2e7d32; padding: 12px 8px;">Event Name</th>
                <th style="font-weight: bold; color: #2e7d32; padding: 12px 8px;">Event Date</th>
                <th style="font-weight: bold; color: #2e7d32; padding: 12px 8px;">Date Scraped</th>
                <th style="font-weight: bold; color: #2e7d32; padding: 12px 8px;">Link</th>
              </tr>
            </thead>
            <tbody>
    """
    
    for ev in new_events:
        formatted_date = _format_event_date(ev.get('event_date', ''))
        formatted_scraped = _format_scraped_at(ev.get('scraped_at', ''))
        formatted_cat = _format_category(ev.get('category', ''))
        
        html += f"""
              <tr style="border-bottom: 1px solid #e0e0e0;">
                <td style="padding: 12px 8px; font-weight: bold; color: #333;">{ev.get('source_name', '')}</td>
                <td style="padding: 12px 8px;"><span style="background-color: #e8f5e9; color: #2e7d32; padding: 4px 8px; border-radius: 4px; font-size: 12px; font-weight: bold;">{formatted_cat}</span></td>
                <td style="padding: 12px 8px; color: #555;">{ev.get('event_name', '')}</td>
                <td style="padding: 12px 8px; font-weight: bold; color: #444;">{formatted_date}</td>
                <td style="padding: 12px 8px; color: #666; font-size: 13px;">{formatted_scraped}</td>
                <td style="padding: 12px 8px;"><a href="{ev.get('url', '#')}" style="color: #0288d1; text-decoration: none; font-weight: bold;">View Post</a></td>
              </tr>
        """
        
    html += """
            </tbody>
          </table>
          <p style="font-size: 13px; color: #888; margin-top: 30px; border-top: 1px solid #eee; padding-top: 15px;">
            <i>This data is now available in the academic_lgu_events table in Supabase.</i>
          </p>
        </div>
      </body>
    </html>
    """
    
    _send_email(subject, html)


def send_calendar_with_attachment(page_name: str, excel_path: str, source_url: str = None):
    """Send an email with the generated academic calendar Excel file attached."""
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECEIVER_EMAILS:
        print("Warning: Email credentials not set. Skipping calendar attachment email.")
        return

    recipients = [email.strip() for email in RECEIVER_EMAILS.split(",") if email.strip()]
    if not recipients:
        return

    filename = os.path.basename(excel_path)
    subject = f"📅 Academic Calendar Detected: {page_name}"

    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #0056b3;">Academic Calendar Post Detected!</h2>
        <p>The automated calendar scraper has found a new academic calendar release.</p>
        <ul>
          <li><b>University / Page:</b> {page_name}</li>
          <li><b>File:</b> {filename}</li>
        </ul>
    """

    if source_url:
        html += f'<p><b>Source Post:</b> <a href="{source_url}">View Facebook Post</a></p>'

    html += """
        <p>The Excel file is attached. Please fill in the <b>event_name</b> and <b>event_date</b> columns manually.</p>
      </body>
    </html>
    """

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject
    msg.attach(MIMEText(html, 'html'))

    # Attach Excel file
    if os.path.exists(excel_path):
        try:
            with open(excel_path, "rb") as f:
                part = MIMEBase("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={filename}")
                msg.attach(part)
        except Exception as e:
            print(f"  Warning: Could not attach Excel file: {e}")

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"Calendar email with attachment sent to {len(recipients)} recipients!")
    except Exception as e:
        print(f"Failed to send calendar email: {e}")
