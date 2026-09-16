# Vantage News - Revised System Architecture

## Overview

Vantage News ingests real-time public discourse across multiple independent platforms (Google News RSS, Reddit, and X), stages the records into isolated platform-specific tables, merges and normalizes them into a unified `CombinedRawData` dataset, applies MinHash/LSH deduplication and bot/spam filtering, clusters topics using HDBSCAN over dense vector embeddings, synthesizes multi-perspective summaries using LLMs, and presents them in a modern web showcase.

---

## Architecture Flow

```text
Google News ──┐
Reddit ───────┼──> Staging Tables
X ────────────┘
                    ↓
              Merge / ETL
                    ↓
           CombinedRawData
                    ↓
        Normalize + Filtering
                    ↓
              HDBSCAN
                    ↓
           LLM Perspectives
                    ↓
             Frontend
```

---

## Key Pipeline Stages

1. **Independent Scrapers & Staging Layer**:
   - **Google News RSS**: Parsed via `feedparser` into `raw_google_news` (`title`, `link`, `source_name`, `published_at`, `snippet`, `slug_id`).
   - **Reddit**: Ingested via official PRAW / REST endpoints into `raw_reddit` (`post_id`, `body`, `score`, `num_comments`, `subreddit`, `author`, `created_utc`, `slug_id`).
   - **X (Twitter)**: Scraped with fail-soft isolation into `raw_x` (`tweet_id`, `text`, `likes`, `retweets`, `replies`, `handle`, `posted_at`, `slug_id`).
   - **Failure Isolation**: Individual scraper rate-limits or network failures never impact other sources.

2. **Merge & Normalization ETL**:
   - Reads `raw_google_news`, `raw_reddit`, and `raw_x` for a given topic.
   - Unions available records into `combined_raw_data`.
   - Preserves source attribution, original URLs, engagement metrics, and timestamps.
   - Idempotent: repeated merge runs do not create duplicate records.
   - Compound index on `(slug_id, source, created_at)`.

3. **Discourse Preprocessing & Deduplication**:
   - Text normalization, HTML entity unescaping, and URL stripping for content matching.
   - **MinHash + LSH**: Locality-Sensitive Hashing detects exact and near-duplicate stories across different platforms (cross-source deduplication).
   - **Bot/Spam Heuristics**: Link density, spam/promo patterns, bot usernames, and extreme repetition flag `is_flagged_bot = True` without deleting raw records.
   - **Minimum-Volume Gate**: Enforces minimum usable item volume (default: 30 items) before downstream clustering.

4. **Embedding & HDBSCAN Clustering Layer**:
   - Generates dense vector embeddings using OpenAI `text-embedding-3-small` (or local embedding models).
   - Cosine-equivalent $L_2$ vector normalization.
   - Density-based HDBSCAN clustering rejecting outliers/noise (`label = -1`).
   - Extracts top representative samples and persists `ClusterRun` execution metadata.

5. **LLM Perspective Synthesis**:
   - Extracts core perspective stances, arguments, and representative citations from top clusters.

6. **Production Monitoring, Alerting & Incident Readiness**:
   - **Centralized Alert Manager (`app.core.alerting`)**:
     - Evaluates system telemetry, database availability, background worker scheduler, source error spikes, pipeline failure rates, and stage latencies.
     - Severity calculation: `info`, `warning`, `critical`.
     - Deduplication & Cooldown: Consecutive occurrences increment `occurrence_count` and update `last_seen` without spamming duplicate alert instances.
     - Automatic Resolution: Resolves active alerts once underlying metrics normalize and records to `resolved_history`.
   - **Operational Incident Checklist (`scripts/incident_readiness.py`)**:
     - Verifies health probes (`/health`, `/ready`), DB ping latency, worker state, source freshness, pipeline telemetry, and runs an alert evaluation pass.
     - Returns exit code 0 when nominal; non-zero if critical failures are present.

7. **Persistent Operational History & Auditability**:
   - **PostgreSQL Operational Tables**:
     - `pipeline_runs`: Tracks run ID, topic slug/ID, duration, sample size, cluster count, perspective count, failure stage, and sanitized error types.
     - `source_executions`: Tracks scraper executions, operations (fetch/scrape), duration ms, item count, status (success/failed/timeout), and sanitized error types.
     - `worker_cycles`: Tracks worker cycle ID, duration, topics considered/refreshed/skipped/failed, and status.
     - `operational_alerts`: Persists active and historical alert records (`alert_id`, `rule_name`, `severity`, `component`, `message`, `occurrence_count`, `first_seen`, `last_seen`, `resolved_at`).
   - **Restart Resilience**: Active alert states and occurrence counts persist across process restarts, ensuring deduplication works continuously.
   - **Data Retention & Maintenance**:
     - Configurable retention: `OPS_RETENTION_DAYS` (default: 30) and `ALERT_RETENTION_DAYS` (default: 90).
     - Maintenance command: `python -m app.database.cleanup_ops_history [--days 30] [--alerts-days 90] [--dry-run]`.
     - Only resolved alerts are purged; active alerts are always preserved until resolved.

