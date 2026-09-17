# Disaster Recovery & Backup Readiness Runbook

## 1. Overview & Operational Scope

Vantage News incorporates a multi-tier database backup, integrity verification, and disaster recovery subsystem designed for PostgreSQL deployments.

This document details the Recovery Point Objective (RPO), Recovery Time Objective (RTO), backup cataloging, SHA-256 integrity verification, fail-safe CLI restoration workflows, test-restore validation, and incident response checklists.

---

## 2. RPO / RTO Assumptions & Targets

> [!NOTE]
> All targets below assume default configuration and standard PostgreSQL single-node or containerized deployments. Targets are configurable via environment variables.

| Metric | Target | Config / Mechanism | Notes & Dependencies |
| :--- | :--- | :--- | :--- |
| **RPO (Recovery Point Objective)** | $\le$ 24 hours (Default) | `BACKUP_INTERVAL_HOURS=24.0` | Configurable down to hourly cadence depending on cluster storage constraints. |
| **RTO (Recovery Time Objective)** | $\le$ 15 minutes | `python -m app.database.restore` | Depends on logical dump size and target database I/O bandwidth. |
| **Integrity Assurance** | 100% SHA-256 Verified | `BACKUP_VERIFY_AFTER_CREATE=true` | In-flight checksum computation + structural header validation. |
| **Retention Window** | 7 snapshots / 30 days | `BACKUP_RETENTION_COUNT=7`, `BACKUP_RETENTION_DAYS=30` | Older snapshots safely pruned via bounded retention lifecycle. |

---

## 3. Data Recoverability Matrix

### What Data is Recoverable
- **Topics & Metadata**: Topic titles, normalized slugs, search frequency counts, trending scores, and coverage ratios.
- **Clusters & Articles**: Synthesized cluster labels, member article titles, URLs, publishers, stances, and cross-source linkages.
- **Perspectives & Quotes**: LLM-generated viewpoint analyses, estimated audience shares, key arguments, and cited quotes.
- **Operational History & Telemetry**: Pipeline execution records, source execution metrics, background worker cycles, alerts history, and backup metadata audit records.

### What Data Remains External / Non-Recoverable from Dumps
- **In-Memory Volatile Queues**: Ephemeral worker thread loops or in-flight HTTP request payloads at the exact millisecond of failure.
- **Third-Party Provider Live State**: External rate limits on Google Trends, Reddit API, or X web scrapers.
- **Filesystem Secrets & API Keys**: Credentials and `.env` secrets are NEVER stored in database dumps or metadata tables.

---

## 4. Database Backup Subsystem

### 4.1 CLI Commands

The backup subsystem is executable locally or inside Docker containers via `python -m app.database.backup`:

```bash
# 1. Create a logical PostgreSQL dump (with auto-compression & SHA-256 checksum)
python -m app.database.backup --create

# 2. Dry-run safety inspection (validates parameters and paths without writing dump)
python -m app.database.backup --create --dry-run

# 3. List cataloged database backups
python -m app.database.backup --list

# 4. Verify integrity of a specific backup snapshot
python -m app.database.backup --verify vantage_backup_20260916_205500

# 5. Run retention pruning lifecycle
python -m app.database.backup --cleanup --retention-count 7 --retention-days 30
```

### 4.2 Automated Worker Cadence & Concurrency Locking

When `BACKUP_ENABLED=true`, the background scheduler (`BackgroundScheduler`) runs periodic backups at the configured interval (`BACKUP_INTERVAL_HOURS`).
- **Concurrency Guard**: Protected by `acquire_backup_lock()` to guarantee that concurrent CLI or worker triggers never execute overlapping dumps.
- **Fail-Soft Isolation**: Backup exceptions are captured and recorded in `BackupRecord` without halting ingestion or perspective synthesis loops.

---

## 5. Safe Restoration Procedure

> [!CAUTION]
> Destructive database restores are intentionally **BLOCKED** from HTTP endpoints to prevent accidental production overwrite or remote exploitation. Restorations strictly require authenticated CLI access with the explicit `--confirm` flag.

### 5.1 Restoring to Primary Database

```bash
# Restores a verified dump to the target database specified in DATABASE_URL
python -m app.database.restore --backup backups/vantage_backup_20260916_205500.sql.gz --confirm
```

