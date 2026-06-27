FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Ensure data directory exists (Railway mounts a volume here)
RUN mkdir -p /app/data/cache

# Railway injects $PORT at runtime
EXPOSE 8080

CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120
