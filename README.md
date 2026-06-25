# 🐦 Pacebird

**Your Strava training data, beautifully visualised.**

Pacebird is a personal stats dashboard that connects to your Strava account and turns your activity history into clean, insightful charts — PRs, pace trends, training load, race predictions, weather correlations, achievements, and more.

---

## Features

- **Overview** — at-a-glance stats, this week vs last week, recent activities, PR cards
- **Running** — personal records (1K → Marathon), race predictor, yearly distance charts, pace trend
- **Cycling** — ride PRs, monthly distance, average speed progression
- **Swimming** — monthly distance, session stats
- **Training** — weekly load, training sweet spot, best time of day
- **Insights** — performance highlights, weather vs pace analysis
- **Stats** — full breakdown: distance, time, elevation, streaks
- **Achievements** — unlockable badges across Running, Cycling, Swimming, Streaks
- **Activities** — searchable/filterable table with clickable rows → map + splits popup
- **Share Card** — 1080×1080 PNG for Instagram/WhatsApp

---

## Tech Stack

- **Backend**: Python 3, Flask
- **Strava API**: OAuth 2.0, read-only (`activity:read_all`)
- **Charts**: Chart.js 4.4.1
- **Maps**: Leaflet.js + OpenStreetMap
- **Weather**: Open-Meteo (free, no API key needed)
- **Image card**: Pillow
- **Data cache**: JSON files on disk (per athlete)

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/yourname/pacebird.git
cd pacebird
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Create a Strava API app

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create an app — set **Authorization Callback Domain** to `localhost`
3. Copy your **Client ID** and **Client Secret**

### 4. Set up `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REDIRECT_URI=http://localhost:8080/callback
FLASK_SECRET=any-random-string
```

> ⚠️ Never commit `.env` to Git. It's already in `.gitignore`.

### 5. Run

```bash
./run.sh
```

Or manually: `python app.py`

Open [http://localhost:8080](http://localhost:8080) and connect your Strava.

### Demo mode (no Strava account needed)

```
http://localhost:8080/demo
```

Loads ~700 synthetic activities so you can explore all features without connecting.

---

## Project Structure

```
pacebird/
├── app.py              # Flask backend, all routes and data processing
├── demo_data.py        # Synthetic activity generator for demo mode
├── requirements.txt    # Python dependencies
├── run.sh              # One-command startup script
├── .env.example        # Environment variable template
├── .env                # Your secrets — never commit this
├── .gitignore
├── templates/
│   ├── login.html      # Connect with Strava page
│   └── dashboard.html  # Main app UI (single-page, all tabs)
├── static/             # Static assets
├── .cache/             # Per-athlete activity cache — never commit this
├── README.md
├── CHANGELOG.md
├── SECURITY.md
└── LICENSE
```

---

## Data & Privacy

- Pacebird only **reads** your Strava data. It never posts, modifies, or deletes anything.
- All data is cached locally on your machine in `.cache/`. Nothing is sent to any third-party server.
- Revoke access anytime at [strava.com/settings/apps](https://www.strava.com/settings/apps).

---

## Rate Limits

Strava allows 200 requests per 15 minutes, 2,000 per day. Pacebird fetches your full history once and caches it permanently — typically 7–10 API calls for 1,000+ activities. After that, all page loads use the local cache. Hit **Refresh** in the app only when you want to pull new activities.

---

## License

MIT — see [LICENSE](LICENSE).
