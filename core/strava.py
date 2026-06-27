import os
import time
import threading
import requests
from datetime import datetime, timedelta
from flask import session

from core.cache import (
    _load_cache, _save_cache,
    _load_enrichment, _save_enrichment,
)
from core.helpers import is_demo

STRAVA_API_BASE  = "https://www.strava.com/api/v3"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"

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


# ── WEATHER & LOCATION ────────────────────────────────────────────

def fetch_weather(lat, lon, date_str):
    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":   lat, "longitude": lon,
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


# ── TOKEN MANAGEMENT ─────────────────────────────────────────────

def _refresh_token_if_needed():
    """
    Check if the current session token is expired (or expiring within 5 min).
    If so, use the stored refresh_token to get a new one from Strava,
    update both the DB and the session.
    Returns True if token is valid (after any refresh), False if refresh failed.
    """
    expires_at = session.get("expires_at", 0)
    # If token is still valid for more than 5 minutes, do nothing
    if expires_at and time.time() < expires_at - 300:
        return True

    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return False

    client_id     = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")

    try:
        resp = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     client_id,
            "client_secret": client_secret,
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        }, timeout=10)
        data = resp.json()
    except Exception as e:
        print(f"[token] refresh request failed: {e}")
        return False

    if "access_token" not in data:
        print(f"[token] refresh failed: {data}")
        return False

    # Update session
    session["access_token"]  = data["access_token"]
    session["refresh_token"] = data["refresh_token"]
    session["expires_at"]    = data["expires_at"]

    # Persist to DB so the new refresh_token isn't lost if session expires
    athlete_id = session.get("athlete", {}).get("id")
    if athlete_id:
        from core.db import upsert_token
        upsert_token(
            athlete_id     = athlete_id,
            access_token   = data["access_token"],
            refresh_token  = data["refresh_token"],
            expires_at     = data["expires_at"],
            athlete_obj    = session.get("athlete", {}),
        )

    print(f"[token] refreshed successfully for athlete {athlete_id}")
    return True


def get_headers():
    """Return Authorization header, refreshing token first if needed."""
    _refresh_token_if_needed()
    return {"Authorization": f"Bearer {session['access_token']}"}


# ── ACTIVITY FETCHING ─────────────────────────────────────────────

def _fetch_page(params, t0):
    """Fetch one page from Strava. Returns (batch, status_code) or raises."""
    r = requests.get(
        f"{STRAVA_API_BASE}/athlete/activities",
        headers=get_headers(),
        params=params,
        timeout=15,
    )
    print(f"[fetch] page {params.get('page',1)} → HTTP {r.status_code} ({round(time.time()-t0,2)}s)")
    return r


def fetch_activities(force_refresh=False):
    """
    Return the full activity list for the current session athlete.

    Caching strategy:
    - Cold start (no cache): fetch full history from Strava.
    - Warm (cache exists, no force_refresh): return cached list immediately.
    - force_refresh=True: incremental sync — only fetch activities newer
      than the most recent cached activity date, then merge + save.
      This means a refresh after one workout costs 1 API call, not 7–10.
    """
    from demo_data import DEMO_ACTIVITIES
    if is_demo():
        return DEMO_ACTIVITIES

    athlete_id = session.get("athlete", {}).get("id")
    t0 = time.time()

    cached, last_date = _load_cache(athlete_id) if athlete_id else (None, None)

    # ── WARM PATH: no refresh requested ──────────────────────────
    if cached is not None and not force_refresh:
        print(f"[fetch] cache hit — {len(cached)} activities in {round(time.time()-t0,2)}s")
        return cached

    # ── INCREMENTAL SYNC: cache exists, just get what's new ──────
    if cached is not None and force_refresh and last_date:
        print(f"[fetch] incremental sync since {last_date}…")
        # Convert last_date to Unix timestamp for Strava `after=` param
        after_ts = int(datetime.strptime(last_date, "%Y-%m-%d").timestamp())
        new_activities = []
        page = 1
        while True:
            try:
                r = _fetch_page({"per_page": 200, "page": page, "after": after_ts}, t0)
            except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
                print(f"[fetch] error during incremental sync: {e}")
                break
            if r.status_code == 429:
                print("[fetch] rate limited — returning cached data")
                raise RuntimeError("rate_limited")
            if r.status_code != 200:
                break
            batch = r.json()
            if not batch or not isinstance(batch, list):
                break
            new_activities.extend(batch)
            print(f"[fetch] incremental: got {len(batch)} new (total new: {len(new_activities)})")
            if len(batch) < 200:
                break
            page += 1

        if new_activities:
            # Merge: new activities first (newest first), deduplicate by id
            existing_ids = {a["id"] for a in cached}
            truly_new = [a for a in new_activities if a["id"] not in existing_ids]
            merged = truly_new + cached
            _save_cache(athlete_id, merged)
            print(f"[fetch] incremental done — added {len(truly_new)} new, total {len(merged)}")
            # Enrich only the new ones in background
            if truly_new:
                threading.Thread(
                    target=enrich_activities_background,
                    args=(athlete_id, truly_new),
                    daemon=True,
                ).start()
            return merged
        else:
            print(f"[fetch] incremental done — no new activities")
            return cached

    # ── COLD START: no cache — fetch full history ─────────────────
    print("[fetch] no cache — fetching full history from Strava…")
    activities, page = [], 1
    while True:
        try:
            r = _fetch_page({"per_page": 200, "page": page}, t0)
        except requests.exceptions.Timeout:
            print(f"[fetch] timeout on page {page}")
            break
        except requests.exceptions.RequestException as e:
            print(f"[fetch] request error: {e}")
            break
        if r.status_code == 429:
            if athlete_id:
                cached, _ = _load_cache(athlete_id)
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
            break
        page += 1

    print(f"[fetch] done — {len(activities)} activities in {round(time.time()-t0,2)}s")
    if athlete_id:
        _save_cache(athlete_id, activities)
        threading.Thread(
            target=enrich_activities_background,
            args=(athlete_id, activities),
            daemon=True,
        ).start()
    return activities


def _safe_fetch(force_refresh=False):
    from flask import jsonify
    try:
        return fetch_activities(force_refresh=force_refresh), None
    except RuntimeError as e:
        if "rate_limited" in str(e):
            return None, jsonify({
                "error":   "rate_limited",
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
