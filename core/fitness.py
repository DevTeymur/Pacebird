import math

VO2_AVG_UPPER_MALE = [
    (24, 41), (34, 39), (44, 36), (54, 33), (64, 29),
]
VO2_AVG_UPPER_FEMALE = [
    (24, 36), (34, 34), (44, 31), (54, 28), (64, 25),
]


def estimate_vo2max(best_5k_speed_ms, best_5k_dist=5000):
    if not best_5k_speed_ms or best_5k_speed_ms <= 0:
        return None
    t_min = best_5k_dist / best_5k_speed_ms / 60.0
    v = best_5k_dist / t_min
    pct = (0.8 + 0.1894393 * math.exp(-0.012778 * t_min)
               + 0.2989558 * math.exp(-0.1932605 * t_min))
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
