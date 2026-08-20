import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from fb_scraper import scrape_pages_batch, is_valid_facebook_post_url
from keywords import classify_post
from llm_classifier import classify_post_llm
from email_notifier import send_pipeline_alert

# Fix Windows console encoding crash on special characters (e.g. arrows, checkmarks)
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

def mask_ci_text(val: str):
    """Emit GitHub Actions ::add-mask:: workflow command to scrub sensitive text from public runner logs."""
    if val and (os.getenv("GITHUB_ACTIONS") == "true" or os.getenv("CI") == "true"):
        for line in str(val).splitlines():
            clean = line.strip()
            if len(clean) >= 6:
                print(f"::add-mask::{clean}", flush=True)

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
        category_code = "academic"
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


def is_olfu_antipolo_post(text: str) -> bool:
    """
    Designated OLFU filter:
    The OLFU official nationwide page posts advisories for various branches:
    - OLFU Antipolo (LRT-2 Target Branch)
    - OLFU Valenzuela
    - OLFU Metro Manila
    - OLFU Quezon City / QC
    - OLFU Nueva Ecija / Cabanatuan
    - OLFU Laguna / Sta. Rosa
    - OLFU Pampanga / San Fernando
    
    Rule:
    1. If text explicitly mentions true systemwide keywords ('all campuses', 'all olfu campuses', 'systemwide', 'entire university', 'across all campuses', 'all branches', 'lahat ng campus'):
       - Check if Antipolo is specifically excepted (e.g. 'except OLFU Antipolo', 'maliban sa Antipolo', 'excluding Antipolo').
       - If Antipolo is NOT excepted, ACCEPT.
       - If Antipolo IS excepted, REJECT.
    2. If text explicitly mentions 'antipolo', ACCEPT (even if other branches are listed in multi-branch announcements).
    3. If text mentions other specific branches without 'all campuses' and without mentioning 'antipolo', REJECT.
    4. Otherwise, REJECT.
    """
    t = text.casefold()

    is_truly_systemwide = any(k in t for k in [
        "all campuses",
        "all olfu campuses",
        "all branches",
        "systemwide",
        "entire university",
        "across all campuses",
        "lahat ng campus",
    ])

    if is_truly_systemwide:
        # Check if Antipolo is specifically exempted/excepted
        antipolo_excepted = bool(re.search(
            r"(?:except|excluding|maliban\s+sa|bukod\s+sa)\s+(?:(?:for|sa)\s+)?(?:olfu\s+)?antipolo",
            t
        ))
        if antipolo_excepted:
            return False
        return True

    if "antipolo" in t:
        return True

    other_branches = [
        "valenzuela",
        "quezon city",
        "olfu qc",
        "pampanga",
        "san fernando",
        "nueva ecija",
        "cabanatuan",
        "laguna",
        "sta. rosa",
        "santa rosa",
        "metro manila"
    ]
    mentions_other_branch = any(b in t for b in other_branches)
    if mentions_other_branch:
        return False

    return False


