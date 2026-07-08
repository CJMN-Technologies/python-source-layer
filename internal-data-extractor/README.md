# Internal Data Extractor

This folder contains the internal ETL scripts for transit operations datasets. It reads local Excel/CSV source files, validates and transforms records, loads them into Supabase PostgreSQL, and archives successfully processed files.

External reference workbook ingestions were moved to `external-data-scraper/`, so this folder is now internal-only.

## Project Scope

| Item | Description |
| --- | --- |
| Main purpose | Load internal transit datasets into Supabase PostgreSQL |
| Pipeline type | Extract, Transform, Load |
| Main language | Python 3.12 |
| Database | Supabase PostgreSQL |
| Data input | Excel and CSV files placed under `data/new_raw/Internal/` |
| Archive output | Processed files moved to `data/read_data/Internal/` |

## Tech Stack

| Technology | Purpose |
| --- | --- |
| Python 3.12 | Main ingestion and orchestration language |
| pandas | Reads, validates, cleans, and transforms Excel/CSV source files |
| openpyxl | Excel engine used by pandas for `.xlsx` files |
| psycopg2-binary | Connects to PostgreSQL/Supabase |
| psycopg2.sql | Safely builds schema/table/column identifiers |
| psycopg2.extras.execute_batch | Performs efficient batch inserts |
| python-dotenv | Loads local `.env` values |
| pathlib | Handles local file paths |
| shutil | Moves processed files into archive folders |
| subprocess | Runs ingestion scripts through the master runner |
| logging | Produces runtime logs and failure messages |

## Folder Structure

```text
internal-data-extractor/
|-- data/
|   |-- new_raw/
|   |   `-- Internal/             # Put pending internal source files here
|   `-- read_data/
|       `-- Internal/             # Successfully processed files are archived here
|-- ingest_raw_internal.py        # AFCS ridership ingestion
|-- ingest_student_transaction.py # Student transaction ingestion
|-- ingest_station_platform_capacity.py  # Station capacity ingestion
|-- ingest_psor_incidents.py      # PSOR incident ingestion
|-- run_all_ingestions.py         # Master runner (runs all scripts in sequence)
|-- requirements.txt              # Python dependencies (pandas, psycopg2-binary, python-dotenv, openpyxl)
|-- .env.example                  # Template for local .env file
|-- .gitignore                    # Ignores .env, data/, certs/, __pycache__/, etc.
`-- README.md
```

## Ingestion Scripts

| Script | Dataset | Target Schema/Table | Key Validations |
| --- | --- | --- | --- |
| `ingest_raw_internal.py` | AFCS ridership files by month/year | `AFCS.ridership_<year>` | Required columns, date parsing, numeric fields |
| `ingest_student_transaction.py` | Student transaction counts | `AFCS.student_transactions` | Required columns, date/numeric validation |
| `ingest_station_platform_capacity.py` | Station and platform capacity data | `Station Capacity.station_platform_capacity` | Required columns, capacity values |
| `ingest_psor_incidents.py` | PSOR incident records | `PSOR.psor_incidents` | Required columns, text field validation |
| `run_all_ingestions.py` | Runs all internal ingestion scripts in sequence | Orchestration only | Stops on first failure |

## Expected Source Files

Place pending files in:

```text
data/new_raw/Internal/
```

After successful loading, scripts move processed files to:

```text
data/read_data/Internal/
```

The scripts are defensive: they validate required columns, expected file names, dates, numeric values, and required text fields before writing to the database. If validation fails, the script exits with an error and the source file is not moved.

## Environment Variables

Create a local `.env` in this folder (see `.env.example` for a template):

```env
DATABASE_URL=YOUR_POSTGRESQL_CONNECTION_STRING
PGSSLROOTCERT=certs/prod-ca-2021.crt
```

Alternative certificate variable:

```env
SUPABASE_SSL_ROOT_CERT=certs/prod-ca-2021.crt
```

The certificate file is intentionally ignored by Git. Each developer should provide their own local copy.

## Install

```bash
pip install -r requirements.txt
```

Recommended Python version: **3.12** (matching the GitHub Actions runners used by other pipelines in this repo).

## Run All Internal Ingestions

From this folder:

```bash
python run_all_ingestions.py
```

Current order:

1. `ingest_raw_internal.py`
2. `ingest_student_transaction.py`
3. `ingest_station_platform_capacity.py`
4. `ingest_psor_incidents.py`

The runner stops if any script fails.

## Run One Ingestion

```bash
python ingest_raw_internal.py
python ingest_student_transaction.py
python ingest_station_platform_capacity.py
python ingest_psor_incidents.py
```

## Security and Data Handling

- Do not commit `.env`, certificates, raw datasets, archives, or generated cache files.
- Database credentials are loaded from environment variables.
- PostgreSQL connections use SSL certificate verification.
- SQL identifiers are built with `psycopg2.sql` to prevent injection.
- Scripts use transaction commits and rollback behavior through PostgreSQL connections.
- Source files move to `data/read_data/Internal/` only after successful processing.

## Moved External Scripts

These scripts used to be in the old internal/external repo, but now live under `external-data-scraper/`:

- `ingest_apta_protocols.py`
- `ingest_external_friction_index.py`

Use `external-data-scraper/README.md` for those workflows.
