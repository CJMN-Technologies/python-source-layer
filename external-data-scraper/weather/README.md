# Weather Pipeline

This folder updates LRT-2 weather observations and forecasts in Supabase using the Open-Meteo API.

## Purpose

The weather pipeline keeps two external weather tables fresh:

| Table | Purpose |
| --- | --- |
| `external.weather_current` | Current observed weather per LRT-2 station |
| `external.weather_forecasts` | Rolling 7-day forecast rows per LRT-2 station |

The pipeline is update-only. It does not insert new station or forecast rows. Required rows must already exist in Supabase.

## Tech Stack

| Technology | Purpose |
| --- | --- |
| Python 3.12 | Main pipeline language |
| Requests | Calls the Open-Meteo Forecast API |
| Open-Meteo | Weather source for current conditions and daily forecasts |
| Supabase Python client | Updates weather rows through Supabase |
| python-dotenv | Loads local `.env` values |
| APScheduler | Optional local hourly scheduler (not included in `requirements.txt` — install separately if needed) |

## Files

| File | Purpose |
| --- | --- |
| `pipeline.py` | Updates observations and forecasts. Supports `--mode` and `--days` arguments. |
| `weather_fetch.py` | Calls Open-Meteo and normalizes API output into station-level weather records. |
| `rainfall_classifier.py` | Converts raw rainfall values (mm) into human-readable rainfall levels. |
| `stations.json` | LRT-2 station names, station codes, latitude, and longitude for all 13 stations. |
| `scheduler.py` | Local hourly scheduler — runs both observations and forecasts every hour at minute `0`. |
| `requirements.txt` | Python dependencies (`requests`, `supabase`, `python-dotenv`). |
| `test_weather.py` | Simple manual API test script for verifying Open-Meteo connectivity. |
| `.env.example` | Template for local `.env` file. |
| `.gitignore` | Ignores `.env`, `__pycache__/`, logs, etc. |

## Environment Variables

Create a local `.env` in this folder when running locally:

```env
SUPABASE_URL=
SUPABASE_KEY=
```

Never commit `.env`.

## Install

```bash
pip install -r requirements.txt
```

If you want to use the local scheduler, also install APScheduler:

```bash
pip install apscheduler
```

## Run Manually

From this folder:

```bash
python pipeline.py
```

Run only observations:

```bash
python pipeline.py --mode observations
```

Run only forecasts:

```bash
python pipeline.py --mode forecasts
```

Change forecast window:

```bash
python pipeline.py --mode forecasts --days 7
```

## Forecast Slot Behavior

Forecast rows are treated as rolling slots.

Example for Anonas:

| Row ID | Meaning when run on 2026-06-18 |
| --- | --- |
| `FCT-ANONAS-0001` | Forecast for 2026-06-19 |
| `FCT-ANONAS-0002` | Forecast for 2026-06-20 |
| `FCT-ANONAS-0007` | Forecast for 2026-06-25 |

When the pipeline runs the next day, each slot moves forward by one day. The code updates forecast rows in reverse order, from `0007` down to `0001`, to avoid the unique constraint on `station + forecast_date`.

If a forecast row ID does not exist, the pipeline logs:

```text
skipping (no insert)
```

That means the pipeline found no matching row and intentionally did not create one.

## Scheduling

**Local scheduler:**

```bash
python scheduler.py
```

The scheduler runs both observations and forecasts every hour at minute `0`. It performs an initial fetch immediately on startup.

**GitHub Actions** uses a single workflow:

```text
.github/workflows/weather_pipeline.yml
```

| Schedule | Behavior |
| --- | --- |
| Daily at 00:00 UTC (8:00 AM PHT) | Runs the full pipeline (observations + forecasts) |
| Manual dispatch | Can be triggered manually from the GitHub Actions UI |

## Required Seed Data

Supabase must already contain:

- one `weather_current` row per station
- seven `weather_forecasts` rows per station
- forecast IDs following `FCT-<STATION_CODE>-0001` through `FCT-<STATION_CODE>-0007`

Station codes come from `stations.json`.
