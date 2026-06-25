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
# Stores fetched activities on disk per athlete, avoids re-fetching
# every page load. Cache expires after CACHE_TTL seconds.
CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
# Cache is permanent — only cleared by explicit ?refresh=1 from the user.
# This keeps Strava API requests minimal.

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
# Stores weather + location per activity ID, permanently.
# Separate from Strava cache so a Strava refresh doesn't wipe enrichment.

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
    """Fetch historical hourly weather from Open-Meteo for a given date/location."""
    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude":        lat,
                "longitude":       lon,
                "start_date":      date_str,
                "end_date":        date_str,
                "hourly":          "temperature_2m,precipitation,windspeed_10m,weathercode",
                "timezone":        "auto",
            },
            timeout=8,
        )
        d = r.json()
        hourly = d.get("hourly", {})
        # Take midday (noon) values as representative
        idx = 12
        return {
            "temp_c":    round(hourly.get("temperature_2m", [None] * 13)[idx] or 0, 1),
            "precip_mm": round(hourly.get("precipitation",  [None] * 13)[idx] or 0, 1),
            "wind_kph":  round(hourly.get("windspeed_10m",  [None] * 13)[idx] or 0, 1),
            "wcode":     hourly.get("weathercode", [None] * 13)[idx],
        }
    except Exception:
        return None


def fetch_location(lat, lon):
    """Reverse geocode via Nominatim (OpenStreetMap). Rate limit: 1 req/sec."""
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


# WMO weather code → human label + emoji
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
    """
    Run in a background thread. For each run with GPS coords:
    - Fetch weather from Open-Meteo (no rate limit)
    - Fetch location from Nominatim (1 req/sec)
    Only processes activities not already enriched.
    """
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

        # Weather — no rate limit, fast
        w = fetch_weather(lat, lon, date_str)
        if w:
            entry["weather"] = w

        # Location — 1 req/sec rate limit
        loc = fetch_location(lat, lon)
        if loc:
            entry["location"] = loc
        time.sleep(1.1)  # respect Nominatim rate limit

        if entry:
            enrichment[act_id] = entry
            # Save incrementally so progress isn't lost if interrupted
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

# Font search — tries Mac system fonts, then Linux, then Pillow default
def _find_font(*candidates):
    """Return the first font path that exists on this system."""
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

