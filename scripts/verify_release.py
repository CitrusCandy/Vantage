#!/usr/bin/env python3
"""
Vantage News - Release & Pre-Deployment Verification Gate
Validates core files, environment templates, backend imports, database initialization,
health/ready probes, and scans for accidental secrets.
Returns 0 on success, non-zero on failure.
"""

import importlib
import os
import re
import sys
from pathlib import Path

# Color helpers
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def log_pass(msg: str):
    print(f"[{GREEN}PASS{RESET}] {msg}")


def log_fail(msg: str):
    print(f"[{RED}FAIL{RESET}] {msg}")


def log_info(msg: str):
    print(f"[{CYAN}INFO{RESET}] {msg}")


def log_warn(msg: str):
    print(f"[{YELLOW}WARN{RESET}] {msg}")


def verify_project_files(root: Path) -> bool:
    """Verify all critical codebase files exist."""
    required_files = [
        # Backend Core
        "backend/requirements.txt",
        "backend/Dockerfile",
        "backend/app/main.py",
        "backend/app/database/database.py",
        "backend/app/database/models.py",
        "backend/app/database/cleanup_ops_history.py",
        "backend/app/database/backup.py",
        "backend/app/database/restore.py",
        "backend/app/core/security.py",
        "backend/app/core/audit.py",
        "backend/app/core/telemetry.py",
        "backend/app/core/alerting.py",
        "backend/app/api/topics.py",

        "backend/app/api/ops.py",
        "backend/app/api/workers.py",
        "backend/app/ingestion/google_news.py",
        "backend/app/ingestion/reddit.py",
        "backend/app/ingestion/x.py",
        "backend/app/ingestion/merge_pipeline.py",
        "backend/app/processing/cluster_pipeline.py",
        "backend/app/llm/perspective.py",
        "backend/app/workers/scheduler.py",
        # Frontend Core
        "frontend/package.json",
        "frontend/Dockerfile",
        "frontend/app/page.tsx",
        "frontend/app/ops/page.tsx",
        "frontend/components/layout/Navbar.tsx",
        "frontend/components/layout/Footer.tsx",
        "frontend/lib/api.ts",
        "frontend/lib/types.ts",
        "frontend/lib/utils.ts",
        # Documentation & Disaster Recovery
        "docs/disaster-recovery.md",
        # Scripts & Readiness
        "scripts/verify_release.py",
        "scripts/incident_readiness.py",
        "scripts/disaster_recovery_check.py",
        # CI & Orchestration
        "docker-compose.yml",
        ".github/workflows/ci.yml",
    ]

    all_found = True
    for rel_path in required_files:
        p = root / rel_path
        if not p.is_file():
            log_fail(f"Missing required file: {rel_path}")
            all_found = False
        else:
            log_pass(f"File verified: {rel_path}")

    return all_found


def verify_environment_examples(root: Path) -> bool:
    """Verify .env.example templates exist and contain key variables."""
    backend_env = root / "backend" / ".env.example"
    frontend_env = root / "frontend" / ".env.example"

    all_valid = True
    if not backend_env.is_file():
        log_fail("backend/.env.example is missing")
        all_valid = False
    else:
        content = backend_env.read_text(encoding="utf-8")
        required_vars = ["DATABASE_URL", "ENVIRONMENT", "CORS_ORIGINS"]
        for var in required_vars:
            if var not in content:
                log_fail(f"backend/.env.example missing variable: {var}")
                all_valid = False
        if all_valid:
            log_pass("backend/.env.example contains required variables")

    if not frontend_env.is_file():
        log_fail("frontend/.env.example is missing")
        all_valid = False
    else:
        content = frontend_env.read_text(encoding="utf-8")
        if "NEXT_PUBLIC_API_URL" not in content:
            log_fail("frontend/.env.example missing NEXT_PUBLIC_API_URL")
            all_valid = False
        else:
            log_pass("frontend/.env.example verified")

    return all_valid


