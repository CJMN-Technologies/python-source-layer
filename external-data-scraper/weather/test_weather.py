import requests

lat, lon = 14.6035, 120.9834  # Recto

url = "https://api.open-meteo.com/v1/forecast"
params = {
    "latitude": lat,
    "longitude": lon,
    "current": "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m,weather_code",
    "timezone": "Asia/Manila"
}

response = requests.get(url, params=params)
print(response.json())