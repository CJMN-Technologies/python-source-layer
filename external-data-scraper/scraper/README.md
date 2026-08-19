# Facebook Event Scraper

This folder scrapes selected public Facebook pages for academic and LGU disruption signals near LRT-2 stations. It classifies relevant posts using a two-stage pipeline (keyword pre-filter → Gemini LLM extraction), saves them to Supabase, and sends email alerts to the team.

A separate **academic calendar scraper** detects full calendar releases, extracts dates into `.xlsx` spreadsheets, and emails them as attachments.

## Purpose

The scraper looks for posts related to class suspensions, LGU advisories, weather disruptions, road closures, transport strikes, PAGASA weather bulletins, concert/arena events, and similar external events that may affect LRT-2 demand or operations.

Target table:

```text
external.academic_lgu_events
```

Post categories:

| Category | Meaning | Example Events |
| --- | --- | --- |
| `academic` | Class suspensions, resumptions, school holidays, exams, enrollment, graduation | "No classes tomorrow", "Midterm exams week" |
| `lgu` | Government advisories, road closures, transport disruptions, concert/arena events | "Tigil Pasada", "Araneta concert", "MMDA road closure" |
| `pagasa` | PAGASA weather bulletins relevant to NCR / LRT-2 catchment areas | "Orange rainfall warning over Metro Manila" |
| `academic_calendar` | A post sharing a full academic calendar document | "A.Y. 2026-2027 Academic Calendar is now available" |

## Tech Stack

| Technology | Purpose |
| --- | --- |
| Python 3.12 | Main pipeline language |
| Playwright | Opens Facebook pages in headless Chromium |
| BeautifulSoup | Parses rendered HTML for post extraction |
| Requests | Downloads image assets for OCR |
| Google Gemini 2.0 Flash (`google-genai`) | OCR (extracts text from post images) and LLM classification (categorizes posts and extracts event details) |
| Pillow + NumPy | Image loading and pre-processing before Gemini OCR |
| Pydantic | Structured output schema for LLM classification responses |
| pandas + openpyxl | Generates `.xlsx` academic calendar spreadsheets |
| Supabase Python client | Writes classified events to Supabase |
| python-dotenv | Loads local `.env` values |
| APScheduler | Optional local long-running scheduler |
| smtplib | Sends automated email alerts (Gmail SMTP with TLS) |

## Files

| File | Purpose |
| --- | --- |
| `pipeline.py` | Main events scraper pipeline — scrapes pages, classifies posts, deduplicates, and saves to Supabase. Supports batch selection (A/B/C/D). |
| `fb_scraper.py` | Core Playwright scraping engine — page loading, caption expansion, permalink extraction, post age parsing, Gemini OCR text extraction, resource blocking. |
| `auth.py` | Builds Facebook cookie profiles from environment variables. Supports multiple accounts (primary + up to 9 backups). |
| `keywords.py` | Pre-filter: classifies post text as `academic`, `lgu`, or irrelevant using keyword groups aligned to the friction weight table. Uses `.casefold()` for case-insensitive matching. |
| `llm_classifier.py` | LLM stage: sends pre-filtered post text to Gemini 2.0 Flash for structured classification with injected current reference date/year (`Today is August 13, 2026`) to prevent misdating events without explicit years to past years (e.g. 2024). Returns `category`, `event_name`, `event_date`, `event_code`, `is_cancellation`, and `cancellation_target_code` via Pydantic schema. |
| `calendar_scraper.py` | Academic calendar release detector — finds calendar posts, extracts dates via Gemini, generates Excel files per school, emails them as attachments, and upserts events to Supabase. |
| `email_notifier.py` | Email alert system — sends pipeline summary emails (new events found), cookie expiration alerts, and academic calendar attachments via Gmail SMTP. |
| `unicode_normalizer.py` | Converts decorative Unicode text (Mathematical Bold, Italic, Script, Double-Struck, Circled, Fullwidth) back to plain ASCII so keyword matching works regardless of Facebook font styling. |
| `clean_and_renumber.py` | Database maintenance utility — deduplicates and re-numbers event IDs in `external.academic_lgu_events`. |
| `debug_facebook_page.py` | Diagnostic tool — opens a Facebook page in Playwright and checks for login walls, keyword matches, and cookie validity. |
| `pages.json` | List of Facebook pages to scrape, with station mappings, batch assignments (A/B/C/D), scrape priorities, and optional `max_scrolls` overrides. |
| `processed_calendars.json` | Deduplication tracker for the calendar scraper — stores URLs of already-processed calendar posts. |
| `scheduler.py` | Local scheduler for high, medium, and low priority page scraping. |
| `Dockerfile` | Container image based on Playwright Python. |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Template for local `.env` file with all required variables. |
| `test_pipeline_logic.py` | Sandbox test — runs keyword and LLM classification against a simulated post. |
| `test_email.py` | Tests the email alert system by sending a sample notification. |
| `test_mbasic.py` | Diagnostic — tests mbasic.facebook.com scraping with Playwright. |

