import os
import io
import json
import time
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

os.makedirs(CACHE_DIR, exist_ok=True)

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
        with open(_cache_path(athlete_id), "w") as f:
            json.dump({"ts": time.time(), "activities": activities}, f)
    except Exception:
        pass

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-to-random-string")

CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET")
REDIRECT_URI  = os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:5000/callback")

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"

# Font paths (Lato shipped with most Linux/Mac; fallback to default)
FONT_BOLD   = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"
FONT_REGULAR= "/usr/share/fonts/truetype/lato/Lato-Regular.ttf"
FONT_BLACK  = "/usr/share/fonts/truetype/lato/Lato-Black.ttf"

def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()


# ─── VO2MAX / FITNESS AGE TABLES ──────────────────────────────────
# VO2max norms by age (male) — from ACSM guidelines
# Each entry: (age_min, age_max, poor, fair, good, excellent, superior)
VO2_NORMS_MALE = [
    (20, 29, 33, 37, 42, 48, 53),
    (30, 39, 31, 35, 40, 45, 51),
    (40, 49, 28, 33, 37, 42, 47),
    (50, 59, 25, 30, 34, 39, 45),
    (60, 69, 21, 26, 30, 35, 41),
]
VO2_NORMS_FEMALE = [
    (20, 29, 28, 32, 36, 41, 45),
    (30, 39, 26, 30, 34, 38, 43),
    (40, 49, 24, 27, 31, 36, 41),
    (50, 59, 21, 24, 28, 32, 37),
    (60, 69, 18, 21, 24, 29, 35),
]

def estimate_vo2max(best_speed_ms):
    """Rough VO2max from best 5K-ish speed using simplified Jack Daniels."""
    if not best_speed_ms or best_speed_ms <= 0:
        return None
    pace_min_km = (1000 / best_speed_ms) / 60
    # VO2 = -4.6 + 0.182258*velocity + 0.000104*velocity^2  (velocity in m/min)
    v = best_speed_ms * 60  # m/min
    vo2 = -4.6 + 0.182258 * v + 0.000104 * v ** 2
    # % VO2max at easy pace correction (assume running at ~75% VO2max)
    vo2max = vo2 / 0.75
    return round(max(20, min(80, vo2max)), 1)

def fitness_age(vo2max, actual_age, gender="M"):
    """Returns estimated fitness age given VO2max."""
    norms = VO2_NORMS_MALE if gender == "M" else VO2_NORMS_FEMALE
    # Find which age bracket the VO2max fits into as "good" or better
    for (age_min, age_max, poor, fair, good, excellent, superior) in norms:
        if vo2max >= good:
            mid = (age_min + age_max) // 2
            return mid
    # If below all "good" levels, fitness age = actual age + penalty
    return min(actual_age + 10, 70)


# ─── AUTH ─────────────────────────────────────────────────────────

@app.route("/")
def index():
    if "access_token" in session:
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
    resp = requests.post(STRAVA_TOKEN_URL, data={
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    })
    data = resp.json()
    if "access_token" not in data:
        return f"Token error: {data}", 400
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


def fetch_activities(months=24, force_refresh=False):
    athlete_id = session.get("athlete", {}).get("id")

    # Try cache first (unless force_refresh requested)
    if athlete_id and not force_refresh:
        cached = _load_cache(athlete_id)
        if cached is not None:
            return cached

    # Fetch fresh from Strava
    after = int((datetime.utcnow() - timedelta(days=months * 30)).timestamp())
    activities, page = [], 1
    while True:
        r = requests.get(f"{STRAVA_API_BASE}/athlete/activities",
                         headers=get_headers(),
                         params={"per_page": 100, "page": page, "after": after})
        batch = r.json()
        if not batch or not isinstance(batch, list):
            break
        activities.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    if athlete_id:
        _save_cache(athlete_id, activities)

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
    cal_sport = defaultdict(list)
    for a in activities:
        sport = a.get("sport_type", "Other")
        cals = a.get("calories", 0)
        mins = a.get("moving_time", 0) / 60
        if cals > 0 and mins > 0:
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
    if "access_token" not in session:
        return redirect("/")
    athlete = session.get("athlete", {})
    return render_template("dashboard.html", athlete=athlete)


@app.route("/api/stats")
def api_stats():
    if "access_token" not in session:
        return jsonify({"error": "not authenticated"}), 401
    birth_year = request.args.get("birth_year")
    if birth_year:
        session["birth_year"] = birth_year
    force_refresh = request.args.get("refresh") == "1"
    activities = fetch_activities(months=24, force_refresh=force_refresh)
    stats = compute_stats(activities)
    return jsonify(stats)


@app.route("/api/activities")
def api_activities():
    if "access_token" not in session:
        return jsonify({"error": "not authenticated"}), 401
    force_refresh = request.args.get("refresh") == "1"
    activities = fetch_activities(months=24, force_refresh=force_refresh)
    rows = []
    for a in activities:
        spd = a.get("average_speed", 0)
        dist_km = round(a.get("distance", 0) / 1000, 2)
        moving  = a.get("moving_time", 0)
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
        })
    # Sort newest first by default
    rows.sort(key=lambda r: r["date"], reverse=True)
    return jsonify(rows)


@app.route("/api/card")
def api_card():
    if "access_token" not in session:
        return jsonify({"error": "not authenticated"}), 401
    activities = fetch_activities(months=24)
    stats = compute_stats(activities)
    buf = generate_card(stats["card_data"])
    athlete = session.get("athlete", {})
    fname = f"{athlete.get('firstname', 'athlete')}_strava_stats.png".lower().replace(" ", "_")
    return send_file(buf, mimetype="image/png",
                     as_attachment=True, download_name=fname)


if __name__ == "__main__":
    app.run(debug=True, port=8080)

