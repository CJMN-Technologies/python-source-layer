import os
import json
import time
import random
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import pandas as pd
import requests
from PIL import Image
import io

from auth import get_all_cookie_profiles
from email_notifier import send_calendar_alert, send_cookie_alert
from fb_scraper import (
    candidate_page_urls, click_see_more_buttons, normalize_playwright_cookies,
    get_ancestor, find_ancestor_with_link, clean_url, parse_age_days,
    is_truncated, fetch_full_post_text, get_post_header_text, is_video_post,
    extract_text_from_image, clean_ocr_text
)
from pipeline import _next_ext_id_for_category
from email_notifier import send_pipeline_alert

PROCESSED_FILE = os.path.join(os.path.dirname(__file__), "processed_calendars.json")
OUTPUT_EXCEL = os.path.join(os.path.dirname(__file__), "academic_calendars.xlsx")

CALENDAR_KEYWORDS = ["academic calendar", "school calendar", "collegiate calendar", "university calendar"]

def load_processed() -> set:
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_processed(processed: set):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(list(processed), f)


def append_to_excel(data: list[dict]):
    if not data:
        return
    df_new = pd.DataFrame(data)
    cols = ["id", "station", "source_name", "source_url", "scraped_at", "post_date", "event_date", "event_name", "category"]
    for col in cols:
        if col not in df_new.columns:
            df_new[col] = ""
    df_new = df_new[cols]
    
    if os.path.exists(OUTPUT_EXCEL):
        try:
            df_old = pd.read_excel(OUTPUT_EXCEL)
            df_combined = pd.concat([df_old, df_new], ignore_index=True)
        except Exception:
            df_combined = df_new
    else:
        df_combined = df_new
        
    df_combined.to_excel(OUTPUT_EXCEL, index=False)

def scrape_calendar(page_url: str, page_name: str, page_station: str, cookies: list, max_scrolls: int = 50):
    processed = load_processed()
    posts_found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        
        norm = normalize_playwright_cookies(cookies)
        if norm:
            ctx.add_cookies(norm)
            
        page = ctx.new_page()
        
        for target_url in candidate_page_urls(page_url):
            print(f"Opening: {target_url}")
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                continue
            time.sleep(3)
            
            # Check for login wall
            if page.locator("text=You must log in to continue").count() > 0 or page.locator("text=See more of").count() > 0:
                print("⚠️ Login wall detected! Cookies likely expired.")
                return ["COOKIE_EXPIRED"]
                
            click_see_more_buttons(page)
            
            hit_cutoff = False
            
            for i in range(max_scrolls):
                print(f"  Scrolling... ({i + 1}/{max_scrolls})")
                soup = BeautifulSoup(page.content(), "html.parser")
                
                message_elements = []
                try:
                    message_elements.extend(soup.find_all("div", {"class": re.compile(r"story_body|story|_5pbx")}))
                except Exception:
                    pass
                message_elements.extend(soup.find_all("div", {"data-ad-preview": "message"}))
                message_elements.extend(soup.find_all("div", {"role": "article"}))
                
                for el in message_elements:
                    if is_video_post(el):
                        continue
                        
                    caption_text = el.get_text(separator=" ", strip=True)
                    href = find_ancestor_with_link(el)
                    post_url = clean_url(href) if href else (page.url if page.url else page_url)
                    
                    if post_url in processed:
                        continue
                        
                    age_source_text = get_post_header_text(el, caption_text)
                    post_age_days = parse_age_days(age_source_text) or parse_age_days(caption_text)
                    
                    if post_age_days is None and post_url != page.url:
                        full_text, fetched_age = fetch_full_post_text(ctx, post_url)
                        if fetched_age is not None:
                            post_age_days = fetched_age
                            
                    # Check age limit (30 days)
                    if post_age_days is not None:
                        try:
                            if post_age_days > 30:
                                hit_cutoff = True
                                print(f"  Hit 30-day cutoff ({post_age_days:.1f} days ago). Stopping scroll.")
                                break
                        except OverflowError:
                            hit_cutoff = True
                            print(f"  Hit cutoff (age {post_age_days} too large). Stopping scroll.")
                            break
                    
                    if is_truncated(caption_text) and post_url != page.url:
                        full_text, fetched_age = fetch_full_post_text(ctx, post_url)
                        if full_text:
                            caption_text = full_text
                            
                    caption_lower = caption_text.lower()
                    
                    if "fatima" in page_url.lower():
                        if "antipolo" not in caption_lower and "𝗔𝗻𝘁𝗶𝗽𝗼𝗹𝗼" not in caption_text:
                            continue
                    
                    is_calendar = any(kw in caption_lower for kw in CALENDAR_KEYWORDS)
                    is_target_year = "2026" in caption_lower or "26-27" in caption_lower
                    
                    # Fallback to OCR if caption doesn't explicitly mention it
                    if not (is_calendar and is_target_year):
                        image_container = get_ancestor(el, 8)
                        if image_container:
                            img_count = 0
                            for img in image_container.find_all("img", {"src": True}):
                                if img_count >= 2: # Only check first 2 images to save time
                                    break
                                src = img.get("src", "") or img.get("data-src", "")
                                if "scontent" in src and "emoji" not in src.lower():
                                    try:
                                        ocr_raw = extract_text_from_image(src)
                                        if ocr_raw:
                                            caption_lower += " " + clean_ocr_text(ocr_raw).lower()
                                            img_count += 1
                                    except Exception:
                                        pass
                            
                            # Re-check after OCR
                            is_calendar = any(kw in caption_lower for kw in CALENDAR_KEYWORDS)
                            is_target_year = "2026" in caption_lower or "26-27" in caption_lower
                    
                    if is_calendar and is_target_year:
                        image_container = get_ancestor(el, 8) # Go up 8 levels to ensure we capture multi-image layouts
                        img_urls = []
                        if image_container:
                            for img in image_container.find_all("img", {"src": True}):
                                src = img.get("src", "") or img.get("data-src", "")
                                # Skip tiny icons and emojis
                                if "scontent" in src and "emoji" not in src.lower() and src not in img_urls:
                                    img_urls.append(src)
                        print(f"DEBUG: Calendar match!")
                        
                        print(f"  -> Found Calendar Post! URL: {post_url}")
                        now = datetime.now()
                        ext_id = _next_ext_id_for_category("academic_calendar")
                        
                        data = [{
                            "id": ext_id,
                            "station": page_station,
                            "source_name": page_name,
                            "source_url": post_url,
                            "scraped_at": now.isoformat(),
                            "post_date": (now - timedelta(days=post_age_days)).isoformat() if post_age_days else now.isoformat(),
                            "event_date": "",
                            "event_name": "",
                            "category": "academic_calendar"
                        }]
                        
                        append_to_excel(data)
                        posts_found.extend(data)
                        print(f"     Saved calendar row to Excel.")
                        # Update processed on success
                        processed.add(post_url)
                        save_processed(processed)
                
                if hit_cutoff:
                    break
                    
                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                time.sleep(random.uniform(2.5, 4.5))
                
            if hit_cutoff or posts_found:
                break
                
        browser.close()
    return posts_found

