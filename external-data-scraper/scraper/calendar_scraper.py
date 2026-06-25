"""
calendar_scraper.py — Academic Calendar Release Scraper

Scrapes Facebook pages of universities near LRT-2 stations for official
Academic Calendar releases (A.Y. 2026-2027).

Logic (from Academic_CalendarScraper_Logic.md):
    IF  [Primary Identifier]  (academic calendar, school calendar, etc.)
    AND [Timeframe Indicator]  (2026-2027, 26-27, etc.)
    AND ([Action Trigger] OR [Structural Signal])
    AND NOT [Negative Keyword]
    THEN -> Flag as valid calendar release.

Features:
- Unicode font-style normalization (handles bold/italic/script Facebook text)
- Case-insensitive keyword matching via .casefold()
- 30-day post window
- Supabase integration (upserts into external.academic_lgu_events)
- Generates Excel file per school (ACRONYM_AcademicCalendar.xlsx)
- Emails Excel file as attachment to team
- Deduplication via processed_calendars.json
"""

import os
import json
import sys
import time
import random
import re
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import pandas as pd

from auth import get_all_cookie_profiles
from email_notifier import send_calendar_with_attachment, send_cookie_alert
from fb_scraper import (
    candidate_page_urls, click_see_more_buttons, normalize_playwright_cookies,
    get_ancestor, find_ancestor_with_link, clean_url, parse_age_days,
    is_truncated, fetch_full_post_text, get_post_header_text, is_video_post,
    extract_text_from_image, clean_ocr_text, _block_unnecessary_resources
)
from unicode_normalizer import normalize_unicode_text

# Fix Windows console encoding
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MAX_AGE_DAYS = 30.0
MAX_SCROLLS = 15  # ~30 days of posts fits in 12-15 scrolls
MAX_OCR_PER_PAGE = 6  # Conservative OCR budget for free-tier Gemini

PROCESSED_FILE = os.path.join(os.path.dirname(__file__), "processed_calendars.json")

# ---------------------------------------------------------------------------
# Keywords (from Academic_CalendarScraper_Logic.md)
# ---------------------------------------------------------------------------
PRIMARY_IDENTIFIERS = [
    "academic calendar",
    "university calendar",
    "school calendar",
    "collegiate calendar",
]

TIMEFRAME_INDICATORS = [
    "a.y. 2026-2027", "ay 2026-2027", "a.y. 26-27",
    "s.y. 2026-2027", "sy 2026-2027",
    "2026-2027", "26-27",
    # Broader: just mentioning the target academic year
    "a.y. 2026", "ay 2026", "s.y. 2026", "sy 2026",
]

ACTION_TRIGGERS = [
    "released", "out now", "now available", "view", "download",
    "access", "check out", "here is", "here's", "announcing",
    "announced", "official", "updated", "revision",
]

STRUCTURAL_SIGNALS = [
    "bit.ly", "tinyurl.com", "cutt.ly",
    ".edu", ".edu.ph",
    "drive.google.com", "docs.google.com",
]

NEGATIVE_KEYWORDS = [
    "draft", "drafting", "proposed", "tentative",
    "subject to change", "preliminary",
]

# ---------------------------------------------------------------------------
# School acronym mapping
# ---------------------------------------------------------------------------
ACRONYM_MAP = {
    "polytechnic university of the philippines main": "PUP",
    "pup sentral na konseho ng mag-aaral": "PUP_SKM",
    "university of the east manila": "UE",
    "university of the east student council": "UE_USC",
    "far eastern university manila": "FEU",
    "feu central student organization": "FEU_CSO",
    "university of santo tomas": "UST",
    "ust central student council": "UST_CSC",
    "san beda university": "SBU",
    "san beda student council": "SBU_SC",
    "uerm memorial medical center": "UERM",
    "uerm medicine student council": "UERM_MSC",
    "st. paul university quezon city": "SPUQC",
    "st. paul university quezon city sao": "SPUQC_SAO",
    "stella maris college": "SMC",
    "technological institute of the philippines cubao": "TIP",
    "world citi colleges quezon city": "WCC",
    "ateneo de manila university": "ADMU",
    "ateneo sanggunian": "ADMU_SG",
    "university of the philippines diliman": "UPD",
    "up diliman university student council": "UPD_USC",
    "our lady of fatima university antipolo": "OLFU",
}


