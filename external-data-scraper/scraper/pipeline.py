import json
import os
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from auth import get_all_cookie_profiles_labeled
from fb_scraper import scrape_page, is_valid_facebook_post_url
from keywords import classify_post
from llm_classifier import classify_post_llm
from email_notifier import send_pipeline_alert, send_cookie_alert

# Fix Windows console encoding crash on special characters (e.g. arrows, checkmarks)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Hard ceiling: never ingest posts older than this many days from now
MAX_AGE_DAYS = 14.0

# Default number of scrolls per page (can be overridden per page in pages.json)
DEFAULT_MAX_SCROLLS = 6


def _next_ext_id_for_category(category: str) -> str:
    """Return next external_<category>_<NNNN> id by querying Supabase for the max existing id."""
    category_key = category.lower()
    if category_key in ("academic", "academic_calendar", "acad"):
        category_code = "acad"
    elif category_key == "lgu":
        category_code = "lgu"
    elif category_key == "pagasa":
        category_code = "pagasa"
    else:
        category_code = category.lower()

    base = f"external_{category_code}"
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
            first = rows[0] if isinstance(rows, list) else rows
            last_id = first.get("id") if isinstance(first, dict) else None
            if last_id:
                try:
                    last_num = int(last_id.rsplit("_", 1)[-1])
                    return f"{base}_{(last_num + 1):04d}"
                except Exception:
                    pass
    except Exception:
        pass
    return f"{base}_0001"

# PAGASA logic removed (historically kept in DB, no longer scraped)


def load_pages(batch: str = "all") -> list[dict]:
    """Load pages.json, optionally filtering by batch letter (A/B/C/D) or 'all'."""
    path = os.path.join(os.path.dirname(__file__), "pages.json")
    with open(path, "r", encoding="utf-8") as f:
        all_pages = json.load(f)

    if batch.lower() == "all":
        return all_pages

    target = batch.upper()
    targets = [t.strip() for t in target.split(",") if t.strip()]
    filtered = [p for p in all_pages if p.get("batch", "").upper() in targets]
    if not filtered:
        print(f"Warning: No pages found for batch(es) '{target}'. Running all pages.")
        return all_pages
    return filtered


def _determine_category(llm_res: dict, page: dict) -> str | None:
    """
    Determine the final category for a post.

    Priority:
    1. If LLM returns 'academic_calendar', preserve it (do NOT override).
    2. If page is PAGASA, force 'pagasa'.
    3. If page is an LGU/government/PIO, force 'lgu'.
    4. Otherwise trust the LLM category.
    """
    llm_category = llm_res.get("category")

    if llm_category is None:
        return None

    # Always preserve academic_calendar — do not override it
    if llm_category == "academic_calendar":
        return "academic_calendar"

    page_name_lower = page["name"].casefold()

    if "pagasa" in page_name_lower:
        return "pagasa"

    if (
        "government" in page_name_lower
        or "pio" in page_name_lower
        or "city" in page_name_lower
        or "municipality" in page_name_lower
        or "lgu" in page_name_lower
    ):
        return "lgu"

    # Prevent universities/academic pages from being wrongly classified as LGU
    is_academic_page = any(kw in page_name_lower for kw in ["university", "college", "school", "institute", "student council", "sanggunian", "student organization", "konseho", "mag-aaral"])
    if is_academic_page and llm_category == "lgu":
        return "acad"

    # For academic/student council pages, trust the LLM
    return llm_category if llm_category else "acad"


