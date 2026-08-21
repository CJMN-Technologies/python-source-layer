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

# ---------------------------------------------------------------------------
# Tiered Scraping Modes ($1/day budget = ~200 posts/day)
#
# Each mode controls TWO knobs:
#   1. max_age_days  — how far back to look for posts
#   2. results_limit — how many posts to scrape per page (by tier)
#
# Tiers (defined per page in pages.json):
#   "lgu"   = High-spam LGU pages (Manila, QC, Pasig, Marikina, etc.)
#   "major" = Active university/student council pages
#   "quiet" = Low-activity college pages
# ---------------------------------------------------------------------------
SCRAPE_MODE_CONFIGS = {
    "aggressive": {
        "max_age_days": 1.0,      # 24 hours
        "limits": {"lgu": 6, "major": 3, "quiet": 2},  # ~100 posts
    },
    "medium": {
        "max_age_days": 0.333,    # 8 hours
        "limits": {"lgu": 3, "major": 2, "quiet": 1},  # ~57 posts
    },
    "light": {
        "max_age_days": 0.25,     # 6 hours
        "limits": {"lgu": 2, "major": 1, "quiet": 1},  # ~36 posts
    },
}

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



def run_pipeline(batch: str = "all", mode: str = "medium"):
    mode = mode.lower()
    if mode not in SCRAPE_MODE_CONFIGS:
        print(f"Warning: Unknown scrape mode '{mode}', defaulting to 'medium'.")
        mode = "medium"

    config = SCRAPE_MODE_CONFIGS[mode]
    max_age_days = config["max_age_days"]
    tier_limits = config["limits"]

    mode_labels = {"aggressive": "🔴 AGGRESSIVE", "medium": "🟡 MEDIUM", "light": "🟢 LIGHT"}
    print(f"=== LRT-2 Scraper Pipeline Starting [Batch: {batch.upper()}] ===")
    print(f"Scrape Mode: {mode_labels.get(mode, mode.upper())}")
    print(f"Time (PHT): {datetime.now(timezone(timedelta(hours=8)))}")
    print(f"Time (UTC): {datetime.now(timezone.utc)}")
    print(f"Max post age: {max_age_days} days ({max_age_days * 24:.0f} hours)")
    print(f"Post limits → LGU: {tier_limits['lgu']}, Major: {tier_limits['major']}, Quiet: {tier_limits['quiet']}")

    # Load existing events for 3-Layer bulletproof deduplication
    existing_urls = set()
    existing_texts = set()
    existing_event_keys = set()
    try:
        res = (
            supabase.schema("external")
            .table("academic_lgu_events")
            .select("source_url, post_text, image_text, source_name, event_name, event_date")
            .execute()
        )
        rows = res.data if hasattr(res, "data") else res
        if rows:
            for row in rows:
                u = row.get("source_url")
                if u:
                    clean_u = u.split("?")[0].strip().lower()
                    existing_urls.add(clean_u)
                    existing_urls.add(u.strip().lower())

                combined_db = re.sub(r"\s+", " ", f"{row.get('post_text') or ''} {row.get('image_text') or ''}").strip().casefold()
                if combined_db:
                    existing_texts.add(combined_db[:100])

                src = (row.get("source_name") or "").strip().lower()
                ev = (row.get("event_name") or "").strip().lower()
                dt = (row.get("event_date") or "").strip().lower()
                if src and ev and ev != "n/a":
                    existing_event_keys.add((src, ev, dt))

        print(f"Loaded {len(existing_urls)} URLs and {len(existing_event_keys)} unique events from database.")
    except Exception as e:
        print(f"Warning: Could not fetch existing data from database: {e}")

    pages = load_pages(batch)
    print(f"Pages to scrape in batch '{batch.upper()}': {len(pages)}")

    # Inject dynamic results_limit per page based on tier + mode
    for p in pages:
        tier = p.get("tier", "quiet")
        p["results_limit"] = tier_limits.get(tier, tier_limits.get("quiet", 1))

    total_expected = sum(p["results_limit"] for p in pages)
    print(f"Target posts to scrape: {total_expected} (~${total_expected * 0.005:.2f} est. cost)")

    # Apify Actor run with tiered per-page limits
    scraped_data_by_url = scrape_pages_batch(
        pages=pages,
        existing_urls=existing_urls,
        existing_texts=existing_texts,
        max_age_days=max_age_days,
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

            # Hard age cutoff based on scrape mode
            if post_age_days is not None and post_age_days > max_age_days:
                print(f"  Skipped old post ({post_age_days:.1f} days > {max_age_days}d limit).")
                continue

            source_url = post.get("source_url", "")
            clean_source_url = source_url.split("?")[0].strip().lower() if source_url else ""

            # Layer 1: Check URL deduplication
            if (clean_source_url and clean_source_url in existing_urls) or (source_url.strip().lower() in existing_urls):
                print(f"  Skipped duplicate URL: {source_url} (Already in Supabase).")
                continue

            combined = f"{post.get('text', '')} {post.get('image_text', '')}".strip()
            normalized_combined = re.sub(r"\s+", " ", combined).strip().casefold()
            post_prefix = normalized_combined[:100]
            mask_ci_text(post_prefix)

            # Layer 2: Deduplicate by text similarity
            if post_prefix and post_prefix in existing_texts:
                print("  Skipped duplicate text (already in DB under different URL).")
                continue

            # OLFU filter: only accept posts for Antipolo branch or systemwide notices
            if "fatima" in page["url"].casefold() or "fatima" in page["name"].casefold():
                if not is_olfu_antipolo_post(combined):
                    print("  Skipped OLFU post: Does not mention Antipolo branch or systemwide notice.")
                    continue

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

            # Layer 3: Semantic Event Deduplication (Prevents reminder posts from duplicating)
            src_key = page["name"].strip().lower()
            ev_key = (event_name or "").strip().lower()
            dt_key = (event_date or "").strip().lower()
            event_tuple = (src_key, ev_key, dt_key)

            if ev_key and ev_key != "n/a" and event_tuple in existing_event_keys:
                print(f"  Skipped duplicate event: [{page['name']}] '{event_name}' on '{event_date}' (Already in Supabase).")
                continue

            now = datetime.now(timezone.utc)

            # Check if extracted event_date is definitively in the past (> 14 days ago)
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
                post_date = now

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

                existing_urls.add(clean_source_url)
                existing_urls.add(source_url.strip().lower())
                if post_prefix:
                    existing_texts.add(post_prefix)
                if ev_key and ev_key != "n/a":
                    existing_event_keys.add(event_tuple)

                total_saved += 1
                newly_saved_events.append({
                    "source_name": page["name"],
                    "station":     page.get("station", "N/A"),
                    "batch":       page.get("batch", batch),
                    "category":    category,
                    "event_name":  event_name or "N/A",
                    "event_date":  event_date or "N/A",
                    "scraped_at":  now.isoformat(),
                    "url":         source_url
                })
                print(f"  >> Saved: [{category}] {event_name or post['text'][:60]}")
            except Exception as e:
                print(f"  Failed to save post: {e}")

    print(f"\n=== Done! {total_saved} new posts saved to Supabase ===")

    if newly_saved_events:
        print(f"Sending email alert for {len(newly_saved_events)} new events (Batch: {batch})...")
        send_pipeline_alert(newly_saved_events, batch=batch)


if __name__ == "__main__":
    # Usage: python pipeline.py [batch] [--mode aggressive|medium|light]
    # batch: Eastbound, Westbound, or all (default: all)
    # mode:  aggressive (4AM, 24hrs), medium (11AM, 8hrs), light (4PM, 6hrs)
    batch_arg = "all"
    mode_arg = "medium"

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--mode" and i + 1 < len(args):
            mode_arg = args[i + 1].strip()
            i += 2
        else:
            batch_arg = args[i].strip()
            i += 1

    run_pipeline(batch=batch_arg, mode=mode_arg)
