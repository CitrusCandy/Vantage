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
| `ENABLE_ALERT_EVALUATION` | Optional | `true` | Master switch for internal production alerting engine |
| `ALERT_WORKER_ENABLED` | Optional | `true` | Enable background scheduler alive/stale checks |
| `ALERT_COOLDOWN_SECONDS` | Optional | `300` | Alert cooldown & deduplication window (seconds) |
| `ALERT_DB_LATENCY_THRESHOLD_MS` | Optional | `2000.0` | Database ping latency warning threshold (ms) |
| `ALERT_WORKER_GRACE_PERIOD_SECONDS` | Optional | `1800` | Worker execution grace period beyond cadence (seconds) |
| `ALERT_SOURCE_STALE_HOURS` | Optional | `24.0` | Inactive enabled source stale threshold (hours) |
| `ALERT_PIPELINE_FAILURE_THRESHOLD` | Optional | `2` | Recent pipeline failure count threshold |
| `ALERT_PIPELINE_LATENCY_THRESHOLD_MS` | Optional | `30000.0` | Pipeline execution high latency threshold (ms) |
| `ALERT_SOURCE_FAILURE_THRESHOLD` | Optional | `3` | Source scraping consecutive failure threshold |
| `ALERT_LLM_FAILURE_THRESHOLD` | Optional | `2` | Perspective LLM provider failure threshold |
| `ALERT_X_FAILURE_THRESHOLD` | Optional | `3` | X scraper consecutive failure threshold |
| `ALERT_MAX_RESOLVED_HISTORY` | Optional | `50` | Maximum resolved alerts kept in in-memory history |
| `OPS_RETENTION_DAYS` | Optional | `30` | Max age in days for pipeline runs, source executions, and worker cycles |
| `ALERT_RETENTION_DAYS` | Optional | `90` | Max age in days for resolved operational alerts |
| `NEXT_PUBLIC_API_URL` | Optional (Frontend) | `http://localhost:8000/api` | Base URL for FastAPI backend proxy |

---

## API & Operations Highlights

- `GET /api/topics/trending` — Top viral topics ranked via multi-source velocity, reach, and time decay.
- `POST /api/topics` — Create topic query.
- `GET /api/topics/{slug}` — Retrieve topic and its synthesized perspectives.
- `POST /api/topics/{slug}/run-pipeline` — Execute full end-to-end flow: Ingestion $\rightarrow$ Staging $\rightarrow$ Merge $\rightarrow$ HDBSCAN Clustering $\rightarrow$ LLM Synthesis.
- `GET /api/ops/overview` — High-level operational health, incident readiness timestamps, and alert counts.
- `GET /api/ops/alerts` — Active alerts, severity (`info`, `warning`, `critical`), occurrence counts, and resolved history.
- `GET /api/ops/history` — Query historical operational audit logs with filtering (`type`, `component`, `status`, `topic_slug`, `start_time`, `end_time`) and bounded pagination.
- `POST /api/ops/alerts/evaluate` — Trigger on-demand alert evaluation pass (protected by `X-Ops-Key`).
- `GET /api/ops/pipeline-metrics` — Multi-stage pipeline latency telemetry, median durations, and slowest stages.
- `GET /api/ops/source-health` — Fault-isolated reliability status for Google News, Reddit, X, and OpenAI.
- `GET /api/workers/status` — Inspect background scheduler status.
- `POST /api/workers/refresh-trending` — Trigger background topic refresh cycle.

---

## Persistent Operational History & Retention

### 1. Database Schema
Operational metadata is persisted to PostgreSQL tables to survive process restarts:
- `pipeline_runs`: Execution IDs, topic slugs, stages breakdown, sample sizes, and sanitized error types.
- `source_executions`: Scraping durations, item yields, statuses (`success`/`failed`/`timeout`), and masked error summaries.
- `worker_cycles`: Background scheduler cycles, topics considered/refreshed/skipped/failed.
- `operational_alerts`: Active and historical alert states, severity, occurrence counts, and resolution timestamps.

### 2. Retention Cleanup Command
To prune expired operational records while preserving active alerts:
```bash
python -m app.database.cleanup_ops_history --days 30 --alerts-days 90
```
Use `--dry-run` to inspect candidate row counts without deleting.

---

## Production Monitoring, Alerting & Incident Response

### 1. Alert Evaluation Rules & Severities

| Rule Name | Component | Severity | Condition |
| :--- | :--- | :--- | :--- |
| `database_unavailable` | `database` | **CRITICAL** | Database connection ping (`SELECT 1`) fails or times out |
| `database_high_latency` | `database` | **WARNING** | Database ping latency exceeds `ALERT_DB_LATENCY_THRESHOLD_MS` |
| `worker_stopped` | `worker` | **CRITICAL / WARNING** | Scheduler crashed with error (Critical) or inactive (Warning) |
| `worker_stale` | `worker` | **WARNING** | Worker last run exceeds `(interval * 3600) + grace_period` |
| `source_failure_spike` | `source:<name>` | **CRITICAL / WARNING** | Enabled source failures exceed `ALERT_SOURCE_FAILURE_THRESHOLD` |
| `source_stale` | `source:<name>` | **WARNING** | Enabled source has no successful ingestion within `ALERT_SOURCE_STALE_HOURS` |
| `llm_repeated_failures` | `llm:openai` | **CRITICAL** | OpenAI / LLM perspective synthesis failures exceed threshold |
| `x_scraper_repeated_failures` | `source:x` | **WARNING** | X scraper consecutive failures/timeouts exceed threshold |
| `pipeline_failure_spike` | `pipeline` | **CRITICAL** | Recent pipeline runs have $\ge 2$ failures in last 5 runs |
| `pipeline_high_latency` | `pipeline` | **WARNING** | Pipeline execution duration exceeds `ALERT_PIPELINE_LATENCY_THRESHOLD_MS` |

### 2. Cooldown & Deduplication Behavior

- Consecutive failures for the same `(rule_name, component)` do not flood logs or UI with duplicate records.
- Instead, the existing active alert updates `last_seen`, increments `occurrence_count`, and refreshes metadata.
- On server restart, active alerts are loaded from the database so occurrence counts continue incrementing properly.
- When metrics normalize on subsequent evaluation passes, the alert is automatically marked `status = "resolved"`, stamped with `resolved_at`, and archived into `resolved_history`.

### 3. How to Manually Evaluate Alerts

- **Via Operations UI**: Navigate to `/ops` and click **Evaluate Alerts** (uses configured `X-Ops-Key`).
- **Via API**:
  ```bash
  curl -X POST http://localhost:8000/api/ops/alerts/evaluate \
    -H "X-Ops-Key: your_admin_ops_key_here"
  ```

### 4. How to Safely Disable Alerts

- Set `ENABLE_ALERT_EVALUATION=false` to globally disable alert evaluation.
- Set `ALERT_WORKER_ENABLED=false` to suppress worker stoppage alerts in environments where workers run out-of-process.

---

## Database Backup, Integrity & Disaster Recovery

For complete disaster recovery runbooks, RPO/RTO parameters, and test restore workflows, refer to [`docs/disaster-recovery.md`](docs/disaster-recovery.md).

### 1. Backup CLI Commands

```bash
# Create a logical PostgreSQL dump (with auto-compression & SHA-256 checksum)
python -m app.database.backup --create

# Dry-run safety inspection (validates parameters and paths without writing dump)
python -m app.database.backup --create --dry-run

# List cataloged database backups
python -m app.database.backup --list

# Verify integrity of a specific backup snapshot
python -m app.database.backup --verify vantage_backup_20260916_205500

# Run retention pruning lifecycle
python -m app.database.backup --cleanup --retention-count 7 --retention-days 30
```

### 2. Safe CLI Restoration (HTTP-Disabled)

> [!CAUTION]
> Destructive database restores are intentionally **BLOCKED** from HTTP endpoints to prevent accidental production overwrite. Restorations strictly require authenticated CLI access with the explicit `--confirm` flag.

```bash
# Restore a verified backup dump into target database
python -m app.database.restore --backup backups/vantage_backup_20260916_205500.sql.gz --confirm

# Dry-run pre-flight validation
python -m app.database.restore --backup backups/vantage_backup_20260916_205500.sql.gz --dry-run
```

---

## Resilience, Fault Tolerance & Graceful Degradation

Vantage News provides mission-critical fault tolerance across all external integrations and platform dependencies:

1. **Bounded Retries with Jitter**: Exponential backoff with configurable jitter (`full`, `equal`, `decorrelated`) prevents retry storms against third-party providers.
2. **Circuit Breaker State Machine**: Circuit breakers (`CLOSED`, `OPEN`, `HALF_OPEN`) protect all provider boundaries (`google_news`, `reddit`, `x`, `openai_synthesis`, `embeddings`, `redis_governance`, `trend_discovery`). Fast-fails immediately when `OPEN` to conserve worker threads.
3. **Graceful Degradation Fallbacks**:
   - Partial scraper failures merge remaining sources cleanly without state corruption.
   - LLM outages fall back to fail-soft extractive summaries with clear degradation notices.
   - Redis disruptions fall back to in-process memory governance with zero dropped requests.
