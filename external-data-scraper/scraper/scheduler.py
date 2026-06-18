from apscheduler.schedulers.blocking import BlockingScheduler
from datetime import datetime
from pipeline import run_pipeline

scheduler = BlockingScheduler(timezone="Asia/Manila")

# High priority - 5 AM and 3 PM daily
scheduler.add_job(
    run_pipeline,
    "cron",
    hour="5,15",
    minute=0,
    kwargs={"priority": "high"},
    id="high_priority"
)

# Medium priority - midnight daily
scheduler.add_job(
    run_pipeline,
    "cron",
    hour=0,
    minute=0,
    kwargs={"priority": "medium"},
    id="medium_priority"
)

# Low priority - Monday, Wednesday, Friday at 9 AM
scheduler.add_job(
    run_pipeline,
    "cron",
    day_of_week="mon,wed,fri",
    hour=9,
    minute=0,
    kwargs={"priority": "low"},
    id="low_priority"
)

print("=== LRT-2 Scheduler Started ===")
print("High priority  : 5:00 AM and 3:00 PM daily")
print("Medium priority: 12:00 AM daily")
print("Low priority   : Mon, Wed, Fri at 9:00 AM")
print(f"Current time   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("\nRunning initial scrape now...")

run_pipeline(priority="all")

scheduler.start()