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
| Python | Main pipeline language |
| Requests | Calls the Open-Meteo Forecast API |
| Open-Meteo | Weather source for current conditions and daily forecasts |
| Supabase Python client | Updates weather rows through Supabase |
| python-dotenv | Loads local `.env` values |
| APScheduler | Optional local hourly scheduler |

## Files

| File | Purpose |
| --- | --- |
| `pipeline.py` | Updates observations and forecasts |
| `weather_fetch.py` | Calls Open-Meteo and normalizes API output |
| `rainfall_classifier.py` | Converts rainfall values into rainfall levels |
| `stations.json` | LRT-2 station names, station codes, latitude, and longitude |
| `scheduler.py` | Local hourly scheduler |
| `requirements.txt` | Python dependencies |
| `test_weather.py` | Simple manual API test script |

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

Local scheduler:

```bash
python scheduler.py
```

The scheduler runs observations and forecasts hourly at minute `0`.

GitHub Actions uses root workflows:

```text
.github/workflows/weather-observations.yml
.github/workflows/weather-forecasts.yml
```

Current workflow behavior:

| Workflow | Schedule |
| --- | --- |
| `weather-observations.yml` | Hourly |
| `weather-forecasts.yml` | Daily at 06:00 Asia/Manila |

## Required Seed Data

Supabase must already contain:

- one `weather_current` row per station
- seven `weather_forecasts` rows per station
- forecast IDs following `FCT-<STATION_CODE>-0001` through `FCT-<STATION_CODE>-0007`

Station codes come from `stations.json`.
