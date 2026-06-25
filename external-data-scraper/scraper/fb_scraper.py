from playwright.sync_api import sync_playwright
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
from datetime import datetime
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

# OCR image pre-check keywords — if these appear in caption, skip OCR (already have text)
# Also used as OCR relevance gate: only OCR if caption alone fails the keyword filter
OCR_KEYWORDS = [
    "university",
    "class",
    "classes",
    "office",
    "work",
    "holiday",
    "advisory",
    "suspension",
    "campus",
    "campuses",
    "admission",
    "test",
    "scheduled",
    "walang",
    "pasok",
    "klase",
    # New: transport/arena
    "concert",
    "event",
    "tigil",
    "welga",
    "araneta",
    "suspended",
    "lrt",
    "train",
    "strike",
    "delay",
]


PERMALINK_PATTERN = re.compile(r"(/posts/|/permalink|story_fbid|/photos/|pfbid|fbid=|/share/)")
VIDEO_PATTERN = re.compile(r"(/reel/|/videos/|/watch)")

# ---------------------------------------------------------------------------
# RELEVANT KEYWORDS — Aligned to Friction Index (all stored as lowercase;
# matching uses .casefold() for ALL CAPS / Title Case / mixed support)
# ---------------------------------------------------------------------------

# Strong: posting any of these = almost certainly a relevant event
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

# General context keywords — used for disambiguation
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

# Suspension-like patterns (broader than just "suspend")
SUSPENSION_PATTERN = re.compile(
    r"\b(suspend(?:ed|ion|ing)?|suspens(?:ion|yon|yo)?|suspenso|suspendido"
    r"|cancel(?:led|lation)?|postpone(?:d|ment)?|closure|closed|walang|tigil"
    r"|delayed?|disrupted?|strike|welga)\b",
    re.I,
)

# Context that confirms a suspension/event is relevant to LRT ridership
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
    # New
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

WEEKDAY_ALIASES = {
    "monday": 0,
    "lunes": 0,
    "tuesday": 1,
    "martes": 1,
    "wednesday": 2,
    "miyerkules": 2,
    "huwebes": 3,
    "thursday": 3,
    "friday": 4,
    "biyernes": 4,
    "saturday": 5,
    "sabado": 5,
    "sunday": 6,
    "linggo": 6,
}


def weekday_age_days(match) -> float:
    weekday = WEEKDAY_ALIASES[match.group(1).lower()]
    today = datetime.now().weekday()
    return float((today - weekday) % 7)


