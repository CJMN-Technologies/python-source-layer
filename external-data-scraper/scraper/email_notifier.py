import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECEIVER_EMAILS = os.getenv("RECEIVER_EMAIL")

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

def send_cookie_alert():
    subject = "⚠️ ACTION REQUIRED: Facebook Cookies Expired (Scraper Blocked)"
    
    html = """
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #d32f2f;">Scraper Blocked by Login Wall</h2>
        <p>The automated calendar scraper was blocked by Facebook's login wall. This means the Facebook cookies in your GitHub Secrets have expired.</p>
        <p><b>Action Required:</b></p>
        <ol>
          <li>Export fresh cookies using your browser extension.</li>
          <li>Go to your GitHub Repository Settings > Secrets and variables > Actions.</li>
          <li>Update <code>FB_XS</code> and <code>FB_SB</code> with the new values.</li>
          <li>Manually re-run the workflow in the Actions tab.</li>
        </ol>
      </body>
    </html>
    """
    
    _send_email(subject, html)

def send_pipeline_alert(new_events: list):
    subject = f"🔔 LRT-2 Scraper: {len(new_events)} New Events Found!"
    
    html = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #2e7d32;">New Events Extracted</h2>
        <p>The automated scraper has successfully extracted and classified new events into Supabase.</p>
        <table border="1" cellpadding="8" style="border-collapse: collapse; width: 100%;">
          <tr style="background-color: #f2f2f2;">
            <th>University/LGU</th>
            <th>Category</th>
            <th>Event Name</th>
            <th>Event Date</th>
            <th>Link</th>
          </tr>
    """
    
    for ev in new_events:
        html += f"""
          <tr>
            <td>{ev.get('source_name', '')}</td>
            <td>{ev.get('category', '')}</td>
            <td>{ev.get('event_name', '')}</td>
            <td>{ev.get('event_date', '')}</td>
            <td><a href="{ev.get('url', '#')}">View Post</a></td>
          </tr>
        """
        
    html += """
        </table>
        <p><i>This data is now available in the academic_lgu_events table in Supabase.</i></p>
      </body>
    </html>
    """
    
    _send_email(subject, html)
