import requests

def fetch_weather_secondary_fallback(lat: float, lon: float) -> dict | None:
    """Secondary fallback weather provider (wttr.in) if Open-Meteo is unreachable."""
    try:
        from datetime import datetime, timezone, timedelta
        url = f"https://wttr.in/{lat},{lon}?format=j1"
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
        curr = data["current_condition"][0]
        now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%dT%H:00:00+08:00")
        
        forecasts = []
        for day in data.get("weather", [])[1:8]:
            forecasts.append({
                "forecast_date": day["date"],
                "temp_max": float(day["maxtempC"]),
                "temp_min": float(day["mintempC"]),
                "rainfall_sum_mm": float(day.get("hourly", [{}])[0].get("precipMM", 0)),
                "humidity_mean": float(curr.get("humidity", 75)),
                "wind_speed_max": float(curr.get("windspeedKmph", 10)),
            })
            
        return {
            "current": {
                "temperature": float(curr["temp_C"]),
                "humidity": float(curr["humidity"]),
                "rainfall_mm": float(curr["precipMM"]),
                "wind_speed": float(curr["windspeedKmph"]),
                "observed_at": now_str,
            },
            "forecasts": forecasts,
        }
    except Exception as e:
        print(f"  Secondary weather API fallback failed: {e}")
        return None

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

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()

        current = data["current"]
        daily = data["daily"]

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
    except Exception as primary_error:
        print(f"  Primary Open-Meteo fetch failed: {primary_error}. Trying secondary API provider...")
        sec_res = fetch_weather_secondary_fallback(lat, lon)
        if sec_res:
            print("  Successfully retrieved weather from secondary provider (wttr.in).")
            return sec_res
        raise primary_error


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