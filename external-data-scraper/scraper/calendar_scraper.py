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
from apify_client import ApifyClient
import pandas as pd

from email_notifier import send_calendar_with_attachment
from fb_scraper import (
    clean_url, is_valid_facebook_post_url,
    extract_text_from_image, clean_ocr_text, clean_caption_text
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
                    cookies: list = None, max_scrolls: int = MAX_SCROLLS):
    """Scrape a single Facebook page for academic calendar posts using Apify."""
    processed = load_processed()
    posts_found = []

    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        print("  Error: APIFY_API_TOKEN not found in environment!")
        return []

    client = ApifyClient(token)
    run_input = {
        "startUrls": [{"url": page_url}],
        "resultsLimit": 10,
    }

    try:
        run = client.actor("apify/facebook-posts-scraper").call(run_input=run_input)
        if not run:
            return []
        items = list(client.dataset(run.default_dataset_id).iterate_items())
    except Exception as e:
        print(f"  Apify error on {page_url}: {e}")
        return []

    now = datetime.now(timezone.utc)
    page_ocr_count = 0

    for item in items:
        post_url = item.get("url") or item.get("topLevelUrl") or ""
        post_url = clean_url(post_url) if post_url else ""

        if not is_valid_facebook_post_url(post_url, page_url):
            top_url = item.get("topLevelUrl")
            if top_url and is_valid_facebook_post_url(clean_url(top_url), page_url):
                post_url = clean_url(top_url)

        if post_url in processed:
            continue

        # Parse post age
        post_age_days = None
        iso_time = item.get("time")
        timestamp = item.get("timestamp")
        if iso_time:
            try:
                dt = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
                post_age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            except Exception:
                pass
        elif timestamp:
            try:
                dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                post_age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
            except Exception:
                pass

        if post_age_days is not None and post_age_days > MAX_AGE_DAYS:
            continue

        caption_text = item.get("text") or ""
        caption_text = clean_caption_text(caption_text)

        # OLFU filter: only Antipolo campus or systemwide notice
        if "fatima" in page_url.casefold() or "fatima" in page_name.casefold():
            normalized_caption = normalize_unicode_text(caption_text).casefold()
            is_sys = any(k in normalized_caption for k in ["all campuses", "all olfu campuses", "all branches", "systemwide", "entire university"])
            antipolo_excepted = bool(re.search(r"(?:except|excluding|maliban\s+sa)\s+(?:(?:for|sa)\s+)?(?:olfu\s+)?antipolo", normalized_caption))
            if is_sys and antipolo_excepted:
                continue
            if not is_sys and "antipolo" not in normalized_caption:
                continue

        combined_text = caption_text

        # Check if caption passes calendar filter, otherwise check OCR
        if not is_valid_calendar_post(combined_text):
            if has_calendar_hint(combined_text) and page_ocr_count < MAX_OCR_PER_PAGE:
                media_list = item.get("media") or []
                for m in media_list:
                    if page_ocr_count >= MAX_OCR_PER_PAGE:
                        break
                    img_uri = None
                    if isinstance(m, dict):
                        photo_img = m.get("photo_image")
                        if isinstance(photo_img, dict) and photo_img.get("uri"):
                            img_uri = photo_img.get("uri")
                        elif m.get("thumbnail"):
                            img_uri = m.get("thumbnail")
                    if img_uri:
                        ocr_res = extract_text_from_image(img_uri)
                        if ocr_res:
                            page_ocr_count += 1
                            combined_text += " " + ocr_res
                            if is_valid_calendar_post(combined_text):
                                break

        if not is_valid_calendar_post(combined_text):
            continue

        print(f"  *** VALID ACADEMIC CALENDAR DETECTED! ***")
        print(f"  Page: {page_name} ({page_station})")
        print(f"  Post URL: {post_url}")
        print(f"  Preview: {combined_text[:120]}...")

        # Construct DB record
        cal_id = _next_calendar_id()
        scraped_at = now.isoformat()
        post_date = (now - timedelta(days=post_age_days)).isoformat() if post_age_days is not None else scraped_at

        row = {
            "id": cal_id,
            "station": page_station,
            "source_name": page_name,
            "source_url": post_url,
            "post_text": caption_text[:5000],
            "image_text": combined_text.replace(caption_text, "").strip()[:5000] if combined_text != caption_text else None,
            "category": "academic_calendar",
            "event_name": f"Academic Calendar A.Y. 2026-2027 — {get_acronym(page_name)}",
            "event_date": "A.Y. 2026-2027",
            "event_code": None,
            "is_cancellation": False,
            "cancellation_target_code": None,
            "scraped_at": scraped_at,
            "post_date": post_date,
        }

        # Upsert into Supabase
        try:
            supabase.schema("external").table("academic_lgu_events").upsert(row).execute()
            print(f"  >> Upserted to Supabase: {cal_id}")
        except Exception as e:
            print(f"  Supabase upsert failed: {e}")

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

    return posts_found


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== Academic Calendar Scraper Starting (Apify Powered) ===")
    print(f"Time (PHT): {datetime.now(timezone(timedelta(hours=8)))}")
    print(f"Max post age: {MAX_AGE_DAYS} days")

    with open(os.path.join(os.path.dirname(__file__), "pages.json"), "r", encoding="utf-8") as f:
        all_pages = json.load(f)

    # Only scrape university/school pages — skip LGUs and weather agencies
    ignore_keywords = ["pagasa", "public information office", "pio", "government", "municipality"]

    all_new = []

    for p in all_pages:
        if any(kw in p['name'].casefold() for kw in ignore_keywords):
            print(f"\nSkipping non-university page: {p['name']}")
            continue

        print(f"\n[Calendar] Scanning: {p['name']} ({p['station']})")
        result = scrape_calendar(p['url'], p['name'], p['station'])

        if isinstance(result, list):
            for r in result:
                if isinstance(r, dict):
                    all_new.append(r)

    print(f"\n=== Done! {len(all_new)} calendar(s) found ===")
