"""
Synthetic activity data for demo/testing purposes.
Based on Teymur Rzali's real profile:
  - Male, ~27 yo, Utrecht/Amsterdam, Netherlands
  - Running avg pace ~5:30/km, best 5K ~27:30, best HM ~2:21
  - Gym (WeightTraining) 2-3x/week, Squash occasionally
  - ~3-4 runs/week, mix of 5K, 8K, 10K, occasional long run

Run ?demo=1 to use this instead of real Strava data.
"""

import random
import math
from datetime import datetime, timedelta

# Seed for reproducibility
random.seed(42)

# ─── CONFIG ───────────────────────────────────────────────────────
ATHLETE = {
    "id":         99999999,
    "firstname":  "Teymur",
    "lastname":   "Demo",
    "city":       "Utrecht",
    "country":    "Netherlands",
    "sex":        "M",
}

# Utrecht coords (Griftpark area)
LOCATIONS = [
    (52.0820, 5.1220, "Griftpark, Utrecht, NL"),
    (52.0907, 5.1214, "Wilhelminapark, Utrecht, NL"),
    (52.1009, 5.0830, "Leidsche Rijn, Utrecht, NL"),
    (52.3731, 4.8922, "Vondelpark, Amsterdam, NL"),
    (52.3600, 4.9000, "Amsterdam Bos, Amsterdam, NL"),
    (52.0667, 5.1333, "De Uithof, Utrecht, NL"),
]

WEATHER_CONDITIONS = [
    # (wcode, label, emoji, temp_range, precip_range, wind_range)
    (0,  "Clear",        "☀️",  (8, 24),  (0.0, 0.0),  (5,  15)),
    (1,  "Mostly clear", "🌤️", (7, 20),  (0.0, 0.0),  (8,  20)),
    (2,  "Partly cloudy","⛅",  (6, 18),  (0.0, 0.1),  (10, 25)),
    (3,  "Overcast",     "☁️", (5, 15),  (0.0, 0.2),  (10, 28)),
    (61, "Light rain",   "🌧️", (4, 13),  (0.5, 3.0),  (12, 30)),
    (63, "Rain",         "🌧️", (3, 11),  (3.0, 8.0),  (15, 35)),
    (80, "Showers",      "🌧️", (5, 14),  (1.0, 5.0),  (10, 28)),
]

def random_weather():
    # Netherlands weather distribution — mostly overcast/cloudy
    weights = [10, 15, 20, 25, 15, 5, 10]
    cond = random.choices(WEATHER_CONDITIONS, weights=weights)[0]
    wcode, label, emoji, temp_r, precip_r, wind_r = cond
    return {
        "temp_c":    round(random.uniform(*temp_r), 1),
        "precip_mm": round(random.uniform(*precip_r), 1),
        "wind_kph":  round(random.uniform(*wind_r), 1),
        "wcode":     wcode,
    }


def pace_to_speed(pace_min_per_km):
    """Convert min/km pace to m/s."""
    return 1000 / (pace_min_per_km * 60)


def make_run(act_id, date, distance_m, pace_min_km, name=None, hr=None, suffer=None):
    """Build a synthetic run activity dict matching Strava API shape."""
    speed = pace_to_speed(pace_min_km)
    moving_time = int(distance_m / speed)
    loc = random.choice(LOCATIONS)
    hour = random.choices(
        [6, 7, 8, 9, 17, 18, 19, 20],
        weights=[5, 10, 8, 5, 15, 20, 20, 15]
    )[0]
    elev = random.randint(int(distance_m / 1000 * 2), int(distance_m / 1000 * 12))
    return {
        "id":                   act_id,
        "name":                 name or random.choice([
            "Morning Run", "Easy Run", "Lunch Run", "Evening Run",
            "Long Run", "Park Run", "Tempo Run", "Recovery Run",
            "Griftpark Loop", "Quick 5K", "Sunday Long Run",
        ]),
        "sport_type":           "Run",
        "start_date_local":     date.strftime(f"%Y-%m-%dT{hour:02d}:") + date.strftime("%M:%S"),
        "distance":             distance_m,
        "moving_time":          moving_time,
        "elapsed_time":         moving_time + random.randint(30, 300),
        "average_speed":        speed,
        "max_speed":            speed * random.uniform(1.1, 1.3),
        "total_elevation_gain": elev,
        "average_heartrate":    hr or random.randint(140, 168),
        "max_heartrate":        random.randint(172, 188),
        "calories":             int(distance_m / 1000 * random.uniform(58, 72)),
        "suffer_score":         suffer or random.randint(20, 85),
        "start_latlng":         [loc[0] + random.uniform(-0.005, 0.005),
                                  loc[1] + random.uniform(-0.005, 0.005)],
        "_location":            loc[2],
        "_weather":             random_weather(),
    }


