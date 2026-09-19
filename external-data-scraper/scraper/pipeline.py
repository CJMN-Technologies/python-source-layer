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

# ---------------------------------------------------------------------------
# Retrospective Photo Recap Guardrail
#
# When a Facebook page posts a photo album / celebratory recap AFTER an event
# already took place, the LLM may extract the past event date, causing the
# pipeline to create retroactive disruption records in events_consolidated.
# These phrases indicate the post is describing a completed past event, not
# issuing a forward-looking disruption notice.
# ---------------------------------------------------------------------------
_RETROSPECTIVE_PHRASES = re.compile(
    r"("
    r"playing\s+it\s+back"
    r"|katatapos\s+lang"
    r"|after\s+the\s+(?:spectacular|opening|ceremony|game|match)"
    r"|officially\s+commenced"
    r"|came\s+together\s+for\s+an\s+opening"
    r"|naging\s+matagumpay"
    r"|came\s+together"
    r"|held\s+(last|on)\s+(september|august|july|june|january|february|march|april|may|october|november|december|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    r"|photo\s+(highlight|album|recap|documentation)"
    r"|event\s+recap"
    r"|look\s+back"
    r"|successfully\s+held"
    r"|isang\s+matagumpay"
    r"|naganap\s+noong"
    r"|nagdaos\s+ng"
    r"|naganap\s+kahapon"
    r"|naging\s+makulay"
    r"|nagtapos\s+na\s+ang"
    r"|natapos\s+na"
    r"|victory\s+over"
    r"|defeated"
    r"|won\s+against"
    r"|edged\s+out"
    r"|loss\s+to"
    r"|final\s+score"
    r"|thank\s+you\s+to\s+our\s+partner"
    r"|couldn'?t\s+have\s+done\s+it\s+without"
    r"|partner\s+companies"
    r"|sponsors?\s+and\s+partners?"
    r"|one\s+to\s+remember"
    r"|for\s+helping\s+make\s+the"
    r"|on\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2},?\s+20\d{2},?\s+the"
    r")",
    flags=re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Institutional Cluster Deduplication
#
# Maps Facebook page display names to a canonical institution cluster key so
# that announcements from a university administration page and its student
# council are treated as the same source for CLASS_SUSPENSION,
# ONLINE_CLASS_SHIFT, and TRANSPORT_STRIKE deduplication. Only the first
# announcement from a cluster+date+code combination is ingested.
# ---------------------------------------------------------------------------
_INSTITUTION_CLUSTER_MAP: list[tuple[re.Pattern, str]] = [
    (re.compile(r"far\s+eastern\s+university|\bfeu\b", re.I), "feu"),
    (re.compile(r"university\s+of\s+santo\s+tomas|\bust\b|varsitarian|thomasian", re.I), "ust"),
    (re.compile(r"university\s+of\s+the\s+east(?!\s+student)|\bue\s+manila|\bue\s+caloocan", re.I), "ue"),
    (re.compile(r"university\s+of\s+the\s+east\s+student|ue\s+(student|usc)", re.I), "ue"),
    (re.compile(r"university\s+of\s+the\s+philippines\s+diliman|\bup\s+diliman|\bupd\b", re.I), "up"),
    (re.compile(r"up\s+diliman\s+university\s+student|\busc\s+up\b", re.I), "up"),
    (re.compile(r"san\s+beda\s+university|san\s+beda\s+student", re.I), "sbu"),
    (re.compile(r"ateneo\s+de\s+manila|ateneo\s+sanggunian", re.I), "ateneo"),
    (re.compile(r"technological\s+institute\s+of\s+the\s+philippines|\btip\s+(cubao|manila|qc|quezon)", re.I), "tip"),
    (re.compile(r"stella\s+maris\s+college", re.I), "stella_maris"),
    (re.compile(r"st\.?\s*paul\s+university\s+quezon\s+city|spuqc", re.I), "spuqc"),
    (re.compile(r"world\s+citi\s+colleges|\bwcc\b", re.I), "wcc"),
    (re.compile(r"uerm|university\s+of\s+the\s+east\s+ramon\s+magsaysay", re.I), "uerm"),
    (re.compile(r"pup\s+|polytechnic\s+university\s+of\s+the\s+philippines", re.I), "pup"),
    (re.compile(r"our\s+lady\s+of\s+fatima|\bolfu\b", re.I), "olfu"),
]
# Transit disruption codes eligible for institutional cluster deduplication
_CLUSTER_DEDUP_CODES = frozenset({"CLASS_SUSPENSION", "ONLINE_CLASS_SHIFT", "TRANSPORT_STRIKE"})


def _get_institution_cluster(page_name: str) -> str | None:
    """Return a canonical institution cluster key for a page name, or None."""
    for pattern, cluster in _INSTITUTION_CLUSTER_MAP:
        if pattern.search(page_name):
            return cluster
    return None

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
# Tiered Scraping Modes  (Budget: ~$1.29/day = ~$40/month)
#
# Apify billing is "Pay per event" at $0.005/result (confirmed from dashboard).
# Each mode controls TWO knobs:
#   1. max_age_days  — how far back to look for posts
#   2. results_limit — how many posts to scrape per page (by tier)
#
# Tiers (defined per page in pages.json):
#   "lgu"   = High-spam LGU pages (7 pages)  — Manila, QC, Pasig, Marikina, etc.
#   "major" = Active university/SC pages (14 pages) — UST, UE, Ateneo, UP, etc.
#   "quiet" = Low-activity college pages (8 pages) — Stella Maris, TIP, WCC, etc.
#
# Schedule (PHT) → Mode:
#   4:00 AM (20:00 UTC) → strong   — Full 24h sweep, max caps for burst-day coverage
#   11:00 AM (03:00 UTC) → medium  — Mid-day surge catcher, 8h window
#   4:00 PM (08:00 UTC) → light    — Afternoon watchdog, 4h window
#
# Cost breakdown per day:
#   strong : 7×8 + 14×5 + 8×3 = 150 posts × $0.005 = ~$0.75
#   medium : 7×4 + 14×2 + 8×2 = 72  posts × $0.005 = ~$0.36
#   light  : 7×2 + 14×1 + 8×1 = 36  posts × $0.005 = ~$0.18
#   Total  : ~258 posts/day → ~$1.29/day → ~$40/month
# ---------------------------------------------------------------------------
SCRAPE_MODE_CONFIGS = {
    # -----------------------------------------------------------------------
    # STRONG — 4:00 AM PHT (20:00 UTC)
    # Primary daily sweep: 24-hour window, highest caps.
    # Sized to handle typhoon/semester-start burst days on LGU and major pages.
    # -----------------------------------------------------------------------
    "strong": {
        "max_age_days": 1.0,      # 24 hours
        "limits": {"lgu": 8, "major": 5, "quiet": 3},  # ~150 posts, ~$0.75
    },
    # -----------------------------------------------------------------------
    # MEDIUM — 11:00 AM PHT (03:00 UTC)
    # Mid-day surge catcher: 8-hour window (3 AM–11 AM PHT).
    # Catches morning class-suspension posts + anything the 4 AM cap missed.
    # -----------------------------------------------------------------------
    "medium": {
        "max_age_days": 0.333,    # 8 hours
        "limits": {"lgu": 4, "major": 2, "quiet": 2},  # ~72 posts, ~$0.36
    },
    # -----------------------------------------------------------------------
    # LIGHT — 4:00 PM PHT (08:00 UTC)
    # Afternoon watchdog: 4-hour window (12 PM–4 PM PHT).
    # Catches late LGU advisories and afternoon road closure notices.
    # -----------------------------------------------------------------------
    "light": {
        "max_age_days": 0.167,    # 4 hours
        "limits": {"lgu": 2, "major": 1, "quiet": 1},  # ~36 posts, ~$0.18
    },
    # -----------------------------------------------------------------------
    # AGGRESSIVE — backward-compatibility alias for "strong"
    # Use "strong" in new code. This alias ensures old manual triggers still work.
    # -----------------------------------------------------------------------
    "aggressive": {
        "max_age_days": 1.0,
        "limits": {"lgu": 8, "major": 5, "quiet": 3},  # same as strong
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
            .execute()
        )
        rows = res.data if hasattr(res, "data") else res

        pattern = re.compile(rf"^{re.escape(base)}_(\d+)$")
        nums = []
        for r in rows:
            if isinstance(r, dict) and "id" in r:
                m = pattern.match(r["id"])
                if m:
                    nums.append(int(m.group(1)))
        if nums:
            return f"{base}_{(max(nums) + 1):04d}"
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


def is_ue_manila_post(text: str) -> bool:
    """
    Designated UE Campus Filter:
    The University of the East official Facebook page (UniversityoftheEastUE)
    posts announcements for both UE Manila (LRT-2 Recto station) and UE Caloocan
    (Samson Road, Caloocan City - outside LRT-2 transit corridor).

    Rules:
    1. Systemwide announcements ('all campuses', 'both campuses', 'manila and caloocan',
       'caloocan and manila', 'entire university', 'across all campuses', 'ue community'):
       - Check if Manila is specifically excepted (e.g. 'except UE Manila', 'maliban sa Manila').
       - If Manila is NOT excepted, ACCEPT (affects LRT-2 Recto).
       - If Manila IS excepted, REJECT.
    2. Explicit Manila mentions ('ue manila', 'manila campus', 'gastambide', 'c.m. recto', 'recto campus'):
       - ACCEPT.
    3. Explicit Caloocan mentions ('ue caloocan', 'caloocan campus', 'samson road', 'caloocan open field',
       'ue-caloocan') without mentioning Manila or systemwide:
       - REJECT (outside LRT-2 transit corridor).
    4. Default:
       - If neither campus is explicitly distinguished, default to True (as UE Manila is the primary
         institutional seat and historical default for LRT-2 Recto).
    """
    t = text.casefold()

    is_systemwide = any(k in t for k in [
        "all campuses",
        "all ue campuses",
        "both campuses",
        "both ue campuses",
        "manila and caloocan",
        "caloocan and manila",
        "entire university",
        "across all campuses",
        "lahat ng campus",
        "ue community",
    ])

    if is_systemwide:
        manila_excepted = bool(re.search(
            r"(?:except|excluding|maliban\s+sa|bukod\s+sa)\s+(?:(?:for|sa)\s+)?(?:ue\s+)?manila",
            t
        ))
        if manila_excepted:
            return False
        return True

    # If explicitly targeting Caloocan and NOT mentioning Manila
    mentions_caloocan = any(k in t for k in [
        "caloocan", "ue caloocan", "cal campus", "ue-cal",
        "ue-caloocan", "samson rd", "samson road"
    ])
    mentions_manila = any(k in t for k in [
        "manila", "ue manila", "gastambide", "c.m. recto", "cm recto", "recto"
    ])

    if mentions_caloocan and not mentions_manila:
        return False

    return True


def is_off_corridor_venue(text: str) -> bool:
    """
    Check whether an announcement describes an event physically held at an
    off-corridor mega venue or stadium outside the LRT-2 transit corridor:
      - SM Mall of Asia Arena (Pasay City — served by LRT-1/MRT-3)
      - SMX Convention Center (Pasay City)
      - Philippine International Convention Center (PICC, Pasay City)
      - San Andres Sports Complex (Malate, Manila District 5)
      - Philippine Arena (Bocaue, Bulacan)
      - World Trade Center (Pasay City)

    If the text explicitly mentions an LRT-2 station or the LRT-2 transit line,
    it is not treated as strictly off-corridor.
    """
    t = text.casefold()
    off_corridor_patterns = [
        r"mall\s+of\s+asia\s+arena",
        r"\bmoa\s+arena\b",
        r"smx\s+convention",
        r"philippine\s+international\s+convention\s+center",
        r"\bpicc\b",
        r"world\s+trade\s+center",
        r"san\s+andres\s+sports\s+complex",
        r"philippine\s+arena",
        r"\bbocaue\b",
    ]
    if any(re.search(pat, t) for pat in off_corridor_patterns):
        corridor_stations = [
            "recto", "legarda", "pureza", "v. mapa", "v.mapa", "j. ruiz", "j.ruiz",
            "gilmore", "betty go", "cubao", "araneta", "anonas", "katipunan",
            "santolan", "marikina", "antipolo", "lrt-2", "lrt 2", "line 2"
        ]
        if not any(k in t for k in corridor_stations):
            return True
    return False


def is_micro_venue_or_administrative(text: str) -> bool:
    """
    Check whether an event announcement is a micro-venue recital, ticket selling booth,
    online admissions form deadline, or civic theme month that must not trigger MAJOR_ARENA_EVENT.
    """
    t = text.casefold()
    patterns = [
        r"ticket\s+(?:selling|booth|reservation|availability)",
        r"dance\s+studio",
        r"covered\s+court",
        r"children'?s\s+choir",
        r"foundation\s+anniversary",
        r"yellow\s+day",
        r"upcat\s+application\s+deadline",
        r"submission\s+of\s+forms",
        r"demobiliz",
        r"foro\s+de\s+intramuros",
        r"tourism\s+expo",
        r"heritage\s+spaces",
        r"intramuros\s+administration",
    ]
    return any(re.search(pat, t) for pat in patterns)



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

    # Always preserve academic_calendar for academic pages (LGUs publish holiday lists/bulletins, not academic calendars)
    if llm_category == "academic_calendar":
        if page.get("source_type") == "lgu":
            return "lgu"
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

    mode_labels = {"strong": "🔴 STRONG", "aggressive": "🔴 STRONG", "medium": "🟡 MEDIUM", "light": "🟢 LIGHT"}
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
    existing_code_keys = set()
    try:
        res = (
            supabase.schema("external")
            .table("academic_lgu_events")
            .select("source_url, post_text, image_text, source_name, event_name, event_date, event_code")
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
                    clean_db_prefix = re.sub(
                        r"^(?:(?:edited\s+)?advisory\s*[:|—\-]|just\s+in\s*[:|!—\-]|update\s*[:|—\-]|panoorin\s*[:|—\-]|\#walangpasok\s*[:|—\-]|advisory\b)\s*",
                        "",
                        combined_db,
                        flags=re.IGNORECASE,
                    ).strip()
                    existing_texts.add(clean_db_prefix[:100])
                    existing_texts.add(combined_db[:100])

                src = (row.get("source_name") or "").strip().lower()
                ev = (row.get("event_name") or "").strip().lower()
                dt = (row.get("event_date") or "").strip().lower()
                cd = (row.get("event_code") or "").strip().upper()
                if src and ev and ev != "n/a":
                    existing_event_keys.add((src, ev, dt))
                if src and dt and cd in ("MAJOR_ARENA_EVENT", "CLASS_SUSPENSION", "ONLINE_CLASS_SHIFT"):
                    existing_code_keys.add((src, dt, cd))
                # Cluster-aware deduplication: seed cluster key so student council
                # announcements skip when the parent admin page already filed the same code+date
                cluster = _get_institution_cluster(row.get("source_name") or "")
                if cluster and dt and cd in _CLUSTER_DEDUP_CODES:
                    existing_code_keys.add((f"__cluster__{cluster}", dt, cd))

        print(f"Loaded {len(existing_urls)} URLs, {len(existing_event_keys)} unique events, and {len(existing_code_keys)} code keys from database.")
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
            clean_combined = re.sub(
                r"^(?:(?:edited\s+)?advisory\s*[:|—\-]|just\s+in\s*[:|!—\-]|update\s*[:|—\-]|panoorin\s*[:|—\-]|\#walangpasok\s*[:|—\-]|advisory\b)\s*",
                "",
                normalized_combined,
                flags=re.IGNORECASE,
            ).strip()
            post_prefix = clean_combined[:100] if clean_combined else normalized_combined[:100]
            mask_ci_text(post_prefix)

            # Layer 2: Deduplicate by text similarity (raw or normalized prefix)
            if (post_prefix and post_prefix in existing_texts) or (normalized_combined[:100] in existing_texts):
                print("  Skipped duplicate text (already in DB under different URL).")
                continue

            # OLFU filter: only accept posts for Antipolo branch or systemwide notices
            if "fatima" in page["url"].casefold() or "fatima" in page["name"].casefold():
                if not is_olfu_antipolo_post(combined):
                    print("  Skipped OLFU post: Does not mention Antipolo branch or systemwide notice.")
                    continue

            # UE filter: only accept posts for Manila branch or systemwide notices
            if "universityoftheeast" in page["url"].casefold() or "university of the east" in page["name"].casefold():
                if not is_ue_manila_post(combined):
                    print("  Skipped UE post: Specific to Caloocan campus (outside LRT-2 corridor).")
                    continue

            # Off-corridor venue filter: skip posts explicitly held at off-corridor arenas (MOA Arena, PICC, San Andres, etc.)
            if is_off_corridor_venue(combined):
                print("  Skipped off-corridor venue post (SM MOA Arena, PICC, or San Andres outside LRT-2 corridor).")
                continue

            # PRE-FILTER with keywords (case-insensitive via classify_post using .casefold())
            pre_category = classify_post(combined, source_type=page.get("source_type"))
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

            # Layer 3: Semantic Event Deduplication (Prevents reminder posts & duplicate sub-events from duplicating)
            src_key = page["name"].strip().lower()
            ev_key = (event_name or "").strip().lower()
            dt_key = (event_date or "").strip().lower()
            event_code_val = (llm_res.get("event_code") or "").strip().upper()
            event_tuple = (src_key, ev_key, dt_key)
            code_tuple = (src_key, dt_key, event_code_val)

            if ev_key and ev_key != "n/a" and event_tuple in existing_event_keys:
                print(f"  Skipped duplicate event: [{page['name']}] '{event_name}' on '{event_date}' (Already in Supabase).")
                continue

            if dt_key and event_code_val in ("MAJOR_ARENA_EVENT", "CLASS_SUSPENSION", "ONLINE_CLASS_SHIFT") and code_tuple in existing_code_keys:
                print(f"  Skipped duplicate {event_code_val} from [{page['name']}] on '{event_date}' (Already recorded in batch).")
                continue

            # Cluster-aware institutional deduplication
            # If the same event_code + date has already been filed by another page
            # from the same institution cluster (e.g. university admin page before
            # the student council), skip this page to avoid double-counting.
            cluster_key = _get_institution_cluster(page["name"])
            if cluster_key and dt_key and event_code_val in _CLUSTER_DEDUP_CODES:
                cluster_code_tuple = (f"__cluster__{cluster_key}", dt_key, event_code_val)
                if cluster_code_tuple in existing_code_keys:
                    print(f"  Skipped cluster duplicate {event_code_val} from [{page['name']}] (cluster: {cluster_key}) on '{event_date}' — another page from the same institution already filed this code.")
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

                        # Micro-venue & ticket booth suppression (MAJOR_ARENA_EVENT only)
                        if event_code_val == "MAJOR_ARENA_EVENT" and is_micro_venue_or_administrative(combined):
                            print(f"  Skipped micro-venue/ticket booth post ({date_match.group(0)}) from MAJOR_ARENA_EVENT.")
                            continue

                        # Retrospective Photo Recap Guardrail (MAJOR_ARENA_EVENT only)
                        # If the event_date is on/before the post_date AND contains recap phrasing,
                        # or is >= 1 day in the past, this is a recap posted after the event.
                        if event_code_val == "MAJOR_ARENA_EVENT" and post_age_days is not None:
                            computed_post_date = now - timedelta(days=post_age_days)
                            days_in_past = (computed_post_date.date() - extracted_dt.date()).days
                            is_retrospective = days_in_past >= 1 or bool(_RETROSPECTIVE_PHRASES.search(combined))
                            if is_retrospective:
                                print(f"  Skipped retrospective recap post: event_date={date_match.group(0)} was {days_in_past}d before post_date. Not a forward disruption notice.")
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
                    "category":                 "academic" if category in ("acad", "academic", "academic_calendar") else ("lgu" if category == "lgu" else category),
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
                existing_texts.add(normalized_combined[:100])
                if ev_key and ev_key != "n/a":
                    existing_event_keys.add(event_tuple)
                if dt_key and event_code_val in ("MAJOR_ARENA_EVENT", "CLASS_SUSPENSION", "ONLINE_CLASS_SHIFT"):
                    existing_code_keys.add(code_tuple)
                # Seed cluster-aware dedup key so subsequent pages in this batch
                # from the same institution cluster skip the same code+date
                cluster_key = _get_institution_cluster(page["name"])
                if cluster_key and dt_key and event_code_val in _CLUSTER_DEDUP_CODES:
                    existing_code_keys.add((f"__cluster__{cluster_key}", dt_key, event_code_val))

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
    # Usage: python pipeline.py [batch] [--mode strong|medium|light]
    # batch: Eastbound, Westbound, or all (default: all)
    # mode:  strong (4AM PHT, 24h, ~$0.75), medium (11AM PHT, 8h, ~$0.36), light (4PM PHT, 4h, ~$0.18)
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
