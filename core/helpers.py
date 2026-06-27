from flask import session


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


def is_demo():
    return session.get("demo_mode") is True
