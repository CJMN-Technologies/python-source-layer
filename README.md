# LRT Data Monorepo

This repository combines the data collection and ingestion work for the LRT capstone project into one monorepo. It is split by responsibility so teammates can quickly find the correct pipeline without needing to understand every script first.

## Repository Map

```text
python-source-layer/
|-- .github/workflows/                   # GitHub Actions for events, calendar, and weather jobs
|-- external-data-scraper/               # Public/external data collection and external reference ingestion
|   |-- scraper/                         # Facebook events + academic calendar scraper
|   |-- weather/                         # Open-Meteo weather observation and forecast updater
|   |-- AcademicCalendars/               # Extracted academic calendar .xlsx files (auto-committed by CI)
|   |-- Academic Calendar Format.xlsx    # Template format for generated calendar spreadsheets
|   |-- ingest_apta_protocols.py         # External APTA workbook ingestion
|   |-- ingest_external_friction_index.py
|   `-- ingestion_helpers.py             # Shared helpers for external workbook ETL scripts
|-- internal-data-extractor/             # Internal transit workbook ingestion
|   |-- data/new_raw/Internal/           # Place pending internal source files here
|   `-- data/read_data/Internal/         # Successfully processed internal files are archived here
|-- Academic_CalendarScraper_Logic.md    # Boolean filter logic and keyword strategy for calendar detection
|-- Capstone_External_Data_Basis.md      # Station-to-institution mapping and Facebook page targets
`-- test_glob/                           # Glob pattern testing (development utility)
```

## README Strategy

Multiple READMEs are intentional here and are the best fit for this repo.

- This root README gives the big-picture map.
- `external-data-scraper/README.md` explains all external-data pipelines together.
- `external-data-scraper/scraper/README.md` explains the Facebook scraper in detail.
- `external-data-scraper/weather/README.md` explains the weather updater in detail.
- `internal-data-extractor/README.md` explains the internal workbook ETL scripts.

This avoids one huge README while still giving each teammate the context they need at the folder they are working in.

## Tech Stack Summary

| Area | Technology |
| --- | --- |
| Language | Python 3.12 |
| Database | Supabase PostgreSQL |
| Supabase REST client | `supabase-py` |
| Direct PostgreSQL client | `psycopg2-binary` |
| Data processing | `pandas`, `openpyxl` |
| Web scraping & Social Ingestion | Apify Cloud Client (`apify-client`), Requests, BeautifulSoup |
| OCR & Image text extraction | Google Gemini 2.0 Flash (`google-genai`) with Pillow and NumPy for image pre-processing |
| LLM Classification | Google Gemini 2.0 Flash (`google-genai`) with Pydantic structured output |
| Unicode normalization | Custom mapper for decorative Facebook text (Mathematical Bold, Script, Double-Struck, etc.) |
| Weather API | Open-Meteo Forecast API |
| Email Alerts | `smtplib` (Gmail SMTP with TLS) |
| Scheduling | GitHub Actions cron, APScheduler for local long-running schedulers |
| Configuration | `.env` files, GitHub Actions secrets |

## Classification & Stealth Scraping Pipeline

The events scraper uses a **two-stage classification pipeline** powered by Apify and Google Gemini:

1. **Full Caption Extraction & Residential Unblocking** — Fetches complete, un-truncated post text and media assets via Apify's residential infrastructure without cookie requirements, eliminating "See More" truncation and login checkpoint walls.
2. **Unicode Font Normalization Order** — `normalize_unicode_text()` executes *prior* to emoji stripping, preventing Mathematical Sans-Serif and Bold Unicode headers (e.g. San Juan City PIO) from being deleted by emoji character classes.
3. **Pre-filter (keywords)** — `keywords.py` checks if the post text contains any known disruption keywords using `.casefold()` matching. Posts without any keyword hit are discarded immediately to save LLM API quota.
4. **Multi-Modal LLM extraction (Gemini)** — `llm_classifier.py` fuses the expanded caption and Gemini Vision OCR from attached infographics to extract structured metadata (`category`, `event_name`, `event_date`, `event_code`).
5. **Real-Time Intra-Batch Deduplication** — In-memory deduplication sets (`existing_urls` and `existing_texts`) update immediately upon each event insertion, preventing duplicate ingestion between base posts and photo overlay URLs within the same scheduled cron matrix run.
6. **Database Transformation Sync** — Once stored in `external.academic_lgu_events`, the PostgreSQL trigger `tg_sync_academic_lgu_events` evaluates the post via `external.classify_event_from_text` (equipped with resilient, tense-agnostic regex and `#WalangPasok` hashtag matching) to automatically insert qualified disruption events into `external.events_consolidated`:
   - **Student Council Petitions & Political Commentary Guardrail**: Unofficial student council petitions, appeals for suspension, position papers, and political accountability critiques (e.g. *"Walang Pasok dahil sa korapsyon"*) are classified as non-disruptive `administrative` notices (`affects_ridership = FALSE`) to prevent premature capacity dampeners until officially approved by University Admin or LGUs.
   - **LGU Weather & Flooding Monitoring Isolation**: Non-disruptive LGU rainfall advisories, river park clearing operations, and road flood updates are classified as non-disruptive `lgu` notices (`affects_ridership = FALSE`) without triggering spurious capacity dampeners or defaulting to `"University Milestone / Surge"`.
   - **OLFU Multi-Campus Exception Parsing**: For nationwide multi-campus posts, evaluates exception clauses (`except/excluding/maliban sa [branch]`). Announcements like *"All OLFU Campuses (except OLFU Quezon City)"* are accepted for Antipolo, while notices specifically exempting Antipolo are rejected.
   - **UST Caption-Less Infographic Extraction**: Targets high-resolution advisory posters within `div[role="article"]`, skipping small avatar thumbnails and generating fallback captions to guarantee downstream ingestion.
   - **Strict Disruption Cancellation vs Active Suspension Isolation**: Declared class suspensions, school holidays, and number coding suspensions are isolated as active disruptions (`is_cancellation = false`). The database cancellation cascade is strictly constrained to explicit resumptions and liftings (`is_cancellation = true` with matching `cancellation_target_code`), preventing false-positive cancellation cascades across institutions.
   - **Traffic & Number Coding Advisory Routing**: MMDA/LGU Number Coding and traffic caravan advisories are classified as `lgu` under `CIVIC_MAINTENANCE`, preventing traffic notices from masquerading as school class suspensions.
   - **Source URL & Post Text Propagation**: Propagates Facebook announcement permalinks and raw post text directly into `external.events_consolidated.source_url` and `description`.

