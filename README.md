# LRT Data Monorepo

This repository combines the data collection and ingestion work for the LRT capstone project into one monorepo. It is split by responsibility so teammates can quickly find the correct pipeline without needing to understand every script first.

## Repository Map

```text
lrt-data/
|-- .github/workflows/              # GitHub Actions for external scraper and weather jobs
|-- external-data-scraper/          # Public/external data collection and external reference ingestion
|   |-- scraper/                    # Facebook/LGU/academic event scraper
|   |-- weather/                    # Open-Meteo weather observation and forecast updater
|   |-- ingest_apta_protocols.py    # External APTA workbook ingestion
|   `-- ingest_external_friction_index.py
`-- internal-data-extractor/        # Internal transit workbook ingestion
    |-- data/new_raw/Internal/      # Place pending internal source files here
    `-- data/read_data/Internal/    # Successfully processed internal files are archived here
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
| Language | Python |
| Database | Supabase PostgreSQL |
| Supabase REST client | `supabase-py` |
| Direct PostgreSQL client | `psycopg2-binary` |
| Data processing | `pandas`, `openpyxl` |
| Web scraping | Playwright, BeautifulSoup, Requests |
| OCR | Tesseract OCR, Pillow, pytesseract |
| Weather API | Open-Meteo Forecast API |
| Scheduling | GitHub Actions cron, APScheduler for local long-running schedulers |
| Configuration | `.env`, GitHub Actions secrets |

## Environment Variables

The repo uses two kinds of database access:

| Variable | Used By | Purpose |
| --- | --- | --- |
| `SUPABASE_URL` | `external-data-scraper/scraper`, `external-data-scraper/weather` | Supabase project URL for the REST client. |
| `SUPABASE_KEY` | `external-data-scraper/scraper`, `external-data-scraper/weather` | Supabase API key for the REST client. |
| `DATABASE_URL` | `internal-data-extractor`, external workbook ingestions | PostgreSQL connection URL for direct ETL loading. |
| `PGSSLROOTCERT` or `SUPABASE_SSL_ROOT_CERT` | Direct PostgreSQL ingestion scripts | Path to the Supabase SSL root certificate. |
| `FB_C_USER`, `FB_XS`, `FB_DATR`, `FB_FR`, `FB_SB` | Facebook scraper | Facebook session cookies used by Playwright. |

Do not commit `.env`, API keys, cookies, certificates, source workbooks, logs, or generated cache files.

## GitHub Actions

The active workflow files are in `.github/workflows/` at the repository root:

- `scraper.yml` runs the Facebook scraper.
- `weather-observations.yml` updates current weather observations hourly.
- `weather-forecasts.yml` updates rolling forecast rows daily.

If folder paths change again, update these workflows at the same time.

## Security Notes

- Secrets must live in local `.env` files or GitHub Actions secrets only.
- Rotate any secret that was ever pushed publicly, even if Git history was later rewritten.
- Source datasets are intentionally ignored under `data/new_raw/` and `data/read_data/`.
- Certificates are ignored and should be installed locally by each developer.

## First-Time Setup

1. Clone the repository.
2. Create local `.env` files only in the folders you need to run.
3. Install dependencies for the specific pipeline you are working on.
4. Read the folder README before running a script that writes to Supabase.

Start here:

- External pipelines: `external-data-scraper/README.md`
- Internal ETL: `internal-data-extractor/README.md`
