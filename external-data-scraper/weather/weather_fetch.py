import requests

def fetch_weather_station(lat: float, lon: float, days: int = 7) -> dict:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,relative_humidity_2m_mean,wind_speed_10m_max",
        "timezone": "Asia/Manila",
        # Request one extra day so that after skipping today's entry
        # we still have `days` forecast entries (tomorrow..tomorrow+days-1).
        "forecast_days": days + 1,
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()

    current = data["current"]
    daily = data["daily"]

    forecasts = []
    # The API returns daily arrays starting with today. We want forecasts
    # for the next `days` days (tomorrow onwards), so skip index 0.
    available = max(0, len(daily["time"]) - 1)
    to_take = min(days, available)
    for i in range(1, 1 + to_take):
        forecasts.append({
            "forecast_date": daily["time"][i],
            "temp_max": daily["temperature_2m_max"][i],
            "temp_min": daily["temperature_2m_min"][i],
            "rainfall_sum_mm": daily["precipitation_sum"][i],
            "humidity_mean": daily["relative_humidity_2m_mean"][i],
            "wind_speed_max": daily["wind_speed_10m_max"][i],
        })

    # Open-Meteo returns time strings in local Manila time (since we passed timezone=Asia/Manila).
    # Append +08:00 timezone offset so PostgreSQL interprets it correctly as PHT instead of UTC.
    observed_time = current["time"]
    if "+" not in observed_time and "Z" not in observed_time:
        observed_time = f"{observed_time}+08:00"

    return {
        "current": {
            "temperature": current["temperature_2m"],
            "humidity": current["relative_humidity_2m"],
            "rainfall_mm": current["precipitation"],
            "wind_speed": current["wind_speed_10m"],
            "observed_at": observed_time,
        },
        "forecasts": forecasts,
    }


def fetch_forecast(lat: float, lon: float, days: int = 7) -> list[dict]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,relative_humidity_2m_mean,wind_speed_10m_max",
        "timezone": "Asia/Manila",
        # Request one extra day so skipping today's daily entry returns
        # the next `days` forecasts (tomorrow onwards).
        "forecast_days": days + 1
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    daily = response.json()["daily"]

    # Skip today's daily entry; return next `days` days
    forecasts = []
    available = max(0, len(daily["time"]) - 1)
    to_take = min(days, available)
    for i in range(1, 1 + to_take):
        forecasts.append({
            "forecast_date": daily["time"][i],
            "temp_max": daily["temperature_2m_max"][i],
            "temp_min": daily["temperature_2m_min"][i],
            "rainfall_sum_mm": daily["precipitation_sum"][i],
            "humidity_mean": daily["relative_humidity_2m_mean"][i],
            "wind_speed_max": daily["wind_speed_10m_max"][i],
        })

    return forecasts