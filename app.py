import os
import io
import json
import time
import threading
import requests
from dotenv import load_dotenv
load_dotenv()
from flask import Flask, redirect, request, session, render_template, jsonify, send_file
from datetime import datetime, timedelta
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont

# ─── ACTIVITY CACHE ───────────────────────────────────────────────
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

def _cache_path(athlete_id):
    return os.path.join(CACHE_DIR, f"activities_{athlete_id}.json")

def _load_cache(athlete_id):
    path = _cache_path(athlete_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("activities")
    except Exception:
        return None

def _save_cache(athlete_id, activities):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_cache_path(athlete_id), "w") as f:
            json.dump({"ts": time.time(), "activities": activities}, f)
    except Exception as e:
        print(f"[cache] save failed: {e}")


# ─── ENRICHMENT CACHE ─────────────────────────────────────────────
def _enrich_path(athlete_id):
    return os.path.join(CACHE_DIR, f"enrichment_{athlete_id}.json")

def _load_enrichment(athlete_id):
    try:
        with open(_enrich_path(athlete_id)) as f:
            return json.load(f)
    except Exception:
        return {}

def _save_enrichment(athlete_id, data):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_enrich_path(athlete_id), "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"[cache] enrichment save failed: {e}")


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
            headers={"User-Agent": "StravaStatsDashboard/1.0"},
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

from demo_data import DEMO_ACTIVITIES, DEMO_ENRICHMENT, ATHLETE as DEMO_ATHLETE

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-to-random-string")

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:5000/callback")

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"

def _find_font(*candidates):
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