4. **Cooperative Cancellation & Timeouts**: `CancellationToken` and bounded timeouts ensure graceful worker shutdown without leaked locks or orphan threads.
## Observability, SLOs, Metrics & Prometheus Exposition

Vantage News provides comprehensive production observability with bounded low-cardinality metrics, real-time Service Level Objective (SLO) compliance, error budget tracking, and Prometheus exposition:

1. **Prometheus Exporter (`/metrics`)**: Standard Prometheus exposition format exposing all platform counters, gauges, and histograms.
2. **Platform Metrics API (`GET /api/ops/metrics`)**: JSON snapshot of all registered platform metrics and rolling sliding-window percentiles ($P_{50}, P_{90}, P_{95}, P_{99}$).
3. **SLO Engine (`GET /api/ops/slos`)**: Tracks 9 core platform reliability objectives:
   - `api_availability` ($\ge 99.9\%$), `api_latency_p95` ($\le 250\text{ ms}$), `ingestion_freshness` ($\le 1800\text{ s}$)
   - `pipeline_success_rate` ($\ge 99.0\%$), `provider_health` ($\ge 95.0\%$), `worker_liveness` ($\ge 99.5\%$)
   - `database_query_p95` ($\le 50\text{ ms}$), `redis_governance_uptime` ($\ge 99.9\%$), `resource_budget_compliance` ($\ge 95.0\%$)
4. **Error Budget & Burn Rates**: Evaluates remaining error budget percentage and multi-window burn rate multipliers ($1\times, 2\times, 5\times, 14.4\times$) with persistent violation logging.
5. **Interactive Dashboard**: Modern `/ops` interface visualizing SLO health scores, error budget progress bars, burn rate badges, and live latency percentiles.

---

## Production Security, Compliance & Data Protection

Vantage News enforces defense-in-depth security across all architectural layers:

1. **Centralized RBAC Hierarchy & Dual-Key Rotation**:
   - 4-Tier privilege levels: `public` (read-only), `operator` (telemetry/evaluations/discovery), `admin` (reprocess/backups/disaster recovery/circuit reset), and `security_admin` (audit trail & key rotation governance).
   - Constant-time secret verification (`hmac.compare_digest`) resisting timing side-channel attacks.
   - Dual-key rotation readiness: Primary (`*_API_KEY`) and secondary (`*_API_KEY_SECONDARY`) keys allow seamless credential rotation with zero downtime.
2. **Multi-Stage SSRF Protections**:
   - Scheme whitelisting (`http`/`https`), embedded credential blocking (`user:pass@host`), and domain TLD restrictions.
   - Decodes integer decimal (`2130706433`), hexadecimal (`0x7f000001`), octal (`0177.0.0.1`), and dotted hex IP representations.
   - Rejects RFC 1918, loopback, link-local, carrier-grade NAT, and cloud metadata targets (`169.254.169.254`, `100.100.100.200`, `metadata.google.internal`).
   - Pre-flight DNS resolution validation against internal subnets.
3. **Security Headers & Request Body Size Limiter**:
   - Middleware enforces HSTS (`max-age=31536000`), CSP, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, Permissions-Policy, COOP, and CORP on all HTTP responses.
   - Enforces 2MB maximum payload size limit, rejecting oversized request bodies with HTTP 413 Payload Too Large.
4. **Structured Security Audit Logging & APIs**:
   - Tracks all security-sensitive actions, authentication failures, authorization denials, backup creations, configuration modifications, and circuit resets in `security_audit_logs`.
   - Emits structured JSON logs concurrently for SIEM integration.
   - Paginated search and filtering endpoint: `GET /api/ops/audit-logs`.
5. **Data Retention & Privacy Controls**:
   - Automated retention pruning in `cleanup_ops_history.py` for operational history (30d), resolved alerts (90d), and security audit logs (180d) with dry-run support.
   - Zero raw content or credential tokens persisted in operational or audit logs.

---

## Automated Quality Gates & Incident Readiness Checklist

Before pushing changes or deploying to production, execute the automated verification gates:

```bash
# 1. Run release verification gate (checks files, env templates, secret scan, imports, probes)
python scripts/verify_release.py

# 2. Run incident readiness checklist (probes, database ping, worker state, source freshness, alerts pass)
python scripts/incident_readiness.py

# 3. Run disaster recovery & backup readiness checklist
python scripts/disaster_recovery_check.py

# 4. Run complete backend test suite (238 unit, integration, resilience, observability, and security compliance tests)
pytest backend/tests/ -v

# 5. Verify frontend production compilation
cd frontend && npm run build
```