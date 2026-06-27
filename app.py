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

from demo_data import DEMO_ACTIVITIES, DEMO_ENRICHMENT, ATHLETE as DEMO_ATHLETE

from core.cache import (
    _cache_path, _load_cache, _save_cache,
    _load_enrichment, _save_enrichment,
)
from core.strava import (
    fetch_weather, fetch_location, wmo_label,
    enrich_activities_background, get_headers,
    fetch_activities, _safe_fetch,
    get_activities_and_enrichment,
    STRAVA_API_BASE,
)
from core.helpers import speed_to_pace, seconds_to_time, seconds_to_hms, is_demo
from core.fitness import (
    estimate_vo2max, estimate_vo2max_from_time, fitness_age,
)
from core.card import FONT_BOLD, FONT_REGULAR, FONT_BLACK, generate_card
from core.stats import compute_streaks, compute_stats
from core.achievements import compute_achievements
from core.db import init_db, upsert_token, delete_token

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-to-random-string")

# Initialise DB on startup (creates tokens table if not exists)
with app.app_context():
    init_db()

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:5000/callback")

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"


# ─── AUTH ─────────────────────────────────────────────────────────

@app.route("/favicon.ico")
def favicon():
    # Bird silhouette on orange circle — shows in browser tab properly
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<circle cx="16" cy="16" r="16" fill="#fc4c02"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="18" fill="white">🐦</text>'
        '</svg>'
    ).encode("utf-8")
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public,max-age=86400"}


@app.route("/favicon.svg")
def favicon_svg():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<circle cx="16" cy="16" r="16" fill="#fc4c02"/>'
        '<text x="16" y="22" text-anchor="middle" font-size="18" fill="white">🐦</text>'
        '</svg>'
    ).encode("utf-8")
    return svg, 200, {"Content-Type": "image/svg+xml", "Cache-Control": "public,max-age=86400"}


@app.route("/manifest.json")
def pwa_manifest():
    import os as _os
    manifest_path = _os.path.join(_os.path.dirname(__file__), "static", "manifest.json")
    with open(manifest_path) as f:
        data = f.read()
    return data, 200, {"Content-Type": "application/manifest+json", "Cache-Control": "public,max-age=3600"}


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
    athlete = data.get("athlete", {})
    athlete_id = athlete.get("id")
    session.clear()
    session["access_token"]  = data["access_token"]
    session["refresh_token"] = data["refresh_token"]
    session["expires_at"]    = data.get("expires_at", 0)
    session["athlete"]       = athlete
    # Persist tokens to DB for automatic refresh across sessions
    if athlete_id:
        upsert_token(
            athlete_id    = athlete_id,
            access_token  = data["access_token"],
            refresh_token = data["refresh_token"],
            expires_at    = data.get("expires_at", 0),
            athlete_obj   = athlete,
        )
    return redirect("/dashboard")


@app.route("/logout")
def logout():
    athlete_id = session.get("athlete", {}).get("id")
    if athlete_id:
        delete_token(athlete_id)
    session.clear()
    return redirect("/")


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


# ─── ERROR PAGES ──────────────────────────────────────────────────

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500


if __name__ == "__main__":
    app.run(debug=True, port=8080)
