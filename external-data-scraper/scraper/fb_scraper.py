from apify_client import ApifyClient
from bs4 import BeautifulSoup
from google import genai
import os
from PIL import Image
import numpy as np
import requests
import time
import random
import io
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlparse
from unicode_normalizer import normalize_unicode_text

# Lazy-initialized list of Gemini API keys
_gemini_keys = None

def _get_gemini_keys():
    global _gemini_keys
    if _gemini_keys is None:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
        
        # Load main key (which could be a comma-separated list of keys)
        keys_raw = os.getenv("GEMINI_API_KEY") or ""
        keys = [k.strip() for k in keys_raw.split(",") if k.strip()]
        
        # Also load backup keys like GEMINI_API_KEY_2, GEMINI_API_KEY_3...
        idx = 2
        while True:
            k = os.getenv(f"GEMINI_API_KEY_{idx}")
            if k:
                keys.append(k.strip())
                idx += 1
            else:
                break
                
        if not keys:
            print("  Warning: GEMINI_API_KEY env variable not found.")
        _gemini_keys = keys
    return _gemini_keys

OCR_CACHE: dict[str, str] = {}

def extract_text_from_image(img_url: str) -> str:
    """Download image and extract text via Gemini 2.0 Flash OCR."""
    if not img_url:
        return ""
    if img_url in OCR_CACHE:
        return OCR_CACHE[img_url]

    keys = _get_gemini_keys()
    if not keys:
        return ""

    try:
        resp = requests.get(img_url, timeout=10)
        if resp.status_code != 200:
            return ""
        image = Image.open(io.BytesIO(resp.content))

        for key in keys:
            try:
                client = genai.Client(api_key=key)
                res = client.models.generate_content(
                    model="gemini-2.0-flash",
                    contents=[image, "Extract all text from this image exactly as written. If no text is present, return nothing."]
                )
                extracted = res.text.strip() if res and res.text else ""
                cleaned = clean_ocr_text(extracted)
                OCR_CACHE[img_url] = cleaned
                return cleaned
            except Exception as ke:
                if "quota" in str(ke).lower() or "429" in str(ke):
                    continue
                break
    except Exception:
        pass

    return ""

PERMALINK_PATTERN = re.compile(r"(/posts/|/permalink|story_fbid=|/photos/|fbid=|/share/(p|photo)/)", re.I)
VIDEO_PATTERN = re.compile(r"(/reel/|/videos/|/watch)")
PERSONAL_OR_UNSAFE_FACEBOOK_PATHS = (
    "/people/",
    "/profile.php",
    "/groups/",
    "/friends/",
    "/messages/",
    "/notifications/",
    "/login/",
    "/reel/",
    "/videos/",
    "/watch",
)

STRONG_RELEVANT_KEYWORDS = [
    # Class suspension / holidays
    "no classes",
    "class suspension",
    "classes suspended",
    "suspended classes",
    "class suspended",
    "school suspension",
    "school closed",
    "class cancellation",
    "class postponement",
    "cancelled classes",
    "resumption of classes",
    "regular holiday",
    "special non-working holiday",
    "non-working holiday",
    "school holiday",
    "academic holiday",
    "holiday break",
    "long weekend",
    "no office transactions",
    "no classes and office work",
    "no classes and work",
    "asynchronous classes",
    "shift to online classes",
    "classes will resume",
    # Tagalog class suspension
    "walang pasok",
    "walang klase",
    "walang pasok sa klase",
    "suspensyon ng klase",
    "kanselado ang klase",
    "suspendido ang klase",
    "suspendido ang pasok",
    "pampublikong pahinga",
    "estado ng kalamidad",
    # LGU weather
    "weather advisory",
    "storm signal",
    "signal number",
    "signal no.",
    "bagyo",
    "baha",
    "orange warning",
    "red warning",
    "habagat",
    "flash flood",
    "flood advisory",
    "state of calamity",
    # Transport strikes (Friction: 0.9)
    "tigil pasada",
    "transport strike",
    "jeepney strike",
    "welga ng drivers",
    "welga ng jeep",
    "welga ng piston",
    "welga",
    # LRT-2 full suspension (Friction: 1.0)
    "lrt-2 suspended",
    "lrt suspended",
    "lrt-2 suspension",
    "train suspended",
    "train suspension",
    "full suspension",
    "power failure",
    "service disruption",
    "train disruption",
    "lrt-2 disruption",
    "no lrt service",
    "provisionary service",
    "partial suspension",
    "cubao-antipolo only",
    "antipolo-cubao only",
    "suspendido ang operasyon",
    "tigil operasyon",
    "walang serbisyo ng lrt",
    "tigil ang lrt",
    # Train degradation (Friction: 0.5)
    "delayed train",
    "train delay",
    "lrt delay",
    "lrt-2 delay",
    "code yellow",
    "code yellow advisory",
    "degraded headway",
    "lrt-2 advisory",
    "lrt advisory",
    "service interruption",
    "delayed ang tren",
    "delayed ang lrt",
    # Arena / Major Events (Friction: 0.65)
    "concert",
    "sports event",
    "arena event",
    "smart araneta",
    "araneta coliseum",
    "big dome",
    "araneta",
    "philsports",
    "moa arena",
    "uaap",
    "ncaa",
    "pba game",
]