if __name__ == "__main__":
    with open(os.path.join(os.path.dirname(__file__), "pages.json"), "r", encoding="utf-8") as f:
        all_pages = json.load(f)
    
    cookie_profiles = get_all_cookie_profiles()
    if not cookie_profiles:
        print("Error: No Facebook cookies found in environment.")
        exit(1)
        
    active_profile_idx = 0
    cookies = cookie_profiles[active_profile_idx]
    
    # Exclude non-university pages like LGUs and weather agencies
    ignore_keywords = ["PAGASA", "Public Information Office", "PIO", "Government", "Municipality"]
    
    all_new_calendars = []
    
    for p in all_pages:
        if any(kw.lower() in p['name'].lower() for kw in ignore_keywords):
            print(f"\nSkipping non-university page: {p['name']}")
            continue
            
        print(f"\nScanning for calendar: {p['name']} ({p['url']})")
        
        while True:
            result = scrape_calendar(p['url'], p['name'], p['station'], cookies)
            
            if result and result[0] == "COOKIE_EXPIRED":
                active_profile_idx += 1
                if active_profile_idx < len(cookie_profiles):
                    print(f"\n⚠️ Account {active_profile_idx} blocked! Rotating to backup Facebook account {active_profile_idx+1}...")
                    cookies = cookie_profiles[active_profile_idx]
                    continue # Retry the exact same page with new cookies
                else:
                    print("\nAborting full run: ALL Facebook accounts are blocked/expired.")
                    send_cookie_alert()
                    exit(1)
            
            if isinstance(result, list) and len(result) > 0 and result[0] != "COOKIE_EXPIRED":
                # result is a list of data dicts returned from scrape_calendar
                for r in result:
                    if isinstance(r, dict):
                        all_new_calendars.append({
                            "source_name": r["source_name"],
                            "category": "academic_calendar",
                            "event_name": "N/A",
                            "event_date": "N/A",
                            "url": r["source_url"]
                        })
            break # Success or non-cookie completion

    if all_new_calendars:
        print(f"\nSending email alert for {len(all_new_calendars)} new calendars...")
        send_pipeline_alert(all_new_calendars)
    else:
        print("\nNo new calendars found today.")
