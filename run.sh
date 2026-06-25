#!/bin/bash
set -e

echo "🐦 Starting Pacebird..."

# Check .env exists
if [ ! -f .env ]; then
  echo "❌  .env not found. Copy .env.example and fill in your Strava credentials."
  echo "    cp .env.example .env"
  exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
  echo "❌  python3 not found. Install Python 3.9+."
  exit 1
fi

# Install dependencies if needed
if ! python3 -c "import flask, requests, dotenv, PIL" 2>/dev/null; then
  echo "📦  Installing dependencies..."
  pip install -r requirements.txt
fi

echo "✅  Open http://localhost:8080 in your browser"
echo "    Press Ctrl+C to stop"
echo ""

python3 app.py
