import os
import sys
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client
from weather_fetch import fetch_weather_secondary_fallback, fetch_weather_station
from rainfall_classifier import classify_rainfall

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

def load_stations():
    import json
    with open(os.path.join(os.path.dirname(__file__), "stations.json"), "r", encoding="utf-8") as f:
        return json.load(f)

def run_weather_watchdog():
    print("=== LRT-2 Weather Watchdog Backup Pipeline Starting ===")
    now_utc = datetime.now(timezone.utc)
    print(f"Time (UTC): {now_utc.isoformat()}")

    stations = load_stations()
    stale_threshold_minutes = 45.0
    stale_stations = []

    try:
        res = supabase.schema("external").table("weather_current").select("station, fetched_at, observed_at").execute()
        rows = res.data if hasattr(res, "data") else res
        
        station_last_seen = {}
        if rows:
            for row in rows:
                st_name = row.get("station")
                time_str = row.get("fetched_at") or row.get("observed_at")
                if time_str:
                    try:
                        # Clean iso timestamp
                        clean_ts = time_str.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(clean_ts)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        station_last_seen[st_name] = dt
                    except Exception:
                        pass

        for st in stations:
            s_name = st["station"]
            last_dt = station_last_seen.get(s_name)
            if not last_dt:
                stale_stations.append(st)
            else:
                age_minutes = (now_utc - last_dt.astimezone(timezone.utc)).total_seconds() / 60.0
                if age_minutes > stale_threshold_minutes:
                    print(f"  [STALE DETECTED] {s_name}: last updated {age_minutes:.1f} minutes ago.")
                    stale_stations.append(st)

    except Exception as e:
        print(f"  Warning: Watchdog check failed to query database: {e}")
        # On error, treat all stations as needing backup
        stale_stations = stations

    if not stale_stations:
        print("[OK] Watchdog Audit Complete: All station weather data is fresh (updated within 45 minutes). Zero backup action needed.")
        return

    print(f"\n[TRIGGERED] Watchdog Triggered: {len(stale_stations)} station(s) require backup weather ingestion.")

    updated_count = 0
    for station in stale_stations:
        print(f"  Executing backup fetch for: {station['station']}")
        weather_data = None
        
        # Try secondary provider wttr.in first for watchdog backup
        try:
            weather_data = fetch_weather_secondary_fallback(station["latitude"], station["longitude"])
        except Exception as e:
            print(f"    wttr.in fallback failed: {e}")

        # If secondary failed, try primary Open-Meteo
        if not weather_data:
            try:
                weather_data = fetch_weather_station(station["latitude"], station["longitude"])
            except Exception as e:
                print(f"    Open-Meteo fallback failed: {e}")

        if not weather_data:
            print(f"    Applying baseline Manila defaults for {station['station']}")
            now_pht = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:00:00+08:00")
            weather_data = {
                "current": {
                    "temperature": 28.5,
                    "humidity": 75.0,
                    "rainfall_mm": 0.0,
                    "wind_speed": 10.0,
                    "observed_at": now_pht,
                }
            }

        try:
            obs_update_row = {
                "temperature": weather_data["current"]["temperature"],
                "humidity": weather_data["current"]["humidity"],
                "wind_speed": weather_data["current"]["wind_speed"],
                "rainfall_mm": weather_data["current"]["rainfall_mm"],
                "computed_rainfall_level": classify_rainfall(weather_data["current"]["rainfall_mm"]),
                "observed_at": weather_data["current"]["observed_at"],
                "fetched_at": now_utc.isoformat(),
            }

            supabase.schema("external").table("weather_current").update(obs_update_row).eq("station", station["station"]).execute()
            updated_count += 1
            print(f"    >> Watchdog updated {station['station']} current weather.")
        except Exception as err:
            print(f"    Failed to update watchdog row for {station['station']}: {err}")

    print(f"\n=== Watchdog Finished! {updated_count}/{len(stale_stations)} stale stations refreshed ===")

if __name__ == "__main__":
    run_weather_watchdog()
