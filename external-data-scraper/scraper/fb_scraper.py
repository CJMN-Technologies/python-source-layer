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

def mask_ci_text(val: str):
    """Emit GitHub Actions ::add-mask:: workflow command to scrub sensitive text from public runner logs."""
    if val and (os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"):
        for line in str(val).splitlines():
            clean = line.strip()
            if len(clean) >= 6:
                print(f"::add-mask::{clean}", flush=True)

from google.genai import types

# Lazy-initialized list of Gemini API keys
_gemini_keys = None
_gemini_key_cooldown: dict[str, float] = {}
_GEMINI_OCR_CALLS_COUNT = 0
MAX_GEMINI_OCR_CALLS_PER_RUN = 10

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

def _get_active_gemini_keys() -> list[str]:
    keys = _get_gemini_keys()
    if not keys:
        return []
    now = time.time()
    ready = [k for k in keys if now >= _gemini_key_cooldown.get(k, 0)]
    if ready:
        return ready
    # If all keys are on cooldown, return them sorted by earliest cooldown expiry
    return sorted(keys, key=lambda k: _gemini_key_cooldown.get(k, 0))

def _mark_key_cooldown(key: str, duration_sec: float = 60.0):
    _gemini_key_cooldown[key] = time.time() + duration_sec

OCR_CACHE: dict[str, str] = {}

from gemini_model_resolver import get_gemini_models

def extract_text_from_image(img_url: str) -> str:
    """Download image and extract text via dynamic Gemini OCR with strict timeouts and key cooldowns."""
    global _GEMINI_OCR_CALLS_COUNT
    if not img_url:
        return ""

    lower_url = img_url.lower()
    # Reject Facebook HTML webpage URLs (e.g. facebook.com/posts/...) that are not direct image files
    if "facebook.com/" in lower_url and not any(ext in lower_url for ext in [".jpg", ".jpeg", ".png", ".webp"]):
        return ""

    if img_url in OCR_CACHE:
        return OCR_CACHE[img_url]

    if _GEMINI_OCR_CALLS_COUNT >= MAX_GEMINI_OCR_CALLS_PER_RUN:
        print(f"  [OCR] Global Gemini OCR budget ({MAX_GEMINI_OCR_CALLS_PER_RUN} calls) reached for this run. Skipping further calls.")
        return ""

    keys = _get_active_gemini_keys()
    if not keys:
        return ""

    try:
        resp = requests.get(img_url, timeout=5)
        if resp.status_code != 200:
            return ""
        content_type = resp.headers.get("Content-Type", "").lower()
        if "image" not in content_type and not any(ext in lower_url for ext in [".jpg", ".jpeg", ".png", ".webp"]):
            return ""
        image = Image.open(io.BytesIO(resp.content))

        for key in keys:
            try:
                client = genai.Client(
                    api_key=key,
                    http_options=types.HttpOptions(
                        timeout=10000,
                        retry_options=types.HttpRetryOptions(attempts=1)
                    )
                )
                models_to_try = get_gemini_models(client=client, task="vision")

                # Test at most the top 2 models (e.g. forward-compatible alias + top versioned model)
                for model_name in models_to_try[:2]:
                    try:
                        _GEMINI_OCR_CALLS_COUNT += 1
                        res = client.models.generate_content(
                            model=model_name,
                            contents=[image, "Extract all text from this image exactly as written. If no text is present, return nothing."]
                        )
                        extracted = res.text.strip() if res and res.text else ""
                        cleaned = clean_ocr_text(extracted)
                        OCR_CACHE[img_url] = cleaned
                        if cleaned:
                            print(f"  [OCR] Successfully extracted text using {model_name} ({len(cleaned)} chars)")
                        return cleaned
                    except Exception as me:
                        err_str = str(me).lower()
                        if "404" in err_str or "not_found" in err_str:
                            # Model not found on this endpoint, try secondary model
                            continue
                        if "quota" in err_str or "429" in err_str or "resource_exhausted" in err_str or "rate" in err_str:
                            # Key rate limited, mark cooldown and switch immediately to next API key
                            _mark_key_cooldown(key)
                            break
                        # For unparseable images, safety filter triggers, or other image-specific errors,
                        # trying another model on the exact same image is pointless
                        break
            except Exception as ke:
                err_str = str(ke).lower()
                if "quota" in err_str or "429" in err_str or "resource_exhausted" in err_str or "rate" in err_str:
                    _mark_key_cooldown(key)
                    continue
                continue
    except Exception as e:
        print(f"  [OCR] Error processing image {img_url[:60]}...: {e}")

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
def scrape_pages_batch(
    pages: list[dict],
    existing_urls: set = None,
    existing_texts: set = None,
    max_age_days: float = 2.0,
    max_ocr_per_page: int = 2,
    results_limit_per_page: int = 4,
) -> dict[str, list[dict]]:
    """
    Scrape multiple Facebook pages using dynamic per-page limits to strictly target the last 48 hours.
    This reduces Apify compute and dataset charges by ~55%!
    
    Returns:
        dict mapping normalized page_url -> list of post dicts.
    """
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

    token = os.getenv("APIFY_API_TOKEN")
    if not token:
        print("  Error: APIFY_API_TOKEN not found in environment!")
        return {}

    if not pages:
        return {}

    client = ApifyClient(token)

    # Group pages by their specific results_limit
    grouped: dict[int, list[dict]] = {}
    for p in pages:
        lim = p.get("results_limit", results_limit_per_page)
        grouped.setdefault(lim, []).append(p)

    dataset_items = []
    for lim, page_group in sorted(grouped.items(), reverse=True):
        start_urls = [{"url": p["url"]} for p in page_group if p.get("url")]
        print(f"  🚀 Launching Apify batch for {len(start_urls)} pages (Target: {lim} posts/page)...")
        run_input = {
            "startUrls": start_urls,
            "resultsLimit": lim,
        }
        try:
            run = client.actor("apify/facebook-posts-scraper").call(run_input=run_input)
            if run:
                items = list(client.dataset(run.default_dataset_id).iterate_items())
                dataset_items.extend(items)
        except Exception as e:
            print(f"  Apify batch error (limit {lim}): {e}")

    print(f"  ✅ Apify scraping finished! Received {len(dataset_items)} total posts across all {len(pages)} pages.")

    # Group raw items by page URL
    items_by_page: dict[str, list[dict]] = {p["url"]: [] for p in pages}
    for item in dataset_items:
        input_url = item.get("inputUrl") or item.get("facebookUrl") or ""
        # Match input_url against pages
        matched_url = None
        for p in pages:
            p_url = p["url"]
            if p_url.rstrip("/").lower() in input_url.lower() or input_url.lower() in p_url.rstrip("/").lower():
                matched_url = p_url
                break
        if not matched_url and pages:
            # Fallback to pageName matching
            page_name = (item.get("pageName") or "").lower()
            for p in pages:
                if page_name and page_name in p["url"].lower():
                    matched_url = p["url"]
                    break

        if matched_url:
            items_by_page[matched_url].append(item)
        elif pages:
            items_by_page[pages[0]["url"]].append(item)

    now = datetime.now(timezone.utc)
    results_by_page: dict[str, list[dict]] = {}

    for p in pages:
        p_url = p["url"]
        raw_items = items_by_page.get(p_url, [])
        page_posts = []
        page_ocr_count = 0

        for item in raw_items:
            post_url = item.get("url") or item.get("topLevelUrl") or ""
            post_url = clean_url(post_url) if post_url else ""

            if not is_valid_facebook_post_url(post_url, p_url):
                top_url = item.get("topLevelUrl")
                if top_url and is_valid_facebook_post_url(clean_url(top_url), p_url):
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
                continue

            caption_text = (
                item.get("text")
                or item.get("resharedText")
                or (item.get("sharePost", {}).get("text") if isinstance(item.get("sharePost"), dict) else None)
                or (item.get("attachedPost", {}).get("text") if isinstance(item.get("attachedPost"), dict) else None)
                or ""
            )
            caption_text = clean_caption_text(caption_text)

            # Announcement cue keywords for posts with longer captions that might have infographics
            ANNOUNCEMENT_CUES = (
                "advisory", "announcement", "walang pasok", "suspension", "suspendido",
                "shift", "classes", "notice", "circular", "memorandum", "guidelines",
                "pasok", "schedule", "modalities", "weather", "typhoon", "bagyo",
                "strike", "holiday", "virtual mode", "enriched virtual"
            )

            media_list = item.get("media") or []
            if not media_list and item.get("images"):
                raw_imgs = item.get("images")
                if isinstance(raw_imgs, list):
                    media_list = [{"url": u} if isinstance(u, str) else u for u in raw_imgs]
            if not media_list and item.get("imageUrl"):
                media_list = [{"url": item.get("imageUrl")}]
            if not media_list and isinstance(item.get("attachedPost"), dict):
                media_list = item.get("attachedPost", {}).get("media") or []

            image_text = ""
            img_uri = None
            fb_ocr_cleaned = ""

            # Check Facebook's built-in OCR from Apify first (instant, free, 0ms latency)
            if media_list:
                for m in media_list:
                    if isinstance(m, dict):
                        photo_img = m.get("photo_image")
                        if isinstance(photo_img, dict) and photo_img.get("uri"):
                            img_uri = photo_img.get("uri")
                        elif m.get("thumbnail"):
                            img_uri = m.get("thumbnail")
                        elif m.get("url"):
                            u = m.get("url")
                            u_lower = u.lower()
                            if ("fbcdn.net" in u_lower or "scontent" in u_lower or any(u_lower.endswith(ext) or ext + "?" in u_lower for ext in [".jpg", ".jpeg", ".png", ".webp"])):
                                img_uri = u

                        raw_fb_ocr = m.get("ocrText") or ""
                        if raw_fb_ocr:
                            c_fb = clean_ocr_text(raw_fb_ocr)
                            if c_fb and not fb_ocr_cleaned:
                                fb_ocr_cleaned = c_fb
                    if img_uri or fb_ocr_cleaned:
                        break

            # Fast path: If Facebook's built-in OCR already caught sufficient text (>= 30 chars)
            # or detected event keywords, use it directly without calling Gemini!
            if fb_ocr_cleaned and (len(fb_ocr_cleaned) >= 30 or is_relevant_event(caption_text, fb_ocr_cleaned)):
                image_text = fb_ocr_cleaned
            else:
                # Fallback path: Only call Gemini OCR if Facebook OCR was empty/insufficient
                # AND the post is a potential announcement/infographic
                caption_lower = caption_text.lower() if caption_text else ""
                needs_gemini_ocr = (
                    bool(img_uri)
                    and page_ocr_count < max_ocr_per_page
                    and (
                        not caption_text
                        or len(caption_text) < 120
                        or any(cue in caption_lower for cue in ANNOUNCEMENT_CUES)
                    )
                )

                if needs_gemini_ocr:
                    ocr_res = extract_text_from_image(img_uri)
                    if ocr_res:
                        image_text = ocr_res
                        page_ocr_count += 1
                    elif fb_ocr_cleaned:
                        # Fallback to whatever partial text Facebook OCR captured
                        image_text = fb_ocr_cleaned
                elif fb_ocr_cleaned:
                    image_text = fb_ocr_cleaned

            if caption_text or image_text:
                mask_ci_text(caption_text)
                mask_ci_text(image_text)
                mask_ci_text(post_url)

                cleaned_text = caption_text
                if not cleaned_text and image_text:
                    cleaned_text = "Official Advisory / Announcement (Infographic)"

                page_posts.append({
                    "text": cleaned_text,
                    "image_text": image_text.strip(),
                    "source_url": post_url or p_url,
                    "age_days": post_age_days,
                })

        seen = set()
        unique_posts = []
        for post_item in page_posts:
            if post_item["text"] not in seen:
                seen.add(post_item["text"])
                unique_posts.append(post_item)

        results_by_page[p_url] = unique_posts

    return results_by_page


def scrape_page(
    page_url: str,
    cookies: list = None,
    existing_urls: set = None,
    max_scrolls: int = 6,
    max_age_days: float = 14.0,
    max_ocr_per_page: int = 2,
    results_limit: int = 10,
) -> list[dict]:
    """Single page scraper fallback using scrape_pages_batch."""
    res = scrape_pages_batch(
        pages=[{"url": page_url}],
        existing_urls=existing_urls,
        max_age_days=max_age_days,
        max_ocr_per_page=max_ocr_per_page,
        results_limit_per_page=results_limit,
    )
    return res.get(page_url, [])