def _determine_category(llm_res: dict, page: dict) -> str | None:
    """
    Determine the final category for a post.

    Authoritative Mapping:
    1. If LLM returns 'academic_calendar', preserve it.
    2. If page has explicit 'source_type':
       - 'academic' -> ALWAYS return 'academic'
       - 'lgu'      -> ALWAYS return 'lgu'
    3. Fallback to name-based registry if source_type is omitted.
    """
    llm_category = llm_res.get("category")
    if llm_category is None and not llm_res.get("llm_failed"):
        return None

    # Always preserve academic_calendar — do not override it
    if llm_category == "academic_calendar":
        return "academic_calendar"

    source_type = page.get("source_type")
    if source_type in ("academic", "lgu"):
        return source_type

    page_name_lower = page["name"].casefold()

    if "pagasa" in page_name_lower:
        return "pagasa"

    # Academic institutions registry
    is_academic = any(kw in page_name_lower for kw in [
        "university", "college", "school", "institute", "student council",
        "sanggunian", "student organization", "konseho", "mag-aaral",
        "feu", "ust", "ue", "pup", "uerm", "sbu", "tip", "wcc", "admu", "fatima"
    ])
    if is_academic:
        return "academic"

    # LGU / Government registry
    is_lgu = any(kw in page_name_lower for kw in [
        "government", "pio", "public information", "municipality", "city government", "lgu"
    ])
    if is_lgu:
        return "lgu"

    return "academic"



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
                combined_db = re.sub(r"\s+", " ", f"{row.get('post_text') or ''} {row.get('image_text') or ''}").strip().casefold()
                if combined_db:
                    existing_texts.add(combined_db[:100])
        print(f"Loaded {len(existing_urls)} existing events from database.")
    except Exception as e:
        print(f"Warning: Could not fetch existing data from database: {e}")

    pages = load_pages(batch)
    print(f"Pages to scrape in batch '{batch.upper()}': {len(pages)}")

    # Single Apify Actor run for all pages in this batch to minimize cost!
    scraped_data_by_url = scrape_pages_batch(
        pages=pages,
        existing_urls=existing_urls,
        max_age_days=MAX_AGE_DAYS,
        results_limit_per_page=5,
    )

    total_saved = 0
    newly_saved_events = []

    for page_idx, page in enumerate(pages):
        print(f"\n[{page_idx + 1}/{len(pages)}] Processing: {page['name']} ({page['station']}) — Batch {page.get('batch','?')}")
        posts = scraped_data_by_url.get(page["url"], [])
        print(f"  Evaluating {len(posts)} posts for events...")

        for post in posts:
            post_age_days = post.get("age_days")

            # Scrub raw text and URLs from public GitHub Actions logs via ::add-mask::
            mask_ci_text(post.get("text"))
            mask_ci_text(post.get("image_text"))
            mask_ci_text(post.get("source_url"))

            # Hard age cutoff (belt-and-suspenders — scrape_page also checks this)
            if post_age_days is not None and post_age_days > MAX_AGE_DAYS:
                print(f"  Skipped old post ({post_age_days:.1f} days).")
                continue

            combined = f"{post.get('text', '')} {post.get('image_text', '')}".strip()
            normalized_combined = re.sub(r"\s+", " ", combined).strip().casefold()
            post_prefix = normalized_combined[:100]
            mask_ci_text(post_prefix)

            # Deduplicate by text similarity (catches /posts/ vs /photo/ for same content)
            if post_prefix and post_prefix in existing_texts:
                print("  Skipped duplicate text (already in DB under different URL).")
                continue

            # OLFU filter: only accept posts for Antipolo branch or systemwide notices
            if "fatima" in page["url"].casefold() or "fatima" in page["name"].casefold():
                if not is_olfu_antipolo_post(combined):
                    print("  Skipped OLFU post: Does not mention Antipolo branch or systemwide notice.")
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

            # Check if extracted event_date is definitively in the past (> 14 days ago, e.g. commemorative photo albums)
            if event_date and category != "academic_calendar":
                date_match = re.search(r"(\d{4})-(\d{2})-(\d{2})", event_date)
                if date_match:
                    try:
                        extracted_dt = datetime(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)), tzinfo=timezone.utc)
                        if (now - extracted_dt).days > 14:
                            print(f"  Skipped past historical/commemorative post ({date_match.group(0)} is > 14 days ago).")
                            continue
                    except Exception:
                        pass

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
                    "id":                       ext_id,
                    "station":                  page["station"],
                    "source_name":              page["name"],
                    "source_url":               source_url,
                    "post_text":                post["text"][:5000],
                    "image_text":               post["image_text"][:5000] if post["image_text"] else None,
                    "category":                 category,
                    "event_name":               event_name[:500] if event_name else None,
                    "event_date":               event_date[:100] if event_date else None,
                    "event_code":               llm_res.get("event_code"),
                    "is_cancellation":          bool(llm_res.get("is_cancellation")),
                    "cancellation_target_code": llm_res.get("cancellation_target_code"),
                    "scraped_at":               now.isoformat(),
                    "post_date":                post_date.isoformat(),
                }).execute()

                existing_urls.add(source_url)
                if post_prefix:
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


if __name__ == "__main__":
    # Usage: python pipeline.py [batch]
    # batch: A, B, C, D, or all (default: all)
    batch_arg = "all"
    if len(sys.argv) > 1:
        batch_arg = sys.argv[1].strip()

    run_pipeline(batch=batch_arg)
