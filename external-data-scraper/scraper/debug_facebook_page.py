import argparse
import re
import sys

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from auth import get_cookies
from fb_scraper import normalize_playwright_cookies


KNOWN_KEYWORDS = [
    "no classes",
    "office work",
    "holiday",
    "university advisory",
    "class suspension",
    "walang pasok",
    "walang klase",
]

LOGIN_PATTERNS = [
    "log in",
    "login",
    "mag-log in",
    "mag-log",
    "gumamit ng ibang profile",
    "create new account",
    "gumawa ng bagong account",
    "checkpoint",
    "security check",
]


def safe_snippet(text: str, limit: int = 1000) -> str:
    snippet = re.sub(r"\s+", " ", text).strip()[:limit]
    return snippet.encode("utf-8", errors="replace").decode("utf-8", errors="replace")


def count_matching_text(text: str, patterns: list[str]) -> dict[str, bool]:
    lowered = text.lower()
    return {pattern: pattern.lower() in lowered for pattern in patterns}


def run_diagnostic(url: str, target: str | None, scrolls: int, wait_ms: int) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cookies = normalize_playwright_cookies(get_cookies())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            viewport={"width": 1365, "height": 900},
        )
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(wait_ms)

        for _ in range(scrolls):
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            page.wait_for_timeout(1000)

        final_url = page.url
        title = page.title()
        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")
    visible_text = soup.get_text("\n", strip=True)
    lowered_text = visible_text.lower()

    article_blocks = soup.find_all(attrs={"role": "article"})
    data_ad_blocks = soup.find_all(attrs={"data-ad-preview": "message"})
    story_blocks = soup.find_all("div", {"class": re.compile(r"story_body|story|_5pbx")})
    feed_blocks = soup.find_all(attrs={"data-pagelet": re.compile(r"FeedUnit|ProfileTimeline|Timeline")})
    image_tags = soup.find_all("img")
    scontent_images = [
        image for image in image_tags
        if "scontent" in (image.get("src") or image.get("data-src") or "")
    ]

    login_hits = count_matching_text(visible_text, LOGIN_PATTERNS)
    keyword_checks = list(KNOWN_KEYWORDS)
    if target:
        keyword_checks.append(target)
    keyword_hits = count_matching_text(visible_text, keyword_checks)

    print("=== Facebook Page Diagnostic ===")
    print(f"Requested URL: {url}")
    print(f"Final loaded URL: {final_url}")
    print(f"Page title: {title}")
    print(f"Cookies supplied: {len(cookies)}")
    print(f"Visible text length: {len(visible_text)}")
    print(f"Contains target page name: {bool(target and target.lower() in lowered_text)}")
    print("Login/checkpoint indicators:")
    for pattern, found in login_hits.items():
        print(f"  - {pattern}: {found}")
    print("DOM block counts:")
    print(f"  - role=article: {len(article_blocks)}")
    print(f"  - data-ad-preview=message: {len(data_ad_blocks)}")
    print(f"  - story-like class blocks: {len(story_blocks)}")
    print(f"  - feed/timeline pagelets: {len(feed_blocks)}")
    print("Image counts:")
    print(f"  - img tags: {len(image_tags)}")
    print(f"  - scontent images: {len(scontent_images)}")
    print("Known text hits:")
    for pattern, found in keyword_hits.items():
        print(f"  - {pattern}: {found}")
    print("First visible text snippet:")
    print(safe_snippet(visible_text))

    preview_blocks = article_blocks or data_ad_blocks or story_blocks
    print("Candidate block previews:")
    if not preview_blocks:
        print("  - none")
    for index, block in enumerate(preview_blocks[:3], start=1):
        print(f"  [{index}] {safe_snippet(block.get_text(' ', strip=True), 500)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--target", default=None)
    parser.add_argument("--scrolls", type=int, default=5)
    parser.add_argument("--wait-ms", type=int, default=4000)
    args = parser.parse_args()

    run_diagnostic(args.url, args.target, args.scrolls, args.wait_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