Before a post can be saved, the scraper validates `source_url` so only trusted Facebook post/photo links from the configured page are inserted. Personal profile links, `/people/` links, comment/reply links, videos, and reels are skipped to keep `external.academic_lgu_events` limited to official page announcements.

Post categories:

| Category | Meaning |
| --- | --- |
| `academic` | Class suspensions, resumptions, school holidays, exams, enrollment, graduation |
| `lgu` | Government advisories, road closures, transport disruptions, concert/arena events |
| `pagasa` | PAGASA weather bulletins relevant to NCR / LRT-2 catchment areas |
| `academic_calendar` | A post sharing a full academic calendar document (triggers Excel generation + email) |

## GitHub Actions Matrix System

The events scraper executes via a matrix strategy running across **Eastbound** and **Westbound** station clusters across **3 daily time windows**:

- **4:00 AM PHT** (20:00 UTC): Early morning class suspension and transport strike scanning
- **11:00 AM PHT** (03:00 UTC): Midday weather, class, and afternoon activity adjustments
- **4:00 PM PHT** (08:00 UTC): Evening advisories, next-day suspensions, and event updates

The matrix automatically partitions your configured station target pages into non-overlapping batch runs (Eastbound & Westbound) executing concurrently on GitHub Actions.

## Environment Variables

The repo uses several secrets and environment variables for its pipelines:

| Variable | Used By | Purpose |
| --- | --- | --- |
| `SUPABASE_URL` | `scraper/`, `weather/` | Supabase project URL for the REST client. |
| `SUPABASE_KEY` | `scraper/`, `weather/` | Supabase API key for the REST client. |
| `DATABASE_URL` | `internal-data-extractor/`, external workbook ingestions | PostgreSQL connection URL for direct ETL loading. |
| `PGSSLROOTCERT` | Direct PostgreSQL ingestion scripts | Path to the Supabase SSL root certificate. |
| `APIFY_API_TOKEN` | Facebook scraper (`scraper/`) | Apify API token used to fetch full post captions and image assets without cookie requirements. |
| `GEMINI_API_KEY` | Facebook scraper, calendar scraper | API key for Gemini 2.0 Flash (OCR and LLM classification). Supports comma-separated lists for key rotation. |
| `GEMINI_API_KEY_2`, `_3`, ... | Facebook scraper, calendar scraper | Additional Gemini API keys loaded sequentially for automatic failover when quota is exhausted. |
| `SENDER_EMAIL` | Facebook scraper | Gmail address used to send automated email alerts. |
| `SENDER_PASSWORD` | Facebook scraper | Gmail app password for SMTP authentication. |
| `RECEIVER_EMAIL` | Facebook scraper | Comma-separated list of email recipients for alerts and calendar attachments. |

Do not commit `.env`, API keys, cookies, certificates, source workbooks, logs, or generated cache files.

## GitHub Actions

The active workflow files are in `.github/workflows/` at the repository root:

| Workflow | File | Schedule | Purpose |
| --- | --- | --- | --- |
| Events Pipeline | `events_pipeline.yml` | 4:00 AM, 11:00 AM, and 4:00 PM PHT daily (3 time windows) | Scrapes Facebook pages for LRT-2 disruption events. Supports manual dispatch with batch selection. |
| Calendar Scraper | `calendar_scraper.yml` | Every 5 days at 8:00 AM PHT | Scrapes for academic calendar releases, generates `.xlsx` files, and auto-commits them to the repo. |
| Weather Pipeline | `weather_pipeline.yml` | Hourly from 5:00 AM to 10:00 PM PHT (`0 21-23,0-14 * * *`) | Updates current weather observations and 7-day forecasts for all 13 LRT-2 stations. |
| Weather Watchdog | `weather_watchdog_pipeline.yml` | Half-hourly backup from 5:30 AM to 10:30 PM PHT (`30 21-23,0-14 * * *`) | Secondary failover watchdog ensuring station weather metrics remain updated. |

## Security Notes

- Secrets must live in local `.env` files or GitHub Actions secrets only.
- Rotate any secret that was ever pushed publicly, even if Git history was later rewritten.
- Source datasets are intentionally ignored under `data/new_raw/` and `data/read_data/`.
- Certificates are ignored and should be installed locally by each developer.
- Facebook cookies should be rotated regularly. The pipeline sends automated email alerts when cookies expire.

## First-Time Setup

1. Clone the repository.
2. Create local `.env` files only in the folders you need to run (see `.env.example` files).
3. Install dependencies for the specific pipeline you are working on.
4. Read the folder README before running a script that writes to Supabase.

Start here:

- External pipelines: `external-data-scraper/README.md`
- Internal ETL: `internal-data-extractor/README.md`
