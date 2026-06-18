import json
import os
from datetime import datetime, timezone
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

def run_observations(days: int = 7):
    print("=== LRT-2 Weather Observations Starting ===")
    print(f"Time: {datetime.now(timezone.utc)}")

    stations = load_stations()
    obs_saved = 0

    for station in stations:
        print(f"\nProcessing station: {station['station']}")
        try:
            weather_data = fetch_weather_station(station["latitude"], station["longitude"], days)
            fetched_at = datetime.now(timezone.utc)

            # Update-only for observations: do not insert new rows.
            update_row = {
                "temperature": weather_data["current"]["temperature"],
                "humidity": weather_data["current"]["humidity"],
                "wind_speed": weather_data["current"]["wind_speed"],
                "rainfall_mm": weather_data["current"]["rainfall_mm"],
                "computed_rainfall_level": classify_rainfall(weather_data["current"]["rainfall_mm"]),
                "observed_at": weather_data["current"]["observed_at"],
                "fetched_at": fetched_at.isoformat(),
            }

            res = supabase.schema("external").table("weather_current").update(
                update_row
            ).eq("station", station["station"]).execute()

            updated = False
            try:
                if isinstance(res, dict) and res.get("data") is not None:
                    updated = len(res.get("data")) > 0
                elif hasattr(res, "data"):
                    updated = bool(getattr(res, "data"))
            except Exception:
                updated = False

            if updated:
                obs_saved += 1
                print(f"  Updated observation: {update_row['temperature']}°C, {update_row['rainfall_mm']}mm rain")
            else:
                print(f"  No existing observation row for station {station['station']} - skipping (no insert)")

        except Exception as e:
            print(f"  Failed: {e}")

    print(f"\n=== Done! {obs_saved} observations saved ===")


def run_forecasts(days: int = 7):
    print("=== LRT-2 Weather Forecasts Starting ===")
    print(f"Time: {datetime.now(timezone.utc)}")

    stations = load_stations()
    fct_saved = 0

    for station in stations:
        print(f"\nProcessing station forecasts: {station['station']}")
        try:
            weather_data = fetch_weather_station(station["latitude"], station["longitude"], days)
            fetched_at = datetime.now(timezone.utc)
            station_saved = 0

            forecasts_by_slot = list(enumerate(weather_data["forecasts"], start=1))

            for idx, forecast in reversed(forecasts_by_slot):
                forecast_id = forecast_row_id(station["code"], idx)
                update_row = {
                    "forecast_date": forecast["forecast_date"],
                    "temp_max": forecast["temp_max"],
                    "temp_min": forecast["temp_min"],
                    "rainfall_sum_mm": forecast["rainfall_sum_mm"],
                    "computed_rainfall_level": classify_rainfall(forecast["rainfall_sum_mm"]),
                    "humidity_mean": forecast["humidity_mean"],
                    "wind_speed_max": forecast["wind_speed_max"],
                    "fetched_at": fetched_at.isoformat(),
                }

                res = supabase.schema("external").table("weather_forecasts").update(
                    update_row
                ).eq("id", forecast_id).eq("station", station["station"]).execute()

                # Determine if update affected any rows
                updated = False
                try:
                    if isinstance(res, dict) and res.get("data") is not None:
                        updated = len(res.get("data")) > 0
                    elif hasattr(res, "data"):
                        updated = bool(getattr(res, "data"))
                except Exception:
                    updated = False

                if updated:
                    fct_saved += 1
                    station_saved += 1
                else:
                    print(f"  No existing forecast row for {forecast_id} - skipping (no insert)")

            print(f"  Updated {station_saved} forecast rows for {station['station']}")

        except Exception as e:
            print(f"  Failed: {e}")

    print(f"\n=== Done! {fct_saved} forecasts saved ===")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "observations", "forecasts"], default="all")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()

    if args.mode in ("all", "observations"):
        run_observations(days=args.days)
    if args.mode in ("all", "forecasts"):
        run_forecasts(days=args.days)
