import os
import json
import time

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", ".cache")

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
