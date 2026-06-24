import json
import os
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from auth import get_all_cookie_profiles
from fb_scraper import scrape_page
from keywords import classify_post
from llm_classifier import classify_post_llm
from email_notifier import send_pipeline_alert

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

DEFAULT_MAX_AGE_DAYS = 7.0


def _next_ext_id_for_category(category: str) -> str:
    """Return next external_<category>_<NNNN> id by querying Supabase for the max existing id.
    """
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
        res = supabase.schema("external").table("academic_lgu_events").select("id").ilike("id", f"{base}_%").order("id", desc=True).limit(1).execute()
        rows = res.data if hasattr(res, "data") else res

        if rows:
            # rows is list of rows; take first
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

def load_pages():
    with open(os.path.join(os.path.dirname(__file__), "pages.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def run_pipeline(priority: str = "all", max_age_days: float | None = DEFAULT_MAX_AGE_DAYS):
    print(f"=== LRT-2 Scraper Pipeline Starting [{priority.upper()}] ===")
    print(f"Time: {datetime.now(timezone.utc)}")
    print(f"Max age: {max_age_days} days")

    existing_urls = set()
    existing_texts = set()
    try:
        res = supabase.schema("external").table("academic_lgu_events").select("source_url, post_text, image_text").execute()
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

    all_pages = load_pages()
    cookie_profiles = get_all_cookie_profiles()
    cookies = cookie_profiles[0] if cookie_profiles else []
    total_saved = 0
    newly_saved_events = []

    # filter pages by priority
    if priority == "all":
        pages = all_pages
    else:
        pages = [p for p in all_pages if p["priority"] == priority]

    print(f"Pages to scrape: {len(pages)}")

    for page in pages:
        print(f"\nScraping: {page['name']} ({page['station']})")

        posts = scrape_page(page["url"], cookies, existing_urls)

        for post in posts:  
            post_age_days = post.get("age_days")
            if post_age_days is not None and max_age_days is not None and post_age_days > max_age_days:
                print(f"  Skipped old post ({post_age_days:.1f} days): {post['text'][:80]}...")
                continue

            combined = f"{post.get('text', '')} {post.get('image_text', '')}".strip()
            
            # Deduplicate by text similarity to catch /posts/ vs /photo/ differences
            post_prefix = combined[:100]
            if post_prefix and post_prefix in existing_texts:
                print("  Skipped duplicate text (already in DB under different URL).")
                continue
            
            if "fatima" in page["url"].lower() or "fatima" in page["name"].lower():
                combined_lower = combined.lower()
                if "antipolo" not in combined_lower and "𝗔𝗻𝘁𝗶𝗽𝗼𝗹𝗼" not in combined:
                    print(f"  Skipped OLFU post: Does not mention Antipolo campus.")
                    continue

            # PRE-FILTER with keywords
            pre_category = classify_post(combined)
            if pre_category is None:
                continue
                
            # SMART EXTRACTION with LLM
            llm_res = classify_post_llm(combined)
            category = llm_res.get("category")
            
            if category is None:
                print("  LLM rejected post (Not a valid event/calendar).")
                continue

            # Override category based on page name
            page_name_lower = page["name"].lower()
            if "pagasa" in page_name_lower:
                category = "pagasa"
            elif "government" in page_name_lower or "pio" in page_name_lower:
                category = "lgu"
            else:
                category = "acad"
                
            event_name = llm_res.get("event_name")
            event_date = llm_res.get("event_date")

            now = datetime.now(timezone.utc)
            if post_age_days is not None:
                post_date = now - timedelta(days=post_age_days)
            else:
                post_date = now

            try:
                # generate a stable external trigger id per category
                ext_id = _next_ext_id_for_category(category)

                supabase.schema("external").table("academic_lgu_events").upsert({
                    "id":            ext_id,
                    "station":       page["station"],
                    "source_name":   page["name"],
                    "source_url":    post.get("source_url", page["url"]),
                    "post_text":     post["text"][:2000],
                    "image_text":    post["image_text"][:2000] if post["image_text"] else None,
                    "category":      category,
                    "scraped_at":    now.isoformat(),
                    "post_date":     post_date.isoformat(),
                }).execute()
                existing_texts.add(post_prefix)
                total_saved += 1
                newly_saved_events.append({
                    "source_name": page["name"],
                    "category": category,
                    "event_name": event_name or "N/A",
                    "event_date": event_date or "N/A",
                    "url": post.get("source_url", page["url"])
                })
            except Exception as e:
                print(f"  Failed to save post: {e}")

    print(f"\n=== Done! {total_saved} posts saved to Supabase ===")
    
    if newly_saved_events:
        print("Sending email alert for new events...")
        send_pipeline_alert(newly_saved_events)

if __name__ == "__main__":
    max_age = DEFAULT_MAX_AGE_DAYS
    if len(sys.argv) > 1:
        try:
            max_age = float(sys.argv[1])
        except ValueError:
            print(f"Invalid max-age argument, using {DEFAULT_MAX_AGE_DAYS:g} days")
    run_pipeline(max_age_days=max_age)