## Two-Stage Classification Pipeline & Truncation Resilience

```text
Facebook Post
     │
     ▼
┌──────────────────────────┐
│ 1. Keyword Pre-Filter    │  keywords.py — fast, no API cost
│    (casefold + Unicode   │  Rejects posts with zero keyword hits
│     normalization)       │  Returns: academic | lgu | None
└──────────┬───────────────┘
           │ (keyword hit)
           ▼
┌──────────────────────────┐
│ 2. In-Place DOM Expansion│  fb_scraper.py — expands 'See More' / 'Tumingin pa' inline
│    & Multi-Modal OCR     │  Zero-cost client-side DOM click (0 extra HTTP requests)
│                          │  Gemini Vision OCR extracts full memo text from infographics
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 3. Gemini LLM Extraction │  llm_classifier.py — structured output with prompt resilience
│    (Gemini 2.0 Flash)    │  Injects post title/source name context; extracts 5-8 word summary
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│ 4. Category Override     │  pipeline.py — page-type-aware
│    PAGASA page → pagasa  │  Preserves academic_calendar from LLM
│    LGU/PIO page → lgu    │  Falls back to keyword category if LLM fails
└──────────┬───────────────┘
           │
           ▼
     Save to Supabase (`external.academic_lgu_events`)
           │
           ▼
┌──────────────────────────┐
│ 5. Database Trigger      │  external.sync_academic_lgu_to_events_consolidated()
│    Classification Sync   │  Broadened regex (tense-agnostic, #WalangPasok hashtag resilience)
└──────────┬───────────────┘
           │
           ▼
     Live Event Feed (`Analytics.descriptive_live_event_feed`)
```

## Environment Variables

Create a local `.env` in this folder when running locally (see `.env.example` for a complete template):

**Required:**

```env
SUPABASE_URL=
SUPABASE_KEY=
FB_C_USER=
FB_XS=
```

**Facebook cookies (optional but recommended):**

```env
FB_DATR=
FB_FR=
FB_SB=
```

`FB_C_USER` and `FB_XS` are the most important cookies. The other Facebook cookies improve session reliability when available.

**Backup Facebook accounts** (up to 9 additional accounts for cookie rotation):

```env
FB_C_USER_1=
FB_XS_1=
FB_DATR_1=
FB_FR_1=
FB_SB_1=
```

**Gemini API keys** (supports comma-separated list and/or sequential variables):

```env
# Option 1: Comma-separated list
GEMINI_API_KEY=key_1,key_2,key_3

# Option 2: Sequential variables (loaded automatically)
# GEMINI_API_KEY_2=key_2
# GEMINI_API_KEY_3=key_3
```

Multiple keys enable automatic rotation when a key's quota is exhausted.

**Email notifications:**

```env
SENDER_EMAIL=your_gmail@gmail.com
SENDER_PASSWORD=your_gmail_app_password
RECEIVER_EMAIL=recipient1@email.com,recipient2@email.com
```

Never commit `.env` or cookie values.

## Install

```bash
pip install -r requirements.txt
playwright install chromium
```

## Run the Events Pipeline

From this folder:

```bash
python pipeline.py
```

Run specific batches:

```bash
python pipeline.py A        # Run batch A only
python pipeline.py A,B      # Run batches A and B
python pipeline.py all      # Run all batches (default)
```

The default max post age is `7` days. Posts older than 7 days are skipped before classification.

## Run the Calendar Scraper

```bash
python calendar_scraper.py
```