# Bold / Black weight candidates (Mac → Linux → None)
_BOLD_CANDIDATES = [
    # Mac system fonts
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Arial.ttf",
    # Linux
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
FONT_BLACK   = FONT_BOLD   # use bold as black fallback

def _font(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # Last resort: Pillow built-in (tiny but always works)
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


# ─── VO2MAX / FITNESS AGE ─────────────────────────────────────────
# Norms: (age_midpoint, avg_upper_bound) — VO2max at which you are
# "average" for that age group. Source: ACSM / Cooper Institute.
# If your VO2max <= avg_upper for an age group, your fitness matches
# that age group's average → fitness age = that midpoint.
VO2_AVG_UPPER_MALE = [
    (24, 41), (34, 39), (44, 36), (54, 33), (64, 29),
]
VO2_AVG_UPPER_FEMALE = [
    (24, 36), (34, 34), (44, 31), (54, 28), (64, 25),
]

import math as _math

def estimate_vo2max(best_5k_speed_ms, best_5k_dist=5000):
    """
    Correct Jack Daniels VDOT formula from race performance.
    best_5k_speed_ms: average m/s of the fastest run >= 4.5 km.
    """
    if not best_5k_speed_ms or best_5k_speed_ms <= 0:
        return None
    # Estimate race time for the target distance at that speed
    t_min = best_5k_dist / best_5k_speed_ms / 60.0
    v = best_5k_dist / t_min  # m/min
    # % VO2max sustained at race duration (Daniels & Gilbert)
    pct = (0.8 + 0.1894393 * _math.exp(-0.012778 * t_min)
               + 0.2989558 * _math.exp(-0.1932605 * t_min))
    vo2_at_v = -4.60 + 0.182258 * v + 0.000104 * v ** 2
    vo2max = vo2_at_v / pct
    return round(max(20, min(85, vo2max)), 1)

def fitness_age(vo2max, actual_age, gender="M"):
    """
    Return the youngest age group whose average VO2max the athlete matches.
    E.g. VO2max=34 matches the average of the 20-29 group → fitness age 24.
    """
    norms = VO2_AVG_UPPER_MALE if gender == "M" else VO2_AVG_UPPER_FEMALE
    for (age_mid, avg_upper) in norms:
        if vo2max <= avg_upper:
            return age_mid
    # Above all averages — fitter than average 64yo → use lowest bracket
    return norms[0][0]


# ─── DEMO HELPERS ─────────────────────────────────────────────────

def is_demo():
    return session.get("demo_mode") is True

def demo_safe_fetch():
    return DEMO_ACTIVITIES, None

def get_activities_and_enrichment(force_refresh=False):
    """Single entry point — returns (activities, enrichment, error_tuple_or_None)."""
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
    # Return a minimal orange SVG circle as favicon
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<circle cx="16" cy="16" r="16" fill="#fc4c02"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="18" fill="white">⚡</text>'
        '</svg>'
    ).encode("utf-8")
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public,max-age=86400"}


@app.route("/api/debug")
def api_debug():
    """Quick Strava connection check — open this in browser to diagnose fetch issues."""
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
        # Rate limit headers
        limit_15  = r.headers.get("X-RateLimit-Limit", "?").split(",")
        usage_15  = r.headers.get("X-RateLimit-Usage", "?").split(",")
        athlete_id = session.get("athlete", {}).get("id")
        cache_file = _cache_path(athlete_id) if athlete_id else "n/a"
        cache_exists = os.path.exists(cache_file)
        cache_size = os.path.getsize(cache_file) if cache_exists else 0
        return jsonify({
            "status_code":        r.status_code,
            "elapsed_sec":        elapsed,
            "rate_limit_15min":   limit_15[0] if limit_15 else "?",
            "rate_used_15min":    usage_15[0] if usage_15 else "?",
            "rate_limit_daily":   limit_15[1] if len(limit_15) > 1 else "?",
            "rate_used_daily":    usage_15[1] if len(usage_15) > 1 else "?",
            "cache_dir":          CACHE_DIR,
            "cache_file":         cache_file,
            "cache_exists":       cache_exists,
            "cache_size_kb":      round(cache_size / 1024, 1),
            "cache_dir_files":    _glob.glob(os.path.join(CACHE_DIR, "*")),
            "font_bold":          FONT_BOLD,
            "font_regular":       FONT_REGULAR,
            "response_preview":   r.json() if r.status_code == 200 else r.text[:300],
        })
    except requests.exceptions.Timeout:
        return jsonify({"error": "timeout", "elapsed_sec": round(_time.time() - t0, 2)}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/demo")
def demo():
    """One-click demo mode — no Strava auth needed."""
    session.clear()
    session["demo_mode"]  = True
    session["athlete"]    = DEMO_ATHLETE
    session["birth_year"] = "1997"   # pre-set so fitness age renders immediately
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
    # Explicitly clear any leftover demo state
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


FETCH_MONTHS = 24

def fetch_activities(months=None, force_refresh=False):
    # Demo mode — never hit the network
    if is_demo():
        return DEMO_ACTIVITIES

    if months is None:
        months = FETCH_MONTHS

    athlete_id = session.get("athlete", {}).get("id")
    t0 = time.time()

    # Try cache first (unless force_refresh requested)
    if athlete_id and not force_refresh:
        cached = _load_cache(athlete_id)
        if cached is not None:
            print(f"[fetch] cache hit — {len(cached)} activities in {round(time.time()-t0,2)}s")
            return cached

    print(f"[fetch] no cache — fetching last {months} months from Strava…")
    after = int((datetime.utcnow() - timedelta(days=months * 30)).timestamp())
    activities, page = [], 1
    while True:
        try:
            r = requests.get(
                f"{STRAVA_API_BASE}/athlete/activities",
                headers=get_headers(),
                params={"per_page": 100, "page": page, "after": after},
                timeout=12,
            )
        except requests.exceptions.Timeout:
            print(f"[fetch] timeout on page {page} after {round(time.time()-t0,1)}s")
            break
        except requests.exceptions.RequestException as e:
            print(f"[fetch] request error: {e}")
            break

        print(f"[fetch] page {page} → HTTP {r.status_code} ({round(time.time()-t0,2)}s)")

        # Handle rate limiting (429) — return cached data if available
        if r.status_code == 429:
            print("[fetch] rate limited!")
            if athlete_id:
                cached = _load_cache(athlete_id)
                if cached is not None:
                    return cached
            raise RuntimeError("rate_limited")

        if r.status_code != 200:
            print(f"[fetch] unexpected status {r.status_code}: {r.text[:200]}")
            break

        batch = r.json()
        if not batch or not isinstance(batch, list):
            print(f"[fetch] empty batch on page {page}, done")
            break
        activities.extend(batch)
        print(f"[fetch] got {len(batch)} activities (total {len(activities)})")
        if len(batch) < 100:
            break
        page += 1

    print(f"[fetch] done — {len(activities)} activities in {round(time.time()-t0,2)}s total")

    if athlete_id:
        _save_cache(athlete_id, activities)
        print(f"[fetch] cache saved to {_cache_path(athlete_id)}")
        # Kick off enrichment in background — doesn't block the response
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


def compute_stats(activities):
    runs   = [a for a in activities if a.get("sport_type") == "Run"]
    gym    = [a for a in activities if a.get("sport_type") == "WeightTraining"]
    squash = [a for a in activities if a.get("sport_type") == "Squash"]

    total_run_dist = sum(a.get("distance", 0) for a in runs) / 1000

    # ── PERSONAL RECORDS ──────────────────────────────────────────
    targets = {"1K": 1000, "5K": 5000, "10K": 10000, "15K": 15000, "HM": 21097}
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

    # ── MONTHLY DISTANCE ──────────────────────────────────────────
    monthly = defaultdict(float)
    for a in runs:
        d = a.get("start_date_local", "")[:7]
        monthly[d] += a.get("distance", 0) / 1000
    monthly_sorted = sorted(monthly.items())
    monthly_labels = [m[0] for m in monthly_sorted]
    monthly_values = [round(m[1], 1) for m in monthly_sorted]

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
    # For each run, look at the total load in the 7 days BEFORE that run
    # Then bucket runs by prior-week load and avg the pace
    run_dates = {}
    for a in runs:
        d = a.get("start_date_local", "")[:10]
        spd = a.get("average_speed", 0)
        if d and spd > 0:
            run_dates[d] = {"pace": round(1000 / 60 / spd, 2), "dt": datetime.strptime(d, "%Y-%m-%d")}

    # Build daily load lookup
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
        if prior_load < 30:       sweet_buckets["Low\n(0–30)"].append(info["pace"])
        elif prior_load < 80:     sweet_buckets["Medium\n(30–80)"].append(info["pace"])
        elif prior_load < 150:    sweet_buckets["High\n(80–150)"].append(info["pace"])
        else:                     sweet_buckets["Very High\n(150+)"].append(info["pace"])

    sweet_labels, sweet_values, sweet_counts = [], [], []
    for label, paces in sweet_buckets.items():
        if paces:
            sweet_labels.append(label)
            sweet_values.append(round(sum(paces) / len(paces), 2))
            sweet_counts.append(len(paces))

    # Find sweet spot = lowest pace (fastest) with enough data points
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

    # ── SPORT BREAKDOWN ───────────────────────────────────────────
    sport_counts = defaultdict(int)
    for a in activities:
        sport_counts[a.get("sport_type", "Other")] += 1
    top_sports = sorted(sport_counts.items(), key=lambda x: -x[1])[:6]

    # ── CALORIE BY SPORT ─────────────────────────────────────────
    # MET (metabolic equivalent) estimates per sport for fallback
    MET = {"Run": 9.8, "WeightTraining": 5.0, "Squash": 10.0,
           "Ride": 7.5, "Walk": 3.8, "Swim": 8.0, "Hike": 6.0}
    BODY_WEIGHT_KG = 70  # reasonable default

    cal_sport = defaultdict(list)
    for a in activities:
        sport = a.get("sport_type", "Other")
        mins  = a.get("moving_time", 0) / 60
        if mins <= 0:
            continue
        cals = a.get("calories") or 0
        # Strava list endpoint often omits calories — estimate from MET if missing
        if cals <= 0:
            met = MET.get(sport, 5.0)
            cals = met * BODY_WEIGHT_KG * (mins / 60)
        cal_sport[sport].append(cals / mins * 60)  # kcal/hr

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
            # Use best 5K speed for VO2max estimate
            best_speed = max((a.get("average_speed", 0) for a in runs
                              if a.get("distance", 0) >= 4500), default=0)
            vo2 = estimate_vo2max(best_speed)
            if vo2:
                fit_age = fitness_age(vo2, actual_age, gender)
                fitness_age_data = {
                    "actual_age":   actual_age,
                    "fitness_age":  fit_age,
                    "vo2max":       vo2,
                    "difference":   actual_age - fit_age,
                }
        except Exception:
            pass

    longest_run = round(max((a.get("distance", 0) for a in runs), default=0) / 1000, 1)

    # ── CARD DATA (subset for shareable image) ────────────────────
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
        "total_runs":       len(runs),
        "total_run_km":     round(total_run_dist, 1),
        "total_gym":        len(gym),
        "total_squash":     len(squash),
        "pace_improvement": pace_improvement,
        "prs":              prs,
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
    }


# ─── SHAREABLE CARD GENERATOR ─────────────────────────────────────

def generate_card(card_data):
    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), "#1a1a2e")
    draw = ImageDraw.Draw(img)

    # Background gradient effect (manual horizontal bands)
    for y in range(H):
        r = int(26 + (y / H) * 10)
        g = int(26 + (y / H) * 5)
        b = int(46 + (y / H) * 20)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Orange accent bar top
    draw.rectangle([0, 0, W, 8], fill="#fc4c02")

    # Decorative circle
    draw.ellipse([W - 280, -120, W + 80, 260], outline="#fc4c02", width=3)
    draw.ellipse([W - 240, -80, W + 40, 220], outline=(252, 76, 2, 60), width=1)

    # Fonts — all sizes boosted for readability
    f_huge   = _font(FONT_BLACK,   160)
    f_big    = _font(FONT_BLACK,    96)
    f_med    = _font(FONT_BOLD,     52)
    f_small  = _font(FONT_BOLD,     40)
    f_tiny   = _font(FONT_BOLD,     32)
    f_label  = _font(FONT_REGULAR,  28)

    # ── HEADER ──
    draw.text((60, 36),  "STRAVA STATS", font=f_label, fill="#fc4c02")
    name = card_data.get("name", "Athlete")
    draw.text((60, 76),  name, font=f_big, fill="#ffffff")
    draw.text((60, 190), f"Running Report  ·  {datetime.utcnow().strftime('%B %Y')}", font=f_tiny, fill="#666688")

    # Divider
    draw.rectangle([60, 248, W - 60, 252], fill="#333355")

    # ── STAT BLOCKS ──
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

    # ── PR SECTION ──
    draw.text((60, 430), "PERSONAL BESTS", font=f_tiny, fill="#fc4c02")
    draw.rectangle([60, 474, W - 60, 478], fill="#fc4c02")

    pr_items = [
        ("5K",           card_data.get("best_5k", "—")),
        ("HALF MARATHON", card_data.get("best_hm", "—")),
    ]
    for i, (dist, time) in enumerate(pr_items):
        x = 60 + i * 500
        draw.text((x, 492), dist, font=f_label, fill="#666688")
        draw.text((x, 530), time, font=f_huge,  fill="#ffffff")

    # ── FITNESS AGE ──
    y_fit = 730
    fit_age = card_data.get("fitness_age")
    if fit_age:
        draw.text((60, y_fit),       "FITNESS AGE",  font=f_tiny,  fill="#fc4c02")
        draw.text((60, y_fit + 48),  str(fit_age),   font=f_big,   fill="#ffffff")
        draw.text((60, y_fit + 152), "years young",  font=f_small, fill="#666688")
    else:
        draw.text((60, y_fit),       "FITNESS AGE",       font=f_tiny,  fill="#fc4c02")
        draw.text((60, y_fit + 48),  "Add birth year",    font=f_small, fill="#555577")
        draw.text((60, y_fit + 100), "to unlock",         font=f_small, fill="#555577")

    # ── PACE IMPROVEMENT ──
    pace_imp = card_data.get("pace_improve")
    if pace_imp and pace_imp > 0:
        draw.text((530, y_fit),       "PACE GAINED",          font=f_tiny,  fill="#fc4c02")
        draw.text((530, y_fit + 48),  f"+{pace_imp}",         font=f_big,   fill="#06d6a0")
        draw.text((530, y_fit + 152), "min/km improvement",   font=f_small, fill="#666688")

    # ── FOOTER ──
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
    if birth_year:
        session["birth_year"] = birth_year
    force_refresh = request.args.get("refresh") == "1"
    activities, enrichment, err = get_activities_and_enrichment(force_refresh)
    if activities is None:
        return err[0] if len(err) == 2 else (err[0], err[1])
    stats = compute_stats(activities)
    return jsonify(stats)