def run_pipeline(batch: str = "all"):
    print(f"=== LRT-2 Scraper Pipeline Starting [Batch: {batch.upper()}] ===")
    print(f"Time (PHT): {datetime.now(timezone(timedelta(hours=8)))}")
    print(f"Time (UTC): {datetime.now(timezone.utc)}")
    print(f"Max post age: {MAX_AGE_DAYS} days")

    # Load existing events for deduplication
    existing_urls = set()
    existing_texts = set()
    try:
        res = (
            supabase.schema("external")
            .table("academic_lgu_events")
            .select("source_url, post_text, image_text")
            .execute()
        )
        rows = res.data if hasattr(res, "data") else res
        if rows:
            existing_urls = {row.get("source_url") for row in rows if row.get("source_url")}
            for row in rows:
                combined_db = f"{row.get('post_text') or ''} {row.get('image_text') or ''}".strip()
                if combined_db:
                    existing_texts.add(combined_db[:100])
        print(f"Loaded {len(existing_urls)} existing events from database.")
    except Exception as e:
        print(f"Warning: Could not fetch existing data from database: {e}")

    pages = load_pages(batch)
    print(f"Pages to scrape in batch '{batch.upper()}': {len(pages)}")

    # Load all cookie profiles (with labels) for rotation
    cookie_profiles = get_all_cookie_profiles_labeled()
    if not cookie_profiles:
        print("Warning: No FB cookie profiles found. Scraping may fail.")
        cookie_profiles = [{"cookies": [], "label": "None", "env_suffix": ""}]

    total_saved = 0
    newly_saved_events = []
    expired_accounts = []  # Track which accounts hit login walls

    for page_idx, page in enumerate(pages):
        print(f"\n[{page_idx + 1}/{len(pages)}] Scraping: {page['name']} ({page['station']}) — Batch {page.get('batch','?')}")

        # Rotate cookie profiles across pages
        profile = cookie_profiles[page_idx % len(cookie_profiles)]
        cookies = profile["cookies"]

        # Respect per-page max_scrolls override (e.g. low-activity pages)
        page_max_scrolls = page.get("max_scrolls", DEFAULT_MAX_SCROLLS)

        posts = scrape_page(
            page["url"],
            cookies,
            existing_urls=existing_urls,
            max_scrolls=page_max_scrolls,
            max_age_days=MAX_AGE_DAYS,
        )

        # Check if cookies expired (login wall detected)
        if posts and isinstance(posts[0], dict) and posts[0].get("_cookie_expired"):
            account_info = {"account_label": profile["label"], "env_suffix": profile["env_suffix"]}
            if account_info not in expired_accounts:
                expired_accounts.append(account_info)
            print(f"  Cookie expired for {profile['label']}! Trying next account...")

            # Try remaining profiles for this page
            recovered = False
            for fallback_idx, fallback_profile in enumerate(cookie_profiles):
                if fallback_profile["label"] == profile["label"]:
                    continue  # skip the one that just failed
                fallback_info = {"account_label": fallback_profile["label"], "env_suffix": fallback_profile["env_suffix"]}
                if fallback_info in expired_accounts:
                    continue  # skip already-expired accounts

                print(f"  Retrying with {fallback_profile['label']}...")
                posts = scrape_page(
                    page["url"],
                    fallback_profile["cookies"],
                    existing_urls=existing_urls,
                    max_scrolls=page_max_scrolls,
                    max_age_days=MAX_AGE_DAYS,
                )
                if posts and isinstance(posts[0], dict) and posts[0].get("_cookie_expired"):
                    if fallback_info not in expired_accounts:
                        expired_accounts.append(fallback_info)
                    print(f"  Cookie also expired for {fallback_profile['label']}!")
                    continue
                else:
                    recovered = True
                    break

            if not recovered:
                print(f"  All accounts expired. Skipping page: {page['name']}")
                continue

        for post in posts:
            post_age_days = post.get("age_days")

            # Hard age cutoff (belt-and-suspenders — scrape_page also checks this)
            if post_age_days is not None and post_age_days > MAX_AGE_DAYS:
                print(f"  Skipped old post ({post_age_days:.1f} days): {post['text'][:80]}...")
                continue

            combined = f"{post.get('text', '')} {post.get('image_text', '')}".strip()

            # Deduplicate by text similarity (catches /posts/ vs /photo/ for same content)
            post_prefix = combined[:100]
            if post_prefix and post_prefix in existing_texts:
                print("  Skipped duplicate text (already in DB under different URL).")
                continue

            # OLFU filter: accept posts mentioning Antipolo campus OR systemwide/all campus suspensions
            if "fatima" in page["url"].casefold() or "fatima" in page["name"].casefold():
                combined_lower = combined.casefold()
                is_antipolo = "antipolo" in combined_lower
                is_systemwide = any(k in combined_lower for k in ["all campuses", "all levels", "systemwide", "entire university", "walang pasok", "class suspension", "suspended", "advisory", "notice"])
                if not is_antipolo and not is_systemwide:
                    print("  Skipped OLFU post: Does not apply to Antipolo campus.")
                    continue

            # (PAGASA filtering logic removed)

            # PRE-FILTER with keywords (case-insensitive via classify_post using .casefold())
            pre_category = classify_post(combined)
            if pre_category is None:
                continue

            # SMART EXTRACTION with LLM (evaluates Caption + Image OCR Text together)
            llm_res = classify_post_llm(post.get("text", ""), post.get("image_text", ""))

            # Determine final category (preserves academic_calendar, respects page type)
            category = _determine_category(llm_res, page)

            if category is None:
                if llm_res.get("llm_failed") and pre_category:
                    category = pre_category
                    event_name = f"[Fallback] Event detected via keywords ({category})"
                    event_date = "Not specified"
                    print(f"  [Fallback] Gemini keys exhausted. Falling back to keyword category: {category}")
                else:
                    print("  LLM rejected post (Not a valid event/calendar).")
                    continue
            else:
                event_name = llm_res.get("event_name")
                event_date = llm_res.get("event_date")

            now = datetime.now(timezone.utc)
            if post_age_days is not None:
                post_date = now - timedelta(days=post_age_days)
            else:
                # Age unknown: use now as best estimate
                post_date = now

            source_url = post.get("source_url", "")
            if not is_valid_facebook_post_url(source_url, page["url"]):
                print(f"  Skipped unsafe/non-post source URL: {source_url or 'missing'}")
                continue

            try:
                ext_id = _next_ext_id_for_category(category)

                supabase.schema("external").table("academic_lgu_events").upsert({
                    "id":          ext_id,
                    "station":     page["station"],
                    "source_name": page["name"],
                    "source_url":  source_url,
                    "post_text":   post["text"][:2000],
                    "image_text":  post["image_text"][:2000] if post["image_text"] else None,
                    "category":    category,
                    "event_name":  event_name[:500] if event_name else None,
                    "event_date":  event_date[:100] if event_date else None,
                    "scraped_at":  now.isoformat(),
                    "post_date":   post_date.isoformat(),
                }).execute()

                existing_urls.add(source_url)
                existing_texts.add(post_prefix)
                total_saved += 1
                newly_saved_events.append({
                    "source_name": page["name"],
                    "category":    category,
                    "event_name":  event_name or "N/A",
                    "event_date":  event_date or "N/A",
                    "scraped_at":  now.isoformat(),
                    "url":         source_url
                })
                print(f"  >> Saved: [{category}] {event_name or post['text'][:60]}")
            except Exception as e:
                print(f"  Failed to save post: {e}")

    print(f"\n=== Done! {total_saved} posts saved to Supabase ===")

    if newly_saved_events:
        print("Sending email alert for new events...")
        send_pipeline_alert(newly_saved_events)

    if expired_accounts:
        print(f"Sending cookie expiration alert for {len(expired_accounts)} account(s)...")
        send_cookie_alert(expired_accounts, scraper_name="Events Pipeline")


if __name__ == "__main__":
    # Usage: python pipeline.py [batch]
    # batch: A, B, C, D, or all (default: all)
    batch_arg = "all"
    if len(sys.argv) > 1:
        batch_arg = sys.argv[1].strip()

    run_pipeline(batch=batch_arg)
