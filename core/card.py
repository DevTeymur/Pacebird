import os
import io
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


def _find_font(*candidates):
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

_BOLD_CANDIDATES = [
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Arial.ttf",
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
FONT_BLACK   = FONT_BOLD


def _font(path, size):
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def generate_card(card_data):
    W, H = 1080, 1080
    img = Image.new("RGB", (W, H), "#1a1a2e")
    draw = ImageDraw.Draw(img)
    for y in range(H):
        r = int(26 + (y / H) * 10)
        g = int(26 + (y / H) * 5)
        b = int(46 + (y / H) * 20)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    draw.rectangle([0, 0, W, 8], fill="#fc4c02")
    draw.ellipse([W - 280, -120, W + 80, 260], outline="#fc4c02", width=3)
    draw.ellipse([W - 240, -80, W + 40, 220], outline=(252, 76, 2, 60), width=1)
    f_huge   = _font(FONT_BLACK,   160)
    f_big    = _font(FONT_BLACK,    96)
    f_med    = _font(FONT_BOLD,     52)
    f_small  = _font(FONT_BOLD,     40)
    f_tiny   = _font(FONT_BOLD,     32)
    f_label  = _font(FONT_REGULAR,  28)
    draw.text((60, 36),  "STRAVA STATS", font=f_label, fill="#fc4c02")
    name = card_data.get("name", "Athlete")
    draw.text((60, 76),  name, font=f_big, fill="#ffffff")
    draw.text((60, 190), f"Running Report  ·  {datetime.utcnow().strftime('%B %Y')}", font=f_tiny, fill="#666688")
    draw.rectangle([60, 248, W - 60, 252], fill="#333355")
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
    draw.text((60, 430), "PERSONAL BESTS", font=f_tiny, fill="#fc4c02")
    draw.rectangle([60, 474, W - 60, 478], fill="#fc4c02")
    pr_items = [
        ("5K",           card_data.get("best_5k", "—")),
        ("HALF MARATHON", card_data.get("best_hm", "—")),
    ]
    for i, (dist, t) in enumerate(pr_items):
        x = 60 + i * 500
        draw.text((x, 492), dist, font=f_label, fill="#666688")
        draw.text((x, 530), t, font=f_huge, fill="#ffffff")
    y_fit = 730
    fit_age = card_data.get("fitness_age")
    if fit_age:
        draw.text((60, y_fit),       "FITNESS AGE",  font=f_tiny,  fill="#fc4c02")
        draw.text((60, y_fit + 48),  str(fit_age),   font=f_big,   fill="#ffffff")
        draw.text((60, y_fit + 152), "years young",  font=f_small, fill="#666688")
    else:
        draw.text((60, y_fit),       "FITNESS AGE",    font=f_tiny,  fill="#fc4c02")
        draw.text((60, y_fit + 48),  "Add birth year", font=f_small, fill="#555577")
    pace_imp = card_data.get("pace_improve")
    if pace_imp and pace_imp > 0:
        draw.text((530, y_fit),       "PACE GAINED",        font=f_tiny,  fill="#fc4c02")
        draw.text((530, y_fit + 48),  f"+{pace_imp}",       font=f_big,   fill="#06d6a0")
        draw.text((530, y_fit + 152), "min/km improvement", font=f_small, fill="#666688")
    draw.rectangle([0, H - 90, W, H], fill="#fc4c02")
    draw.text((60,      H - 62), "stravastats.app",   font=f_small, fill="#ffffff")
    draw.text((W - 380, H - 62), "Share your stats!", font=f_small, fill="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf
