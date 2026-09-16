# Vantage News

**Multi-Perspective Real-Time Public Discourse Intelligence Platform**

Vantage News aggregates public discourse across **Google News RSS**, **Reddit**, and **X (Twitter)**, eliminates bots and duplicates via MinHash/LSH, clusters viewpoints using HDBSCAN with normalized vector embeddings, and synthesizes structured, traceable perspectives using OpenAI models.

---

## Architecture Overview

```text
                                 [ User Enters Topic / Query ]
                                               ↓
                                   [ Topic Creation & Slug ]
                                               ↓
                  ┌────────────────────────────┼────────────────────────────┐
                  ↓                            ↓                            ↓
         [ Google News RSS ]              [ Reddit API ]                 [ X Scraper ]
                  ↓                            ↓                            ↓
         (raw_google_news)               (raw_reddit)                    (raw_x)
                  └────────────────────────────┼────────────────────────────┘
                                               ↓
                                   [ Merge & Normalization ]
                                               ↓
                                      (combined_raw_data)
                                               ↓
                                [ MinHash/LSH Deduplication ]
                                [ Bot & Spam Heuristics ]
                                               ↓
                                [ OpenAI Embeddings Engine ]
                                               ↓
                                [ HDBSCAN Vector Clustering ]
                                               ↓
                               [ Representative Sample Extraction ]
                                               ↓
                                [ LLM Perspective Synthesis ]
                                               ↓
                                    (perspectives table)
                                               ↓
                               [ Next.js Classy Showcase UI ]
```

---

## Repository Structure

```text
vantage-news/
├── backend/
│   ├── app/
│   │   ├── api/                # FastAPI routes (topics, workers)
│   │   ├── database/           # SQLAlchemy models, schemas, seed script
│   │   ├── ingestion/          # Source scrapers (Google News, Reddit, X, Merge)
│   │   ├── processing/         # MinHash/LSH, Bot Detection, Embeddings, HDBSCAN
│   │   ├── llm/                # OpenAI JSON synthesis & representative sample pipeline
│   │   └── workers/            # Background scheduler, trending score, topic refresh
│   ├── tests/                  # 43 unit and end-to-end integration tests
│   └── requirements.txt
├── frontend/
│   ├── app/                    # Next.js App Router (Home, Topic Showcase)
│   ├── components/             # Perspective cards, filters, charts, drawers, navbar
│   └── lib/                    # API client, TypeScript definitions, formatting utils
└── docs/                       # Architecture diagrams & specifications
```

---

## Quick Start Guide

### 1. Backend Setup

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env to set your OPENAI_API_KEY (and optional REDDIT API keys)

# (Optional) Seed database with demo topics & perspectives
python -m app.database.seed

# Run tests (100% offline, mocked providers)
pytest -v

# Start FastAPI development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

FastAPI Swagger Documentation will be available at: `http://localhost:8000/docs`

---

### 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Build production bundle
npm run build

# Start Next.js development server
npm run dev
```

Frontend will be running at: `http://localhost:3000`

---

### 3. Docker Compose (Full Stack)

To run the full stack (PostgreSQL + FastAPI backend + Next.js frontend) in containerized production mode:

```bash
# Start all services
docker compose up --build -d

# View logs
docker compose logs -f

# Stop all services
docker compose down
```

Services will be accessible at:
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:8000`
- PostgreSQL: `localhost:5432`

---

## Production Configuration Audit

| Variable | Category | Default / Local Dev | Description |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | **Required (Prod)** | `postgresql://postgres:password@localhost:5432/vantage_news` | PostgreSQL or SQLite connection URI |
| `OPENAI_API_KEY` | **Required (Prod)** | *(Empty in tests / offline mock)* | OpenAI API key for embeddings & synthesis |
| `OPENAI_PERSPECTIVE_MODEL` | Optional | `gpt-4o-mini` | OpenAI chat completion model |
| `EMBEDDING_PROVIDER` | Optional | `openai` | Embedding model provider (`openai` / `mock`) |
| `LLM_PROVIDER` | Optional | `openai` | Perspective synthesis provider (`openai` / `mock`) |
| `REDDIT_CLIENT_ID` | Optional | *(Empty / public fallback)* | Reddit developer script client ID |
| `REDDIT_CLIENT_SECRET` | Optional | *(Empty / public fallback)* | Reddit developer client secret |
| `REDDIT_USER_AGENT` | Optional | `VantageNews/2.0.0` | Reddit custom user-agent header |
| `TRENDING_W1_VELOCITY` | Optional | `0.35` | Trending score weight: 24h mention velocity |
| `TRENDING_W2_SOURCES` | Optional | `0.25` | Trending score weight: unique platforms count |
| `TRENDING_W3_ENGAGEMENT` | Optional | `0.25` | Trending score weight: log engagement rate |
| `TRENDING_W4_DECAY` | Optional | `0.15` | Trending score penalty weight: exponential time decay |
| `WORKER_INTERVAL_HOURS` | Optional | `2.0` | Background worker cadence interval (hours) |
| `MIN_TRENDING_SCORE_REFRESH` | Optional | `0.20` | Minimum score threshold for automated ML refresh |
| `STAGNANT_HOURS_THRESHOLD` | Optional | `48.0` | Inactivity threshold before marking topic stagnant |
| `DECAY_HALF_LIFE_HOURS` | Optional | `24.0` | Half-life constant for exponential score decay |
| `NEXT_PUBLIC_API_URL` | Optional (Frontend) | `http://localhost:8000/api` | Base URL for FastAPI backend proxy |

---

## API Highlights

- `GET /api/topics/trending` — Top viral topics ranked via multi-source velocity, reach, and time decay.
- `POST /api/topics` — Create topic query.
- `GET /api/topics/{slug}` — Retrieve topic and its synthesized perspectives.
- `POST /api/topics/{slug}/run-pipeline` — Execute full end-to-end flow: Ingestion $\rightarrow$ Staging $\rightarrow$ Merge $\rightarrow$ HDBSCAN Clustering $\rightarrow$ LLM Synthesis.
- `GET /api/workers/status` — Inspect background scheduler status.
- `POST /api/workers/refresh-trending` — Trigger background topic refresh cycle.

