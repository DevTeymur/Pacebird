from datetime import datetime, timedelta
from collections import defaultdict

from core.helpers import seconds_to_hms
from core.stats import compute_streaks


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
