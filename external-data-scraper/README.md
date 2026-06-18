# External Data Scraper and Ingestion

This folder owns external data sources used by the LRT capstone system. It contains live external collectors, weather updaters, and external reference workbook ingestions.

## What Is Inside

```text
external-data-scraper/
|-- scraper/                         # Facebook page scraper for academic/LGU events
|-- weather/                         # Open-Meteo weather observations and forecasts
|-- data/new_raw/External/           # Pending external reference workbooks
|-- data/read_data/External/         # Archived external reference workbooks
|-- ingestion_helpers.py             # Shared helpers for external workbook ETL scripts
|-- ingest_apta_protocols.py         # Loads APTA protocol workbook
|-- ingest_external_friction_index.py
`-- requirements.txt                 # Dependencies for the root-level workbook ETL scripts
```

## External Pipelines

| Folder or Script | Purpose | Target Table |
| --- | --- | --- |
| `scraper/` | Scrapes selected Facebook pages for academic and LGU disruptions. | `external.academic_lgu_events` |
| `weather/` | Fetches current weather and 7-day forecasts for LRT-2 stations. | `external.weather_current`, `external.weather_forecasts` |
| `ingest_apta_protocols.py` | Loads `APTA_Protocols.xlsx`. | `APTA.apta_protocols` |
| `ingest_external_friction_index.py` | Loads friction index Excel sources. | `external.friction_index` |

## Tech Stack

| Area | Technology |
| --- | --- |
| Language | Python |
| API/database client | Supabase Python client for scraper and weather |
| Direct database loading | psycopg2 with SSL verification |
| Data processing | pandas, openpyxl |
| Web scraping | Playwright, BeautifulSoup, Requests |
| OCR | Tesseract OCR, pytesseract, Pillow |
| Weather source | Open-Meteo Forecast API |
| Scheduling | GitHub Actions and APScheduler |

## Environment Variables

For `scraper/` and `weather/`:

```env
SUPABASE_URL=
SUPABASE_KEY=
```

For `scraper/` only:

```env
FB_C_USER=
FB_XS=
FB_DATR=
FB_FR=
FB_SB=
```

For the root-level external workbook ingestion scripts:

```env
DATABASE_URL=YOUR_POSTGRESQL_CONNECTION_STRING
PGSSLROOTCERT=certs/prod-ca-2021.crt
```

`SUPABASE_SSL_ROOT_CERT` can be used instead of `PGSSLROOTCERT`.

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

- The Facebook scraper and weather updater use Supabase REST credentials.
- The workbook ingestion scripts use a direct PostgreSQL `DATABASE_URL`.
- Source workbooks, `.env`, certificates, and generated files are ignored by Git.
- Read `scraper/README.md` and `weather/README.md` before changing those pipelines.
