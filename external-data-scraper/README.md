# External Data Scraper and Ingestion

This folder owns external data sources used by the LRT capstone system. It contains live external collectors (Facebook events scraper, academic calendar scraper, weather updater) and external reference workbook ingestions.

## What Is Inside

```text
external-data-scraper/
|-- scraper/                         # Facebook page scraper for events + academic calendar releases
|-- weather/                         # Open-Meteo weather observations and forecasts
|-- AcademicCalendars/               # Generated academic calendar .xlsx files (auto-committed by CI)
|-- Academic Calendar Format.xlsx    # Template format for generated calendar spreadsheets
|-- data/new_raw/External/           # Pending external reference workbooks
|-- data/read_data/External/         # Archived external reference workbooks
|-- ingestion_helpers.py             # Shared helpers for external workbook ETL scripts
|-- ingest_apta_protocols.py         # Loads APTA protocol workbook
|-- ingest_external_friction_index.py
|-- requirements.txt                 # Dependencies for the root-level workbook ETL scripts
|-- .env.example                     # Template for local .env file
`-- .gitignore                       # Ignores .env, data/, certs/, __pycache__/, etc.
```

## External Pipelines

| Folder or Script | Purpose | Target Table | Trigger |
| --- | --- | --- | --- |
| `scraper/` (events pipeline) | Scrapes selected Facebook pages for academic and LGU disruptions using a two-stage keyword + Gemini LLM classifier. | `external.academic_lgu_events` | GitHub Actions (`events_pipeline.yml`) — 2× daily |
| `scraper/` (calendar scraper) | Detects academic calendar releases on Facebook, extracts dates via Gemini OCR, generates `.xlsx` files, and emails them to the team. | `external.academic_lgu_events` | GitHub Actions (`calendar_scraper.yml`) — every 5 days |
| `weather/` | Fetches current weather and 7-day forecasts for LRT-2 stations from Open-Meteo. | `external.weather_current`, `external.weather_forecasts` | GitHub Actions (`weather_pipeline.yml`) — daily |
| `ingest_apta_protocols.py` | Loads `APTA_Protocols.xlsx` into Supabase. | `APTA.apta_protocols` | Manual |
| `ingest_external_friction_index.py` | Loads friction index Excel sources into Supabase. | `external.friction_index` | Manual |

## Tech Stack

| Area | Technology |
| --- | --- |
| Language | Python 3.12 |
| API/database client | Supabase Python client for scraper and weather |
| Direct database loading | psycopg2 with SSL verification |
| Data processing | pandas, openpyxl |
| Web scraping | Playwright (headless Chromium), BeautifulSoup, Requests |
| OCR & image text extraction | Google Gemini 2.0 Flash (`google-genai`) with Pillow and NumPy |
| LLM classification | Google Gemini 2.0 Flash with Pydantic structured output |
| Unicode normalization | Custom module for decorative Facebook text |
| Weather source | Open-Meteo Forecast API |
| Email alerts | smtplib (Gmail SMTP with TLS) |
| Scheduling | GitHub Actions and APScheduler |

## Environment Variables

For `scraper/` and `weather/`:

```env
SUPABASE_URL=
SUPABASE_KEY=
```

For `scraper/` only (Facebook cookies):

```env
FB_C_USER=
FB_XS=
FB_DATR=
FB_FR=
FB_SB=
```

For `scraper/` only (Gemini API — supports comma-separated keys or sequential variables):

```env
GEMINI_API_KEY=your_key_1,your_key_2
# Or use sequential variables:
# GEMINI_API_KEY_2=your_key_2
# GEMINI_API_KEY_3=your_key_3
```

For `scraper/` only (email notifications):

```env
SENDER_EMAIL=
SENDER_PASSWORD=
RECEIVER_EMAIL=
```

For the root-level external workbook ingestion scripts:

```env
DATABASE_URL=YOUR_POSTGRESQL_CONNECTION_STRING
PGSSLROOTCERT=certs/prod-ca-2021.crt
```

`SUPABASE_SSL_ROOT_CERT` can be used instead of `PGSSLROOTCERT`.

See `.env.example` files in each folder for templates.

## Running External Workbook Ingestions

From this folder:

```bash
pip install -r requirements.txt
```

Place workbooks in:

```text
data/new_raw/External/
```

Expected filenames:

- `APTA_Protocols.xlsx`
- `FrictionIndex_Academic.xlsx`
- `FrictionIndex_Operational.xlsx`
- `FrictionIndex_PagASA.xlsx`

Run:

```bash
python ingest_apta_protocols.py
python ingest_external_friction_index.py
```

Successfully processed files are moved to:

```text
data/read_data/External/
```

## Important Notes

- The Facebook scraper and weather updater use Supabase REST credentials (`SUPABASE_URL` / `SUPABASE_KEY`).
- The workbook ingestion scripts use a direct PostgreSQL `DATABASE_URL` with SSL certificate verification.
- The scraper uses Gemini 2.0 Flash for both OCR (image text extraction) and LLM classification (event categorization).
- Email alerts are sent via Gmail SMTP when new events are found or when Facebook cookies expire.
- Source workbooks, `.env`, certificates, and generated files are ignored by Git.
- Read `scraper/README.md` and `weather/README.md` before changing those pipelines.
