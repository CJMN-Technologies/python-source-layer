from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import pytesseract
from PIL import Image
import requests
import time
import random
import io
import platform
import re
from datetime import datetime

if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'


PERMALINK_PATTERN = re.compile(r"(/posts/|/permalink|story_fbid|/photos/|pfbid|fbid=|/share/)")
VIDEO_PATTERN = re.compile(r"(/reel/|/videos/|/watch)")
SUSPENSION_PATTERN = re.compile(r"\b(suspend(?:ed|ion|ing)?|suspens(?:ion|yon|yo)?|suspenso|suspendido)\b", re.I)
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
]
STRONG_RELEVANT_KEYWORDS = [
    "no classes",
    "class suspension",
    "classes suspended",
    "suspended classes",
    "school suspension",
    "school closed",
    "class cancellation",
    "class postponement",
    "cancelled classes",
    "resumption of classes",
    "walang pasok",
    "suspendido ang klase",
    "suspendido ang pasok",
    "pampublikong pahinga",
    "estado ng kalamidad",
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
]

DATE_PATTERNS = [
    (re.compile(r"(\d+)\s+minutes?\s+ago", re.I), lambda m: int(m.group(1)) / 60 / 24),
    (re.compile(r"(\d+)\s+hours?\s+ago", re.I), lambda m: int(m.group(1)) / 24),
    (re.compile(r"(\d+)\s+days?\s+ago", re.I), lambda m: int(m.group(1))),
    (re.compile(r"Yesterday", re.I), lambda m: 1.0),
    (re.compile(r"Today", re.I), lambda m: 0.0),
    (re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})", re.I), None),
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


def extract_text_from_image(image_url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(image_url, headers=headers, timeout=10)
        image = Image.open(io.BytesIO(response.content))

        data = pytesseract.image_to_data(image, lang="eng", output_type=pytesseract.Output.DICT)

        words = []
        for i, word in enumerate(data["text"]):
            try:
                conf = int(data["conf"][i])
            except ValueError:
                conf = -1
            if conf > 60 and word.strip():
                words.append(word.strip())

        return " ".join(words)
    except Exception as e:
        print(f"  OCR error: {e}")
        return ""


def is_suspension_related(text: str, image_text: str = "") -> bool:
    """Return True if the combined text likely refers to a relevant academic/LGU or suspension-related event."""
    combined = (text or "") + " " + (image_text or "")
    if not combined.strip():
        return False

    lowered = combined.lower()

    # Accept clearly relevant academic or LGU phrases immediately.
    for kw in STRONG_RELEVANT_KEYWORDS:
        if kw in lowered:
            return True

    # Require a suspension-like term for other posts.
    if not SUSPENSION_PATTERN.search(lowered):
        return False

    # Accept the post if it has any relevant context keyword.
    for kw in SUSPENSION_CONTEXT_KEYWORDS + GENERAL_RELEVANT_KEYWORDS:
        if kw in lowered:
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
        # Prefer the server-rendered mbasic variant for full text (no client JS truncation)
        if "facebook.com" in post_url:
            post_url = post_url.replace("www.facebook.com", "mbasic.facebook.com").replace("m.facebook.com", "mbasic.facebook.com")
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


def scrape_page(page_url: str, cookies: list, max_scrolls: int = 5) -> list[dict]:
    posts = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
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

        try:
            # Try mbasic variant first (server-rendered, avoids client-side "See more" truncation)
            target_url = page_url.replace("www.facebook.com", "mbasic.facebook.com").replace("m.facebook.com", "mbasic.facebook.com")
            print(f"  Opening: {target_url}")
            try:
                page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                # fallback to original if mbasic fails
                print("  mbasic load failed, falling back to original URL")
                page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)

            click_see_more_buttons(page)

            for i in range(max_scrolls):
                print(f"  Scrolling... ({i + 1}/{max_scrolls})")

                soup = BeautifulSoup(page.content(), "html.parser")

                # support multiple message selectors: standard www data-ad-preview, mbasic/story containers, role=article
                message_elements = []
                message_elements.extend(soup.find_all("div", {"data-ad-preview": "message"}))
                try:
                    message_elements.extend(soup.find_all("div", {"class": re.compile(r"story_body|story|_5pbx")}))
                except Exception:
                    pass
                message_elements.extend(soup.find_all("div", {"role": "article"}))

                for el in message_elements:

                    if is_video_post(el):
                        continue

                    caption_text = el.get_text(separator=" ", strip=True)

                    href = find_ancestor_with_link(el)
                    prefer_mbasic = "mbasic.facebook.com" in page.url
                    post_url = clean_url(href, prefer_mbasic=prefer_mbasic) if href else (page.url if page.url else page_url)

                    post_age_days = None
                    if post_url != page_url:
                        full_text, post_age_days = fetch_full_post_text(ctx, post_url)
                        if full_text:
                            caption_text = full_text

                    if is_truncated(caption_text) and post_url != page_url and post_age_days is None:
                        full_text, post_age_days = fetch_full_post_text(ctx, post_url)
                        if full_text:
                            caption_text = full_text

                    # strip leftover "See more"/"Tumingin pa" artifact if we couldn't expand it
                    for suffix in ["see more", "tumingin pa"]:
                        if caption_text.lower().endswith(suffix):
                            caption_text = caption_text[: -len(suffix)].rstrip(". ").strip()

                    image_container = get_ancestor(el, 3)
                    image_text = ""
                    if image_container:
                        images = image_container.find_all("img", {"src": True})
                        seen_images = set()
                        ocr_count = 0
                        for img in images:
                            if ocr_count >= 5:
                                break
                            src = img.get("src", "") or img.get("data-src", "")
                            if "scontent" in src and "p" in src and src not in seen_images:
                                seen_images.add(src)
                                ocr_result = extract_text_from_image(src)
                                if ocr_result:
                                    image_text += " " + ocr_result
                                ocr_count += 1
                        if ocr_count > 0:
                            print(f"  OCR processed {ocr_count} image(s)")

                    if caption_text or image_text:
                        # Tighten filter: only keep posts that look related to suspensions
                        if not is_suspension_related(caption_text, image_text):
                            continue

                        posts.append({
                            "text": caption_text,
                            "image_text": image_text.strip(),
                            "source_url": post_url,
                            "age_days": post_age_days
                        })

                page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
                time.sleep(random.uniform(2.5, 4.5))

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