DATE_PATTERNS = [
    (re.compile(r"\b(\d+)\s*m\b", re.I), lambda m: int(m.group(1)) / 60 / 24),
    (re.compile(r"\b(\d+)\s*h\b", re.I), lambda m: int(m.group(1)) / 24),
    (re.compile(r"\b(\d+)\s*d\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"(\d+)\s+minutes?\s+ago", re.I), lambda m: int(m.group(1)) / 60 / 24),
    (re.compile(r"(\d+)\s+hours?\s+ago", re.I), lambda m: int(m.group(1)) / 24),
    (re.compile(r"(\d+)\s+days?\s+ago", re.I), lambda m: int(m.group(1))),
    (re.compile(r"(\d+)\s+minuto(?:\s+ang\s+nakalipas)?", re.I), lambda m: int(m.group(1)) / 60 / 24),
    (re.compile(r"(\d+)\s+oras(?:\s+ang\s+nakalipas)?", re.I), lambda m: int(m.group(1)) / 24),
    (re.compile(r"(\d+)\s+araw(?:\s+ang\s+nakalipas)?", re.I), lambda m: int(m.group(1))),
    (re.compile(r"Yesterday", re.I), lambda m: 1.0),
    (re.compile(r"Today", re.I), lambda m: 0.0),
    (re.compile(r"Kahapon", re.I), lambda m: 1.0),
    (re.compile(r"Ngayon", re.I), lambda m: 0.0),
    (re.compile(r"(?:noong\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday|lunes|martes|miyerkules|huwebes|biyernes|sabado|linggo)", re.I), weekday_age_days),
    (re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})", re.I), None),
    (re.compile(r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)(?:\s+at\s+\d{1,2}:\d{2})?", re.I), lambda m: max(0.0, (datetime.utcnow() - datetime(datetime.utcnow().year, datetime.strptime(m.group(2), "%B").month, int(m.group(1)))).days)),
    (re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:\s+at\s+\d{1,2}:\d{2})?", re.I), lambda m: max(0.0, (datetime.utcnow() - datetime(datetime.utcnow().year, datetime.strptime(m.group(1), "%B").month, int(m.group(2)))).days)),
]


def parse_age_days(text: str) -> float | None:
    if not text:
        return None
    for pattern, converter in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            if converter is None:
                month_name = m.group(1)
                day = int(m.group(2))
                year = int(m.group(3))
                try:
                    month = datetime.strptime(month_name, "%B").month
                    post_date = datetime(year, month, day)
                    return max(0.0, (datetime.utcnow() - post_date).days)
                except Exception:
                    continue
            try:
                return converter(m)
            except Exception:
                continue
    return None


def extract_post_age(soup: BeautifulSoup) -> float | None:
    patterns = [re.compile(r"(\d+)\s+minutes?\s+ago", re.I),
                re.compile(r"(\d+)\s+hours?\s+ago", re.I),
                re.compile(r"(\d+)\s+days?\s+ago", re.I),
                re.compile(r"Yesterday", re.I),
                re.compile(r"Today", re.I),
                re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})", re.I)]
    for string in soup.stripped_strings:
        age = parse_age_days(string)
        if age is not None:
            return age
    return None


# Regex to match emoji and other non-text Unicode symbols
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U00002B50"             # star
    "\U000023F0-\U000023FA"  # misc technical
    "\U0000203C-\U00003299"  # misc symbols
    "]+",
    flags=re.UNICODE,
)

# Patterns that indicate a social-media footer line (logos, handles, URLs)
FOOTER_LINE_PATTERN = re.compile(
    r"(#\w+|@\w+|www\.|https?://|\.edu\.ph|\.gov\.ph|\.com\.ph|\.facebook\.|f\s*[|/]\s*@|chooseSAN|#choose)",
    re.IGNORECASE,
)


def strip_emojis(text: str) -> str:
    """Remove emoji characters from text."""
    return EMOJI_PATTERN.sub("", text)