def get_acronym(name: str) -> str:
    lower = name.strip().lower()
    if lower in ACRONYM_MAP:
        return ACRONYM_MAP[lower]
    # Fallback: take first letter of each word
    words = name.split()
    return "".join([w[0].upper() for w in words if w[0].isalpha()])


# ---------------------------------------------------------------------------
# Deduplication persistence
# ---------------------------------------------------------------------------
def load_processed() -> set:
    if os.path.exists(PROCESSED_FILE):
        with open(PROCESSED_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_processed(processed: set):
    with open(PROCESSED_FILE, "w") as f:
        json.dump(sorted(processed), f, indent=2)


# ---------------------------------------------------------------------------
# ID generation (matches existing pattern: external_acad_cal_NNNN)
# ---------------------------------------------------------------------------
def _next_calendar_id() -> str:
    base = "external_acad_cal"
    try:
        res = (
            supabase.schema("external")
            .table("academic_lgu_events")
            .select("id")
            .ilike("id", f"{base}_%")
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
        rows = res.data if hasattr(res, "data") else res
        if rows:
            last_id = rows[0].get("id") if isinstance(rows[0], dict) else None
            if last_id:
                try:
                    last_num = int(last_id.rsplit("_", 1)[-1])
                    return f"{base}_{(last_num + 1):04d}"
                except Exception:
                    pass
    except Exception:
        pass
    return f"{base}_0001"


# ---------------------------------------------------------------------------
# Boolean filter logic
# ---------------------------------------------------------------------------
def is_valid_calendar_post(text: str) -> bool:
    """
    Apply the Boolean filter from Academic_CalendarScraper_Logic.md:
    
    IF [Primary Identifier] AND [Timeframe Indicator]
       AND ([Action Trigger] OR [Structural Signal])
       AND NOT [Negative Keyword]
    THEN -> Valid calendar release.
    """
    normalized = normalize_unicode_text(text)
    lowered = normalized.casefold()

    # Check primary identifier
    has_primary = any(kw in lowered for kw in PRIMARY_IDENTIFIERS)
    if not has_primary:
        return False

    # Check timeframe indicator
    has_timeframe = any(kw in lowered for kw in TIMEFRAME_INDICATORS)
    if not has_timeframe:
        return False

    # Check action trigger OR structural signal
    has_action = any(kw in lowered for kw in ACTION_TRIGGERS)
    has_structural = any(kw in lowered for kw in STRUCTURAL_SIGNALS)
    if not (has_action or has_structural):
        return False

    # Check negative keywords (reject)
    has_negative = any(kw in lowered for kw in NEGATIVE_KEYWORDS)
    if has_negative:
        return False

    return True


def has_calendar_hint(text: str) -> bool:
    """
    Lighter check: does the text mention a primary identifier + timeframe?
    Used to decide whether to invest in OCR.
    """
    normalized = normalize_unicode_text(text)
    lowered = normalized.casefold()
    has_primary = any(kw in lowered for kw in PRIMARY_IDENTIFIERS)
    has_timeframe = any(kw in lowered for kw in TIMEFRAME_INDICATORS)
    return has_primary or has_timeframe


# ---------------------------------------------------------------------------
# Excel generation
# ---------------------------------------------------------------------------
def generate_calendar_excel(data: list[dict], source_name: str) -> str:
    """
    Generate an Excel file for the calendar with correct columns.
    Returns the file path.
    """
    calendars_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "AcademicCalendars")
    os.makedirs(calendars_dir, exist_ok=True)

    acronym = get_acronym(source_name)
    output_path = os.path.join(calendars_dir, f"{acronym}_AcademicCalendar.xlsx")

    cols = ["id", "station", "source_name", "source_url", "scraped_at", "post_date", "event_date", "event_name", "category"]
    df_new = pd.DataFrame(data)
    for col in cols:
        if col not in df_new.columns:
            df_new[col] = ""
    df_new = df_new[cols]

    # Append to existing file if it exists
    if os.path.exists(output_path):
        try:
            df_old = pd.read_excel(output_path)
            df_combined = pd.concat([df_old, df_new], ignore_index=True)
        except Exception:
            df_combined = df_new
    else:
        df_combined = df_new

    df_combined.to_excel(output_path, index=False)
    return output_path


# ---------------------------------------------------------------------------
# Main scraper function
# ---------------------------------------------------------------------------
def scrape_calendar(page_url: str, page_name: str, page_station: str,
                    cookies: list, max_scrolls: int = MAX_SCROLLS):
    """Scrape a single Facebook page for academic calendar posts."""
    processed = load_processed()
    posts_found = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        norm = normalize_playwright_cookies(cookies)
        if norm:
            try:
                ctx.add_cookies(norm)
            except Exception as e:
                print(f"  Warning: add_cookies failed: {e}")

        page = ctx.new_page()
        page.route("**/*", _block_unnecessary_resources)

        try:
            for target_url in candidate_page_urls(page_url):
                print(f"  Opening: {target_url}")
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"  Surface failed: {e}")
                    continue
                time.sleep(3)

                # Check for login wall
                if page.locator("text=You must log in to continue").count() > 0 or \
                   page.locator("text=See more of").count() > 0:
                    print("  Login wall detected! Cookies likely expired.")
                    browser.close()
                    return ["COOKIE_EXPIRED"]

                click_see_more_buttons(page)

                hit_cutoff = False
                page_ocr_count = 0

                for i in range(max_scrolls):
                    print(f"  Scrolling... ({i + 1}/{max_scrolls})")
                    soup = BeautifulSoup(page.content(), "html.parser")

                    message_elements = []
                    try:
                        message_elements.extend(
                            soup.find_all("div", {"class": re.compile(r"story_body|story|_5pbx")})
                        )
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

                        # --- AGE CHECK ---
                        age_source_text = get_post_header_text(el, caption_text)
                        post_age_days = parse_age_days(age_source_text) or parse_age_days(caption_text)

                        if post_age_days is not None and post_age_days > MAX_AGE_DAYS:
                            hit_cutoff = True
                            print(f"  Hit {MAX_AGE_DAYS}-day cutoff ({post_age_days:.1f}d). Stopping scroll.")
                            break

                        # Fetch full post text if truncated
                        if is_truncated(caption_text) and post_url != page.url:
                            full_text, fetched_age = fetch_full_post_text(ctx, post_url)
                            if full_text:
                                caption_text = full_text
                            if fetched_age is not None:
                                post_age_days = fetched_age
                                if post_age_days > MAX_AGE_DAYS:
                                    hit_cutoff = True
                                    break

                        # Strip trailing "See more" artifacts
                        for suffix in ["see more", "tumingin pa"]:
                            if caption_text.lower().endswith(suffix):
                                caption_text = caption_text[: -len(suffix)].rstrip(". ").strip()

                        # OLFU filter: only Antipolo campus
                        if "fatima" in page_url.casefold() or "fatima" in page_name.casefold():
                            normalized_caption = normalize_unicode_text(caption_text).casefold()
                            if "antipolo" not in normalized_caption:
                                continue

                        # --- CALENDAR KEYWORD CHECK ---
                        combined_text = caption_text

                        # Check if caption alone passes the full Boolean filter
                        if not is_valid_calendar_post(combined_text):
                            # Try OCR if there's at least a hint (primary OR timeframe)
                            if has_calendar_hint(combined_text) and page_ocr_count < MAX_OCR_PER_PAGE:
                                image_container = get_ancestor(el, 8)
                                if image_container:
                                    img_count = 0
                                    for img in image_container.find_all("img", {"src": True}):
                                        if img_count >= 2 or page_ocr_count >= MAX_OCR_PER_PAGE:
                                            break
                                        src = img.get("src", "") or img.get("data-src", "")
                                        if "scontent" in src and "emoji" not in src.lower():
                                            try:
                                                ocr_raw = extract_text_from_image(src)
                                                if ocr_raw:
                                                    combined_text += " " + clean_ocr_text(ocr_raw)
                                                    img_count += 1
                                                    page_ocr_count += 1
                                            except Exception:
                                                page_ocr_count += 1
                                    if img_count > 0:
                                        print(f"  OCR processed {img_count} image(s) [budget: {page_ocr_count}/{MAX_OCR_PER_PAGE}]")

                            # Re-check after OCR
                            if not is_valid_calendar_post(combined_text):
                                continue

                        # --- VALID CALENDAR POST FOUND ---
                        print(f"  >> Calendar match found! URL: {post_url}")

                        now = datetime.now(timezone.utc)
                        if post_age_days is not None:
                            post_date = now - timedelta(days=post_age_days)
                        else:
                            post_date = now

                        ext_id = _next_calendar_id()

                        row = {
                            "id": ext_id,
                            "station": page_station,
                            "source_name": page_name,
                            "source_url": post_url,
                            "scraped_at": now.isoformat(),
                            "post_date": post_date.isoformat(),
                            "event_date": "",  # To be filled manually
                            "event_name": "",  # To be filled manually
                            "category": "academic_calendar",
                        }

                        # Save to Supabase
                        try:
                            supabase.schema("external").table("academic_lgu_events").upsert({
                                "id": ext_id,
                                "station": page_station,
                                "source_name": page_name,
                                "source_url": post_url,
                                "post_text": combined_text[:2000],
                                "category": "academic_calendar",
                                "scraped_at": now.isoformat(),
                                "post_date": post_date.isoformat(),
                            }).execute()
                            print(f"  >> Saved to Supabase: {ext_id}")
                        except Exception as e:
                            print(f"  Failed to save to Supabase: {e}")

                        # Generate Excel
                        excel_path = generate_calendar_excel([row], page_name)
                        print(f"  >> Excel saved: {excel_path}")

                        # Email with attachment
                        try:
                            send_calendar_with_attachment(page_name, excel_path, post_url)
                        except Exception as e:
                            print(f"  Email failed: {e}")

                        posts_found.append(row)
                        processed.add(post_url)
                        save_processed(processed)

                    if hit_cutoff:
                        break

                    page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    time.sleep(random.uniform(2.0, 3.5))

                if hit_cutoff or posts_found:
                    break

        finally:
            browser.close()

    return posts_found


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== Academic Calendar Scraper Starting ===")
    print(f"Time (PHT): {datetime.now(timezone(timedelta(hours=8)))}")
    print(f"Max post age: {MAX_AGE_DAYS} days")

    with open(os.path.join(os.path.dirname(__file__), "pages.json"), "r", encoding="utf-8") as f:
        all_pages = json.load(f)

    cookie_profiles = get_all_cookie_profiles()
    if not cookie_profiles:
        print("Error: No Facebook cookies found in environment.")
        exit(1)

    active_profile_idx = 0
    cookies = cookie_profiles[active_profile_idx]

    # Only scrape university/school pages — skip LGUs and weather agencies
    ignore_keywords = ["pagasa", "public information office", "pio", "government", "municipality"]

    all_new = []

    for p in all_pages:
        if any(kw in p['name'].casefold() for kw in ignore_keywords):
            print(f"\nSkipping non-university page: {p['name']}")
            continue

        print(f"\n[Calendar] Scanning: {p['name']} ({p['station']})")

        while True:
            result = scrape_calendar(p['url'], p['name'], p['station'], cookies)

            if result and result[0] == "COOKIE_EXPIRED":
                active_profile_idx += 1
                if active_profile_idx < len(cookie_profiles):
                    print(f"  Account blocked! Rotating to backup #{active_profile_idx + 1}...")
                    cookies = cookie_profiles[active_profile_idx]
                    continue
                else:
                    print("\nAborting: ALL Facebook accounts are blocked/expired.")
                    send_cookie_alert()
                    exit(1)

            if isinstance(result, list):
                for r in result:
                    if isinstance(r, dict):
                        all_new.append(r)
            break

    print(f"\n=== Done! {len(all_new)} calendar(s) found ===")
