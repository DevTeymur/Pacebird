import os
import json
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")

def _cache_path(athlete_id):
    return os.path.join(CACHE_DIR, f"activities_{athlete_id}.json")

def _load_cache(athlete_id):
    """Return (activities, last_date) or (None, None) if no cache."""
    path = _cache_path(athlete_id)
    if not os.path.exists(path):
        return None, None
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("activities"), data.get("last_date")
    except Exception:
        return None, None

def _save_cache(athlete_id, activities):
    """Save activities list, recording the most recent start_date_local."""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # Find the latest activity date so incremental sync knows where to start
        dates = [
            a.get("start_date_local", "")[:10]
            for a in activities
            if a.get("start_date_local", "")
        ]
        last_date = max(dates) if dates else None
        with open(_cache_path(athlete_id), "w") as f:
            json.dump({
                "ts":         time.time(),
                "last_date":  last_date,
                "activities": activities,
            }, f)
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