def clean_ocr_text(text: str) -> str:
    text = strip_emojis(text)
    text = normalize_unicode_text(text)  # Convert stylized Unicode fonts to plain ASCII
    text = re.sub(r"[^\S\r\n]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    lines = []
    seen = set()
    for line in text.splitlines():
        cleaned = line.strip(" -_|•·")
        if len(cleaned) < 3:
            continue
        # Skip footer-like lines (social handles, URLs, hashtags)
        if FOOTER_LINE_PATTERN.search(cleaned):
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(cleaned)
    return " ".join(lines)


def clean_caption_text(text: str) -> str:
    """Strip emojis and normalize whitespace from post caption text."""
    text = strip_emojis(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def crop_footer(image: Image.Image, footer_fraction: float = 0.12) -> Image.Image:
    """Crop the bottom portion of an image to remove social-media footers/logos."""
    width, height = image.size
    crop_height = int(height * (1 - footer_fraction))
    return image.crop((0, 0, width, crop_height))


def short_url_for_log(image_url: str) -> str:
    parsed = urlparse(image_url)
    filename = parsed.path.rsplit("/", 1)[-1]
    return f"{parsed.netloc}/{filename}" if filename else parsed.netloc


def extract_text_from_image(image_url: str) -> str:
    """Download an image and extract text using Google's Gemini Vision API."""
    if image_url in OCR_CACHE:
        return OCR_CACHE[image_url]

    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        }
        response = requests.get(image_url, headers=headers, timeout=(5, 20))
        response.raise_for_status()
        image = Image.open(io.BytesIO(response.content))
        if min(image.size) < 150 or max(image.size) < 250:
            OCR_CACHE[image_url] = ""
            return ""

        keys = _get_gemini_keys()
        if not keys:
            print("  OCR skipped: No Gemini API keys found in environment.")
            return ""

        prompt = (
            "You are an OCR and information extraction assistant. "
            "Read all the text visible in the attached image. "
            "Provide ONLY the extracted main announcement or advisory text. "
            "Follow these rules:\n"
            "1. Ignore social media footers, logos, icons, links, usernames, and contact information at the bottom.\n"
            "2. Ignore headers or logos that only contain the organization's name unless it is part of the announcement text itself.\n"
            "3. Preserve paragraph breaks and spacing where appropriate.\n"
            "4. Strip out any standalone decorative emojis or symbols.\n"
            "5. Do NOT add any extra commentary or introductory text (like 'Here is the text:'). Just output the transcribed text."
        )

        # Try cheapest model first to conserve quota
        models_to_try = ["gemini-2.5-flash-lite", "gemini-2.5-flash"]
        gemini_response = None
        
        # Loop through each API key, trying all models on it
        for key_idx, api_key in enumerate(keys, start=1):
            try:
                client = genai.Client(api_key=api_key)
            except Exception as e:
                print(f"  Failed to initialize Gemini client for Key #{key_idx}: {e}")
                continue
                
            for model_name in models_to_try:
                try:
                    gemini_response = client.models.generate_content(
                        model=model_name,
                        contents=[prompt, image]
                    )
                    break
                except Exception as e:
                    if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                        print(f"  {model_name} quota exhausted on API Key #{key_idx}. Trying next model/key...")
                        continue
                    else:
                        raise e
            if gemini_response is not None:
                break

        if gemini_response is None:
            raise RuntimeError("All configured Gemini API keys and models returned RESOURCE_EXHAUSTED.")

        raw_text = gemini_response.text or ""
        result = clean_ocr_text(raw_text)
        OCR_CACHE[image_url] = result
        return result
    except requests.RequestException as e:
        print(f"  OCR fetch skipped ({short_url_for_log(image_url)}): {e.__class__.__name__}")
        OCR_CACHE[image_url] = ""
        return ""
    except Exception as e:
        print(f"  OCR skipped ({short_url_for_log(image_url)}): {e.__class__.__name__}")
        OCR_CACHE[image_url] = ""
        return ""


def is_relevant_event(text: str, image_text: str = "") -> bool:
    """
    Return True if the combined text likely refers to a relevant LRT ridership-
    affecting event: class suspensions, LGU advisories, transport disruptions,
    arena/concert events, or academic calendar events.

    Uses .casefold() for case-insensitive matching — handles ALL CAPS, Title
    Case, lowercase, and mixed-case Filipino Facebook posts equally.
    """
    combined = (text or "") + " " + (image_text or "")
    if not combined.strip():
        return False

    # Normalize decorative Unicode fonts (bold, italic, script, etc.) to plain ASCII
    combined = normalize_unicode_text(combined)
    lowered = combined.casefold()

    # Reject generic calendar-title posts (not actionable events)
    calendar_titles = [
        "academic calendar",
        "school calendar",
        "collegiate calendar",
        "university calendar",
    ]
    # Only reject if it's ONLY a calendar title post (no suspension/event context)
    if any(kw in lowered for kw in calendar_titles):
        # Still allow if it also contains actionable keywords
        has_action = any(kw.casefold() in lowered for kw in [
            "no classes", "suspended", "walang pasok", "holiday", "holiday break"
        ])
        if not has_action:
            return False

    # Accept immediately on any strong keyword match (case-insensitive)
    for kw in STRONG_RELEVANT_KEYWORDS:
        if kw.casefold() in lowered:
            return True

    # For posts with a suspension/event signal word, check for context
    if SUSPENSION_PATTERN.search(lowered):
        for kw in SUSPENSION_CONTEXT_KEYWORDS + GENERAL_RELEVANT_KEYWORDS:
            if kw.casefold() in lowered:
                return True

    return False


def is_truncated(text: str) -> bool:
    t = text.strip().lower()
    return t.endswith("see more") or t.endswith("tumingin pa")


def get_ancestor(el, levels: int):
    current = el
    for _ in range(levels):
        if current is None or current.parent is None:
            break
        current = current.parent
    return current


def get_post_header_text(el, caption_text: str, max_levels: int = 3) -> str:
    current = el.parent
    normalized_caption = " ".join((caption_text or "").split())
    for _ in range(max_levels):
        if current is None:
            break
        ancestor_text = " ".join(current.get_text(" ", strip=True).split())
        if normalized_caption and normalized_caption in ancestor_text:
            header_text = ancestor_text.split(normalized_caption, 1)[0].strip()
            if header_text:
                return header_text
        current = current.parent
    return ""


def is_video_post(el, levels: int = 4) -> bool:
    """Check if this post is a video/reel - skip those entirely."""
    container = get_ancestor(el, levels)
    if container is None:
        return False
    if container.find("video"):
        return True
    for a in container.find_all("a", {"href": True}, limit=50):
        if VIDEO_PATTERN.search(a["href"]):
            return True
    return False


def find_ancestor_with_link(el, max_levels: int = 12):
    current = el
    for _ in range(max_levels):
        if current is None:
            break
        for a in current.find_all("a", {"href": True}, limit=50):
            href = a["href"]
            if PERMALINK_PATTERN.search(href):
                return href
        current = current.parent
    return None


def clean_url(href: str, prefer_mbasic: bool = False) -> str:
    if href.startswith("/"):
        host = "mbasic.facebook.com" if prefer_mbasic else "www.facebook.com"
        href = f"https://{host}" + href
    # normalize and strip tracking params
    return href.split("&")[0].split("?__cft__")[0]


def canonical_facebook_url(page_url: str) -> str:
    return page_url.replace("mbasic.facebook.com", "www.facebook.com").replace("m.facebook.com", "www.facebook.com")


def facebook_plugin_url(page_url: str) -> str:
    canonical_url = canonical_facebook_url(page_url)
    encoded_url = quote(canonical_url, safe="")
    return (
        "https://www.facebook.com/plugins/page.php"
        f"?href={encoded_url}"
        "&tabs=timeline"
        "&width=500"
        "&height=1000"
        "&small_header=false"
        "&adapt_container_width=true"
        "&hide_cover=false"
        "&show_facepile=false"
    )


def candidate_page_urls(page_url: str) -> list[str]:
    candidates = [facebook_plugin_url(page_url), canonical_facebook_url(page_url)]
    unique_candidates = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def click_see_more_buttons(page, max_clicks: int = 30):
    for _ in range(max_clicks):
        buttons = page.locator("text=/See more|Tumingin pa/i")
        if buttons.count() == 0:
            break
        try:
            buttons.first.click(timeout=5000, force=True)
            time.sleep(0.5)
        except Exception:
            break


def fetch_full_post_text(ctx, post_url: str):
    """Visit a post's permalink and extract the untruncated caption text and post age."""
    page2 = ctx.new_page()
    try:
        post_url = canonical_facebook_url(post_url)
        page2.goto(post_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

        click_see_more_buttons(page2)

        soup2 = BeautifulSoup(page2.content(), "html.parser")
        post_age = extract_post_age(soup2)

        # Try known selectors in order: www (data-ad-preview), mbasic/story containers, article/role=article
        el2 = soup2.find("div", {"data-ad-preview": "message"})
        if not el2:
            try:
                el2 = soup2.find("div", id=re.compile(r"m_story_permalink_view|story"))
            except Exception:
                el2 = None
        if not el2:
            el2 = soup2.find("article") or soup2.find("div", {"role": "article"})

        if el2:
            return el2.get_text(separator=" ", strip=True), post_age
    except Exception as e:
        print(f"    Failed to fetch full post text: {e}")
    finally:
        page2.close()
    return None, None


def normalize_playwright_cookies(cookies: list) -> list:
    """Ensure cookie entries are valid strings for Playwright's add_cookies.

    - Converts values to str
    - Skips cookies missing a name or value
    - Ensures domain and path are present
    """
    out = []
    if not cookies:
        return out
    for c in cookies:
        try:
            name = c.get("name") if isinstance(c, dict) else None
            value = c.get("value") if isinstance(c, dict) else None
            if not name or value is None:
                # skip invalid cookie
                continue
            # convert value to string (Playwright expects a string)
            value_str = value if isinstance(value, str) else str(value)
            domain = c.get("domain") or ".facebook.com"
            path = c.get("path") or "/"

            out.append({
                "name": name,
                "value": value_str,
                "domain": domain,
                "path": path,
            })
        except Exception:
            continue
    return out


def _block_unnecessary_resources(route, request):
    """Block CSS, fonts, media, and tracking pixels to speed up page loads."""
    resource_type = request.resource_type
    url = request.url
    if resource_type in ("stylesheet", "font", "media"):
        route.abort()
        return
    # Block known tracking/analytics domains
    blocked_domains = [
        "facebook.net/signals",
        "connect.facebook.net",
        "staticxx.facebook.com",
        "pixel.facebook.com",
        "an.facebook.com",
    ]
    if any(domain in url for domain in blocked_domains):
        route.abort()
        return
    route.continue_()


def scrape_page(
    page_url: str,
    cookies: list,
    existing_urls: set = None,
    max_scrolls: int = 8,
    max_age_days: float = 7.0,
    max_ocr_per_page: int = 10,
) -> list[dict]:
    """
    Scrape a single Facebook page for relevant events.

    Args:
        page_url:          Facebook page URL to scrape.
        cookies:           Authenticated Facebook cookies.
        existing_urls:     Set of already-known post URLs to skip (dedup).
        max_scrolls:       How many times to scroll down per surface (default 8).
        max_age_days:      Hard cutoff — posts older than this are skipped
                           immediately without OCR or LLM calls. Default 7 days.
        max_ocr_per_page:  Max total Gemini Vision OCR calls across the whole page
                           to prevent quota exhaustion on busy pages. Default 10.
    """
    posts = []
    page_ocr_count = 0  # tracks total OCR calls for this page

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            # Disable images for faster loads (we only fetch specific scontent images)
            java_script_enabled=True,
        )
        norm = normalize_playwright_cookies(cookies)
        if not norm:
            print("  Warning: no valid cookies provided to Playwright context")
        else:
            try:
                ctx.add_cookies(norm)
            except Exception as e:
                print(f"  Warning: add_cookies failed: {e}")
        page = ctx.new_page()

        # Block unnecessary resources to speed up page loads
        page.route("**/*", _block_unnecessary_resources)

        try:
            for target_url in candidate_page_urls(page_url):
                posts_before_surface = len(posts)
                print(f"  Opening: {target_url}")
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                except Exception as e:
                    print(f"  Surface failed: {e}")
                    continue
                time.sleep(3)

                click_see_more_buttons(page)

                surface_success = False

                for i in range(max_scrolls):
                    print(f"  Scrolling... ({i + 1}/{max_scrolls})")

                    soup = BeautifulSoup(page.content(), "html.parser")

                    # Support multiple message selectors: plugin timeline, standard www, and legacy surfaces
                    message_elements = []
                    try:
                        message_elements.extend(soup.find_all("div", {"class": re.compile(r"story_body|story|_5pbx")}))
                    except Exception:
                        pass
                    message_elements.extend(soup.find_all("div", {"data-ad-preview": "message"}))
                    message_elements.extend(soup.find_all("div", {"role": "article"}))

                    if message_elements:
                        surface_success = True

                    for el in message_elements:

                        if is_video_post(el):
                            continue

                        caption_text = el.get_text(separator=" ", strip=True)

                        href = find_ancestor_with_link(el)
                        post_url = clean_url(href) if href else (page.url if page.url else page_url)

                        if existing_urls and post_url in existing_urls:
                            continue

                        # --- EARLY AGE CHECK (before any expensive calls) ---
                        age_source_text = get_post_header_text(el, caption_text)
                        post_age_days = parse_age_days(age_source_text) or parse_age_days(caption_text)
                        
                        # Hard cutoff: skip posts definitively older than max_age_days
                        if post_age_days is not None and post_age_days > max_age_days:
                            print(f"  Skipped old post ({post_age_days:.1f}d > {max_age_days}d limit).")
                            continue

                        # --- CAPTION KEYWORD PRE-CHECK (before fetching full post / OCR) ---
                        caption_passes = is_relevant_event(caption_text)

                        # Only fetch full post text if the URL is a true permalink
                        if post_url != page.url and "plugins/page.php" not in page.url:
                            full_text, fetched_post_age_days = fetch_full_post_text(ctx, post_url)
                            if full_text:
                                caption_text = full_text
                            if fetched_post_age_days is not None:
                                post_age_days = fetched_post_age_days
                                # Re-check age after fetching full text
                                if post_age_days > max_age_days:
                                    print(f"  Skipped old post ({post_age_days:.1f}d) after permalink fetch.")
                                    continue

                        if is_truncated(caption_text) and post_url != page.url and post_age_days is None:
                            full_text, fetched_post_age_days = fetch_full_post_text(ctx, post_url)
                            if full_text:
                                caption_text = full_text
                            if fetched_post_age_days is not None:
                                post_age_days = fetched_post_age_days

                        # Strip leftover "See more"/"Tumingin pa" artifact
                        for suffix in ["see more", "tumingin pa"]:
                            if caption_text.lower().endswith(suffix):
                                caption_text = caption_text[: -len(suffix)].rstrip(". ").strip()

                        # --- OCR: Only run if caption alone didn't pass the keyword filter
                        # AND the per-page OCR budget hasn't been exhausted ---
                        image_text = ""
                        if not caption_passes and page_ocr_count < max_ocr_per_page:
                            # Caption alone didn't match — check images for additional text
                            image_container = get_ancestor(el, 3)
                            if image_container:
                                images = image_container.find_all("img", {"src": True})
                                seen_images = set()
                                ocr_count = 0
                                for img in images:
                                    if ocr_count >= 3 or page_ocr_count >= max_ocr_per_page:
                                        break
                                    src = img.get("src", "") or img.get("data-src", "")
                                    if "scontent" in src and src not in seen_images:
                                        seen_images.add(src)
                                        ocr_result = extract_text_from_image(src)
                                        if ocr_result:
                                            image_text += " " + ocr_result
                                        ocr_count += 1
                                        page_ocr_count += 1
                                if ocr_count > 0:
                                    print(f"  OCR processed {ocr_count} image(s) [page budget: {page_ocr_count}/{max_ocr_per_page}]")

                        if caption_text or image_text:
                            # Final relevance check with all text combined
                            if not is_relevant_event(caption_text, image_text):
                                continue

                            posts.append({
                                "text": clean_caption_text(caption_text),
                                "image_text": image_text.strip(),
                                "source_url": post_url,
                                "age_days": post_age_days
                            })

                    page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    time.sleep(random.uniform(2.0, 3.5))

                if surface_success:
                    print("  Timeline loaded successfully. Skipping fallback surface.")
                    break

        except Exception as e:
            print(f"  Error on {page_url}: {e}")

        finally:
            browser.close()

    seen = set()
    unique_posts = []
    for post in posts:
        if post["text"] not in seen:
            seen.add(post["text"])
            unique_posts.append(post)

    print(f"  Found {len(unique_posts)} unique posts")
    return unique_posts
