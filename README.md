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
4. **Multi-Modal LLM extraction (Gemini)** — `llm_classifier.py` fuses the expanded caption and Gemini Vision OCR from attached infographics to extract structured metadata (`category`, `event_name`, `event_date`, `event_code`). Includes a strict duration guardrail that prioritizes explicit disruption dates (e.g. *"September 21–22"*) and prevents colloquial or administrative phrases (e.g. *"swap modalities for this week"*) from artificially inflating the event date to a 6-day week.
5. **Real-Time Intra-Batch Deduplication (5-Layer Bulletproof Strategy)** — In-memory deduplication sets (`existing_urls`, `existing_texts`, `existing_event_keys`, `existing_code_keys`) update immediately upon each event insertion:
   - **Layer 1: URL Deduplication** — Skips posts whose `source_url` (cleaned of query tracking parameters) already exists in `external.academic_lgu_events`.
   - **Layer 2: Normalized Text Prefix Similarity** — Strips advisory prefixes (`"Advisory |"`, `"Edited Advisory |"`, `"Just In |"`, `"#WalangPasok"`, `"Panoorin |"`) and compares the first 100 characters against existing records to catch identical notices reposted under different URL schemes (e.g. `/posts/` vs `/photo/`) or corrected replacement advisories (*"Edited Advisory: This advisory has been edited to correct an error..."*).
   - **Layer 3: Semantic Event & Code Key Collisions** — Prevents reminder posts `(source_name, event_name, event_date)` and duplicate major transit disruption codes `(source_name, event_date, event_code)` like `MAJOR_ARENA_EVENT`, `CLASS_SUSPENSION`, and `ONLINE_CLASS_SHIFT` from duplicating within the same run.
   - **Layer 4: Retrospective Photo Recap & Street Festival Guardrail** (`MAJOR_ARENA_EVENT` only) — Detects posts published *after* an arena, sports event, or street festival has already concluded. A post is blocked if its LLM-extracted `event_date` is on or before the post's publication date (`days_in_past >= 1`), or if `event_date` is in the past and the post contains retrospective phrasing (`"naging matagumpay"`, `"photo highlight"`, `"event recap"`, `"successfully held"`, `"naganap noong"`, `"playing it back"`, `"katatapos lang"`, `"came together for an opening"`, `"officially commenced"`, `"after the ... ceremony"`, `"idinaos na"`, `"napuno ng masasayang aktibidad"`, etc. in both Filipino and English), post-event sponsor appreciation phrasing (`"thank you to our partner"`, `"couldn't have done it without"`, `"partner companies"`, `"sponsors and partners"`, `"one to remember"`), or off-corridor micro-venues (`banawe`, `chinatown`, `mooncake fest`). This guardrail operates synchronously in `pipeline.py` and is mirrored in the `external.sync_academic_lgu_to_events_consolidated` database trigger as a secondary defense (`purge_cleanup_mooncake_and_cluster_duplicate_anomalies.sql`).
   - **Layer 5: Institutional Cluster Deduplication (Start-Date Normalized)** — When two pages from the *same institution* (e.g. *Far Eastern University Manila* and *FEU Central Student Organization*) post about the same disruption code (`CLASS_SUSPENSION`, `ONLINE_CLASS_SHIFT`, or `TRANSPORT_STRIKE`), deduplication indexes match by both exact date strings and normalized start dates. Only the parent university announcement is ingested, preventing student council advisories with multi-day wording variations (e.g. *"this week"*) from generating duplicate or inflated shock sequences.
