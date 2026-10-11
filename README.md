# Prudent backend

FastAPI service for historical heatwave analysis: canonical geography, Open-Meteo
ingestion, versioned heatwave science (`heatwave-v2`), background jobs, MongoDB
persistence with deterministic cache reuse.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit origins / mongo as needed
uvicorn app.main:app --reload --port 8000
# API docs: http://localhost:8000/docs ; health: /health ; ready: /ready
```

MongoDB is optional for development: without a reachable `MONGODB_URI` the app
runs on an in-memory store (data is not shared across restarts). For persistence:

```bash
docker compose up -d mongo   # or full stack: docker compose up --build
```

## Tests / checks

```bash
pip install pytest pytest-asyncio httpx scipy pandas numpy
python -m pytest tests -q
```

## Endpoints (`/api/v1`)

- `GET /geography/search?q=` · `GET /geography/regions/{id}` ·
  `GET /geography/regions/{id}/boundary` · `GET /geography/metadata`
- `GET /climate/hazards` · `GET /climate/coverage`
- `POST /analyses` (202 + job) · `GET /analyses/{id}` ·
  `GET /jobs/{id}` · `POST /jobs/{id}/cancel`

## Methodology

`heatwave-v2`: 1991–2020 baseline sampled with the same points/aggregation as the
analysis series (v1 used the centroid only), calendar-day 95th percentile (±7-day window,
≥100 obs), ≥3 consecutive exceedance days. Missing days break runs and are never
bridged or treated as zero. Trends are OLS estimates with p-values; incomplete
years (<80% valid days) are flagged and excluded from trend interpretation. See `app/science/heatwave.py`.
