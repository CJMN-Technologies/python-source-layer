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
    except Exception as e:
        pass

    return ""

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


MONTH_MAP = {
    "january": 1, "enero": 1,
    "february": 2, "pebrero": 2,
    "march": 3, "marso": 3,
    "april": 4, "abril": 4,
    "may": 5, "mayo": 5,
    "june": 6, "hunyo": 6,
    "july": 7, "hulyo": 7,
    "august": 8, "agosto": 8,
    "september": 9, "setyembre": 9,
    "october": 10, "oktubre": 10,
    "november": 11, "nobyembre": 11,
    "december": 12, "disyembre": 12,
}

MONTHS_REGEX = r"(January|February|March|April|May|June|July|August|September|October|November|December|Enero|Pebrero|Marso|Abril|Mayo|Hunyo|Hulyo|Agosto|Setyembre|Oktubre|Nobyembre|Disyembre)"

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
    (re.compile(rf"{MONTHS_REGEX}\s+(\d{{1,2}}),\s*(\d{{4}})", re.I), None),
    (re.compile(rf"(\d{{1,2}})\s+(?:ng\s+)?{MONTHS_REGEX}(?:\s+at\s+\d{{1,2}}:\d{{2}})?", re.I), lambda m: max(0.0, (datetime.utcnow() - datetime(datetime.utcnow().year, MONTH_MAP.get(m.group(2).lower(), 1), int(m.group(1)))).days)),
    (re.compile(rf"{MONTHS_REGEX}\s+(\d{{1,2}})(?:\s+at\s+\d{{1,2}}:\d{{2}})?", re.I), lambda m: max(0.0, (datetime.utcnow() - datetime(datetime.utcnow().year, MONTH_MAP.get(m.group(1).lower(), 1), int(m.group(2)))).days)),
]


def parse_age_days(text: str) -> float | None:
    if not text:
        return None
    for pattern, converter in DATE_PATTERNS:
        m = pattern.search(text)
        if m:
            if converter is None:
                month_name = m.group(1).lower()
                day = int(m.group(2))
                year = int(m.group(3))
                try:
                    month = MONTH_MAP.get(month_name, 1)
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


def strip_social_boilerplate(text: str) -> str:
    """Strip social media reaction counts, share counters, and trailing UI text."""
    if not text:
        return ""
    cleaned = re.sub(r"All reactions:.*", "", text, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\b\d+\s+reactions?\b.*", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\b\d+\s+shares?\b.*", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    return cleaned.strip()


def clean_caption_text(text: str) -> str:
    """Strip emojis, social boilerplate, and normalize whitespace from post caption text."""
    text = strip_social_boilerplate(text)
    text = strip_emojis(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
    """Check if caption text ends abruptly or contains Facebook 'See More' / ellipsis truncation."""
    if not text:
        return False
    t = text.strip().casefold()
    truncation_patterns = [
        "see more", "tumingin pa", "read more", "see less",
        "...", "\u2026", "at al…", "al…", "see more…", "tumingin pa…"
    ]
    return any(t.endswith(p) or f" {p}" in t for p in truncation_patterns)


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


def find_ancestor_with_link(el, max_levels: int = 12, expected_page_url: str | None = None):
    current = el
    for _ in range(max_levels):
        if current is None:
            break
        for a in current.find_all("a", {"href": True}, limit=50):
            href = a["href"]
            cleaned_href = clean_url(href)
            if is_valid_facebook_post_url(cleaned_href, expected_page_url):
                return href
        current = current.parent
    return None


def clean_url(href: str, prefer_mbasic: bool = False) -> str:
    if not href:
        return ""
    if href.startswith("/"):
        host = "mbasic.facebook.com" if prefer_mbasic else "www.facebook.com"
        href = f"https://{host}" + href
    # normalize and strip tracking params
    return href.split("&")[0].split("?__cft__")[0]


def _facebook_page_slug(page_url: str | None) -> str | None:
    if not page_url:
        return None
    parsed = urlparse(canonical_facebook_url(page_url))
    path_parts = [part for part in parsed.path.casefold().split("/") if part]
    if not path_parts or path_parts[0] == "profile.php":
        return None
    return path_parts[0]


def is_valid_facebook_post_url(href: str, expected_page_url: str | None = None) -> bool:
    """Accept only real Facebook post/photo/story URLs from trusted pages.

    Personal profiles, people links, comment/reply links, videos, and reels are
    intentionally rejected so they cannot be saved as source_url.
    """
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

    if "plugins/page.php" in combined:
        return False

    if any(blocked in path for blocked in PERSONAL_OR_UNSAFE_FACEBOOK_PATHS):
        return False

    if "comment_id=" in query or "reply_comment_id=" in query:
        return False

    if not PERMALINK_PATTERN.search(combined):
        return False

    expected_slug = _facebook_page_slug(expected_page_url)
    path_parts = [part for part in path.split("/") if part]
    if expected_slug and len(path_parts) >= 2 and path_parts[1] in {"posts", "photos"}:
        return path_parts[0] == expected_slug

    return True


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
    max_age_days: float = 14.0,
    max_ocr_per_page: int = 25,
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

                # Check for login wall — cookies expired
                try:
                    if page.locator("text=You must log in to continue").count() > 0 or \
                       (page.locator("text=Log In").count() > 0 and page.locator("text=Create new account").count() > 0):
                        print("  Login wall detected! Cookies likely expired.")
                        browser.close()
                        return [{"_cookie_expired": True}]
                except Exception:
                    pass

                click_see_more_buttons(page)

                surface_success = False

                for i in range(max_scrolls):
                    print(f"  Scrolling... ({i + 1}/{max_scrolls})")

                    try:
                        html_content = page.content()
                    except Exception as e:
                        if "navigating" in str(e).lower():
                            try:
                                page.wait_for_load_state("domcontentloaded", timeout=10000)
                                html_content = page.content()
                            except Exception:
                                continue
                        else:
                            continue

                    soup = BeautifulSoup(html_content, "html.parser")

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

                        href = find_ancestor_with_link(el, expected_page_url=page_url)
                        post_url = clean_url(href) if href else ""

                        if not is_valid_facebook_post_url(post_url, page_url):
                            continue

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

                        # --- OCR: Run on post images if OCR budget is available ---
                        image_text = ""
                        if page_ocr_count < max_ocr_per_page:
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

                    try:
                        page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                    except Exception as e:
                        if "navigation" in str(e).lower() or "context was destroyed" in str(e).lower():
                            print("  Navigation detected during scroll. Stopping scroll.")
                            break
                        else:
                            print(f"  Warning: Scroll failed ({e})")
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
