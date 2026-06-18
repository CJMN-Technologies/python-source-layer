from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
from pipeline import run_forecasts, run_observations


def run_pipeline():
    run_observations()
    run_forecasts()

scheduler = BlockingScheduler(timezone="Asia/Manila")

# Current conditions and 7-day forecasts.
scheduler.add_job(
    run_pipeline,
    "cron",
    minute=0,
    id="weather_station_weather_hourly"
)

print("=== LRT-2 Weather Scheduler Started ===")
print("Weather rows  : hourly at minute 0")
print(f"Current time  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
print("Running initial weather fetch now...")

run_pipeline()

scheduler.start()
