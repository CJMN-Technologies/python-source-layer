from playwright.sync_api import sync_playwright
from auth import get_cookies

url = "https://mbasic.facebook.com/PAGASA.DOST.GOV.PH"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    ctx.add_cookies(get_cookies())
    page = ctx.new_page()
    page.goto(url, wait_until="domcontentloaded", timeout=60000)

    # Wait for actual post content to appear
    page.wait_for_selector("div[data-ad-preview='message']", timeout=30000)
    page.wait_for_timeout(3000)

    html = page.content()
    with open("mbasic_sample.html", "w", encoding="utf-8") as f:
        f.write(html)

    print("Saved mbasic_sample.html")
    browser.close()