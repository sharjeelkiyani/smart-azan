#!/usr/bin/env python3
"""
Small, dependency-free helpers for the dashboard: Hijri date display, Qibla
bearing, and weather. No API keys needed anywhere here - Hijri/Qibla are pure
math, weather uses Open-Meteo's free keyless API.
"""
import json
import math
import urllib.request

KAABA_LAT = 21.4225
KAABA_LON = 39.8262

_HIJRI_MONTHS = [
    "Muharram", "Safar", "Rabi al-Awwal", "Rabi al-Thani",
    "Jumada al-Awwal", "Jumada al-Thani", "Rajab", "Shaban",
    "Ramadan", "Shawwal", "Dhul Qadah", "Dhul Hijjah",
]


def _gregorian_to_jdn(year, month, day):
    a = (14 - month) // 12
    y = year + 4800 - a
    m = month + 12 * a - 3
    return day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def gregorian_to_hijri(date):
    """Tabular (arithmetic) Hijri calendar - the standard approximation used
    by most software calendars. Typically within a day or two of the
    moon-sighting-based date your local mosque announces."""
    jdn = _gregorian_to_jdn(date.year, date.month, date.day)
    l = jdn - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = ((10985 - l) // 5316) * ((50 * l) // 17719) + (l // 5670) * ((43 * l) // 15238)
    l = l - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    month = (24 * l) // 709
    day = l - (709 * month) // 24
    year = 30 * n + j - 30
    month = max(1, min(12, month))
    return {"year": year, "month": month, "day": day, "month_name": _HIJRI_MONTHS[month - 1]}


def hijri_date_string(date):
    h = gregorian_to_hijri(date)
    return f"{h['day']} {h['month_name']} {h['year']} AH"


def qibla_bearing(lat, lon):
    """Initial great-circle bearing from (lat, lon) to the Kaaba, in degrees
    clockwise from true north."""
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2, lon2 = math.radians(KAABA_LAT), math.radians(KAABA_LON)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def get_weather(lat, lon, timeout=5):
    """Current conditions via Open-Meteo - free, no API key. Returns None on
    any failure (no lat/lon configured, network down, etc.) so callers can
    just hide the weather card rather than error out."""
    if not lat and not lon:
        return None
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code,surface_pressure"
        )
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        cur = data.get("current", {})
        return {
            "temperature_c": cur.get("temperature_2m"),
            "humidity_pct": cur.get("relative_humidity_2m"),
            "wind_kmh": cur.get("wind_speed_10m"),
            "pressure_hpa": cur.get("surface_pressure"),
            "condition": _weather_code_label(cur.get("weather_code")),
        }
    except Exception as e:
        print(f"[Weather] fetch error: {e}")
        return None


_WEATHER_CODES = {
    0: "Clear Sky", 1: "Mainly Clear", 2: "Partly Cloudy", 3: "Overcast",
    45: "Fog", 48: "Fog", 51: "Light Drizzle", 53: "Drizzle", 55: "Dense Drizzle",
    61: "Light Rain", 63: "Rain", 65: "Heavy Rain", 71: "Light Snow", 73: "Snow",
    75: "Heavy Snow", 80: "Rain Showers", 81: "Rain Showers", 82: "Violent Showers",
    95: "Thunderstorm", 96: "Thunderstorm", 99: "Thunderstorm",
}


def _weather_code_label(code):
    return _WEATHER_CODES.get(code, "—")
