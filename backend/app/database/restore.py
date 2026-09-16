"""Database Restore and Disaster Recovery Subsystem for Vantage News.

Provides safe, verified PostgreSQL database restoration with strict confirmation
guards, pre-restore integrity validation, and zero credential leakage.

NOTE: This module is strictly CLI-operated and must never be exposed via HTTP endpoints.

Usage:
    python -m app.database.restore --backup <file_or_backup_id> --confirm [--target-db <db_name>] [--dry-run]
"""

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Dict, Optional

from app.core.security import mask_sensitive_data
from app.database.backup import (
    compute_sha256,
    parse_database_connection,
    verify_backup_file,
)
from dataclasses import dataclass

logger = logging.getLogger("app.database.restore")


@dataclass
class RestoreConfig:
    backup_path: str = ""
    confirm: bool = False
    target_db: Optional[str] = None
    dry_run: bool = False
    pg_restore_path: str = os.getenv("PG_RESTORE_PATH", "pg_restore")
    psql_path: str = os.getenv("PSQL_PATH", "psql")


def parse_restore_target(db_url: Optional[str] = None, target_db: Optional[str] = None) -> Dict[str, Any]:
    """Parse restore target connection info."""
    conn = parse_database_connection(db_url)
    return {
        "scheme": conn["scheme"],
        "host": conn["host"],
        "port": conn["port"],
        "user": conn["username"],
        "dbname": target_db or conn["database"],
        "is_postgres": conn["is_postgres"],
    }


def restore_database(
    backup_path: Optional[str] = None,
    confirm: bool = False,
    target_db: Optional[str] = None,
    dry_run: bool = False,
    pg_restore_path: str = "pg_restore",
    psql_path: str = "psql",
    runner_fn: Optional[Any] = None,
    config: Optional[RestoreConfig] = None,
) -> Dict[str, Any]:
    """Execute verified PostgreSQL restore against the target database.

    Args:
        backup_path: Absolute or relative path to the dump file.
        confirm: Mandatory explicit confirmation flag to avoid accidental data overwrite.
        target_db: Optional database name override.
        dry_run: If True, simulates restore verification without altering database state.
        pg_restore_path: Executable path for pg_restore.
        psql_path: Executable path for psql.
        runner_fn: Optional mock runner for tests.
        config: Optional RestoreConfig dataclass.

    Returns:
        Structured result summary.
    """
    if config:
        backup_path = config.backup_path
        confirm = config.confirm
        target_db = config.target_db
        dry_run = config.dry_run
        pg_restore_path = config.pg_restore_path
        psql_path = config.psql_path

    if not confirm and not dry_run:
        raise RuntimeError(
            "Destructive restore aborted: Mandatory '--confirm' flag was not provided. "
            "Pass the '--confirm' flag explicitly to execute database restoration."
        )

    if not backup_path:
        raise ValueError("Backup path must be provided for database restoration.")

    p = Path(backup_path)
    if not p.is_file():
        # Check standard backups/ directory
        alt_path = Path("backups") / backup_path
        if alt_path.is_file():
            p = alt_path
        else:
            raise FileNotFoundError(f"Backup file not found at: {backup_path}")

    # 1. Pre-restore Integrity Validation
    is_valid, verify_msg = verify_backup_file(str(p))
    if not is_valid:
        raise ValueError(f"Pre-restore validation failed: {verify_msg}")

    checksum = compute_sha256(str(p))
    size_bytes = p.stat().st_size
    conn_info = parse_database_connection()
    target_database = target_db or conn_info["database"] or "vantage_news"

    t_start = time.perf_counter()
    started_at = datetime.utcnow()

    summary: Dict[str, Any] = {
        "status": "pending",
        "backup_file": p.name,
        "target_database": target_database,
        "target_host": conn_info["host"],
        "size_bytes": size_bytes,
        "checksum": checksum,
        "dry_run": dry_run,
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "duration_seconds": 0.0,
    }

    if dry_run:
        logger.info("[DRY-RUN] Pre-restore verification passed for %s against %s", p.name, target_database)
        summary["status"] = "success"
        summary["completed_at"] = datetime.utcnow().isoformat()
        summary["duration_seconds"] = 0.05
        return summary

    logger.warning("Initiating PostgreSQL database restore: %s -> %s", p.name, target_database)

    # 2. Build Restore Command
    env_vars = os.environ.copy()
    if conn_info["password"]:
        env_vars["PGPASSWORD"] = conn_info["password"]

    is_custom_dump = False
    try:
        with open(p, "rb") as f:
            header = f.read(8)
            if header.startswith(b"PGDMP"):
                is_custom_dump = True
    except Exception as e:
        logger.warning("Could not inspect header: %s", e)

    if conn_info["is_postgres"]:
        if is_custom_dump:
            cmd = [
                pg_restore_path,
                "-h", conn_info["host"],
                "-p", conn_info["port"],
                "-U", conn_info["username"],
                "-d", target_database,
                "--clean",
                "--if-exists",
                "--no-owner",
                "--no-privileges",
                str(p),
            ]
        else:
            cmd = [
                psql_path,
                "-h", conn_info["host"],
                "-p", conn_info["port"],
                "-U", conn_info["username"],
                "-d", target_database,
                "-f", str(p),
            ]
    else:
        # SQLite / offline test mode
        cmd = ["echo", f"Restore simulated for {p.name}"]

    try:
        if runner_fn:
            runner_fn(cmd, env_vars, str(p))
        else:
            if conn_info["is_postgres"]:
                proc = subprocess.run(
                    cmd,
                    env=env_vars,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=600,
                )
                if proc.returncode != 0:
                    err_msg = mask_sensitive_data(proc.stderr or "Restore process exited with non-zero code")
                    raise RuntimeError(f"Database restore failed: {err_msg}")

        duration = round(time.perf_counter() - t_start, 2)
        summary["status"] = "success"
        summary["completed_at"] = datetime.utcnow().isoformat()
        summary["duration_seconds"] = duration
        logger.info("Database restore completed successfully in %s seconds", duration)
        return summary

    except Exception as e:
        sanitized_err = mask_sensitive_data(str(e))
        logger.error("Database restore failed: %s", sanitized_err)
        summary["status"] = "failed"
        summary["completed_at"] = datetime.utcnow().isoformat()
        summary["duration_seconds"] = round(time.perf_counter() - t_start, 2)
        summary["error"] = sanitized_err
        raise RuntimeError(f"Restore failed: {sanitized_err}") from e


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Vantage News Database Restore & Disaster Recovery CLI")
    parser.add_argument("--backup", type=str, required=True, help="Path to backup dump file to restore")
    parser.add_argument("--confirm", action="store_true", help="Explicit confirmation required to execute restore")
    parser.add_argument("--target-db", type=str, default=None, help="Target PostgreSQL database name override")
    parser.add_argument("--dry-run", action="store_true", help="Verify backup integrity without executing restore")

    args = parser.parse_args()

    try:
        res = restore_database(
            backup_path=args.backup,
            confirm=args.confirm,
            target_db=args.target_db,
            dry_run=args.dry_run,
        )
        print("\n--- Database Restore Summary ---")
        print(json.dumps(res, indent=2))
    except Exception as err:
        print(f"\n[ERROR] Restore execution failed: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