The calendar scraper scans university Facebook pages for academic calendar releases (A.Y. 2026-2027), extracts calendar dates via Gemini, generates an `.xlsx` file per school, emails the file to the team, and upserts the event to Supabase. Processed URLs are tracked in `processed_calendars.json` to avoid reprocessing.

## Batch System

Pages in `pages.json` are assigned to batches A, B, C, or D. This system splits scraping across two GitHub Actions runs per day to stay within the free-tier budget:

| Time (PHT) | Batches | Approx Pages |
| --- | --- | --- |
| 6:00 AM | A, B | ~14 pages |
| 3:00 PM | C, D | ~17 pages |

Each page scan takes ~50 seconds (3 scrolls), totaling ~32 minutes/day (~960 min/month — well under the 2,000-minute free tier).

## Scheduling

**Local scheduler:**

```bash
python scheduler.py
```

Schedule behavior:

| Priority | Schedule |
| --- | --- |
| High | 5:00 AM and 3:00 PM daily |
| Medium | 12:00 AM daily |
| Low | Monday, Wednesday, Friday at 9:00 AM |

The scheduler runs an initial scrape immediately on startup.

**GitHub Actions** uses the root workflows:

| Workflow | File | Schedule |
| --- | --- | --- |
| Events Pipeline | `.github/workflows/events_pipeline.yml` | 4:00 AM, 11:00 AM, and 4:00 PM PHT daily (3 time windows) |
| Calendar Scraper | `.github/workflows/calendar_scraper.yml` | Every 5 days at 8:00 AM PHT |

## How Data Is Saved

For each relevant post, the pipeline generates a sequential ID and saves:

| Field | Example | Description |
| --- | --- | --- |
| `id` | `external_acad_0001` | Auto-incremented per category (`external_acad_`, `external_lgu_`, `external_pagasa_`) |
| `station` | `Katipunan` | LRT-2 station from `pages.json` |
| `source_name` | `Ateneo de Manila University` | Facebook page display name |
| `source_url` | `https://facebook.com/.../posts/...` or `https://www.facebook.com/photo/?fbid=...` | Permalink to the original page post or photo |
| `post_text` | _(truncated to 2000 chars)_ | Caption text from the post |
| `image_text` | _(truncated to 2000 chars)_ | OCR text extracted from post images via Gemini (null if no images) |
| `category` | `academic` | Classification result (`academic`, `lgu`, `pagasa`, `academic_calendar`) |
| `scraped_at` | `2026-07-08T14:00:00Z` | UTC timestamp of when the post was scraped |
| `post_date` | `2026-07-06T14:00:00Z` | Estimated original post date (calculated from age) |

## Deduplication

The pipeline uses two deduplication strategies:

1. **URL deduplication** — skips posts whose `source_url` already exists in the database.
2. **Text similarity** — compares the first 100 characters of `post_text + image_text` against existing records. This catches the same content posted under different URL formats (e.g., `/posts/` vs `/photo/` vs `/permalink/`).

## Source URL Safety

The scraper validates each Facebook `source_url` before it can be saved to Supabase or shown in email alerts. This prevents profile/comment/share-author links from being stored as official LGU or academic announcement links.

Allowed URL formats:

- Page post links such as `https://www.facebook.com/QCGov/posts/...`
- Page post links with tracking such as `?ref=embed_page`
- Photo permalinks such as `https://www.facebook.com/photo/?fbid=...`
- Facebook share links for posts/photos such as `/share/p/...` or `/share/photo/...`

Blocked URL formats:

- Personal profile URLs such as `/people/...` and `/profile.php`
- Comment or reply URLs containing `comment_id=` or `reply_comment_id=`
- Reels, videos, and watch links because they are not reliably readable by the LLM/OCR flow
- Mismatched page-slug post URLs, for example a personal `/janlexcasas/posts/...` URL while scraping the configured `PasigPIO` page

## Authoritative Station Source Registry

Each of the 29 LRT-2 sources in `pages.json` is strictly classified by `source_type` (`academic` vs `lgu`), guaranteeing that academic institutions are never miscategorized as LGU:

| Station | Source Type | Official Page / Organization |
| --- | --- | --- |
| **Recto** | `academic` | University of the East (UE) Manila, UE Student Council, Far Eastern University (FEU) Manila, FEU Central Student Organization |
| **Recto** | `lgu` | Manila Public Information Office |
| **Legarda** | `academic` | University of Santo Tomas (UST), UST Central Student Council, San Beda University, San Beda Student Council |
| **Pureza** | `academic` | Polytechnic University of the Philippines (PUP Main), PUP Sentral na Konseho ng Mag-aaral |
| **V. Mapa** | `academic` | UERM Memorial Medical Center, UERM Medicine Student Council |
| **J. Ruiz** | `lgu` | San Juan City Government |
| **Gilmore** | `academic` | St. Paul University Quezon City, St. Paul University QC SAO |
| **Gilmore** | `lgu` | Quezon City Government |
| **Betty Go-Belmonte** | `academic` | Stella Maris College |
| **Cubao** | `academic` | Technological Institute of the Philippines (TIP Cubao) |
| **Anonas** | `academic` | World Citi Colleges (WCC) Quezon City |
| **Katipunan** | `academic` | University of the Philippines Diliman, UP Diliman USC, Ateneo de Manila University (ADMU), Ateneo Sanggunian |
| **Santolan** | `lgu` | Pasig City Public Information Office |
| **Marikina-Pasig** | `lgu` | Marikina Public Information Office, Municipality of Cainta |
| **Antipolo** | `academic` | Our Lady of Fatima University (OLFU Antipolo) |
| **Antipolo** | `lgu` | Antipolo City Government PIO |

## Special Filters

- **OLFU Antipolo Branch Filter** — Our Lady of Fatima University posts from a nationwide page covering all Philippine branches (`Valenzuela`, `Metro Manila`, `Quezon City`, `Antipolo`, `Nueva Ecija`, `Laguna`, `Pampanga`). The pipeline strictly isolates the **Antipolo** branch:
  1. If a post explicitly mentions `"antipolo"` (including multi-branch announcements listing Antipolo alongside other branches), it is **accepted**.
  2. If a post is verified systemwide (`"all campuses"`, `"all olfu campuses"`, `"systemwide"`, `"entire university"`), the pipeline parses exception clauses (`except/excluding/maliban sa [branch]`). If another branch is excepted (e.g. `All OLFU Campuses (except OLFU Quezon City)`), Antipolo is **accepted**. If Antipolo itself is excepted, it is **rejected**.
- **Anti-Detection Pacing & Startup Jitter** — To prevent Facebook account checkpoints and bot flagging from cloud data center IPs:
  1. **Startup Jitter**: Westbound runners wait 20–35 seconds before launching so concurrent matrix jobs never hit Facebook at the exact same millisecond.
  2. **Inter-Page Pacing**: The pipeline enforces a randomized 12–22 second cooldown between consecutive page scrapes.
  3. **User-Agent Pool**: Playwright randomly rotates among modern Windows and macOS desktop User-Agent strings (`Chrome 131`, `Chrome 130`, `Edge 129`).
- **Cookie Rotation & Unauthenticated Public Fallback** — When authenticated cookie profiles hit login walls or checkpoints, the pipeline automatically rotates through backup profiles. If all accounts hit challenges, the pipeline immediately falls back to **unauthenticated public mode** (`/plugins/page.php`), ensuring public civic/academic scraping continues uninterrupted without failing.
- **GitHub Actions Free Quota Optimization** — Redundant half-hourly watchdog polling is disabled; primary weather pipelines run with built-in 5x retries, keeping overall monorepo consumption at ~750 minutes/month (well within the 2,000 min/mo private repository quota).

## Email Alerts

The pipeline sends three types of email alerts:

1. **Pipeline summary** — sent after each run with a table of all newly saved events.
2. **Cookie expiration alert** — sent when one or more Facebook accounts hit a login wall.
3. **Calendar attachment** — sent when the calendar scraper finds a new academic calendar release, with the generated `.xlsx` file attached.

## Maintenance Notes

- Update `pages.json` when adding or removing source pages. Assign a batch letter (A/B/C/D).
- Update `keywords.py` when classification rules change (keyword groups are aligned to `external.friction_weight`).
- Update the LLM prompt in `llm_classifier.py` if new event types need to be recognized.
- Facebook markup can change, so scraper selectors in `fb_scraper.py` may need maintenance.
- Gemini API keys have daily free-tier quotas — rotate or add keys if quota errors increase.
- OCR quality depends on image clarity and Gemini's ability to read the image.
- Monitor `processed_calendars.json` to verify calendar deduplication is working correctly.