def _safe_fetch(force_refresh=False):
    """Wraps fetch_activities and returns (activities, error_response_or_None)."""
    try:
        return fetch_activities(force_refresh=force_refresh), None
    except RuntimeError as e:
        if "rate_limited" in str(e):
            return None, jsonify({
                "error": "rate_limited",
                "message": "Strava rate limit reached. Your cached data is shown. Try again in 15 minutes.",
            }), 429
        return None, jsonify({"error": str(e)}), 500


@app.route("/api/enrichment")
def api_enrichment():
    """Returns enrichment progress + data. Polled by frontend."""
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
    """Compute pace-vs-temperature and pace-vs-condition buckets."""
    if "access_token" not in session and not is_demo():
        return jsonify({"error": "not authenticated"}), 401
    activities, enrichment, err = get_activities_and_enrichment()
    if activities is None:
        return jsonify({"temp_labels": [], "temp_values": [], "cond_labels": [], "cond_values": [], "runs_with_weather": 0})
    runs = [a for a in activities if a.get("sport_type") == "Run"]

    # Pace vs temperature buckets (<5, 5-10, 10-15, 15-20, 20-25, 25+°C)
    temp_buckets = {
        "<5°C":    [], "5–10°C":  [], "10–15°C": [],
        "15–20°C": [], "20–25°C": [], "25°C+":   [],
    }
    # Pace vs condition
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

    # Best temperature range (lowest = fastest pace)
    best_temp = min(temp_avgs, key=temp_avgs.get) if temp_avgs else None
    best_cond = min(cond_avgs, key=cond_avgs.get) if cond_avgs else None

    return jsonify({
        "temp_labels":  list(temp_avgs.keys()),
        "temp_values":  list(temp_avgs.values()),
        "cond_labels":  list(cond_avgs.keys()),
        "cond_values":  list(cond_avgs.values()),
        "best_temp":    best_temp,
        "best_cond":    best_cond,
        "runs_with_weather": len([a for a in runs if str(a["id"]) in enrichment]),
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
        return "Rate limited — try again in 15 minutes.", 429
    stats = compute_stats(activities)
    buf = generate_card(stats["card_data"])
    athlete = session.get("athlete", {})
    fname = f"{athlete.get('firstname', 'athlete')}_strava_stats.png".lower().replace(" ", "_")
    return send_file(buf, mimetype="image/png",
                     as_attachment=True, download_name=fname)


if __name__ == "__main__":
    app.run(debug=True, port=8080)