6. **Off-Corridor Mega-Venue Geofence Pre-Filter (`is_off_corridor_venue()`)** — Blocks events taking place at major sports/entertainment arenas or exhibition centers outside the LRT-2 corridor transit catchment walkshed (e.g. SM Mall of Asia Arena Pasay, PICC Plenary Hall Pasay, San Andres Sports Complex Malate, Rizal Memorial Stadium Pasay/Manila, Ninoy Aquino Stadium, Philsports Arena/Ultra Pasig, Cuneta Astrodome, Filoil EcoOil Centre San Juan, Foro de Intramuros, Banawe Chinatown). Prevents non-corridor mega-events and heritage expos from generating spurious transit capacity spikes across LRT-2 stations.
7. **Micro-Venue, Expo & Street Festival Filter (`is_micro_venue_or_administrative()`)** — Filters out ticket sales booths, dance studios, covered court rehearsals, heritage/tourism expos (`foro de intramuros`, `tourism expo`, `heritage spaces`), district street festivals (`banawe`, `chinatown`, `mooncake fest`), and administrative form deadlines (e.g. admissions, transcript clearance, graduation form submissions) from being classified as `MAJOR_ARENA_EVENT` (weight 0.65).
8. **Database Transformation Sync** — Once stored in `external.academic_lgu_events`, the PostgreSQL trigger `tg_sync_academic_lgu_events` evaluates the post via `external.classify_event_from_text` (equipped with resilient, tense-agnostic regex, constrained statutory holiday matches, and `#WalangPasok` hashtag matching) to automatically insert qualified disruption events into `external.events_consolidated`:
   - **Constrained Statutory Holiday Regex Guardrail**: In `external.classify_event_from_text()`, the pattern for statutory holidays (`araw\s+ng`) is restricted to statutory/memorial terms (`araw\s+ng\s+(kagitingan|maynila|kalayaan|quezon|pasig|marikina|san\s+juan|manggagawa|mga\s+bayani|wika)`), preventing municipal civic posts (e.g. *"isang araw ng paglilinis"*) from falsely triggering full Holiday (`1.0`) capacity dampeners.
   - **Civic & Environmental Cleanup Isolation**: Community cleanup activities, coastal cleanup drives, waterway clearing, and flood drain declogging are classified under `infrastructure` / `LGU Municipal Clearing & Maintenance` (`affects_ridership = FALSE`), preventing municipal sanitation updates from impacting passenger capacity.
   - **Student Council Petitions & Administrative Services Guardrail**: Unofficial student council petitions, appeals for suspension, position papers, student ID processing, and studio photoshoot/yearbook/toga rental advisories are classified as non-disruptive `administrative` notices (`affects_ridership = FALSE`) to prevent premature capacity dampeners or spurious `WARNING` alerts.
   - **Health & Medical Surveillance Isolation**: Routine post-flood Leptospirosis warnings, Doxycycline prophylaxis distribution, dengue alerts, and vaccination drives are isolated as non-disruptive medical notices (`affects_ridership = FALSE`), preventing false weather friction index spikes on fair weather recovery days.
   - **Post-Disaster Community Relief Isolation**: Community food pack distribution, donation drives, and volunteer operations (*#IskoOps*) are categorized as non-disruptive administrative aid (`affects_ridership = FALSE`).
   - **Youth, Tech & Cultural Festivals (`MAJOR_ARENA_EVENT`)**: Youth summits, tech expos (*Teknolodi Fest*), Buwan ng Kabataan celebrations, and concerts held at major sports complexes/arenas within the corridor are correctly classified as `MAJOR_ARENA_EVENT` (`friction_weight = 0.65`) rather than being misclassified as weather advisories.
   - **Emergency IMT Demobilization Isolation**: Emergency operations reports stating that an Incident Management Team (IMT) or staging post has "demobilized" following the subsidence of flood waters are isolated from `Civic Rally & Public Mobilization`, preventing false crowd mobilization alerts.
   - **PAGASA Storm Bulletin Isolation**: Severe weather warning reposts (e.g. Typhoon/Tropical Storm Signal updates) from LGU information offices are preserved as meteorological advisories rather than misclassifying into `"Holiday"` or `"University Milestone / Surge"`.
   - **Satellite Campus Local Holiday Geofencing**: Local LGU holidays specific to university satellite branches situated entirely outside the LRT-2 corridor (e.g. *Makati Day (Makati Holiday)* for FEU Makati) are quarantined and excluded from triggering academic disruption shocks across LRT-2 stations.
   - **LGU Sanitation & Maintenance Isolation**: Non-disruptive LGU rainfall advisories, river park clearing, canal/estero dredging, trash collection, and road flood updates are classified as non-disruptive `lgu` notices (`affects_ridership = FALSE`) without triggering spurious capacity dampeners or defaulting to `"Holiday"` or `"University Milestone / Surge"`.
   - **OLFU Multi-Campus Exception Parsing**: For nationwide multi-campus posts, evaluates exception clauses (`except/excluding/maliban sa [branch]`). Announcements like *"All OLFU Campuses (except OLFU Quezon City)"* are accepted for Antipolo, while notices specifically exempting Antipolo are rejected.
   - **UE Multi-Campus Disambiguation & Caloocan Corridor Guardrail**: Evaluates announcements from University of the East (`is_ue_manila_post()`). Announcements explicitly targeting UE Caloocan only (e.g. Caloocan campus concerts, local suspensions, orientations at Caloocan Open Field) are skipped at the pre-filter level to prevent out-of-corridor events from being misattributed to UE Manila or registered at LRT-2 Recto station. Announcements targeting UE Manila or systemwide/all campuses are accepted and assigned to Recto station.
   - **Academic Calendar Release Hardening**: In `calendar_scraper.py`, evaluates strict negative keywords (`petition`, `shift to evm`, `enhanced virtual mode`, `position paper`, `appeal`, `resolution`, `statement on`, `transport strike`) and requires primary calendar identifiers to appear in headlines or structured document attachments, preventing student council petitions from being misidentified as academic calendar releases.
   - **Numeric Sequence ID Isolation**: In `pipeline.py` and `calendar_scraper.py`, ID generation uses numeric regular expression matching (`rf"^{re.escape(base)}_(\d+)$"`) and integer maximum comparison, guaranteeing that calendar release IDs (`external_acad_cal_`) never cause lexicographical sorting collisions or clobber regular academic sequence IDs (`external_acad_`).
   - **Civic Theme Month & Multi-Week Celebration Guardrail**: Multi-week civic observances and broad promotional campaigns (e.g. *World Tourism Month*, *Philippine Creative Industries Month*, *Buwan ng Wika*, *Anniversary Month*) are prevented from generating multi-week daily `MAJOR_ARENA_EVENT` shocks across LRT-2 stations and are categorized as non-disruptive civic observances (`affects_ridership = FALSE`).
   - **UST Enriched Virtual Mode & Infographic Ingestion**: Equipped with dedicated phrase recognition for UST's *"Enriched Virtual Mode of Instruction"* (EVM), institutional demographic identifiers (*Thomasians*, *UST*, *Office of the Secretary-General*), and institutional source context inheritance, ensuring short hashtag captions (`#USTAdvisory`) and formal infographic advisories are reliably classified without being dropped at the pre-filter.
   - **Strict Disruption Cancellation & Rescheduling Synchronization**: Declared class suspensions, school holidays, and number coding suspensions are isolated as active disruptions (`is_cancellation = false`). For explicit postponements or resumptions (`is_cancellation = true`), the consolidation trigger deactivates prior matching events on the announcement date in `academic_lgu_events` and deletes them from `events_consolidated`. If the announcement reschedules a major sports or arena event (e.g. UAAP kickoff party or exhibition match) to a new future date, the trigger honors `event_code = MAJOR_ARENA_EVENT` and registers the rescheduled event on the target date under `major_event` (weight `0.65`), preventing incidental suspension clauses from hijacking the event into a false Critical (`1.0`) class suspension. For official resumptions of classes/work (`event_code = 'RESUMPTION_CLASSES'`), the trigger strictly exits without inserting active suspension records, preventing advance notices (where `event_date > post_date`) from being inverted into active class suspensions.
   - **Annual Holiday Proclamation Guardrail**: Nationwide holiday schedules published months in advance for an upcoming calendar year (e.g. Malacañang 2027 Holiday Schedule) are classified as non-disruptive reference notices (`CIVIC_MAINTENANCE` / non-disruptive), preventing them from being scheduled as pseudo class suspensions on January 1.
   - **Strict LGU vs Academic Source Entity Classification (`source_type`)**: Guarantees that all events originating from LGU channels (e.g. Quezon City Government, Manila PIO, San Juan City PIO, Pasig City PIO, Marikina PIO, Antipolo City Government, Municipality of Cainta) are stored with `source_type = 'lgu'` in `external.events_consolidated`, regardless of whether the announcement pertains to class suspensions. This preserves reporting authority fidelity and ensures that downstream dashboards render blue LGU badges and route them to LGU filter tabs.
   - **Retrospective Photo Recap & Sports Outcome Guardrail**: When `external.sync_academic_lgu_to_events_consolidated` is triggered by a new INSERT or UPDATE, the trigger evaluates whether the post is a past-event photo recap or sports game outcome (`MAJOR_ARENA_EVENT` with `post_date::date - event_date::date >= 1`, or retrospective phrasing including sports game results like `victory over`, `defeated`, `won against`, `idinaos na`, `napuno ng masasayang aktibidad`). Retrospective records are silently rejected and their rows removed from `events_consolidated`.
   - **Institutional Cluster Deduplication (Database-level)**: In `external.sync_academic_lgu_to_events_consolidated()`, the trigger enforces active parent-child deduplication: if a student council posts an advisory for an event code where the parent university has already filed an active disruption, the student council post is suppressed from inserting duplicate or inflated shock rows into `events_consolidated`.
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

## GitHub Actions Tiered Scraping Schedule

The events scraper runs **3 times per day** via GitHub Actions cron (scheduled at off-peak minutes to avoid global runner queue contention), each with a purpose-built role and intensity:

| Run | Time (PHT) | Mode | Role | Window | Posts | Est. Cost |
| --- | --- | --- | --- | --- | --- | --- |
| Morning sweep | 4:18 AM (20:18 UTC) | `strong` | Primary daily sweep — catches all events from the past 24h | 24h | ~150 | ~$0.75 |
| Mid-day catcher | 11:23 AM (03:23 UTC) | `medium` | Catches morning class suspensions + 4 AM cap overflows | 8h | ~72 | ~$0.36 |
| Afternoon watchdog | 4:14 PM (08:14 UTC) | `light` | Late LGU advisories, afternoon road closures | 4h | ~36 | ~$0.18 |

**Budget:** ~258 posts/day → ~$1.29/day → ~**$40/month** (Apify Starter $29 + ~$11 overage at $0.005/result Pay-per-event billing, confirmed from Apify dashboard).

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
| Events Pipeline | `events_pipeline.yml` | 4:18 AM, 11:23 AM, and 4:14 PM PHT daily (3 off-peak windows) | Scrapes Facebook pages for LRT-2 disruption events. Supports manual dispatch with batch selection. |
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