GENERAL_RELEVANT_KEYWORDS = [
    "school",
    "university",
    "college",
    "academy",
    "campus",
    "student",
    "class",
    "classes",
    "pasok",
    "road",
    "weather",
    "typhoon",
    "bagyo",
    "signal",
    "storm",
    "flood",
    "baha",
    "lrt",
    "train",
    "strike",
    "concert",
    "event",
]

SUSPENSION_PATTERN = re.compile(
    r"\b(suspend(?:ed|ion|ing)?|suspens(?:ion|yon|yo)?|suspenso|suspendido"
    r"|cancel(?:led|lation)?|postpone(?:d|ment)?|closure|closed|walang|tigil"
    r"|delayed?|disrupted?|strike|welga)\b",
    re.I,
)

SUSPENSION_CONTEXT_KEYWORDS = [
    "lrt",
    "mrt",
    "train",
    "rail",
    "line",
    "service",
    "station",
    "operations",
    "classes",
    "safety",
    "suspension of classes",
    "suspensiyon",
    "istasyon",
    "tren",
    "arena",
    "concert",
    "jeepney",
    "welga",
    "strike",
    "power",
    "cubao",
    "antipolo",
    "government",
    "lgu",
    "city",
]

EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U000024C2-\U000024E9"
    "\U0001F100-\U0001F251"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "\U00002B50"
    "\U000023F0-\U000023FA"
    "\U0000203C-\U00003299"
    "]+",
    flags=re.UNICODE,
)

FOOTER_LINE_PATTERN = re.compile(
    r"(#\w+|@\w+|www\.|https?://|\.edu\.ph|\.gov\.ph|\.com\.ph|\.facebook\.|f\s*[|/]\s*@|chooseSAN|#choose)",
    re.IGNORECASE,
)

def strip_emojis(text: str) -> str:
    return EMOJI_PATTERN.sub("", text)

