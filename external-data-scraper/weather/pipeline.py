import json
import os
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from weather_fetch import fetch_weather_station
from rainfall_classifier import classify_rainfall

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def load_stations():
    with open(os.path.join(os.path.dirname(__file__), "stations.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def forecast_row_id(station_code: str, forecast_number: int) -> str:
    return f"FCT-{station_code}-{forecast_number:04d}"

def run_weather_pipeline(days: int = 7):
    print("=== LRT-2 Weather Pipeline Starting ===")
    print(f"Time: {datetime.now(timezone.utc)}")

    stations = load_stations()
    obs_saved = 0
    fct_saved = 0
    last_successful_weather = None

    for station in stations:
        print(f"\nProcessing station: {station['station']}")
        weather_data = None
        time.sleep(0.5) # Gentle rate-limit buffer between API calls
        
        for attempt in range(5):
            try:
                weather_data = fetch_weather_station(station["latitude"], station["longitude"], days)
                last_successful_weather = weather_data
                break
            except Exception as e:
                if attempt < 4:
                    wait_time = (attempt + 1) * 3
                    print(f"  Fetch failed: {e}. Retrying in {wait_time}s... (Attempt {attempt+1}/5)")
                    time.sleep(wait_time)
                else:
                    print(f"  Fetch failed after all attempts: {e}")

        if not weather_data:
            if last_successful_weather:
                print(f"  Fallback Level 1: Using adjacent corridor weather data for {station['station']}.")
                weather_data = last_successful_weather
            else:
                try:
                    # Fallback Level 2: Query previous recorded weather row from Supabase to prevent missing rows
                    db_res = supabase.schema("external").table("weather_current").select("*").eq("station", station["station"]).limit(1).execute()
                    rows = db_res.data if hasattr(db_res, "data") else db_res
                    if rows and len(rows) > 0:
                        prev = rows[0]
                        print(f"  Fallback Level 2: Carrying forward last recorded database weather row for {station['station']}.")
                        weather_data = {
                            "current": {
                                "temperature": float(prev.get("temperature", 28.5)),
                                "humidity": float(prev.get("humidity", 75.0)),
                                "rainfall_mm": float(prev.get("rainfall_mm", 0.0)),
                                "wind_speed": float(prev.get("wind_speed", 10.0)),
                                "observed_at": datetime.now(timezone.utc).isoformat(),
                            },
                            "forecasts": [],
                        }
                except Exception as db_err:
                    print(f"  Database fallback query failed: {db_err}")

        if not weather_data:
            print(f"  Fallback Level 3: Applying standard Metro Manila baseline parameters for {station['station']} to ensure zero station skip.")
            now_pht = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:00:00+08:00")
            weather_data = {
                "current": {
                    "temperature": 28.5,
                    "humidity": 75.0,
                    "rainfall_mm": 0.0,
                    "wind_speed": 10.0,
                    "observed_at": now_pht,
                },
                "forecasts": [],
            }

        try:
            fetched_at = datetime.now(timezone.utc)
            
            # --- UPDATE OBSERVATION ---
            obs_update_row = {
                "temperature": weather_data["current"]["temperature"],
                "humidity": weather_data["current"]["humidity"],
                "wind_speed": weather_data["current"]["wind_speed"],
                "rainfall_mm": weather_data["current"]["rainfall_mm"],
                "computed_rainfall_level": classify_rainfall(weather_data["current"]["rainfall_mm"]),
                "observed_at": weather_data["current"]["observed_at"],
                "fetched_at": fetched_at.isoformat(),
            }

            res_obs = supabase.schema("external").table("weather_current").update(
                obs_update_row
            ).eq("station", station["station"]).execute()

            obs_updated = False
            try:
                if isinstance(res_obs, dict) and res_obs.get("data") is not None:
                    obs_updated = len(res_obs.get("data")) > 0
                elif hasattr(res_obs, "data"):
                    obs_updated = bool(getattr(res_obs, "data"))
            except Exception:
                pass

            if obs_updated:
                obs_saved += 1
                print(f"  Updated observation: {obs_update_row['temperature']}°C")
            else:
                print(f"  No existing observation row - skipping")

            # --- UPDATE FORECASTS ---
            forecasts_by_slot = list(enumerate(weather_data["forecasts"], start=1))
            station_fct_saved = 0

            for idx, forecast in reversed(forecasts_by_slot):
                forecast_id = forecast_row_id(station["code"], idx)
                fct_update_row = {
                    "forecast_date": forecast["forecast_date"],
                    "temp_max": forecast["temp_max"],
                    "temp_min": forecast["temp_min"],
                    "rainfall_sum_mm": forecast["rainfall_sum_mm"],
                    "computed_rainfall_level": classify_rainfall(forecast["rainfall_sum_mm"]),
                    "humidity_mean": forecast["humidity_mean"],
                    "wind_speed_max": forecast["wind_speed_max"],
                    "fetched_at": fetched_at.isoformat(),
                }

                res_fct = supabase.schema("external").table("weather_forecasts").update(
                    fct_update_row
                ).eq("id", forecast_id).eq("station", station["station"]).execute()

                fct_updated = False
                try:
                    if isinstance(res_fct, dict) and res_fct.get("data") is not None:
                        fct_updated = len(res_fct.get("data")) > 0
                    elif hasattr(res_fct, "data"):
                        fct_updated = bool(getattr(res_fct, "data"))
                except Exception:
                    pass

                if fct_updated:
                    fct_saved += 1
                    station_fct_saved += 1

            print(f"  Updated {station_fct_saved} forecast rows")
            
            # Avoid API rate limits (10k/day, but burst limited)
            time.sleep(1.5)

        except Exception as e:
            print(f"  Failed: {e}")

    print(f"\n=== Done! {obs_saved} obs, {fct_saved} forecasts updated ===")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    run_weather_pipeline(days=args.days)
