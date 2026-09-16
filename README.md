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

## API Highlights

- `GET /api/topics/trending` — Top viral topics ranked via multi-source velocity, reach, and time decay.
- `POST /api/topics` — Create topic query.
- `GET /api/topics/{slug}` — Retrieve topic and its synthesized perspectives.
- `POST /api/topics/{slug}/run-pipeline` — Execute full end-to-end flow: Ingestion $\rightarrow$ Staging $\rightarrow$ Merge $\rightarrow$ HDBSCAN Clustering $\rightarrow$ LLM Synthesis.
- `GET /api/workers/status` — Inspect background scheduler status.
- `POST /api/workers/refresh-trending` — Trigger background topic refresh cycle.