If `--confirm` is omitted, the CLI aborts immediately with exit code `1`:
```text
[ABORTED] Database restoration modifies existing database data.
To proceed with restoration, you must pass the '--confirm' flag.
```

### 5.2 Dry-Run Pre-Flight Validation

```bash
# Validates dump existence, size, SHA-256 checksum against metadata without executing restore
python -m app.database.restore --backup backups/vantage_backup_20260916_205500.sql.gz --dry-run
```

### 5.3 Test Restore into Temporary Database (Staging / Verification)

Before restoring against production, operators should validate dump integrity against a staging or temporary test database:

```bash
# 1. Create isolated test database
createdb vantage_news_test

# 2. Run test restore
python -m app.database.restore --backup backups/vantage_backup_20260916_205500.sql.gz --target-db vantage_news_test --confirm

# 3. Run disaster recovery validation on restored database
DATABASE_URL=postgresql://postgres:password@localhost:5432/vantage_news_test python scripts/disaster_recovery_check.py

# 4. Clean up test database
dropdb vantage_news_test
```

---

## 6. Operational APIs & Dashboard Monitoring

The `/ops` web dashboard provides a dedicated **Disaster Recovery & Backup Readiness** panel:
- **`GET /api/ops/backups`**: Retrieves cataloged backups, latest backup age, verification status, total footprint, and retention policy.
- **`POST /api/ops/backups/create`**: Manually triggers on-demand logical backup (protected by `X-Ops-Key`).
- **`POST /api/ops/backups/verify/{backup_id}`**: Triggers in-place SHA-256 integrity check and updates `BackupRecord.is_verified`.

---

## 7. Disaster Recovery Operator Checklist

During an infrastructure incident or data recovery event:

1. **Assess Incident Scope**:
   - Determine whether database instance is degraded, corrupted, or unreachable.
   - Run `python scripts/disaster_recovery_check.py` to inspect service connectivity and latest snapshots.
2. **Select Recovery Snapshot**:
   - Run `python -m app.database.backup --list` to inspect cataloged snapshots.
   - Choose the latest snapshot with status `SUCCESS` and verification state `VERIFIED`.
3. **Validate Target Database**:
   - Ensure the target PostgreSQL instance is provisioned and accessible.
4. **Execute Restoration**:
   - Run `python -m app.database.restore --backup <selected_backup_file> --confirm`.
5. **Verify Post-Restore Invariants**:
   - Run `python scripts/disaster_recovery_check.py`.
   - Verify HTTP `/health` and `/ready` probes return `200 OK`.
   - Verify topics count and recent pipeline runs in `/ops`.
6. **Resume Background Workers**:
   - Re-enable background workers once schema integrity is verified.

---

## 8. Dependency Outage & Degraded Mode Runbooks

### 8.1 Circuit Breaker Inspection & Manual Reset
When a third-party service suffers an outage, its corresponding circuit breaker transitions to `OPEN`, immediately short-circuiting downstream calls to protect worker threads:
- **Inspect States**: `GET /api/ops/resilience` returns current state (`CLOSED`, `OPEN`, `HALF_OPEN`), failure counters, and retry countdown.
- **Manual Reset**: `POST /api/ops/resilience/reset` (with `X-Ops-Key`) forces all circuit breakers to `CLOSED` and reconnects shared governance.

### 8.2 Redis / Shared Governance Outage Runbook
If Redis crashes or encounters network partitioning:
1. **Automatic Failover**: `GovernanceCoordinator` automatically switches to in-process memory store with zero dropped requests.
2. **Degraded Signal**: `/ready` and `/api/ops/overview` return `status: "degraded"`.
3. **Recovery**: Once Redis recovers, probe calls automatically restore distributed coordination, or operators can trigger `POST /api/ops/resilience/reset`.

### 8.3 LLM Provider Outage Runbook
If OpenAI API experiences elevated errors or HTTP 429 quota exhaustion:
1. **Circuit Tripping**: `openai_synthesis` breaker trips to `OPEN`.
2. **Extractive Fallback**: Pipeline automatically synthesizes briefs from top representative discourse clusters, tagging perspectives with a fail-soft confidence note.
3. **Zero State Corruption**: Existing valid perspectives are preserved or updated gracefully with zero unhandled 500 errors.