def clean_ocr_text(text: str) -> str:
    text = normalize_unicode_text(text)
    text = strip_emojis(text)
    text = re.sub(r"[^\S\r\n]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    lines = []
    seen = set()
    for line in text.splitlines():
        cleaned = line.strip(" -_|•·")
        if len(cleaned) < 3:
            continue
        if FOOTER_LINE_PATTERN.search(cleaned):
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(cleaned)
    return " ".join(lines)

def strip_social_boilerplate(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"^.*?·\s*Shared with (?:Public|Friends)\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"All reactions:.*", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\b\d+\s+reactions?\b.*", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\b\d+\s+shares?\b.*", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()

def clean_caption_text(text: str) -> str:
    text = strip_social_boilerplate(text)
    text = normalize_unicode_text(text)
    text = strip_emojis(text)
    text = re.sub(r"\s*(?:See more|Tumingin pa|Read more)\s*[…\.]*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def is_relevant_event(text: str, image_text: str = "") -> bool:
    combined = (text or "") + " " + (image_text or "")
    if not combined.strip():
        return False

    combined = normalize_unicode_text(combined)
    lowered = combined.casefold()

    calendar_titles = [
        "academic calendar",
        "school calendar",
        "collegiate calendar",
        "university calendar",
    ]
    if any(kw in lowered for kw in calendar_titles):
        has_action = any(kw.casefold() in lowered for kw in [
            "no classes", "suspended", "walang pasok", "holiday", "holiday break"
        ])
        if not has_action:
            return False

    for kw in STRONG_RELEVANT_KEYWORDS:
        if kw.casefold() in lowered:
            return True

    if SUSPENSION_PATTERN.search(lowered):
        for kw in SUSPENSION_CONTEXT_KEYWORDS + GENERAL_RELEVANT_KEYWORDS:
            if kw.casefold() in lowered:
                return True

    return False

def clean_url(href: str) -> str:
    if not href:
        return ""
    return href.split("&")[0].split("?__cft__")[0]

def is_valid_facebook_post_url(href: str, expected_page_url: str | None = None) -> bool:
    cleaned = clean_url(href)
    if not cleaned:
        return False
    parsed = urlparse(cleaned)
    host = parsed.netloc.casefold()
    path = parsed.path.casefold()
    query = parsed.query.casefold()
    combined = f"{path}?{query}"

    if "facebook.com" not in host:
        return False
    if any(blocked in path for blocked in PERSONAL_OR_UNSAFE_FACEBOOK_PATHS):
        return False
    if "comment_id=" in query or "reply_comment_id=" in query:
        return False
    if not PERMALINK_PATTERN.search(combined):
        return False
    return True

# --- Backward-compatible helper stubs for calendar_scraper / legacy callers ---
def candidate_page_urls(page_url: str) -> list[str]:
    return [page_url]

def click_see_more_buttons(page, max_clicks: int = 8):
    pass

def normalize_playwright_cookies(cookies: list) -> list:
    return []

def get_ancestor(el, levels: int):
    return getattr(el, "parent", None)

def find_ancestor_with_link(el, max_levels: int = 12, expected_page_url: str | None = None):
    return None

def parse_age_days(text: str) -> float | None:
    return None

def is_truncated(text: str) -> bool:
    return False

def get_post_header_text(el, caption_text: str, max_levels: int = 3) -> str:
    return ""

def is_video_post(el, levels: int = 4) -> bool:
    return False

def _block_unnecessary_resources(route, request):
    pass


# ---------------------------------------------------------------------------
# Core Apify Scraper Engine
# ---------------------------------------------------------------------------
def scrape_page(
    page_url: str,
    cookies: list = None,
    existing_urls: set = None,
    max_scrolls: int = 6,
    max_age_days: float = 14.0,
    max_ocr_per_page: int = 25,
    results_limit: int = 6,
) -> list[dict]:
    """
    Scrape a single Facebook page for relevant events using Apify.
    
    Args:
        page_url: Facebook page URL.
        cookies: (Deprecated, unused with Apify).
        existing_urls: Set of known post URLs to skip.
        max_scrolls: Unused legacy parameter.
        max_age_days: Cutoff threshold in days.
        max_ocr_per_page: Max images to OCR per page.
        results_limit: Number of latest posts to fetch.
    """
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
    
    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        print("  Error: APIFY_API_TOKEN not found in environment!")
        return []

    client = ApifyClient(token)
    run_input = {
        "startUrls": [{"url": page_url}],
        "resultsLimit": results_limit,
    }

    try:
        run = client.actor("apify/facebook-posts-scraper").call(run_input=run_input)
        if not run:
            print(f"  Apify run failed for {page_url}")
            return []

        dataset_items = list(client.dataset(run.default_dataset_id).iterate_items())
    except Exception as e:
        print(f"  Apify call failed on {page_url}: {e}")
        return []

    now = datetime.now(timezone.utc)
    posts = []
    page_ocr_count = 0

    for item in dataset_items:
        post_url = item.get("url") or item.get("topLevelUrl") or ""
        post_url = clean_url(post_url) if post_url else ""

        if not is_valid_facebook_post_url(post_url, page_url):
            # Still check topLevelUrl if url is a share link
            top_url = item.get("topLevelUrl")
            if top_url and is_valid_facebook_post_url(clean_url(top_url), page_url):
                post_url = clean_url(top_url)

        if existing_urls and post_url in existing_urls:
            continue

        # Parse post timestamp / age
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

        if post_age_days is not None and post_age_days > max_age_days:
            print(f"  Skipped old post ({post_age_days:.1f}d > {max_age_days}d limit).")
            continue

        caption_text = item.get("text") or ""
        caption_text = clean_caption_text(caption_text)

        # Extract image text via OCR
        image_text = ""
        media_list = item.get("media") or []
        for m in media_list:
            if page_ocr_count >= max_ocr_per_page:
                break
            img_uri = None
            if isinstance(m, dict):
                photo_img = m.get("photo_image")
                if isinstance(photo_img, dict) and photo_img.get("uri"):
                    img_uri = photo_img.get("uri")
                elif m.get("thumbnail"):
                    img_uri = m.get("thumbnail")
                elif m.get("url") and "facebook.com/photo" not in m.get("url"):
                    img_uri = m.get("url")

                fb_ocr = m.get("ocrText") or ""
            else:
                fb_ocr = ""

            if img_uri:
                ocr_res = extract_text_from_image(img_uri)
                if ocr_res:
                    image_text += " " + ocr_res
                    page_ocr_count += 1
                elif fb_ocr:
                    cleaned_fb_ocr = clean_ocr_text(fb_ocr)
                    if cleaned_fb_ocr:
                        image_text += " " + cleaned_fb_ocr

        if caption_text or image_text:
            cleaned_text = caption_text
            if not cleaned_text and image_text:
                cleaned_text = "Official Advisory / Announcement (Infographic)"

            posts.append({
                "text": cleaned_text,
                "image_text": image_text.strip(),
                "source_url": post_url or page_url,
                "age_days": post_age_days,
            })

    seen = set()
    unique_posts = []
    for p in posts:
        if p["text"] not in seen:
            seen.add(p["text"])
            unique_posts.append(p)

    print(f"  Found {len(unique_posts)} relevant unique posts")
    return unique_posts