def make_gym(act_id, date):
    duration = random.randint(45, 90) * 60
    hour = random.choices([6, 7, 17, 18, 19], weights=[5, 5, 25, 35, 30])[0]
    return {
        "id":               act_id,
        "name":             random.choice(["Gym Session", "Weight Training",
                                           "Strength", "Upper Body", "Legs Day",
                                           "Full Body", "Push Day", "Pull Day"]),
        "sport_type":       "WeightTraining",
        "start_date_local": date.strftime(f"%Y-%m-%dT{hour:02d}:") + date.strftime("%M:%S"),
        "distance":         0,
        "moving_time":      duration,
        "elapsed_time":     duration + random.randint(0, 600),
        "average_speed":    0,
        "calories":         random.randint(250, 450),
        "suffer_score":     random.randint(30, 70),
        "start_latlng":     [],
    }


def make_squash(act_id, date):
    duration = random.randint(50, 90) * 60
    hour = random.choice([9, 10, 18, 19, 20])
    return {
        "id":               act_id,
        "name":             random.choice(["Squash", "Squash Match",
                                           "Squash Training", "Squash Game"]),
        "sport_type":       "Squash",
        "start_date_local": date.strftime(f"%Y-%m-%dT{hour:02d}:") + date.strftime("%M:%S"),
        "distance":         0,
        "moving_time":      duration,
        "elapsed_time":     duration + 300,
        "average_speed":    0,
        "calories":         random.randint(400, 700),
        "suffer_score":     random.randint(50, 90),
        "start_latlng":     [],
    }


def generate_demo_activities():
    """
    Generate ~24 months of realistic synthetic activities.
    Pace improves gradually from ~5:55 → ~5:20 min/km over 2 years.
    """
    activities = []
    act_id = 10000000
    now = datetime.utcnow()
    start = now - timedelta(days=730)

    # Week-by-week schedule
    week = 0
    d = start
    while d < now:
        week += 1
        progress = min(week / 104, 1.0)  # 0 → 1 over 2 years

        # Pace improves from 5:55 to 5:18
        base_pace = 5.92 - progress * 0.62
        # Add some noise week-to-week
        week_pace = base_pace + random.uniform(-0.15, 0.20)

        # Decide activity schedule for this week
        # Runs: 2-4 per week
        n_runs = random.choices([2, 3, 3, 4], weights=[15, 35, 35, 15])[0]
        run_days = sorted(random.sample(range(7), n_runs))

        # Gym: 2-3 per week
        n_gym = random.choices([1, 2, 2, 3], weights=[10, 35, 35, 20])[0]
        gym_days = sorted(random.sample([i for i in range(7) if i not in run_days], min(n_gym, 7 - n_runs)))

        # Squash: 0-1 per week (more common in winter)
        month = (d + timedelta(days=3)).month
        squash_prob = 0.35 if month in [10, 11, 12, 1, 2, 3] else 0.15
        has_squash = random.random() < squash_prob

        for offset in run_days:
            run_date = d + timedelta(days=offset)
            if run_date >= now:
                break

            # Mix of distances
            is_long_run = (offset == run_days[-1] and week % 2 == 0)
            if is_long_run:
                dist = random.uniform(14000, 22000)
                pace = week_pace + random.uniform(0.3, 0.7)  # slower on long runs
            elif random.random() < 0.25:
                # Tempo/fast run
                dist = random.uniform(5000, 8000)
                pace = week_pace - random.uniform(0.1, 0.3)
            else:
                dist = random.uniform(5000, 11000)
                pace = week_pace + random.uniform(-0.1, 0.25)

            pace = max(4.5, pace)  # floor at 4:30/km

            # Special PRs — plant a few fast efforts
            if week == 20 and offset == run_days[0]:
                # Best 5K
                dist, pace = 5100, 5.45
            elif week == 45 and offset == run_days[0]:
                # 10K PR attempt
                dist, pace = 10200, 5.52
            elif week == 78 and offset == run_days[-1]:
                # HM
                dist, pace = 21300, 6.72

            activities.append(make_run(act_id, run_date, dist, pace))
            act_id += 1

        for offset in gym_days:
            gym_date = d + timedelta(days=offset)
            if gym_date >= now:
                break
            activities.append(make_gym(act_id, gym_date))
            act_id += 1

        if has_squash:
            sq_offset = random.randint(0, 6)
            sq_date = d + timedelta(days=sq_offset)
            if sq_date < now:
                activities.append(make_squash(act_id, sq_date))
                act_id += 1

        d += timedelta(weeks=1)

    # Sort newest first (matches Strava API)
    activities.sort(key=lambda a: a["start_date_local"], reverse=True)
    return activities


def generate_demo_enrichment(activities):
    """
    Build a pre-filled enrichment dict (weather + location) for all demo runs.
    Uses the _weather and _location fields planted on each activity.
    """
    enrichment = {}
    for a in activities:
        if a.get("sport_type") == "Run" and a.get("_weather"):
            enrichment[str(a["id"])] = {
                "weather":  a["_weather"],
                "location": a.get("_location", "Utrecht, NL"),
            }
    return enrichment


DEMO_ACTIVITIES = generate_demo_activities()
DEMO_ENRICHMENT = generate_demo_enrichment(DEMO_ACTIVITIES)
