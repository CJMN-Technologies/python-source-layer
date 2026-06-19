import json
import os
import sys
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from auth import get_cookies
from fb_scraper import scrape_page
from keywords import classify_post

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

DEFAULT_MAX_AGE_DAYS = 7.0


def _next_ext_id_for_category(category: str) -> str:
    """Return next EVNTS-<CATEGORY>-<NNNN> id by querying Supabase for the max existing id.

    Category names are mapped to shorter identifiers for the event id:
    - academic -> ACAD
    - lgu -> LGU
    Falls back to the uppercased category name if no mapping exists.
    """
    category_key = category.lower()
    if category_key == "academic":
        category_code = "ACAD"
    elif category_key == "lgu":
        category_code = "LGU"
    else:
        category_code = category.upper()

    base = f"EVNTS-{category_code}"
    try:
        res = supabase.schema("external").table("academic_lgu_events").select("id").ilike("id", f"{base}-%").order("id", desc=True).limit(1).execute()
        rows = None
        if isinstance(res, dict) and res.get("data") is not None:
            rows = res.get("data")
        elif hasattr(res, "data"):
            rows = getattr(res, "data")
        elif isinstance(res, (list, tuple)) and len(res) > 0:
            rows = res[0]

        if rows:
            # rows is list of rows; take first
            first = rows[0] if isinstance(rows, list) else rows
            last_id = first.get("id") if isinstance(first, dict) else None
            if last_id:
                try:
                    last_num = int(last_id.rsplit("-", 1)[-1])
                    return f"{base}-{(last_num + 1):04d}"
                except Exception:
                    pass
    except Exception:
        pass
    return f"{base}-0001"

def load_pages():
    with open(os.path.join(os.path.dirname(__file__), "pages.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def run_pipeline(priority: str = "all", max_age_days: float | None = DEFAULT_MAX_AGE_DAYS):
    print(f"=== LRT-2 Scraper Pipeline Starting [{priority.upper()}] ===")
    print(f"Time: {datetime.now(timezone.utc)}")
    print(f"Max age: {max_age_days} days")

    all_pages = load_pages()
    cookies = get_cookies()
    total_saved = 0

    # filter pages by priority
    if priority == "all":
        pages = all_pages
    else:
        pages = [p for p in all_pages if p["priority"] == priority]

    print(f"Pages to scrape: {len(pages)}")

    for page in pages:
        print(f"\nScraping: {page['name']} ({page['station']})")

        posts = scrape_page(page["url"], cookies)

        for post in posts:  
            post_age_days = post.get("age_days")
            if post_age_days is not None and max_age_days is not None and post_age_days > max_age_days:
                print(f"  Skipped old post ({post_age_days:.1f} days): {post['text'][:80]}...")
                continue

            combined = f"{post['text']} {post.get('image_text', '')}"
            category = classify_post(combined)

            if category is None:
                continue

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
                total_saved += 1
            except Exception as e:
                print(f"  Failed to save post: {e}")

    print(f"\n=== Done! {total_saved} posts saved to Supabase ===")

if __name__ == "__main__":
    max_age = DEFAULT_MAX_AGE_DAYS
    if len(sys.argv) > 1:
        try:
            max_age = float(sys.argv[1])
        except ValueError:
            print(f"Invalid max-age argument, using {DEFAULT_MAX_AGE_DAYS:g} days")
    run_pipeline(max_age_days=max_age)