def verify_backend_imports_and_probes(root: Path) -> bool:
    """Verify backend modules import cleanly and /health & /ready probes respond."""
    backend_dir = root / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))

    # Set default SQLite URL for offline verification if no DB is running
    if not os.getenv("DATABASE_URL") or "localhost" in os.getenv("DATABASE_URL", ""):
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"

    modules_to_test = [
        "app.database.database",
        "app.database.models",
        "app.database.backup",
        "app.database.restore",
        "app.core.security",
        "app.core.telemetry",
        "app.core.alerting",
        "app.ingestion.google_news",
        "app.ingestion.reddit",
        "app.ingestion.x",
        "app.ingestion.merge_pipeline",
        "app.processing.cluster_pipeline",
        "app.llm.perspective",
        "app.workers.scheduler",
        "app.api.topics",
        "app.api.ops",
        "app.main",
    ]

    all_imported = True
    for mod in modules_to_test:
        try:
            importlib.import_module(mod)
            log_pass(f"Imported module: {mod}")
        except Exception as e:
            log_fail(f"Failed to import {mod}: {e}")
            all_imported = False

    if not all_imported:
        return False

    # Test FastAPI endpoints with TestClient
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database.database import Base, engine

        # Ensure schema initialization works
        Base.metadata.create_all(bind=engine)
        log_pass("Database schema initialization verified")

        client = TestClient(app)
        # Test /health
        resp_health = client.get("/health")
        if resp_health.status_code == 200 and resp_health.json().get("status") == "healthy":
            log_pass("Liveness probe /health verified (HTTP 200 OK)")
        else:
            log_fail(f"/health probe failed with status {resp_health.status_code}")
            all_imported = False

        # Test /ready
        resp_ready = client.get("/ready")
        if resp_ready.status_code == 200 and resp_ready.json().get("status") == "ready":
            log_pass("Readiness probe /ready verified (HTTP 200 OK)")
        else:
            log_fail(f"/ready probe failed with status {resp_ready.status_code}")
            all_imported = False

        # Test /api/ops/overview
        resp_ops = client.get("/api/ops/overview")
        if resp_ops.status_code == 200:
            log_pass("Operations overview probe /api/ops/overview verified (HTTP 200 OK)")
        else:
            log_fail(f"/api/ops/overview failed with status {resp_ops.status_code}")
            all_imported = False

        # Test /api/ops/alerts
        resp_alerts = client.get("/api/ops/alerts")
        if resp_alerts.status_code == 200:
            log_pass("Production alerts probe /api/ops/alerts verified (HTTP 200 OK)")
        else:
            log_fail(f"/api/ops/alerts failed with status {resp_alerts.status_code}")
            all_imported = False

    except Exception as e:
        log_fail(f"HTTP Probe testing error: {e}")
        all_imported = False

    return all_imported


def scan_for_secrets_and_forbidden_files(root: Path) -> bool:
    """Scan tracked directory for accidental .env files, private keys, or exposed credentials."""
    forbidden_filenames = {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
    }

    # Assembled dynamically to prevent self-matching in scanner source
    prefix_openai = "sk-proj-"
    prefix_github = "ghp_"
    pat_openai = re.compile(rf"{prefix_openai}[a-zA-Z0-9_\-]{{20,}}", re.IGNORECASE)
    pat_github = re.compile(rf"{prefix_github}[a-zA-Z0-9]{{20,}}", re.IGNORECASE)
    pat_rsa = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

    clean = True
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip virtualenvs, git, node_modules, tests, scripts, and caches
        dirnames[:] = [
            d for d in dirnames
            if d not in {".git", ".venv", "venv", "node_modules", ".next", "__pycache__", ".pytest_cache", "tests", "scripts"}
        ]

        for fname in filenames:
            if fname in forbidden_filenames:
                log_fail(f"Forbidden secret file discovered: {Path(dirpath) / fname}")
                clean = False

            # Check file extensions for suspicious secrets
            if fname.endswith((".pem", ".key", ".pfx", ".p12")) and not fname.startswith("sample"):
                log_fail(f"Potential private key file discovered: {Path(dirpath) / fname}")
                clean = False

            # Scan text files for hardcoded secrets
            if fname.endswith((".py", ".tsx", ".ts", ".json", ".yml", ".yaml", ".md", ".sh")) and not fname.endswith(".example"):
                fpath = Path(dirpath) / fname
                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                    if pat_openai.search(content) or pat_github.search(content) or pat_rsa.search(content):
                        log_fail(f"Secret pattern matched in: {fpath}")
                        clean = False
                except Exception:
                    pass

    if clean:
        log_pass("Secret scanning clean: No unmasked credentials, keys, or .env files found")

    return clean


def main():
    print(f"\n{BOLD}======================================================{RESET}")
    print(f"{BOLD}  VANTAGE NEWS - RELEASE & PRE-DEPLOYMENT QUALITY GATE {RESET}")
    print(f"{BOLD}======================================================{RESET}\n")

    # Find project root
    script_path = Path(__file__).resolve()
    root = script_path.parent.parent
    log_info(f"Workspace root: {root}")

    results = []

    print("\n--- 1. Project Files Verification ---")
    results.append(verify_project_files(root))

    print("\n--- 2. Environment Configuration Verification ---")
    results.append(verify_environment_examples(root))

    print("\n--- 3. Secret & Credentials Scan ---")
    results.append(scan_for_secrets_and_forbidden_files(root))

    print("\n--- 4. Backend Imports, DB Initialization & Probes ---")
    results.append(verify_backend_imports_and_probes(root))

    print(f"\n{BOLD}======================================================{RESET}")
    if all(results):
        print(f"{GREEN}{BOLD}  ALL RELEASE QUALITY GATES PASSED SUCCESSFULLY! {RESET}")
        print(f"{BOLD}======================================================{RESET}\n")
        sys.exit(0)
    else:
        print(f"{RED}{BOLD}  RELEASE QUALITY GATE CHECK FAILED! {RESET}")
        print(f"{BOLD}======================================================{RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