8. **Frontend Showcase & Operations Dashboard**:
   - Classy multi-perspective showcase interface.
   - Operations dashboard (`/ops`) featuring live telemetry, active alerts panel with severity indicators, incident status cards, manual evaluation triggers, disaster recovery management, and an audit history log with filtering and pagination.

9. **Database Backup, Integrity & Disaster Recovery Subsystem**:
   - **PostgreSQL Logical Dumps**: Automated `pg_dump` creation with configurable compression (`gzip`) and timestamped file nomenclature.
   - **Metadata Tracking (`backup_records`)**: Persists backup ID, filename, created/completed timestamps, status, size, and SHA-256 checksum with zero credentials/payloads stored in DB.
   - **Integrity Verification**: Automatic in-flight SHA-256 computation and file header validation preventing corrupt snapshots from being marked healthy.
   - **Retention Lifecycle**: Prunes expired backup files from storage and updates DB catalog according to `BACKUP_RETENTION_COUNT` and `BACKUP_RETENTION_DAYS`.
   - **Restoration Tooling**: Safe, verified CLI restoration (`python -m app.database.restore --confirm`) with explicit HTTP-endpoint isolation for maximum disaster protection.

10. **Production Cost Control & Distributed Resource Governance** (`app.core.resource_governor`):
    - **Pluggable Governance Backend Abstraction**: `BaseGovernanceStore` interface with concrete implementations:
      - `InMemoryGovernanceStore`: Thread-safe, in-process sliding window rate limiting and local semaphore concurrency tracking.
      - `RedisGovernanceStore`: Distributed rate limiting, concurrency leasing with TTLs, synthesis tracking, and cost metric tracking across multiple worker processes using atomic Lua scripts.
      - `GovernanceCoordinator`: Manages backend selection (`GOVERNANCE_BACKEND=memory|redis`), connection lifecycle, and automatic fallback to `InMemoryGovernanceStore` on network, Redis down, or timeout errors.
    - **Atomic Distributed Rate Limiting**: Redis Lua-backed sliding-window token bucket for all public/expensive and operational endpoints. Returns HTTP 429 with `Retry-After` header. Zero multi-worker race conditions.
    - **Distributed Concurrency Leasing with TTLs**: Atomic slot reservation (`acquire`/`release`/`slot_count`) with automatic lease expiration (`CONCURRENCY_LEASE_TTL_SECONDS`) ensuring dead or crashed workers never leak concurrency slots.
    - **Resource Budget Manager**: Configurable limits for ingestion items per source, merged items per topic, processing items, embedding batch sizes, embeddings per run, clusters to LLM, samples per cluster, synthesis calls per topic/hour, concurrent pipelines, concurrent source calls, backup operations, and maintenance operations. All limits loaded from environment variables with sensible defaults.
    - **Cost-Control Tracking**: Distributed atomic counters for embedding calls/items, synthesis calls/estimated tokens, pipeline invocations, and external requests per source. Metadata-only tracking — never stores prompt contents or raw source text.
    - **Concurrency Governor**: Bounded semaphore-style acquire/release for pipelines, source calls, backups, and maintenance. Context manager support guarantees slot release on failure. `release_all()` on scheduler shutdown.
    - **External API Request Governance**: Per-source (Google News, Reddit, X, OpenAI) settings for concurrent request limits, timeouts, max retries, exponential backoff, and hourly request budgets. Fail-soft: returns empty/default on budget exhaustion rather than crashing.
    - **Utilization Monitoring**: Configurable warning (70%) and critical (90%) thresholds. Budget utilization status classification (`normal`, `warning`, `critical`) exposed via operational endpoints and frontend dashboard.
    - **Operational Endpoints**: `GET /api/ops/resource-usage` and `GET /api/ops/resource-budgets` return comprehensive governance metrics, backend type, and fallback status. Both protected by `X-Ops-Key` guard.
    - **Frontend Governance Dashboard**: Resource Governance section on `/ops` with backend status badge, fallback alerts, rate limit utilization bars, concurrency slot indicators, cost tracking counters, budget utilization warnings, and external source governance panels.
