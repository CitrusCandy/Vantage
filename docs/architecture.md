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

11. **Resilience, Fault Tolerance & Graceful Degradation** (`app.core.resilience`):
    - **Bounded Retries with Exponential Backoff & Jitter**: Configurable `BackoffStrategy` supporting full, equal, and decorrelated jitter distributions to eliminate thundering herd behavior against third-party APIs.
    - **Circuit Breakers**: State machine (`CLOSED`, `OPEN`, `HALF_OPEN`) protecting all external boundaries (`google_news`, `reddit`, `x`, `openai_synthesis`, `embeddings`, `redis_governance`, `trend_discovery`). Fast-fails immediately when OPEN without consuming socket/thread capacity.
    - **Centralized Registry & Observability**: `CircuitBreakerRegistry` maintains platform-wide breaker states, short-circuit counts, failure timestamps, and exposes `GET /api/ops/resilience` and `POST /api/ops/resilience/reset`.
    - **Cooperative Cancellation & Timeouts**: `CancellationToken` and `TimeoutScope` propagation across long-running pipelines and thread pools prevents orphan execution during worker restarts or client disconnects.
    - **Database Transient Lock Retries**: `safe_db_operation` helper transparently retries SQLite/PostgreSQL transient concurrency locks.
    - **Graceful Degradation & Partial Failure Isolation**:
      - Single-source scrapers failing (e.g. Google News 503 or Reddit rate-limit) do not crash ingestion; remaining sources merge cleanly into `combined_raw_data`.
      - LLM synthesis failure or open circuit breaker degrades gracefully to extractive perspective briefs with clear degradation notices instead of database corruption or unhandled 500 errors.
      - Redis shared governance failure automatically degrades to in-memory local coordination with zero dropped requests, followed by self-healing reconnect probes.
    - **Graceful Worker Lifecycle**: Background scheduler cleans up all held locks, concurrency slots, and heartbeat keys on shutdown (`SIGINT`/`SIGTERM`), and safely reclaims orphaned locks on startup.
    - **Enriched Health/Readiness Probes**: `/health` (liveness) and `/ready` (readiness) accurately distinguish `healthy`, `degraded` (circuit open or Redis fallback active), and `unready` (database unreachable) without exposing secrets.

12. **Observability, SLOs, Metrics & Automated Alerting** (`app.core.metrics`, `app.core.slo`, `app.core.alerting`):
    - **Low-Cardinality Structured Metrics Registry**:
      - Thread-safe metric primitives: `CounterMetric`, `GaugeMetric`, and `HistogramMetric`.
      - Strict memory bounds: label combinations capped at 200 to eliminate high-cardinality label explosion risks.
      - Sliding-window percentiles ($P_{50}, P_{90}, P_{95}, P_{99}$, mean, min, max, sum, count) for HTTP API, pipeline execution, source scrapers, and database queries.
      - Prometheus Exposition: `/metrics` endpoint exports standard Prometheus text metrics.
    - **Service Level Objectives (SLOs) & SLI Engine**:
      - 9 Platform SLOs tracked with real-time compliance evaluation:
        1. `api_availability`: Target $\ge 99.9\%$ (HTTP non-5xx requests)
        2. `api_latency_p95`: Target $\le 250\text{ ms}$ (95th percentile API response time)
        3. `ingestion_freshness`: Target $\le 1800\text{ s}$ ($30\text{ min}$ freshness)
        4. `pipeline_success_rate`: Target $\ge 99.0\%$ (Discourse pipeline completions)
        5. `provider_health`: Target $\ge 95.0\%$ (External scrapers success rate)
        6. `worker_liveness`: Target $\ge 99.5\%$ (Background scheduler active heartbeats)
        7. `database_query_p95`: Target $\le 50\text{ ms}$ (95th percentile database latency)
        8. `redis_governance_uptime`: Target $\ge 99.9\%$ (Native Redis vs memory fallback)
        9. `resource_budget_compliance`: Target $\ge 95.0\%$ (Operations operating within safety ceilings)
      - Real-time Error Budget calculation (% remaining) and multi-window burn rate tracking ($1\times, 2\times, 5\times, 14.4\times$).
      - Historical violation tracking persisted in `slo_violation_records` table.
    - **Automated Alerting Lifecycle & Flapping Suppression**:
      - Multi-domain alert evaluation: Database health, worker liveness, source error spikes, pipeline latency, open circuit breakers, and SLO violations.
      - Transition tracking with automatic structured recovery events (`alert_recovered`).
      - Rapid transition suppression (flapping prevention) ensuring alerts do not storm during transient blips.
    - **Ops API & Frontend Observability Dashboard**:
      - `GET /api/ops/slos`: Exposes full SLO status, error budgets, health score, and burn rates.
      - `GET /api/ops/metrics`: Exposes JSON snapshot of all platform metric distributions.
      - `GET /metrics`: Standard Prometheus metrics exporter.
      - Frontend `/ops` Dashboard: Real-time SLO cards, error budget progress bars, burn rate badges, and live latency percentiles viewer.

13. **Production Security, Compliance & Data Protection** (`app.core.security`, `app.core.audit`, `app.database.models.SecurityAuditLog`):
    - **Centralized Authentication & RBAC Hierarchy**:
      - 4-tier privilege hierarchy: `public` (read-only), `operator` (telemetry/evaluations/discovery), `admin` (pipeline reprocessing/backups/disaster recovery/circuit reset/worker lifecycle), and `security_admin` (audit trail & key rotation governance).
      - Constant-time secret verification using `hmac.compare_digest()` preventing timing side-channels.
      - Dual-key rotation readiness: Primary (`*_API_KEY`) and secondary (`*_API_KEY_SECONDARY`) keys allow seamless credential rotation without downtime.
    - **Multi-Stage SSRF Defense**:
      - Strict scheme whitelisting (`http`/`https`), embedded credential detection (`user:pass@host`), and domain TLD restrictions.
      - Decodes non-standard IP notations (integer decimal, hexadecimal, dotted octal/hex) and validates against RFC 1918, loopback, link-local, carrier-grade NAT, and cloud metadata networks (`169.254.169.254`, `100.100.100.200`, `metadata.google.internal`).
      - Optional DNS pre-flight verification prevents DNS rebinding to internal addresses.
    - **Comprehensive Security Headers & Size Limiter Middleware**:
      - Enforces HSTS (`max-age=31536000`), CSP (`default-src 'self'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, Permissions-Policy, COOP, and CORP on all HTTP responses.
      - Enforces 2MB maximum payload size limit, rejecting oversized request bodies with HTTP 413 Payload Too Large.
    - **Persistent Security Audit Logging**:
      - Tracks all security-sensitive actions, authentication failures, authorization denials, backup creations, configuration modifications, and circuit resets in `security_audit_logs`.
      - Emits structured JSON logs concurrently for SIEM integration.
      - Paginated search and filtering endpoint: `GET /api/ops/audit-logs`.
    - **Data Retention & Privacy Controls**:
      - Automated retention pruning in `cleanup_ops_history.py` for operational history (30d), resolved alerts (90d), and security audit logs (180d) with dry-run support.
      - Zero raw content or credential tokens persisted in operational or audit logs.


