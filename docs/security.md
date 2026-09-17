# Vantage News - Production Security, Compliance & Data Protection

## Overview

This document specifies the centralized authentication, role-based authorization, multi-layer SSRF protections, dual-key rotation architecture, request body size limiting, security headers, structured security auditing, and data privacy controls implemented across the Vantage News platform.

---

## 1. Centralized Authentication & Role-Based Access Control (RBAC)

The platform implements centralized, constant-time RBAC across four privilege tiers:

| Role | Access Level | Description | Key Variable(s) |
| :--- | :--- | :--- | :--- |
| **`public`** | Read-Only | Public queries, health/readiness probes, metrics exposition, and topic search. No authentication required. | *None* |
| **`operator`** | Operational | Triggering alert evaluations, trending discovery runs, topic evaluations, and operational metrics inspection. | `OPS_API_KEY`, `OPS_API_KEY_SECONDARY` |
| **`admin`** | Administrative | Topic pipeline reprocessing, logical backups, disaster recovery verification, worker lifecycle controls, and circuit resets. | `ADMIN_API_KEY`, `ADMIN_API_KEY_SECONDARY` |
| **`security_admin`** | Security Officer | Full system access, audit trail inspection, secret rotation verification, and retention policy overrides. | `SECURITY_API_KEY`, `SECURITY_API_KEY_SECONDARY` |

### Constant-Time Credential Comparison & Key Rotation
- **Timing Attack Resistance**: Keys are verified using `hmac.compare_digest()` to eliminate timing side-channels.
- **Zero-Downtime Key Rotation**: Each role supports a primary (`*_API_KEY`) and secondary (`*_API_KEY_SECONDARY`) key, allowing new credentials to be phased in before retiring older keys.
- **Header Flexibility**: Endpoints accept credentials via `Authorization: Bearer <key>`, `X-API-Key`, `X-Ops-Key`, or `X-Admin-Key`.

---

## 2. Multi-Layer SSRF Protections

All external URLs ingested or cited are verified via `is_safe_url()` and `sanitize_url()`:

1. **Strict Scheme Whitelisting**: Strictly restricts protocols to `http` and `https`. Schemes such as `file://`, `gopher://`, `dict://`, `ftp://`, `data://`, and `javascript:` are blocked.
2. **Embedded Credential Detection**: URLs containing `user:pass@host` patterns are rejected.
3. **Internal & Cloud Metadata Network Filtering**:
   - Loopback (`127.0.0.0/8`, `::1`) and unspecified addresses (`0.0.0.0/8`, `::/128`).
   - RFC 1918 Private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
   - Carrier-grade NAT (`100.64.0.0/10`).
   - Link-local and Cloud metadata endpoints (`169.254.0.0/16`, `100.100.100.200/32`, `metadata.google.internal`, `metadata.internal`, `instance-data`).
   - IPv6 link-local (`fe80::/10`) and Unique Local Addresses (`fc00::/7`, `fd00::/8`).
   - Local domains (`.local`, `.internal`, `.lan`, `.localhost`, `.localdomain`, `.intranet`, `.corp`).
4. **Obfuscated IP Decoding**:
   - Decodes integer decimal IPs (e.g. `2130706433` -> `127.0.0.1`, `2852039166` -> `169.254.169.254`).
   - Decodes hexadecimal representations (`0x7f000001` -> `127.0.0.1`).
   - Decodes octal and mixed dotted representations (`0177.0.0.1`, `0x7f.0.0.1`).
5. **DNS Pre-Flight Checks**: Optional DNS resolution verification prevents DNS rebinding attacks to internal infrastructure.

---

## 3. Security Headers & Request Body Size Limits

### Modern Security Headers
Every HTTP response from FastAPI backend enforces comprehensive security headers:
- `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https:; connect-src 'self' https:; frame-ancestors 'none'; object-src 'none'; base-uri 'self'`
- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()`
- `Cross-Origin-Opener-Policy: same-origin`
- `Cross-Origin-Resource-Policy: same-origin`

### Request Payload Limiter (HTTP 413)
- Middleware inspects `Content-Length` headers and streams to enforce a hard maximum payload limit of **2MB** (2,097,152 bytes) on all requests, rejecting oversized payloads with `HTTP 413 Payload Too Large`.

---

## 4. Credential Sanitization & Secret Masking

- **Zero Credential Leakage in Telemetry & Alerts**:
  - Telemetry summaries (`/api/ops/overview`, `/api/ops/pipeline-metrics`, `/api/ops/source-health`) and alerting payloads (`/api/ops/alerts`) strictly mask all secret tokens, API keys, bearer auth headers, and passwords.
  - `mask_sensitive_data()` automatically redacts `sk-[a-zA-Z0-9_-]{20,}` API keys, `ghp_[a-zA-Z0-9]{20,}` tokens, `eyJ...` JWTs, `postgresql://user:password@host` connection strings, and `redis://:password@host` credentials.
  - `sanitize_dict_secrets()` recursively strips sensitive fields (`password`, `secret`, `token`, `api_key`, `authorization`, `cookie`, `session`) from JSON objects before storage or logging.
- **500 Error Sanitization**:
  - Internal exceptions caught by the HTTP middleware return generic sanitized JSON errors, preventing stack traces, internal paths, or secrets from being revealed to callers.

---

## 5. Security Audit Logging & Investigation

- **Persistent Security Audit Trail**:
  - All security-relevant actions, authentication failures, authorization denials, backup creations, configuration modifications, and circuit resets are recorded in the `security_audit_logs` database table.
  - Audit records capture timestamp, actor, role, action, resource, client IP address, User-Agent, status (`allowed`, `denied`, `failed`, `error`), sanitized details JSON, and redacted error messages.
- **Structured JSON Logging**:
  - Concurrently outputs structured log events (`[AUDIT_ALLOWED]`, `[AUDIT_DENIED]`) to stderr/stdout for SIEM ingestion.
- **Audit Logs Query API**:
  - `GET /api/ops/audit-logs` provides paginated, indexed queries by action, actor, status, and ISO datetime ranges, guarded by `require_operator` / `require_admin`.

---

## 6. Data Retention, Privacy & Backup Protection

- **Automated Lifecycle Retention**:
  - `cleanup_ops_history.py` prunes historical telemetry older than retention thresholds:
    - Operational history (pipeline runs, source executions, worker cycles): `OPS_RETENTION_DAYS` (default 30 days).
    - Resolved alerts: `ALERT_RETENTION_DAYS` (default 90 days). Active alerts are NEVER purged.
    - Security audit logs: `AUDIT_RETENTION_DAYS` (default 180 days).
- **Zero Raw Content in History**:
  - Raw scraped article bodies, prompts, and social media texts are never persisted in operational history or audit tables.
- **Database Backup Security**:
  - Logical backups (`pg_dump`) inject credentials strictly via private subprocess environment dictionaries (`PGPASSWORD`), never exposed on the CLI process list (`ps aux`).
  - Restorations are blocked via HTTP endpoints and require an authenticated CLI session with `--confirm`.
  - SHA-256 checksums verify snapshot integrity before backups are marked valid.

---

## 7. CI/CD Automated Security Checks

- **Pre-Commit / Release Quality Gate**:
  - `scripts/verify_release.py` scans tracked repositories for accidental `.env` files, unmasked private keys (`.pem`, `.key`), or OpenAI / GitHub credential patterns.
  - Automated test suite (`tests/test_security_compliance.py`) exercises RBAC privilege boundaries, token rotation, SSRF decoding, header presence, payload limits, and retention pruning on every build.
