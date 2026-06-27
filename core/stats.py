import os
from datetime import datetime, timedelta
from collections import defaultdict
import math
from flask import session

from core.helpers import speed_to_pace, seconds_to_time, seconds_to_hms
from core.fitness import estimate_vo2max, estimate_vo2max_from_time, fitness_age


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
    # Lazy import to break circular dependency with achievements
    from core.achievements import compute_achievements

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
                    "id":      a.get("id"),
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
    # Compare first-30-days avg pace vs last-30-days avg pace (min/km).
    # Positive = got faster (old pace was slower, new pace is faster).
    pace_improvement = None
    runs_sorted_asc = sorted(runs, key=lambda a: a.get("start_date_local", ""))
    if len(runs_sorted_asc) >= 6:
        first_batch = runs_sorted_asc[:max(6, len(runs_sorted_asc) // 10)]
        last_batch  = runs_sorted_asc[-max(6, len(runs_sorted_asc) // 10):]
        def avg_pace(batch):
            speeds = [a.get("average_speed", 0) for a in batch if a.get("average_speed", 0) > 0]
            if not speeds:
                return None
            avg_spd = sum(speeds) / len(speeds)
            return 1000 / 60 / avg_spd  # min/km
        old_pace = avg_pace(first_batch)
        new_pace = avg_pace(last_batch)
        if old_pace and new_pace:
            pace_improvement = round(old_pace - new_pace, 2)  # positive = faster now

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

    # ── TRAINING RECOMMENDATIONS ─────────────────────────────────
    # Pure rule engine — every card is derived from this athlete's
    # own numbers. Nothing is hardcoded per-user.
    # Card shape: {icon, title, body, type}  type: "warn"|"tip"|"good"
    training_recs = []

    # Shared date anchors used across multiple rules
    four_weeks_ago  = now - timedelta(weeks=4)
    eight_weeks_ago = now - timedelta(weeks=8)
    two_weeks_ago   = now - timedelta(weeks=2)
    twelve_wks_ago  = now - timedelta(weeks=12)

    # Swimming data needed for cross-training section
    swims = [a for a in activities if a.get("sport_type") == "Swim"]

    def run_dt(a):
        try: return datetime.strptime(a["start_date_local"][:10], "%Y-%m-%d")
        except Exception: return None

    if runs:
        # ── A. LOAD RAMP (week-over-week) ────────────────────────
        if len(week_values) >= 4:
            avg4    = sum(week_values[-4:]) / 4
            last_wk = week_values[-1]
            last3   = week_values[-3:]
            is_dropping = all(last3[i] > last3[i+1] for i in range(len(last3)-1))

            if avg4 > 0 and last_wk > avg4 * 1.3:
                pct = round((last_wk / avg4 - 1) * 100)
                training_recs.append({
                    "icon": "⚠️",
                    "title": "Load spike this week",
                    "body": f"Training load is {pct}% above your 4-week average. One easy or rest day tomorrow will let your body absorb the work rather than just accumulate fatigue.",
                    "type": "warn",
                })
            elif is_dropping and last_wk < avg4 * 0.6:
                training_recs.append({
                    "icon": "📉",
                    "title": "3 weeks of declining load",
                    "body": "Your training has been getting lighter each week. Unless you're in a planned recovery block, a gradual build will keep your fitness from drifting backwards.",
                    "type": "tip",
                })
            elif avg4 > 0 and last_wk < avg4 * 0.4:
                training_recs.append({
                    "icon": "💤",
                    "title": "Very light week compared to normal",
                    "body": f"This week's load is well below your usual average. A single moderate run can be enough to keep your aerobic system engaged without adding fatigue.",
                    "type": "tip",
                })

        # ── B. LONG RUN RECENCY ───────────────────────────────────
        all_long    = [a for a in runs if a.get("distance", 0) >= 14000]
        recent_long = [a for a in all_long if run_dt(a) and run_dt(a) >= four_weeks_ago]

        if all_long and not recent_long:
            last_long_dt = run_dt(max(all_long, key=lambda x: x.get("start_date_local", "")))
            days_since = (now - last_long_dt).days if last_long_dt else 0
            training_recs.append({
                "icon": "🛣️",
                "title": f"No long run in {days_since} days",
                "body": "Long runs (14 km+) are the single best driver of aerobic base. They teach your body to burn fat, strengthen tendons, and make shorter runs feel easier. Aim for one every 10–14 days.",
                "type": "tip",
            })
        elif recent_long:
            # They're doing long runs consistently — reinforce it
            longest_recent = max(a.get("distance", 0) for a in recent_long) / 1000
            training_recs.append({
                "icon": "🛣️",
                "title": f"Long runs are consistent",
                "body": f"You've done at least one run over {longest_recent:.0f} km in the last 4 weeks. Long runs are the bedrock of endurance — keep scheduling them.",
                "type": "good",
            })

        # ── C. RUN FREQUENCY & CONSISTENCY ───────────────────────
        if len(runs) >= 8:
            runs_last4 = [a for a in runs if run_dt(a) and run_dt(a) >= four_weeks_ago]
            runs_prev4 = [a for a in runs if run_dt(a) and eight_weeks_ago <= run_dt(a) < four_weeks_ago]
            avg_last4  = len(runs_last4) / 4
            avg_prev4  = len(runs_prev4) / 4 if runs_prev4 else avg_last4

            if avg_last4 >= 4:
                training_recs.append({
                    "icon": "🔥",
                    "title": f"High consistency — {avg_last4:.1f} runs/week",
                    "body": "Running 4+ times a week is where real adaptation happens. Make sure at least one of those is easy-paced to keep recovery balanced.",
                    "type": "good",
                })
            elif avg_last4 >= 3:
                training_recs.append({
                    "icon": "✅",
                    "title": f"Good frequency — {avg_last4:.1f} runs/week",
                    "body": "Three runs a week is a solid base. Adding a fourth — even a short 20-minute easy run — can noticeably accelerate progress over time.",
                    "type": "good",
                })
            elif avg_prev4 > 0 and avg_last4 < avg_prev4 * 0.6:
                training_recs.append({
                    "icon": "📅",
                    "title": "Fewer runs than usual",
                    "body": f"You averaged {avg_prev4:.1f} runs/week before but only {avg_last4:.1f} in the last 4 weeks. Consistency is the biggest predictor of improvement — even one extra short run helps.",
                    "type": "tip",
                })
            elif avg_last4 < 2:
                training_recs.append({
                    "icon": "📅",
                    "title": "Running less than twice a week",
                    "body": "With fewer than 2 runs a week, it's hard to build fitness — each run is mostly just recovering to where you were. Aim for at least 3 to see real progress.",
                    "type": "tip",
                })

        # ── D. RUN VARIETY (short vs long balance) ────────────────
        if len(runs) >= 10:
            recent_runs = [a for a in runs if run_dt(a) and run_dt(a) >= eight_weeks_ago]
            if recent_runs:
                short = [a for a in recent_runs if a.get("distance", 0) < 5000]
                total = len(recent_runs)
                short_pct = len(short) / total
                if short_pct > 0.7:
                    training_recs.append({
                        "icon": "📏",
                        "title": "Most runs are under 5 km",
                        "body": f"{round(short_pct*100)}% of your recent runs are shorter than 5 km. Adding a medium run (8–12 km) each week builds the aerobic range that makes all your shorter runs faster.",
                        "type": "tip",
                    })
                elif short_pct < 0.2 and total >= 6:
                    training_recs.append({
                        "icon": "⚡",
                        "title": "Very few short/fast runs",
                        "body": "Most of your runs are long. Adding a shorter, faster session (5–7 km at a harder effort) once a week builds speed and keeps your fast-twitch fibres engaged.",
                        "type": "tip",
                    })

        # ── E. SWEET SPOT ZONE ────────────────────────────────────
        if sweet_spot_label and week_values:
            last_load = week_values[-1]
            spot_map  = {
                "Low\n(0–30)": 15, "Medium\n(30–80)": 55,
                "High\n(80–150)": 115, "Very High\n(150+)": 180,
            }
            spot_mid   = spot_map.get(sweet_spot_label, 55)
            spot_clean = sweet_spot_label.replace("\n", " ")
            if last_load < spot_mid * 0.6:
                training_recs.append({
                    "icon": "🎯",
                    "title": f"Below your sweet spot ({spot_clean})",
                    "body": f"Your data shows you run fastest after {spot_clean} load weeks. This week was lighter — a moderate effort this week could sharpen your next run.",
                    "type": "tip",
                })
            elif last_load > spot_mid * 1.6:
                training_recs.append({
                    "icon": "🎯",
                    "title": f"Above your sweet spot ({spot_clean})",
                    "body": f"You tend to run fastest after {spot_clean} load weeks. You're currently above that — a lighter week might actually produce a faster run than pushing harder.",
                    "type": "warn",
                })
            else:
                training_recs.append({
                    "icon": "🎯",
                    "title": f"In your sweet spot ({spot_clean})",
                    "body": f"Your recent load is right in the zone where your pace data shows your best performances. This is exactly the balance to maintain.",
                    "type": "good",
                })

        # ── F. PACE TREND ─────────────────────────────────────────
        if pace_improvement is not None:
            if pace_improvement > 0.3:
                training_recs.append({
                    "icon": "📈",
                    "title": f"Strong pace progress — {pace_improvement:.2f} min/km faster",
                    "body": "Your recent runs are significantly faster than your earlier ones. Whatever structure you've built is working. Protect it — avoid big load spikes that could cause injury.",
                    "type": "good",
                })
            elif pace_improvement > 0.05:
                training_recs.append({
                    "icon": "📈",
                    "title": f"Pace improving — {pace_improvement:.2f} min/km gained",
                    "body": "You're trending in the right direction. Small, consistent improvements add up — a 0.1 min/km gain over a year is a major transformation.",
                    "type": "good",
                })
            elif pace_improvement < -0.2:
                training_recs.append({
                    "icon": "🔄",
                    "title": "Pace has slowed recently",
                    "body": "Your recent average pace is slower than your earlier runs. Common causes: more long/easy runs, accumulated fatigue, or less speedwork. Check if your easy runs are actually easy.",
                    "type": "tip",
                })

        # ── G. REST & RECOVERY ────────────────────────────────────
        last_7_dates = set()
        for a in activities:
            d = a.get("start_date_local", "")[:10]
            if d:
                try:
                    if (now - datetime.strptime(d, "%Y-%m-%d")).days < 7:
                        last_7_dates.add(d)
                except Exception:
                    pass

        if len(last_7_dates) >= 7:
            training_recs.append({
                "icon": "🚨",
                "title": "Active every single day this week",
                "body": "Training every day without rest is a reliable path to overuse injury. Even one complete rest day per week dramatically reduces injury risk and improves performance.",
                "type": "warn",
            })
        elif len(last_7_dates) >= 6:
            training_recs.append({
                "icon": "😴",
                "title": "Only one rest day this week",
                "body": "You've been active 6 out of 7 days. Muscles rebuild during rest, not during exercise. Try to protect at least 1–2 full rest days each week.",
                "type": "warn",
            })

        # ── H. RECENT INACTIVITY ──────────────────────────────────
        if activities:
            last_act_dt = run_dt(max(activities, key=lambda x: x.get("start_date_local", "")))
            if last_act_dt:
                days_inactive = (now - last_act_dt).days
                if days_inactive >= 14:
                    training_recs.append({
                        "icon": "🏃",
                        "title": f"No activity in {days_inactive} days",
                        "body": "After 2+ weeks off, aerobic fitness starts to decline measurably. A short easy run — even 20 minutes — is enough to restart the engine. Start easy and build back gradually.",
                        "type": "warn",
                    })
                elif days_inactive >= 7:
                    training_recs.append({
                        "icon": "🏃",
                        "title": f"{days_inactive} days since your last activity",
                        "body": "A week without training won't lose you fitness, but two weeks will. Now is a good time to get a run in.",
                        "type": "tip",
                    })

        # ── I. STREAK MOMENTUM ────────────────────────────────────
        if cur_streak >= 14:
            training_recs.append({
                "icon": "🔥",
                "title": f"{cur_streak}-day activity streak",
                "body": "An impressive streak — but make sure some of those days include genuine rest-level effort (walking counts). Streaks built on daily hard efforts tend to end in injury.",
                "type": "good",
            })
        elif cur_streak >= 7:
            training_recs.append({
                "icon": "⚡",
                "title": f"{cur_streak} days in a row",
                "body": "Good momentum. Keep going but listen to your body — if something feels off, a rest day won't break the progress you've built.",
                "type": "good",
            })

        # ── J. BEST TIME OF DAY ───────────────────────────────────
        if tod_values and len(tod_labels) >= 2:
            best_idx  = tod_values.index(min(tod_values))
            worst_idx = tod_values.index(max(tod_values))
            pace_diff = round(tod_values[worst_idx] - tod_values[best_idx], 2)
            best_slot  = tod_labels[best_idx].replace("\n", " ")
            worst_slot = tod_labels[worst_idx].replace("\n", " ")
            if pace_diff >= 0.15:
                training_recs.append({
                    "icon": "⏰",
                    "title": f"{best_slot} is your fastest window",
                    "body": f"Your {best_slot} pace is {pace_diff:.2f} min/km faster than your {worst_slot} average across all your runs. Schedule hard sessions and races in the {best_slot} when possible.",
                    "type": "tip",
                })

        # ── K. BEST DAY OF WEEK ───────────────────────────────────
        if best_dow and dow_values and len(dow_values) >= 3:
            worst_dow_val = max(dow_values)
            best_dow_val  = min(dow_values)
            diff = round(worst_dow_val - best_dow_val, 2)
            worst_dow_idx = dow_values.index(worst_dow_val)
            worst_dow_label = dow_labels[worst_dow_idx] if worst_dow_idx < len(dow_labels) else ""
            if diff >= 0.2:
                training_recs.append({
                    "icon": "📆",
                    "title": f"{best_dow} is your best running day",
                    "body": f"Your average pace on {best_dow} is {diff:.2f} min/km faster than on {worst_dow_label}. Plan your key workout or long run on {best_dow} when you can.",
                    "type": "tip",
                })

        # ── L. CROSS-TRAINING MIX ─────────────────────────────────
        gym_last4  = [a for a in gym if run_dt(a) and run_dt(a) >= four_weeks_ago]
        swim_last4 = [a for a in swims if run_dt(a) and run_dt(a) >= four_weeks_ago]
        runs_last4_ct = [a for a in runs if run_dt(a) and run_dt(a) >= four_weeks_ago]

        if runs_last4_ct and not gym_last4 and not swim_last4:
            training_recs.append({
                "icon": "💪",
                "title": "Only running in the last 4 weeks",
                "body": "Strength work 1–2× a week reduces injury risk and improves running economy. Even 20 minutes of bodyweight exercises makes a difference over months.",
                "type": "tip",
            })
        elif gym_last4 and runs_last4_ct:
            gym_per_week = len(gym_last4) / 4
            if gym_per_week >= 2:
                training_recs.append({
                    "icon": "💪",
                    "title": "Good strength + running balance",
                    "body": f"You're combining {gym_per_week:.1f} gym sessions/week with your running. Strength training this consistently will pay off in injury prevention and running economy.",
                    "type": "good",
                })

    # ── PRIORITY & CAP ────────────────────────────────────────────
    # warnings first → tips → good news. Cap to produce 3 or 4 cards
    # (so they always fill a row evenly at the default 3-col grid).
    def rec_priority(r):
        return {"warn": 0, "tip": 1, "good": 2}[r["type"]]

    training_recs = sorted(training_recs, key=rec_priority)

    # Pick 3 or 4 — whichever fits the content better.
    # Prefer 4 if we have enough; otherwise 3. Never 2 or 5 (awkward rows).
    if len(training_recs) >= 4:
        training_recs = training_recs[:4]
    elif len(training_recs) >= 3:
        training_recs = training_recs[:3]
    else:
        training_recs = training_recs[:len(training_recs)]

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
                    "id":      a.get("id"),
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
        # Training recommendations
        "training_recs": training_recs,
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