_BOLD_CANDIDATES = [
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/lato/Lato-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_REGULAR_CANDIDATES = [
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/lato/Lato-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

FONT_BOLD    = _find_font(*_BOLD_CANDIDATES)
FONT_REGULAR = _find_font(*_REGULAR_CANDIDATES)
FONT_BLACK   = FONT_BOLD

def _font(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ─── VO2MAX / FITNESS AGE ─────────────────────────────────────────
VO2_AVG_UPPER_MALE = [
    (24, 41), (34, 39), (44, 36), (54, 33), (64, 29),
]
VO2_AVG_UPPER_FEMALE = [
    (24, 36), (34, 34), (44, 31), (54, 28), (64, 25),
]

import math as _math

def estimate_vo2max(best_5k_speed_ms, best_5k_dist=5000):
    if not best_5k_speed_ms or best_5k_speed_ms <= 0:
        return None
    t_min = best_5k_dist / best_5k_speed_ms / 60.0
    v = best_5k_dist / t_min
    pct = (0.8 + 0.1894393 * _math.exp(-0.012778 * t_min)
               + 0.2989558 * _math.exp(-0.1932605 * t_min))
    vo2_at_v = -4.60 + 0.182258 * v + 0.000104 * v ** 2
    vo2max = vo2_at_v / pct
    return round(max(20, min(85, vo2max)), 1)

def estimate_vo2max_from_time(minutes, dist_m=5000):
    """Estimate VO2max from a user-entered race time in minutes."""
    if minutes <= 0:
        return None
    speed_ms = dist_m / (minutes * 60)
    return estimate_vo2max(speed_ms, dist_m)

def fitness_age(vo2max, actual_age, gender="M"):
    norms = VO2_AVG_UPPER_MALE if gender == "M" else VO2_AVG_UPPER_FEMALE
    for (age_mid, avg_upper) in norms:
        if vo2max <= avg_upper:
            return age_mid
    return norms[0][0]


# ─── DEMO HELPERS ─────────────────────────────────────────────────
def is_demo():
    return session.get("demo_mode") is True

def get_activities_and_enrichment(force_refresh=False):
    if is_demo():
        return DEMO_ACTIVITIES, DEMO_ENRICHMENT, None
    result = _safe_fetch(force_refresh)
    activities = result[0]
    if activities is None:
        return None, {}, result[1:]
    athlete_id = session.get("athlete", {}).get("id")
    enrichment = _load_enrichment(athlete_id) if athlete_id else {}
    return activities, enrichment, None


# ─── AUTH ─────────────────────────────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<circle cx="16" cy="16" r="16" fill="#fc4c02"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="18" fill="white">⚡</text>'
        '</svg>'
    ).encode("utf-8")
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public,max-age=86400"}


@app.route("/api/debug")
def api_debug():
    if "access_token" not in session:
        return jsonify({"error": "not logged in"}), 401
    import time as _time, glob as _glob
    token = session["access_token"]
    after = int((datetime.utcnow() - timedelta(days=730)).timestamp())
    t0 = _time.time()
    try:
        r = requests.get(
            f"{STRAVA_API_BASE}/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 1, "page": 1, "after": after},
            timeout=12,
        )
        elapsed = round(_time.time() - t0, 2)
        limit_15 = r.headers.get("X-RateLimit-Limit", "?").split(",")
        usage_15 = r.headers.get("X-RateLimit-Usage", "?").split(",")
        athlete_id = session.get("athlete", {}).get("id")
        cache_file = _cache_path(athlete_id) if athlete_id else "n/a"
        cache_exists = os.path.exists(cache_file)
        cache_size = os.path.getsize(cache_file) if cache_exists else 0
        return jsonify({
            "status_code":      r.status_code,
            "elapsed_sec":      elapsed,
            "rate_limit_15min": limit_15[0] if limit_15 else "?",
            "rate_used_15min":  usage_15[0] if usage_15 else "?",
            "cache_exists":     cache_exists,
            "cache_size_kb":    round(cache_size / 1024, 1),
            "font_bold":        FONT_BOLD,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/demo")
def demo():
    session.clear()
    session["demo_mode"]  = True
    session["athlete"]    = DEMO_ATHLETE
    session["birth_year"] = "1997"
    return redirect("/dashboard")


@app.route("/")
def index():
    if "access_token" in session or is_demo():
        return redirect("/dashboard")
    return render_template("login.html")


@app.route("/login")
def login():
    params = {
        "client_id":       CLIENT_ID,
        "redirect_uri":    REDIRECT_URI,
        "response_type":   "code",
        "approval_prompt": "auto",
        "scope":           "read,activity:read_all",
    }
    url = STRAVA_AUTH_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    return redirect(url)


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "Authorization failed.", 400
    try:
        resp = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code":          code,
            "grant_type":    "authorization_code",
        }, timeout=10)
        data = resp.json()
    except requests.exceptions.Timeout:
        return "Strava timed out during login. Try again.", 504
    except Exception as e:
        return f"Login error: {e}", 500
    if "access_token" not in data:
        return f"Token error: {data}", 400
    session.clear()
    session["access_token"]  = data["access_token"]
    session["refresh_token"] = data["refresh_token"]
    session["athlete"]       = data.get("athlete", {})
    return redirect("/dashboard")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ─── HELPERS ──────────────────────────────────────────────────────
def get_headers():
    return {"Authorization": f"Bearer {session['access_token']}"}

def fetch_activities(force_refresh=False):
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


def speed_to_pace(speed_ms):
    if not speed_ms or speed_ms == 0:
        return None
    pace_sec = 1000 / speed_ms
    mins = int(pace_sec // 60)
    secs = int(pace_sec % 60)
    return f"{mins}:{secs:02d}"


def seconds_to_time(sec):
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def seconds_to_hms(sec):
    """Return h hrs m min string for display."""
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


# ─── STREAK HELPERS ───────────────────────────────────────────────
def compute_streaks(activities):
    """Return (current_streak, max_streak, active_days, max_rest_days, streak_needs_today)."""
    dates = sorted(set(
        a.get("start_date_local", "")[:10]
        for a in activities
        if a.get("start_date_local", "")[:10]
    ))
    if not dates:
        return 0, 0, 0, 0, False

    active_days = len(dates)
    dt_dates = [datetime.strptime(d, "%Y-%m-%d") for d in dates]

    # Max streak
    max_streak = cur = 1
    for i in range(1, len(dt_dates)):
        if (dt_dates[i] - dt_dates[i-1]).days == 1:
            cur += 1
            max_streak = max(max_streak, cur)
        else:
            cur = 1

    # Current streak — start from today, but if no activity today
    # fall back to yesterday so the streak isn't broken yet mid-day
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_str = today.strftime("%Y-%m-%d")
    date_set = set(dates)

    trained_today = today_str in date_set
    # If no activity yet today, count from yesterday but flag it
    check = today if trained_today else today - timedelta(days=1)

    cur_streak = 0
    for _ in range(365):
        if check.strftime("%Y-%m-%d") in date_set:
            cur_streak += 1
            check -= timedelta(days=1)
        else:
            break

    # streak_needs_today = streak is alive but today not done yet
    streak_needs_today = (cur_streak > 0) and (not trained_today)

    # Max rest gap
    max_rest = 0
    for i in range(1, len(dt_dates)):
        gap = (dt_dates[i] - dt_dates[i-1]).days - 1
        max_rest = max(max_rest, gap)

    return cur_streak, max_streak, active_days, max_rest, streak_needs_today


def compute_stats(activities):
    runs   = [a for a in activities if a.get("sport_type") == "Run"]
    gym    = [a for a in activities if a.get("sport_type") == "WeightTraining"]
    squash = [a for a in activities if a.get("sport_type") == "Squash"]

    total_run_dist = sum(a.get("distance", 0) for a in runs) / 1000
    total_dist_all = sum(a.get("distance", 0) for a in activities) / 1000

    # ── PERSONAL RECORDS ──────────────────────────────────────────
    targets = {"1K": 1000, "5K": 5000, "10K": 10000, "15K": 15000, "HM": 21097, "Marathon": 42195}
    prs = {}
    for label, dist in targets.items():
        candidates = [a for a in runs if a.get("distance", 0) >= dist * 0.97]
        best = None
        for a in candidates:
            spd = a.get("average_speed", 0)
            if spd <= 0:
                continue
            est_time = dist / spd
            if best is None or est_time < best["time"]:
                best = {
                    "time":    est_time,
                    "pace":    speed_to_pace(spd),
                    "date":    a.get("start_date_local", "")[:10],
                    "name":    a.get("name", ""),
                    "label":   label,
                    "display": seconds_to_time(int(est_time)),
                    "speed":   spd,
                }
        if best:
            prs[label] = best

    # ── MONTHLY DISTANCE (all available) ──────────────────────────
    monthly = defaultdict(float)
    for a in runs:
        d = a.get("start_date_local", "")[:7]
        monthly[d] += a.get("distance", 0) / 1000
    monthly_sorted = sorted(monthly.items())
    monthly_labels = [m[0] for m in monthly_sorted]
    monthly_values = [round(m[1], 1) for m in monthly_sorted]

    # ── YEARLY DISTANCE (per year, per month for multi-line) ──────
    yearly_by_month = defaultdict(lambda: defaultdict(float))
    for a in runs:
        d = a.get("start_date_local", "")
        if len(d) >= 7:
            yr = d[:4]
            mo = int(d[5:7])
            yearly_by_month[yr][mo] += a.get("distance", 0) / 1000

    years_sorted = sorted(yearly_by_month.keys())
    yearly_data = {}
    for yr in years_sorted:
        yearly_data[yr] = [round(yearly_by_month[yr].get(m, 0), 1) for m in range(1, 13)]

    # Cumulative per year
    yearly_cumulative = {}
    for yr in years_sorted:
        vals = yearly_data[yr]
        cum = []
        s = 0
        for v in vals:
            s += v
            cum.append(round(s, 1))
        yearly_cumulative[yr] = cum

    # ── PACE TREND ────────────────────────────────────────────────
    pace_runs = [(a.get("start_date_local", "")[:10], a.get("average_speed", 0))
                 for a in sorted(runs, key=lambda x: x.get("start_date_local", ""))
                 if a.get("average_speed", 0) > 0]
    pace_labels = [r[0] for r in pace_runs]
    pace_values = [round(1000 / 60 / r[1], 2) for r in pace_runs]

    # ── DISTANCE DISTRIBUTION ─────────────────────────────────────
    dist_buckets = {"<3km": 0, "3–6km": 0, "6–10km": 0,
                    "10–16km": 0, "16–20km": 0, "20km+": 0}
    for a in runs:
        d = a.get("distance", 0) / 1000
        if d < 3:    dist_buckets["<3km"]    += 1
        elif d < 6:  dist_buckets["3–6km"]   += 1
        elif d < 10: dist_buckets["6–10km"]  += 1
        elif d < 16: dist_buckets["10–16km"] += 1
        elif d < 20: dist_buckets["16–20km"] += 1
        else:        dist_buckets["20km+"]   += 1

    # ── WEEKLY LOAD (last 12 weeks) ───────────────────────────────
    now = datetime.utcnow()
    week_loads = {}
    for a in activities:
        d = a.get("start_date_local", "")
        if not d:
            continue
        try:
            dt = datetime.strptime(d[:10], "%Y-%m-%d")
        except Exception:
            continue
        weeks_ago = (now - dt).days // 7
        if weeks_ago > 11:
            continue
        label = (now - timedelta(weeks=weeks_ago)).strftime("W%b %d")
        effort = a.get("suffer_score") or (a.get("moving_time", 0) / 60 * 0.5)
        week_loads[label] = week_loads.get(label, 0) + effort
    week_labels = sorted(week_loads.keys())
    week_values = [round(week_loads[k]) for k in week_labels]

    # ── TRAINING SWEET SPOT ───────────────────────────────────────
    run_dates = {}
    for a in runs:
        d = a.get("start_date_local", "")[:10]
        spd = a.get("average_speed", 0)
        if d and spd > 0:
            run_dates[d] = {"pace": round(1000 / 60 / spd, 2), "dt": datetime.strptime(d, "%Y-%m-%d")}

    daily_load = defaultdict(float)
    for a in activities:
        d = a.get("start_date_local", "")[:10]
        if d:
            effort = a.get("suffer_score") or (a.get("moving_time", 0) / 60 * 0.5)
            daily_load[d] += effort

    sweet_buckets = {"Low\n(0–30)": [], "Medium\n(30–80)": [],
                     "High\n(80–150)": [], "Very High\n(150+)": []}
    for d, info in run_dates.items():
        prior_load = sum(
            daily_load.get((info["dt"] - timedelta(days=i)).strftime("%Y-%m-%d"), 0)
            for i in range(1, 8)
        )
        if prior_load < 30:    sweet_buckets["Low\n(0–30)"].append(info["pace"])
        elif prior_load < 80:  sweet_buckets["Medium\n(30–80)"].append(info["pace"])
        elif prior_load < 150: sweet_buckets["High\n(80–150)"].append(info["pace"])
        else:                  sweet_buckets["Very High\n(150+)"].append(info["pace"])

    sweet_labels, sweet_values, sweet_counts = [], [], []
    for label, paces in sweet_buckets.items():
        if paces:
            sweet_labels.append(label)
            sweet_values.append(round(sum(paces) / len(paces), 2))
            sweet_counts.append(len(paces))

    sweet_spot_label = None
    if sweet_values:
        valid = [(v, l) for v, l, c in zip(sweet_values, sweet_labels, sweet_counts) if c >= 2]
        if valid:
            sweet_spot_label = min(valid, key=lambda x: x[0])[1]

    # ── TIME OF DAY ───────────────────────────────────────────────
    tod = {"Morning\n(5–10)": [], "Midday\n(10–14)": [],
           "Afternoon\n(14–18)": [], "Evening\n(18–23)": []}
    for a in runs:
        d = a.get("start_date_local", "")
        spd = a.get("average_speed", 0)
        if not d or spd <= 0:
            continue
        try:
            hour = int(d[11:13])
        except Exception:
            continue
        pace = round(1000 / 60 / spd, 2)
        if 5 <= hour < 10:    tod["Morning\n(5–10)"].append(pace)
        elif 10 <= hour < 14: tod["Midday\n(10–14)"].append(pace)
        elif 14 <= hour < 18: tod["Afternoon\n(14–18)"].append(pace)
        elif 18 <= hour < 23: tod["Evening\n(18–23)"].append(pace)

    tod_labels, tod_values = [], []
    for label, paces in tod.items():
        if paces:
            tod_labels.append(label)
            tod_values.append(round(sum(paces) / len(paces), 2))

    # Best day of week for running
    dow_paces = defaultdict(list)
    DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for a in runs:
        d = a.get("start_date_local", "")[:10]
        spd = a.get("average_speed", 0)
        if d and spd > 0:
            try:
                dow = datetime.strptime(d, "%Y-%m-%d").weekday()
                dow_paces[dow].append(round(1000 / 60 / spd, 2))
            except Exception:
                pass
    dow_labels = []
    dow_values = []
    for i in range(7):
        if dow_paces[i]:
            dow_labels.append(DOW_NAMES[i])
            dow_values.append(round(sum(dow_paces[i]) / len(dow_paces[i]), 2))
    best_dow = None
    if dow_values:
        best_idx = dow_values.index(min(dow_values))
        best_dow = dow_labels[best_idx]

    # ── SPORT BREAKDOWN ───────────────────────────────────────────
    sport_counts = defaultdict(int)
    for a in activities:
        sport_counts[a.get("sport_type", "Other")] += 1
    top_sports = sorted(sport_counts.items(), key=lambda x: -x[1])[:6]

    # ── CALORIE BY SPORT ─────────────────────────────────────────
    MET = {"Run": 9.8, "WeightTraining": 5.0, "Squash": 10.0,
           "Ride": 7.5, "Walk": 3.8, "Swim": 8.0, "Hike": 6.0}
    BODY_WEIGHT_KG = 70

    cal_sport = defaultdict(list)
    for a in activities:
        sport = a.get("sport_type", "Other")
        mins  = a.get("moving_time", 0) / 60
        if mins <= 0:
            continue
        cals = a.get("calories") or 0
        if cals <= 0:
            met = MET.get(sport, 5.0)
            cals = met * BODY_WEIGHT_KG * (mins / 60)
        cal_sport[sport].append(cals / mins * 60)

    cal_labels, cal_values = [], []
    for sport, vals in sorted(cal_sport.items(), key=lambda x: -sum(x[1]) / len(x[1]))[:6]:
        cal_labels.append(sport)
        cal_values.append(round(sum(vals) / len(vals)))

    # ── HEATMAP ──────────────────────────────────────────────────
    heatmap = []
    for w in range(51, -1, -1):
        week_start = now - timedelta(weeks=w + 1)
        week_end   = now - timedelta(weeks=w)
        km = sum(a.get("distance", 0) / 1000 for a in runs
                 if a.get("start_date_local", "") and
                 week_start <= datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d") < week_end)
        heatmap.append(round(km, 1))

    # ── PACE IMPROVEMENT ─────────────────────────────────────────
    pace_improvement = None
    if len(pace_values) >= 2:
        pace_improvement = round(pace_values[0] - pace_values[-1], 2)

    # ── RACE PREDICTOR ───────────────────────────────────────────
    predictor = {}
    if "5K" in prs:
        t5 = prs["5K"]["time"]
        for name, dist in [("10K", 10000), ("HM", 21097), ("Marathon", 42195)]:
            pred = t5 * (dist / 5000) ** 1.06
            predictor[name] = seconds_to_time(int(pred))

    # ── FITNESS AGE ──────────────────────────────────────────────
    fitness_age_data = None
    athlete = session.get("athlete", {})
    birth_year = session.get("birth_year") or athlete.get("birth_year") or athlete.get("birthdate", "")[:4]
    gender = "M" if athlete.get("sex", "M") == "M" else "F"
    if birth_year:
        try:
            actual_age = datetime.utcnow().year - int(birth_year)
            best_speed = max((a.get("average_speed", 0) for a in runs
                              if a.get("distance", 0) >= 4500), default=0)
            vo2 = estimate_vo2max(best_speed)
            if vo2:
                fit_age = fitness_age(vo2, actual_age, gender)
                fitness_age_data = {
                    "actual_age":  actual_age,
                    "fitness_age": fit_age,
                    "vo2max":      vo2,
                    "difference":  actual_age - fit_age,
                }
        except Exception:
            pass

    longest_run = round(max((a.get("distance", 0) for a in runs), default=0) / 1000, 1)

    # ── DATA RANGE ───────────────────────────────────────────────
    all_dates = sorted(
        a.get("start_date_local", "")[:10]
        for a in activities
        if a.get("start_date_local", "")[:10]
    )
    data_range = {
        "first": all_dates[0] if all_dates else None,
        "last":  all_dates[-1] if all_dates else None,
        "total": len(activities),
    }

    # ── STREAKS & TIME STATS ─────────────────────────────────────
    cur_streak, max_streak, active_days, max_rest, streak_needs_today = compute_streaks(activities)

    # Total moving time
    total_moving_sec = sum(a.get("moving_time", 0) for a in activities)
    total_elapsed_sec = sum(a.get("elapsed_time", 0) for a in activities)
    efficiency = round(total_moving_sec / total_elapsed_sec * 100, 1) if total_elapsed_sec > 0 else 0

    # Max single-activity moving time
    max_moving = max((a.get("moving_time", 0) for a in activities), default=0)
    max_moving_name = next(
        (a.get("name", "") for a in activities if a.get("moving_time", 0) == max_moving), ""
    )

    # ── ELEVATION STATS ──────────────────────────────────────────
    total_elev = sum(a.get("total_elevation_gain", 0) for a in activities)
    max_elev = max((a.get("total_elevation_gain", 0) for a in activities), default=0)
    max_elev_name = next(
        (a.get("name", "") for a in activities if a.get("total_elevation_gain", 0) == max_elev), ""
    )
    everest_climbs = round(total_elev / 8849, 2)
    climb_rate = round(total_elev / total_dist_all, 1) if total_dist_all > 0 else 0

    # ── GENERAL STATS ────────────────────────────────────────────
    total_activities = len(activities)
    avg_dist = round(total_dist_all / total_activities, 2) if total_activities > 0 else 0
    max_dist = max((a.get("distance", 0) for a in activities), default=0) / 1000
    max_dist_name = next(
        (a.get("name", "") for a in activities
         if a.get("distance", 0) / 1000 == max_dist), ""
    )
    trips_world = round(total_dist_all / 40075, 3)
    trips_moon  = round(total_dist_all / 384400, 3)

    # Distance stats this year / rolling
    this_year = now.year
    this_month = now.month
    year_start = datetime(this_year, 1, 1)
    month_start = datetime(this_year, this_month, 1)
    rolling_year_start = now - timedelta(days=365)
    rolling_month_start = now - timedelta(days=30)
    week_start_dt = now - timedelta(days=now.weekday())

    def dist_in_range(start_dt, end_dt=None):
        total = 0
        for a in runs:
            d = a.get("start_date_local", "")[:10]
            if not d:
                continue
            try:
                dt = datetime.strptime(d, "%Y-%m-%d")
            except Exception:
                continue
            if dt >= start_dt and (end_dt is None or dt < end_dt):
                total += a.get("distance", 0) / 1000
        return round(total, 1)

    dist_this_year    = dist_in_range(year_start)
    dist_rolling_year = dist_in_range(rolling_year_start)
    dist_this_month   = dist_in_range(month_start)
    dist_rolling_month = dist_in_range(rolling_month_start)
    dist_this_week    = dist_in_range(week_start_dt)

    weeks_in_year = max(1, (now - year_start).days // 7)
    weeks_rolling = 52
    dist_year_weekly_avg    = round(dist_this_year / weeks_in_year, 1)
    dist_rolling_weekly_avg = round(dist_rolling_year / weeks_rolling, 1)

    # ── ACHIEVEMENTS ─────────────────────────────────────────────
    achievements = compute_achievements(runs, activities, prs)

    # ── BEST MONTH ───────────────────────────────────────────────
    best_month_label = None
    best_month_km = 0
    if monthly_sorted:
        best_month_item = max(monthly_sorted, key=lambda x: x[1])
        best_month_label = best_month_item[0]
        best_month_km = round(best_month_item[1], 1)

    # ── MOST ACTIVE SPORT ────────────────────────────────────────
    most_active_sport = top_sports[0][0] if top_sports else None

    # ── WEEKLY SUMMARY (this week vs last week) ───────────────────
    week_start_mon = now - timedelta(days=now.weekday())
    last_week_start = week_start_mon - timedelta(weeks=1)

    def week_summary(start_dt, activities_list):
        end_dt = start_dt + timedelta(weeks=1)
        week_acts = [a for a in activities_list
                     if a.get("start_date_local", "")[:10] and
                     start_dt <= datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d") < end_dt]
        return {
            "sessions": len(week_acts),
            "km": round(sum(a.get("distance", 0) for a in week_acts) / 1000, 1),
            "time_sec": sum(a.get("moving_time", 0) for a in week_acts),
        }

    this_week_summary = week_summary(week_start_mon, activities)
    last_week_summary = week_summary(last_week_start, activities)

    # ── RECENT ACTIVITIES (last 8) ───────────────────────────────
    recent = []
    for a in sorted(activities, key=lambda x: x.get("start_date_local", ""), reverse=True)[:8]:
        spd = a.get("average_speed", 0)
        recent.append({
            "id":     a.get("id"),
            "name":   a.get("name", ""),
            "sport":  a.get("sport_type", ""),
            "date":   a.get("start_date_local", "")[:10],
            "dist_km": round(a.get("distance", 0) / 1000, 1),
            "time":   seconds_to_time(a.get("moving_time", 0)) if a.get("moving_time") else "—",
            "pace":   speed_to_pace(spd) if spd > 0 and a.get("sport_type") == "Run" else None,
            "speed_kmh": round(spd * 3.6, 1) if spd > 0 else None,
        })

    # ── CYCLING DATA ─────────────────────────────────────────────
    rides = [a for a in activities if a.get("sport_type") == "Ride"]
    ride_monthly = defaultdict(float)
    for a in rides:
        d = a.get("start_date_local", "")[:7]
        ride_monthly[d] += a.get("distance", 0) / 1000
    ride_monthly_sorted = sorted(ride_monthly.items())

    ride_speed_runs = [(a.get("start_date_local", "")[:10], a.get("average_speed", 0) * 3.6)
                       for a in sorted(rides, key=lambda x: x.get("start_date_local", ""))
                       if a.get("average_speed", 0) > 0]

    # Cycling PRs (longest rides)
    ride_prs = {}
    for label, dist in [("10K", 10000), ("20K", 20000), ("50K", 50000), ("100K", 100000)]:
        candidates = [a for a in rides if a.get("distance", 0) >= dist * 0.97]
        best = None
        for a in candidates:
            spd = a.get("average_speed", 0)
            if spd <= 0:
                continue
            est_time = dist / spd
            if best is None or est_time < best["time"]:
                best = {
                    "time":    est_time,
                    "display": seconds_to_time(int(est_time)),
                    "speed":   round(spd * 3.6, 1),
                    "date":    a.get("start_date_local", "")[:10],
                    "name":    a.get("name", ""),
                }
        if best:
            ride_prs[label] = best

    # Cycling yearly
    ride_yearly_by_month = defaultdict(lambda: defaultdict(float))
    for a in rides:
        d = a.get("start_date_local", "")
        if len(d) >= 7:
            yr, mo = d[:4], int(d[5:7])
            ride_yearly_by_month[yr][mo] += a.get("distance", 0) / 1000
    ride_years = sorted(ride_yearly_by_month.keys())
    ride_yearly_data = {yr: [round(ride_yearly_by_month[yr].get(m, 0), 1) for m in range(1, 13)] for yr in ride_years}

    # Swimming data
    swims = [a for a in activities if a.get("sport_type") == "Swim"]
    swim_monthly = defaultdict(float)
    for a in swims:
        d = a.get("start_date_local", "")[:7]
        swim_monthly[d] += a.get("distance", 0) / 1000
    swim_monthly_sorted = sorted(swim_monthly.items())

    # ── CARD DATA ────────────────────────────────────────────────
    card_data = {
        "name":         f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip(),
        "total_runs":   len(runs),
        "total_km":     round(total_run_dist, 1),
        "longest_run":  longest_run,
        "best_5k":      prs.get("5K", {}).get("display", "—"),
        "best_hm":      prs.get("HM", {}).get("display", "—"),
        "fitness_age":  fitness_age_data.get("fitness_age") if fitness_age_data else None,
        "pace_improve": pace_improvement,
    }

    return {
        # Overview
        "total_runs":       len(runs),
        "total_run_km":     round(total_run_dist, 1),
        "total_gym":        len(gym),
        "total_squash":     len(squash),
        "pace_improvement": pace_improvement,
        "prs":              prs,
        # Charts
        "monthly_labels":   monthly_labels,
        "monthly_values":   monthly_values,
        "pace_labels":      pace_labels,
        "pace_values":      pace_values,
        "dist_buckets":     dist_buckets,
        "week_labels":      week_labels,
        "week_values":      week_values,
        "tod_labels":       tod_labels,
        "tod_values":       tod_values,
        "sport_counts":     [{"sport": s, "count": c} for s, c in top_sports],
        "cal_labels":       cal_labels,
        "cal_values":       cal_values,
        "heatmap":          heatmap,
        "predictor":        predictor,
        "longest_run":      longest_run,
        "fitness_age":      fitness_age_data,
        "sweet_labels":     sweet_labels,
        "sweet_values":     sweet_values,
        "sweet_counts":     sweet_counts,
        "sweet_spot":       sweet_spot_label,
        "card_data":        card_data,
        # Yearly
        "yearly_data":      yearly_data,
        "yearly_cumulative": yearly_cumulative,
        "years":            years_sorted,
        # Insights extras
        "dow_labels":       dow_labels,
        "dow_values":       dow_values,
        "best_dow":         best_dow,
        "best_month":       best_month_label,
        "best_month_km":    best_month_km,
        "most_active_sport": most_active_sport,
        # Stats tab
        "stats": {
            "total_activities":    total_activities,
            "total_dist_km":       round(total_dist_all, 1),
            "avg_dist_km":         avg_dist,
            "max_dist_km":         round(max_dist, 1),
            "max_dist_name":       max_dist_name,
            "trips_world":         trips_world,
            "trips_moon":          trips_moon,
            "dist_this_year":      dist_this_year,
            "dist_rolling_year":   dist_rolling_year,
            "dist_this_month":     dist_this_month,
            "dist_rolling_month":  dist_rolling_month,
            "dist_this_week":      dist_this_week,
            "dist_year_weekly_avg": dist_year_weekly_avg,
            "dist_rolling_weekly_avg": dist_rolling_weekly_avg,
            "total_moving_sec":    total_moving_sec,
            "total_elapsed_sec":   total_elapsed_sec,
            "efficiency_pct":      efficiency,
            "max_moving_sec":      max_moving,
            "max_moving_name":     max_moving_name,
            "active_days":         active_days,
            "current_streak":      cur_streak,
            "streak_needs_today":  streak_needs_today,
            "max_streak":          max_streak,
            "max_rest_days":       max_rest,
            "total_elev_m":        round(total_elev),
            "max_elev_m":          round(max_elev),
            "max_elev_name":       max_elev_name,
            "everest_climbs":      everest_climbs,
            "climb_rate_m_per_km": climb_rate,
        },
        # Achievements
        "achievements": achievements,
        # Overview extras
        "recent_activities": recent,
        "this_week":  this_week_summary,
        "last_week":  last_week_summary,
        # Cycling
        "total_rides":          len(rides),
        "total_ride_km":        round(sum(a.get("distance", 0) for a in rides) / 1000, 1),
        "ride_monthly_labels":  [m[0] for m in ride_monthly_sorted],
        "ride_monthly_values":  [round(m[1], 1) for m in ride_monthly_sorted],
        "ride_speed_labels":    [r[0] for r in ride_speed_runs],
        "ride_speed_values":    [round(r[1], 1) for r in ride_speed_runs],
        "ride_prs":             ride_prs,
        "ride_years":           ride_years,
        "ride_yearly_data":     ride_yearly_data,
        # Swimming
        "total_swims":          len(swims),
        "swim_monthly_labels":  [m[0] for m in swim_monthly_sorted],
        "swim_monthly_values":  [round(m[1], 1) for m in swim_monthly_sorted],
        # Data range
        "data_range": data_range,
        # Athlete profile pic
        "profile_pic": athlete.get("profile") or athlete.get("profile_medium") or "",
        "profile_city": athlete.get("city", ""),
        "profile_country": athlete.get("country", ""),
        "profile_followers": athlete.get("follower_count", "—"),
        "profile_following": athlete.get("friend_count", "—"),
    }


def compute_achievements(runs, all_activities, prs):
    """Return list of achievement dicts with unlocked/locked status."""
    rides = [a for a in all_activities if a.get("sport_type") == "Ride"]
    swims = [a for a in all_activities if a.get("sport_type") == "Swim"]

    # Sorted tuples (dist_m, date, speed_m_s) for runs, rides, swims
    def sorted_acts(acts):
        return sorted(
            [(a.get("distance", 0), a.get("start_date_local", "")[:10], a.get("average_speed", 0))
             for a in acts if a.get("start_date_local")],
            key=lambda x: x[1]
        )

    run_sorted   = sorted_acts(runs)
    ride_sorted  = sorted_acts(rides)
    swim_sorted  = sorted_acts(swims)

    def first_over(acts_sorted, dist_m):
        for d, date, spd in acts_sorted:
            if d >= dist_m:
                return date, round(d / 1000, 2)
        return None, None

    def best_pace_under(pace_min_km):
        best = None
        for d, date, spd in run_sorted:
            if spd > 0:
                p = 1000 / 60 / spd
                if p <= pace_min_km and d >= 1000:
                    if best is None or date < best[0]:
                        best = (date, round(p, 2))
        return best if best else (None, None)

    def cumulative_milestone_dates(acts_sorted, targets):
        cumulative = 0
        dates = {}
        for d, date, _ in acts_sorted:
            cumulative += d / 1000
            for target in targets:
                if target not in dates and cumulative >= target:
                    dates[target] = date
        return dates

    achievements = []

    # ── RUNNING distance milestones ───────────────────────────────
    run_dist_milestones = [
        ("First Run",     0.1,    "🏃", "Ran your first ever activity"),
        ("1K Club",       1.0,    "1️⃣", "Completed a 1 km run"),
        ("5K Finisher",   5.0,    "5️⃣", "Ran 5 km"),
        ("10K Finisher",  10.0,   "🔟", "Ran 10 km"),
        ("Half Marathon", 21.097, "🥈", "Ran 21.1 km — half marathon distance"),
        ("Full Marathon", 42.195, "🏅", "Ran 42.195 km — marathon distance"),
        ("50K Ultra",     50.0,   "🦁", "Ran 50 km ultramarathon distance"),
        ("100K Ultra",    100.0,  "💎", "Ran 100 km — elite ultramarathon"),
    ]
    for name, dist_km, icon, desc in run_dist_milestones:
        date, actual = first_over(run_sorted, dist_km * 1000)
        achievements.append({
            "id": name, "icon": icon, "name": name, "desc": desc,
            "category": "running",
            "unlocked": date is not None,
            "date": date,
            "value": f"{actual} km" if actual else None,
        })

    # ── RUNNING cumulative total ───────────────────────────────────
    run_cum_dates = cumulative_milestone_dates(run_sorted, [100, 500, 1000, 2000, 5000])
    for target, icon, desc in [
        (100,  "💯", "Ran a total of 100 km"),
        (500,  "🚀", "Ran a total of 500 km"),
        (1000, "🌍", "Ran a total of 1,000 km"),
        (2000, "⭐", "Ran a total of 2,000 km"),
        (5000, "🌙", "Ran 5,000 km total"),
    ]:
        date = run_cum_dates.get(target)
        achievements.append({
            "id": f"run_total_{target}km", "icon": icon,
            "name": f"{target} km Running Total", "desc": desc,
            "category": "total_distance",
            "unlocked": date is not None,
            "date": date,
            "value": f"{target} km reached",
        })

    # ── PACE milestones ───────────────────────────────────────────
    pace_milestones = [
        ("Sub 7:00 /km", 7.0, "🐢", "Ran at sub 7:00 min/km"),
        ("Sub 6:00 /km", 6.0, "🏃", "Ran at sub 6:00 min/km"),
        ("Sub 5:30 /km", 5.5, "⚡", "Ran at sub 5:30 min/km"),
        ("Sub 5:00 /km", 5.0, "🔥", "Ran at sub 5:00 min/km"),
        ("Sub 4:30 /km", 4.5, "💨", "Ran at sub 4:30 min/km — very fast"),
        ("Sub 4:00 /km", 4.0, "🚀", "Ran at sub 4:00 min/km — elite pace"),
    ]
    for name, pace, icon, desc in pace_milestones:
        date, val = best_pace_under(pace)
        achievements.append({
            "id": name, "icon": icon, "name": name, "desc": desc,
            "category": "pace",
            "unlocked": date is not None,
            "date": date,
            "value": f"{val} min/km" if val else None,
        })

    # ── CYCLING distance milestones ───────────────────────────────
    ride_dist_milestones = [
        ("First Ride",      0.1,   "🚴", "Completed your first cycling activity"),
        ("10K Ride",        10.0,  "🔟", "Cycled 10 km in one ride"),
        ("50K Ride",        50.0,  "🚵", "Cycled 50 km in one ride"),
        ("100K Ride",       100.0, "💯", "Cycled 100 km — a century ride"),
        ("200K Ride",       200.0, "🏆", "Cycled 200 km in one ride — epic"),
    ]
    for name, dist_km, icon, desc in ride_dist_milestones:
        date, actual = first_over(ride_sorted, dist_km * 1000)
        achievements.append({
            "id": f"ride_{name}", "icon": icon, "name": name, "desc": desc,
            "category": "cycling",
            "unlocked": date is not None,
            "date": date,
            "value": f"{actual} km" if actual else None,
        })

    # ── CYCLING cumulative total ───────────────────────────────────
    ride_cum_dates = cumulative_milestone_dates(ride_sorted, [100, 500, 1000, 5000])
    for target, icon, desc in [
        (100,  "🥉", "Cycled 100 km total"),
        (500,  "🥈", "Cycled 500 km total"),
        (1000, "🥇", "Cycled 1,000 km total"),
        (5000, "🌍", "Cycled 5,000 km total — incredible"),
    ]:
        date = ride_cum_dates.get(target)
        achievements.append({
            "id": f"ride_total_{target}km", "icon": icon,
            "name": f"{target} km Cycling Total", "desc": desc,
            "category": "cycling",
            "unlocked": date is not None,
            "date": date,
            "value": f"{target} km reached",
        })

    # ── SWIMMING milestones ───────────────────────────────────────
    swim_dist_milestones = [
        ("First Swim",    0.1,  "🏊", "Logged your first swimming activity"),
        ("500m Swim",     0.5,  "💧", "Swam 500 m in one session"),
        ("1K Swim",       1.0,  "🌊", "Swam 1,000 m in one session"),
        ("2K Swim",       2.0,  "🐟", "Swam 2,000 m in one session"),
        ("5K Open Water", 5.0,  "🌊", "Swam 5 km in one session"),
        ("10K Swim Total",10.0, "💯", "Swam 10 km total — serious swimmer"),
    ]
    swim_cum = 0
    swim_cum_10k_date = None
    for d, date, _ in swim_sorted:
        swim_cum += d / 1000
        if swim_cum_10k_date is None and swim_cum >= 10:
            swim_cum_10k_date = date

    for i, (name, dist_km, icon, desc) in enumerate(swim_dist_milestones):
        if name == "10K Swim Total":
            # cumulative milestone
            achievements.append({
                "id": f"swim_{name}", "icon": icon, "name": name, "desc": desc,
                "category": "swimming",
                "unlocked": swim_cum_10k_date is not None,
                "date": swim_cum_10k_date,
                "value": f"{round(swim_cum, 1)} km total" if swim_cum > 0 else None,
            })
        else:
            date, actual = first_over(swim_sorted, dist_km * 1000)
            achievements.append({
                "id": f"swim_{name}", "icon": icon, "name": name, "desc": desc,
                "category": "swimming",
                "unlocked": date is not None,
                "date": date,
                "value": f"{round(actual * 1000)} m" if actual else None,
            })

    # ── DURATION milestones ───────────────────────────────────────
    max_single_sec = max((a.get("moving_time", 0) for a in all_activities), default=0)
    dur_milestones = [
        ("30 Min Session", 1800,  "⏱️", "Trained for 30+ minutes in one session"),
        ("1 Hour Session", 3600,  "🕐", "Trained for 1+ hour in one session"),
        ("2 Hour Session", 7200,  "🕑", "Trained for 2+ hours in one session"),
        ("3 Hour Session", 10800, "🕒", "Trained for 3+ hours in one session"),
        ("5 Hour Session", 18000, "🦸", "Trained for 5+ hours in one session"),
    ]
    for name, secs, icon, desc in dur_milestones:
        unlocked = max_single_sec >= secs
        date = None
        if unlocked:
            for a in sorted(all_activities, key=lambda x: x.get("start_date_local", "")):
                if a.get("moving_time", 0) >= secs:
                    date = a.get("start_date_local", "")[:10]
                    break
        achievements.append({
            "id": name, "icon": icon, "name": name, "desc": desc,
            "category": "duration",
            "unlocked": unlocked,
            "date": date,
            "value": seconds_to_hms(max_single_sec) if unlocked else None,
        })

    # ── STREAK milestones ─────────────────────────────────────────
    _, max_streak, active_days, _, _ = compute_streaks(all_activities)
    streak_milestones = [
        ("7-Day Streak",   7,   "🔥", "Trained 7 days in a row"),
        ("30-Day Streak",  30,  "🌟", "Trained 30 days in a row"),
        ("50-Day Streak",  50,  "👑", "Trained 50 days in a row"),
        ("100-Day Streak", 100, "💎", "Trained 100 days in a row — incredible"),
    ]
    for name, days, icon, desc in streak_milestones:
        achievements.append({
            "id": name, "icon": icon, "name": name, "desc": desc,
            "category": "streak",
            "unlocked": max_streak >= days,
            "date": None,
            "value": f"Best streak: {max_streak} days",
        })

    return achievements


# ─── SHAREABLE CARD ───────────────────────────────────────────────
def generate_card(card_data):
    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), "#1a1a2e")
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(26 + (y / H) * 10)
        g = int(26 + (y / H) * 5)
        b = int(46 + (y / H) * 20)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    draw.rectangle([0, 0, W, 8], fill="#fc4c02")
    draw.ellipse([W - 280, -120, W + 80, 260], outline="#fc4c02", width=3)
    draw.ellipse([W - 240, -80, W + 40, 220], outline=(252, 76, 2, 60), width=1)
    f_huge   = _font(FONT_BLACK,   160)
    f_big    = _font(FONT_BLACK,    96)
    f_med    = _font(FONT_BOLD,     52)
    f_small  = _font(FONT_BOLD,     40)
    f_tiny   = _font(FONT_BOLD,     32)
    f_label  = _font(FONT_REGULAR,  28)
    draw.text((60, 36),  "STRAVA STATS", font=f_label, fill="#fc4c02")
    name = card_data.get("name", "Athlete")
    draw.text((60, 76),  name, font=f_big, fill="#ffffff")
    draw.text((60, 190), f"Running Report  ·  {datetime.utcnow().strftime('%B %Y')}", font=f_tiny, fill="#666688")
    draw.rectangle([60, 248, W - 60, 252], fill="#333355")
    stats = [
        ("TOTAL RUNS",     str(card_data.get("total_runs", "—"))),
        ("TOTAL DISTANCE", f"{card_data.get('total_km', '—')} km"),
        ("LONGEST RUN",    f"{card_data.get('longest_run', '—')} km"),
    ]
    block_w = (W - 120) // 3
    for i, (label, value) in enumerate(stats):
        x = 60 + i * block_w
        draw.text((x, 268), label, font=f_label, fill="#666688")
        draw.text((x, 306), value, font=f_med,   fill="#ffffff")
    draw.text((60, 430), "PERSONAL BESTS", font=f_tiny, fill="#fc4c02")
    draw.rectangle([60, 474, W - 60, 478], fill="#fc4c02")
    pr_items = [
        ("5K",           card_data.get("best_5k", "—")),
        ("HALF MARATHON", card_data.get("best_hm", "—")),
    ]
    for i, (dist, t) in enumerate(pr_items):
        x = 60 + i * 500
        draw.text((x, 492), dist, font=f_label, fill="#666688")
        draw.text((x, 530), t, font=f_huge, fill="#ffffff")
    y_fit = 730
    fit_age = card_data.get("fitness_age")
    if fit_age:
        draw.text((60, y_fit),       "FITNESS AGE",  font=f_tiny,  fill="#fc4c02")
        draw.text((60, y_fit + 48),  str(fit_age),   font=f_big,   fill="#ffffff")
        draw.text((60, y_fit + 152), "years young",  font=f_small, fill="#666688")
    else:
        draw.text((60, y_fit),       "FITNESS AGE",    font=f_tiny,  fill="#fc4c02")
        draw.text((60, y_fit + 48),  "Add birth year", font=f_small, fill="#555577")
    pace_imp = card_data.get("pace_improve")
    if pace_imp and pace_imp > 0:
        draw.text((530, y_fit),       "PACE GAINED",        font=f_tiny,  fill="#fc4c02")
        draw.text((530, y_fit + 48),  f"+{pace_imp}",       font=f_big,   fill="#06d6a0")
        draw.text((530, y_fit + 152), "min/km improvement", font=f_small, fill="#666688")
    draw.rectangle([0, H - 90, W, H], fill="#fc4c02")
    draw.text((60,      H - 62), "stravastats.app",   font=f_small, fill="#ffffff")
    draw.text((W - 380, H - 62), "Share your stats!", font=f_small, fill="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf


# ─── ROUTES ───────────────────────────────────────────────────────

@app.route("/dashboard")
def dashboard():
    if "access_token" not in session and not is_demo():
        return redirect("/")
    athlete = session.get("athlete", {})
    return render_template("dashboard.html", athlete=athlete, demo=is_demo())


@app.route("/api/stats")
def api_stats():
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401
    birth_year = request.args.get("birth_year")
    gender = request.args.get("gender")
    five_k_time = request.args.get("five_k_time")  # "mm:ss" or total minutes as float

    if birth_year:
        session["birth_year"] = birth_year
    if gender:
        session["gender_override"] = gender
    if five_k_time:
        session["five_k_override"] = five_k_time

    force_refresh = request.args.get("refresh") == "1"
    activities, enrichment, err = get_activities_and_enrichment(force_refresh)
    if activities is None:
        return err[0] if len(err) == 2 else (err[0], err[1])
    stats = compute_stats(activities)

    # If user supplied a manual 5K time, recompute fitness age with it
    five_k_override = session.get("five_k_override")
    gender_override = session.get("gender_override")
    if five_k_override and stats.get("fitness_age") is not None or five_k_override:
        try:
            parts = str(five_k_override).split(":")
            if len(parts) == 2:
                mins_total = int(parts[0]) + int(parts[1]) / 60
            else:
                mins_total = float(five_k_override)
            vo2 = estimate_vo2max_from_time(mins_total)
            athlete = session.get("athlete", {})
            birth_yr = session.get("birth_year")
            g = gender_override or ("M" if athlete.get("sex", "M") == "M" else "F")
            if vo2 and birth_yr:
                actual_age = datetime.utcnow().year - int(birth_yr)
                fit_age = fitness_age(vo2, actual_age, g)
                stats["fitness_age"] = {
                    "actual_age":  actual_age,
                    "fitness_age": fit_age,
                    "vo2max":      vo2,
                    "difference":  actual_age - fit_age,
                }
        except Exception:
            pass

    return jsonify(stats)


def _safe_fetch(force_refresh=False):
    try:
        return fetch_activities(force_refresh=force_refresh), None  # type: ignore
    except RuntimeError as e:
        if "rate_limited" in str(e):
            return None, jsonify({
                "error": "rate_limited",
                "message": "Strava rate limit reached. Try again in 15 minutes.",
            }), 429
        return None, jsonify({"error": str(e)}), 500


@app.route("/api/enrichment")
def api_enrichment():
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401
    activities, enrichment, err = get_activities_and_enrichment()
    if activities is None:
        return jsonify({"total": 0, "done": 0, "complete": True, "enrichment": {}})
    runs_with_gps = [a for a in activities
                     if a.get("sport_type") == "Run" and a.get("start_latlng")]
    total = len(runs_with_gps)
    done  = sum(1 for a in runs_with_gps if str(a["id"]) in enrichment)
    return jsonify({
        "total":      total,
        "done":       done,
        "complete":   done >= total,
        "enrichment": enrichment,
    })


@app.route("/api/weather-insights")
def api_weather_insights():
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401
    activities, enrichment, err = get_activities_and_enrichment()
    if activities is None:
        return jsonify({"temp_labels": [], "temp_values": [], "cond_labels": [], "cond_values": [], "runs_with_weather": 0})
    runs = [a for a in activities if a.get("sport_type") == "Run"]

    temp_buckets = {
        "<5°C": [], "5–10°C": [], "10–15°C": [],
        "15–20°C": [], "20–25°C": [], "25°C+": [],
    }
    cond_buckets = defaultdict(list)

    for a in runs:
        spd = a.get("average_speed", 0)
        if spd <= 0:
            continue
        pace = round(1000 / 60 / spd, 2)
        e = enrichment.get(str(a["id"]), {})
        w = e.get("weather")
        if not w:
            continue
        t = w["temp_c"]
        if   t <  5: temp_buckets["<5°C"].append(pace)
        elif t < 10: temp_buckets["5–10°C"].append(pace)
        elif t < 15: temp_buckets["10–15°C"].append(pace)
        elif t < 20: temp_buckets["15–20°C"].append(pace)
        elif t < 25: temp_buckets["20–25°C"].append(pace)
        else:        temp_buckets["25°C+"].append(pace)
        label, _ = wmo_label(w.get("wcode"))
        cond_buckets[label].append(pace)

    def avg_bucket(b):
        return {k: round(sum(v)/len(v), 2) for k, v in b.items() if v}

    temp_avgs = avg_bucket(temp_buckets)
    cond_avgs = avg_bucket(cond_buckets)
    best_temp = min(temp_avgs, key=temp_avgs.get) if temp_avgs else None
    best_cond = min(cond_avgs, key=cond_avgs.get) if cond_avgs else None

    return jsonify({
        "temp_labels":        list(temp_avgs.keys()),
        "temp_values":        list(temp_avgs.values()),
        "cond_labels":        list(cond_avgs.keys()),
        "cond_values":        list(cond_avgs.values()),
        "best_temp":          best_temp,
        "best_cond":          best_cond,
        "runs_with_weather":  len([a for a in runs if str(a["id"]) in enrichment]),
    })


@app.route("/api/activities")
def api_activities():
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401
    force_refresh = request.args.get("refresh") == "1"
    activities, enrichment, err = get_activities_and_enrichment(force_refresh)
    if activities is None:
        return err[0] if len(err) == 2 else (err[0], err[1])
    rows = []
    for a in activities:
        spd     = a.get("average_speed", 0)
        dist_km = round(a.get("distance", 0) / 1000, 2)
        moving  = a.get("moving_time", 0)
        e       = enrichment.get(str(a.get("id")), {})
        w       = e.get("weather", {})
        wlabel, wemoji = wmo_label(w.get("wcode")) if w else ("", "")
        rows.append({
            "id":        a.get("id"),
            "name":      a.get("name", ""),
            "sport":     a.get("sport_type", ""),
            "date":      a.get("start_date_local", "")[:10],
            "dist_km":   dist_km,
            "time":      seconds_to_time(moving) if moving else "—",
            "pace":      speed_to_pace(spd) if spd > 0 else "—",
            "elevation": a.get("total_elevation_gain", 0),
            "hr":        a.get("average_heartrate") or "—",
            "calories":  a.get("calories") or "—",
            "suffer":    a.get("suffer_score") or "—",
            "location":  e.get("location", ""),
            "temp_c":    w.get("temp_c", ""),
            "weather":   f"{wemoji} {wlabel}".strip() if wlabel else "",
        })
    rows.sort(key=lambda r: r["date"], reverse=True)
    return jsonify(rows)


@app.route("/api/card")
def api_card():
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401
    activities, enrichment, err = get_activities_and_enrichment()
    if activities is None:
        return "Rate limited — try again later.", 429
    stats = compute_stats(activities)
    buf = generate_card(stats["card_data"])
    athlete = session.get("athlete", {})
    fname = f"{athlete.get('firstname', 'athlete')}_strava_stats.png".lower().replace(" ", "_")
    return send_file(buf, mimetype="image/png",
                     as_attachment=True, download_name=fname)


@app.route("/api/activity/<int:act_id>")
def api_activity_detail(act_id):
    """Fetch detailed activity data including GPS polyline for map display."""
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401

    # Demo mode — return a fake detail from cached demo activities
    if is_demo():
        from demo_data import DEMO_ACTIVITIES
        act = next((a for a in DEMO_ACTIVITIES if a.get("id") == act_id), None)
        if not act:
            return jsonify({"error": "not found"}), 404
        lat_lng = act.get("start_latlng", [])
        spd = act.get("average_speed", 0)
        mov = act.get("moving_time", 0)
        return jsonify({
            "id": act_id,
            "name": act.get("name", ""),
            "description": "",
            "polyline": None,  # demo has no real polyline
            "start_latlng": lat_lng,
            "sport_type": act.get("sport_type", ""),
            "date": act.get("start_date_local", "")[:10],
            "dist_km": round(act.get("distance", 0) / 1000, 1),
            "time": seconds_to_time(mov) if mov else None,
            "calories": act.get("calories"),
            "average_heartrate": act.get("average_heartrate"),
            "max_heartrate": act.get("max_heartrate"),
            "average_speed": act.get("average_speed"),
            "max_speed": act.get("max_speed"),
            "total_elevation_gain": act.get("total_elevation_gain"),
            "suffer_score": act.get("suffer_score"),
            "splits_metric": [],
        })

    try:
        r = requests.get(
            f"{STRAVA_API_BASE}/activities/{act_id}",
            headers=get_headers(),
            params={"include_all_efforts": False},
            timeout=12,
        )
    except requests.exceptions.Timeout:
        return jsonify({"error": "timeout"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if r.status_code == 429:
        return jsonify({"error": "rate_limited"}), 429
    if r.status_code != 200:
        return jsonify({"error": f"strava {r.status_code}"}), r.status_code

    d = r.json()
    # Extract only what we need — keep response small
    map_data = d.get("map", {})
    mov = d.get("moving_time", 0)
    return jsonify({
        "id":                  act_id,
        "name":                d.get("name", ""),
        "description":         d.get("description", ""),
        "sport_type":          d.get("sport_type", ""),
        "date":                (d.get("start_date_local") or "")[:10],
        "dist_km":             round(d.get("distance", 0) / 1000, 1),
        "time":                seconds_to_time(mov) if mov else None,
        "polyline":            map_data.get("polyline") or map_data.get("summary_polyline"),
        "start_latlng":        d.get("start_latlng", []),
        "calories":            d.get("calories"),
        "average_heartrate":   d.get("average_heartrate"),
        "max_heartrate":       d.get("max_heartrate"),
        "average_speed":       d.get("average_speed"),
        "max_speed":           d.get("max_speed"),
        "total_elevation_gain": d.get("total_elevation_gain"),
        "suffer_score":        d.get("suffer_score"),
        "splits_metric":       d.get("splits_metric", []),
        "gear":                (d.get("gear") or {}).get("name", ""),
    })


if __name__ == "__main__":
    app.run(debug=True, port=8080)
