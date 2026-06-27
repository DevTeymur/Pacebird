import os
import json
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import session

from core.cache import (
    _cache_path, _load_cache, _save_cache,
    _load_enrichment, _save_enrichment,
)
from core.helpers import is_demo

STRAVA_API_BASE = "https://www.strava.com/api/v3"

WMO_LABELS = {
    0: ("Clear", "☀️"), 1: ("Mostly clear", "🌤️"), 2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"), 45: ("Foggy", "🌫️"), 48: ("Foggy", "🌫️"),
    51: ("Light drizzle", "🌦️"), 53: ("Drizzle", "🌦️"), 55: ("Heavy drizzle", "🌦️"),
    61: ("Light rain", "🌧️"), 63: ("Rain", "🌧️"), 65: ("Heavy rain", "🌧️"),
    71: ("Light snow", "❄️"), 73: ("Snow", "❄️"), 75: ("Heavy snow", "❄️"),
    80: ("Showers", "🌧️"), 81: ("Showers", "🌧️"), 82: ("Heavy showers", "⛈️"),
    95: ("Thunderstorm", "⛈️"),
}

def wmo_label(code):
    if code is None:
        return ("Unknown", "")
    return WMO_LABELS.get(int(code), ("Unknown", ""))


def fetch_weather(lat, lon, date_str):
    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":  lat, "longitude": lon,
                "start_date": date_str, "end_date": date_str,
                "hourly": "temperature_2m,precipitation,windspeed_10m,weathercode",
                "timezone": "auto",
            },
            timeout=8,
        )
        d = r.json()
        hourly = d.get("hourly", {})
        idx = 12
        return {
            "temp_c":    round(hourly.get("temperature_2m", [None]*13)[idx] or 0, 1),
            "precip_mm": round(hourly.get("precipitation",  [None]*13)[idx] or 0, 1),
            "wind_kph":  round(hourly.get("windspeed_10m",  [None]*13)[idx] or 0, 1),
            "wcode":     hourly.get("weathercode", [None]*13)[idx],
        }
    except Exception:
        return None


def fetch_location(lat, lon):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json"},
            headers={"User-Agent": "Pacebird/1.0"},
            timeout=6,
        )
        d = r.json()
        addr = d.get("address", {})
        city = (addr.get("city") or addr.get("town") or
                addr.get("village") or addr.get("municipality") or "")
        country = addr.get("country_code", "").upper()
        return f"{city}, {country}" if city else country or None
    except Exception:
        return None


def enrich_activities_background(athlete_id, activities):
    enrichment = _load_enrichment(athlete_id)
    runs_with_gps = [
        a for a in activities
        if a.get("sport_type") == "Run"
        and a.get("start_latlng")
        and len(a["start_latlng"]) == 2
        and str(a["id"]) not in enrichment
    ]
    if not runs_with_gps:
        return
    for a in runs_with_gps:
        act_id = str(a["id"])
        lat, lon = a["start_latlng"]
        date_str = a.get("start_date_local", "")[:10]
        if not date_str:
            continue
        entry = {}
        w = fetch_weather(lat, lon, date_str)
        if w:
            entry["weather"] = w
        loc = fetch_location(lat, lon)
        if loc:
            entry["location"] = loc
        time.sleep(1.1)
        if entry:
            enrichment[act_id] = entry
            _save_enrichment(athlete_id, enrichment)


def get_headers():
    return {"Authorization": f"Bearer {session['access_token']}"}


def fetch_activities(force_refresh=False):
    from demo_data import DEMO_ACTIVITIES
    if is_demo():
        return DEMO_ACTIVITIES
    athlete_id = session.get("athlete", {}).get("id")
    t0 = time.time()
    if athlete_id and not force_refresh:
        cached = _load_cache(athlete_id)
        if cached is not None:
            print(f"[fetch] cache hit — {len(cached)} activities in {round(time.time()-t0,2)}s")
            return cached
    # Fetch entire history — no date filter, max page size
    print(f"[fetch] no cache — fetching full history from Strava…")
    activities, page = [], 1
    while True:
        try:
            r = requests.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers=get_headers(),
                params={"per_page": 200, "page": page},
                timeout=15,
            )
        except requests.exceptions.Timeout:
            print(f"[fetch] timeout on page {page}")
            break
        except requests.exceptions.RequestException as e:
            print(f"[fetch] request error: {e}")
            break
        print(f"[fetch] page {page} → HTTP {r.status_code} ({round(time.time()-t0,2)}s)")
        if r.status_code == 429:
            if athlete_id:
                cached = _load_cache(athlete_id)
                if cached is not None:
                    return cached
            raise RuntimeError("rate_limited")
        if r.status_code != 200:
            break
        batch = r.json()
        if not batch or not isinstance(batch, list) or len(batch) == 0:
            break
        activities.extend(batch)
        print(f"[fetch] got {len(batch)} activities (total {len(activities)})")
        if len(batch) < 200:
            break  # last page
        page += 1
    print(f"[fetch] done — {len(activities)} activities in {round(time.time()-t0,2)}s")
    if athlete_id:
        _save_cache(athlete_id, activities)
        t = threading.Thread(
            target=enrich_activities_background,
            args=(athlete_id, activities),
            daemon=True,
        )
        t.start()
    return activities


def _safe_fetch(force_refresh=False):
    from flask import jsonify
    try:
        return fetch_activities(force_refresh=force_refresh), None  # type: ignore
    except RuntimeError as e:
        if "rate_limited" in str(e):
            return None, jsonify({
                "error": "rate_limited",
                "message": "Strava rate limit reached. Try again in 15 minutes.",
            }), 429
        return None, jsonify({"error": str(e)}), 500


def get_activities_and_enrichment(force_refresh=False):
    from demo_data import DEMO_ACTIVITIES, DEMO_ENRICHMENT
    if is_demo():
        return DEMO_ACTIVITIES, DEMO_ENRICHMENT, None
    result = _safe_fetch(force_refresh)
    activities = result[0]
    if activities is None:
        return None, {}, result[1:]
    athlete_id = session.get("athlete", {}).get("id")
    enrichment = _load_enrichment(athlete_id) if athlete_id else {}
    return activities, enrichment, None
