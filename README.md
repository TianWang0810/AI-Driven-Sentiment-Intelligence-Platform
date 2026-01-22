# Sentiment Intelligence Platform

This project is a production-grade backend platform for asynchronous media sentiment intelligence. It is designed to reliably crawl large-scale user-generated text, analyze sentiment using hybrid AI agents, and produce actionable insights through a fault-tolerant, extensible pipeline.

## Features

- **Multi-source Crawling**: YouTube comments, webpages, RSS feeds
- **Hybrid Sentiment Analysis**: TextBlob (fast) + OpenAI (accurate)
- **Structured Output**: Score, confidence, topics, action recommendation, rationale
- **Automatic Alerts**: Triggers when negative ratio exceeds threshold
- **Production Ready**: Docker, Gunicorn, PostgreSQL, concurrent workers

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/yourusername/sentiment-intel-platform.git
cd sentiment-intel-platform

# Copy environment file
cp .env.example .env

# Edit .env with your OpenAI API key
vim .env
```

### 2. Start with Docker

```bash
# Start all services
docker compose up -d

# View logs
docker compose logs -f

# Check health
curl http://localhost:8000/healthz
```

### 3. Create a Job

```bash
# Create a YouTube analysis job
curl -X POST http://localhost:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "source_type": "youtube",
    "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "options": {"max_items": 50}
  }'

# Response:
# {"job_id": 1, "status": "queued"}
```

### 4. Check Job Status

```bash
# Get job details
curl http://localhost:8000/v1/jobs/1

# Response:
# {
#   "id": 1,
#   "source_type": "youtube",
#   "status": "succeeded",
#   "progress": 50,
#   "stats": {
#     "total_items": 50,
#     "label_counts": {"positive": 20, "neutral": 25, "negative": 5},
#     "negative_ratio": 0.1,
#     ...
#   }
# }
```

### 5. Get Results

```bash
# Get analyzed items (with pagination)
curl "http://localhost:8000/v1/jobs/1/items?limit=10&offset=0"

# Get alerts
curl http://localhost:8000/v1/jobs/1/alerts
```

## API Reference

### POST /v1/jobs

Create a new sentiment analysis job.

**Request:**
```json
{
  "source_type": "youtube|webpage|rss",
  "source_url": "https://...",
  "options": {
    "max_items": 100,
    "sort": "newest"
  }
}
```

**Response:**
```json
{
  "job_id": 1,
  "status": "queued"
}
```

### GET /v1/jobs/{id}

Get job status and details.

### GET /v1/jobs/{id}/items?limit=50&offset=0

Get analyzed items with pagination.

### GET /v1/jobs/{id}/alerts

Get alerts triggered for this job.

### GET /healthz

Health check endpoint.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | - | PostgreSQL connection string |
| `OPENAI_API_KEY` | - | OpenAI API key |
| `OPENAI_MODEL` | gpt-4o-mini | Model to use |
| `WORKER_POLL_INTERVAL` | 5 | Seconds between job checks |
| `WORKER_MAX_RETRIES` | 3 | Max retry attempts |
| `WORKER_LOCK_TIMEOUT` | 600 | Stuck job timeout (seconds) |
| `ALERT_NEGATIVE_RATIO_THRESHOLD` | 0.3 | Trigger alert when ratio exceeds |
| `SENTIMENT_TB_WEIGHT` | 0.3 | TextBlob weight in fusion |
| `SENTIMENT_LLM_WEIGHT` | 0.7 | LLM weight in fusion |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   API (Flask + Gunicorn)                    │
│       POST /v1/jobs │ GET /v1/jobs/{id} │ /healthz         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PostgreSQL Database                       │
│    jobs │ items │ alerts (FOR UPDATE SKIP LOCKED)           │
└─────────────────────────────────────────────────────────────┘
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
   ┌──────────┐        ┌──────────┐        ┌──────────┐
   │ Worker 1 │        │ Worker 2 │        │ Worker N │
   │          │        │          │        │          │
   │ Crawler→ │        │ Crawler→ │        │ Crawler→ │
   │ Sentiment│        │ Sentiment│        │ Sentiment│
   │ →Actions │        │ →Actions │        │ →Actions │
   └──────────┘        └──────────┘        └──────────┘
```

## Development

### Local Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
python -m textblob.download_corpora

# Start PostgreSQL (via Docker)
docker compose up -d db

# Initialize database
python -c "from app.extensions import init_db; init_db()"

# Run API server
flask --app app:create_app run --debug --port 8000

# Run worker (in another terminal)
python -c "from app.worker import run_worker; run_worker()"
```

### Run Migrations

```bash
# Generate new migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

## End-to-End Demo

```bash
# 1. Start services
docker compose up -d

# 2. Create job
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{"source_type":"youtube","source_url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ","options":{"max_items":20}}' \
  | jq -r '.job_id')

echo "Created job: $JOB_ID"

# 3. Wait for processing
sleep 30

# 4. Check status
curl -s http://localhost:8000/v1/jobs/$JOB_ID | jq

# 5. Get items
curl -s "http://localhost:8000/v1/jobs/$JOB_ID/items?limit=5" | jq

# 6. Get alerts
curl -s http://localhost:8000/v1/jobs/$JOB_ID/alerts | jq
```

## License

MIT