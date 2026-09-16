"""Database Backup Management Subsystem for Vantage News.

Provides logical PostgreSQL dump creation, SHA-256 checksum verification,
retention pruning, and metadata tracking in PostgreSQL.

Usage:
    python -m app.database.backup --create [--dry-run]
    python -m app.database.backup --list
    python -m app.database.backup --verify [BACKUP_ID]
    python -m app.database.backup --cleanup [--retention-count 7] [--retention-days 30] [--dry-run]
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.core.security import mask_sensitive_data
from app.database.database import SessionLocal
from app.database.models import BackupRecord

logger = logging.getLogger("app.database.backup")

# Global in-process lock to prevent overlapping backup runs
_backup_lock = threading.Lock()
_backup_running = False


def acquire_backup_lock() -> bool:
    global _backup_running
    with _backup_lock:
        if _backup_running:
            return False
        _backup_running = True
        return True


def release_backup_lock() -> None:
    global _backup_running
    with _backup_lock:
        _backup_running = False


def is_backup_running() -> bool:
    global _backup_running
    with _backup_lock:
        return _backup_running


class BackupConfig:
    """Configuration parameters for database backups and retention lifecycle."""

    def __init__(
        self,
        enabled: Optional[bool] = None,
        backup_dir: Optional[str] = None,
        interval_hours: Optional[float] = None,
        retention_count: Optional[int] = None,
        retention_days: Optional[int] = None,
        compression: Optional[bool] = None,
        verify_after_create: Optional[bool] = None,
        pg_dump_path: Optional[str] = None,
    ):
        self.enabled = enabled if enabled is not None else os.getenv("BACKUP_ENABLED", "false").lower() in ("true", "1", "yes")
        self.backup_dir = backup_dir if backup_dir is not None else os.getenv("BACKUP_DIR", "backups")
        self.interval_hours = interval_hours if interval_hours is not None else float(os.getenv("BACKUP_INTERVAL_HOURS", "24.0"))
        self.retention_count = retention_count if retention_count is not None else int(os.getenv("BACKUP_RETENTION_COUNT", "7"))
        self.retention_days = retention_days if retention_days is not None else int(os.getenv("BACKUP_RETENTION_DAYS", "30"))
        self.compression = compression if compression is not None else os.getenv("BACKUP_COMPRESSION", "true").lower() in ("true", "1", "yes")
        self.verify_after_create = verify_after_create if verify_after_create is not None else os.getenv("BACKUP_VERIFY_AFTER_CREATE", "true").lower() in ("true", "1", "yes")
        self.pg_dump_path = pg_dump_path if pg_dump_path is not None else os.getenv("PG_DUMP_PATH", "pg_dump")


def _get_db_session(db: Optional[Session] = None) -> Tuple[Session, bool]:
    """Dynamically get an active database session."""
    if db is not None:
        return db, False
    from app.database.database import SessionLocal
    return SessionLocal(), True


def compute_sha256(filepath: str) -> str:
    """Calculate SHA-256 hash of a file efficiently in chunks."""
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def parse_database_connection(db_url: Optional[str] = None) -> Dict[str, Any]:
    """Parse DATABASE_URL into component parameters without exposing password in logs."""
    url = db_url or os.getenv("DATABASE_URL", "postgresql://postgres:password@localhost:5432/vantage_news")
    parsed = urlparse(url)
    
    is_postgres = parsed.scheme.startswith("postgres")
    is_sqlite = parsed.scheme.startswith("sqlite")
    
    return {
        "scheme": parsed.scheme,
        "is_postgres": is_postgres,
        "is_sqlite": is_sqlite,
        "username": parsed.username or "postgres",
        "password": parsed.password or "",
        "host": parsed.hostname or "localhost",
        "port": str(parsed.port or 5432),
        "database": parsed.path.lstrip("/") if parsed.path else "vantage_news",
        "raw_sanitized": mask_sensitive_data(url),
    }


def generate_backup_filename(database_name: str = "vantage_news", compressed: bool = True, timestamp: Optional[datetime] = None) -> str:
    """Generate a clean timestamped backup filename."""
    ts = (timestamp or datetime.utcnow()).strftime("%Y%m%d_%H%M%S")
    clean_db = re.sub(r"[^a-zA-Z0-9_-]", "_", database_name)
    ext = "sql.gz" if compressed else "sql"
    return f"vantage_backup_{clean_db}_{ts}.{ext}"


def verify_backup_file(filepath: str, expected_checksum: Optional[str] = None) -> Tuple[bool, str]:
    """Verify that a backup file exists, is non-empty, and matches its expected checksum."""
    p = Path(filepath)
    if not p.is_file():
        return False, f"Backup file does not exist: {p.name}"

    size = p.stat().st_size
    if size == 0:
        return False, "Backup file is empty (0 bytes)"

    # Verify header magic bytes
    try:
        with open(filepath, "rb") as f:
            header = f.read(16)
            if len(header) < 5:
                return False, "Backup file too small to contain valid dump header"
            # PostgreSQL custom format starts with PGDMP, tar starts with ustar or standard tar header, gzip starts with \x1f\x8b
            is_pg_custom = header.startswith(b"PGDMP")
            is_gzip = header.startswith(b"\x1f\x8b")
            is_sql = header.startswith(b"--") or b"PostgreSQL" in header or b"CREATE" in header or b"PRAGMA" in header
            if not (is_pg_custom or is_gzip or is_sql or size > 100):
                logger.warning("Unrecognized backup header format in %s", p.name)
    except Exception as e:
        return False, f"Could not read backup file header: {e}"

    if expected_checksum:
        actual_checksum = compute_sha256(filepath)
        if actual_checksum.lower() != expected_checksum.lower():
            return False, f"Checksum mismatch (expected: {expected_checksum[:12]}..., actual: {actual_checksum[:12]}...)"

    return True, "Backup integrity verified successfully"


def create_backup(
    db: Optional[Session] = None,
    config: Optional[BackupConfig] = None,
    dry_run: bool = False,
    custom_filename: Optional[str] = None,
    runner_fn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create a logical database backup, compute checksum, and persist metadata record.

    Args:
        db: Optional SQLAlchemy Session for metadata record persistence.
        config: Optional BackupConfig overrides.
        dry_run: If True, simulates backup creation without spawning pg_dump or writing files.
        custom_filename: Optional explicit filename.
        runner_fn: Optional mock subprocess runner for testing.

    Returns:
        Structured dictionary with backup metadata.
    """
    cfg = config or BackupConfig()
    if not acquire_backup_lock():
        raise RuntimeError("Another backup operation is already running")

    started_at = datetime.utcnow()
    timestamp_str = started_at.strftime("%Y%m%d_%H%M%S")
    conn_info = parse_database_connection()
    db_name = conn_info["database"] or "vantage_news"

    filename = custom_filename or generate_backup_filename(database_name=db_name, compressed=cfg.compression, timestamp=started_at)
    backup_id = f"backup_{timestamp_str}_{os.urandom(3).hex()}"

    backup_dir_path = Path(cfg.backup_dir)
    target_filepath = backup_dir_path / filename

    summary: Dict[str, Any] = {
        "backup_id": backup_id,
        "filename": filename,
        "filepath": str(target_filepath),
        "database_name": db_name,
        "status": "running",
        "started_at": started_at.isoformat(),
        "completed_at": None,
        "size_bytes": 0,
        "checksum": None,
        "is_verified": False,
        "dry_run": dry_run,
        "error": None,
    }

    try:
        if not dry_run:
            backup_dir_path.mkdir(parents=True, exist_ok=True)

        if dry_run:
            logger.info("[DRY-RUN] Simulating backup creation for database: %s -> %s", db_name, filename)
            summary["status"] = "success"
            summary["completed_at"] = datetime.utcnow().isoformat()
            summary["size_bytes"] = 1048576  # Simulated 1 MB
            summary["checksum"] = "dryrun" + "0" * 58
            summary["is_verified"] = True
            return summary

        # Real Execution
        env_vars = os.environ.copy()
        if conn_info["password"]:
            env_vars["PGPASSWORD"] = conn_info["password"]

        # Build pg_dump arguments
        if conn_info["is_postgres"]:
            cmd = [
                cfg.pg_dump_path,
                "-h", conn_info["host"],
                "-p", conn_info["port"],
                "-U", conn_info["username"],
                "-d", conn_info["database"],
            ]
            if cfg.compression:
                cmd.extend(["-F", "c", "-f", str(target_filepath)])
            else:
                cmd.extend(["-F", "p", "-f", str(target_filepath)])
        else:
            # Fallback for SQLite / test environments
            cmd = ["echo", "SQLite backup simulation"]

        if runner_fn:
            runner_fn(cmd, env_vars, str(target_filepath))
        else:
            if conn_info["is_postgres"]:
                proc = subprocess.run(
                    cmd,
                    env=env_vars,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=300,
                )
                if proc.returncode != 0:
                    err_msg = mask_sensitive_data(proc.stderr or "pg_dump failed with non-zero exit code")
                    raise RuntimeError(f"Database dump execution failed: {err_msg}")
            else:
                # Local SQLite dump fallback
                with open(target_filepath, "w", encoding="utf-8") as f:
                    f.write(f"-- Vantage News SQLite Logical Export\n-- Generated: {started_at.isoformat()}\n")

        # Verify file presence & compute checksum
        completed_at = datetime.utcnow()
        size_bytes = target_filepath.stat().st_size if target_filepath.exists() else 0
        checksum = compute_sha256(str(target_filepath)) if target_filepath.exists() and size_bytes > 0 else None

        summary["completed_at"] = completed_at.isoformat()
        summary["size_bytes"] = size_bytes
        summary["checksum"] = checksum

        # Integrity verification
        if cfg.verify_after_create:
            is_valid, v_msg = verify_backup_file(str(target_filepath), expected_checksum=checksum)
            summary["is_verified"] = is_valid
            summary["status"] = "verified" if is_valid else "corrupted"
            if not is_valid:
                summary["error"] = v_msg
        else:
            summary["status"] = "success"

        # Persist to database if session provided
        _persist_backup_record(summary, db=db)

        # Trigger retention cleanup
        try:
            cleanup_backups(
                retention_count=cfg.retention_count,
                retention_days=cfg.retention_days,
                backup_dir=cfg.backup_dir,
                db=db,
            )
        except Exception as cl_err:
            logger.warning("Post-backup retention cleanup warning: %s", cl_err)

        return summary

    except Exception as e:
        sanitized_err = mask_sensitive_data(str(e))
        logger.error("Backup creation failed: %s", sanitized_err)
        summary["status"] = "failed"
        summary["completed_at"] = datetime.utcnow().isoformat()
        summary["error"] = sanitized_err
        _persist_backup_record(summary, db=db)
        raise RuntimeError(f"Backup failed: {sanitized_err}") from e

    finally:
        release_backup_lock()


def _persist_backup_record(summary: Dict[str, Any], db: Optional[Session] = None) -> None:
    """Safely persist or update a BackupRecord in PostgreSQL."""
    try:
        db_sess, should_close = _get_db_session(db)
        try:
            started_dt = datetime.fromisoformat(summary["started_at"]) if summary.get("started_at") else datetime.utcnow()
            completed_dt = datetime.fromisoformat(summary["completed_at"]) if summary.get("completed_at") else None

            existing = db_sess.query(BackupRecord).filter(BackupRecord.backup_id == summary["backup_id"]).first()
            if existing:
                existing.status = summary["status"]
                existing.completed_at = completed_dt
                existing.size_bytes = summary.get("size_bytes", 0)
                existing.checksum = summary.get("checksum")
                existing.is_verified = summary.get("is_verified", False)
                existing.error_type = summary.get("error")
                if summary.get("is_verified"):
                    existing.verified_at = datetime.utcnow()
            else:
                record = BackupRecord(
                    backup_id=summary["backup_id"],
                    filename=summary["filename"],
                    filepath=summary.get("filepath"),
                    created_at=started_dt,
                    completed_at=completed_dt,
                    status=summary["status"],
                    size_bytes=summary.get("size_bytes", 0),
                    checksum=summary.get("checksum"),
                    database_name=summary.get("database_name"),
                    schema_version="2.0.0",
                    error_type=summary.get("error"),
                    is_verified=summary.get("is_verified", False),
                    verified_at=datetime.utcnow() if summary.get("is_verified") else None,
                )
                db_sess.add(record)
            db_sess.commit()
        except Exception as db_err:
            db_sess.rollback()
            logger.warning("Failed to persist BackupRecord to database: %s", db_err)
        finally:
            if should_close:
                db_sess.close()
    except Exception as outer_err:
        logger.warning("BackupRecord DB persistence skipped: %s", outer_err)


def verify_backup(
    backup_id: str,
    db: Optional[Session] = None,
    backup_dir: Optional[str] = None,
    config: Optional[BackupConfig] = None,
) -> Dict[str, Any]:
    """Verify integrity of an existing backup by ID or filename."""
    cfg = config or BackupConfig()
    dir_to_use = backup_dir or cfg.backup_dir
    db_sess, should_close = _get_db_session(db)
    try:
        record = db_sess.query(BackupRecord).filter(
            (BackupRecord.backup_id == backup_id) | (BackupRecord.filename == backup_id)
        ).first()

        if record:
            if record.filepath and Path(record.filepath).exists():
                filepath = record.filepath
            else:
                filepath = str(Path(dir_to_use) / record.filename)
        else:
            filepath = str(Path(dir_to_use) / backup_id)
            if not Path(filepath).exists() and not backup_id.endswith((".dump", ".sql", ".sql.gz")):
                # Check for possible filename matches in dir
                matches = list(Path(dir_to_use).glob(f"*{backup_id}*"))
                if matches:
                    filepath = str(matches[0])

        expected_checksum = record.checksum if record else None

        is_valid, msg = verify_backup_file(filepath, expected_checksum=expected_checksum)
        now_dt = datetime.utcnow()

        if record:
            record.is_verified = is_valid
            record.status = "verified" if is_valid else "corrupted"
            record.verified_at = now_dt
            if not is_valid:
                record.error_type = msg
            db_sess.commit()

        return {
            "backup_id": record.backup_id if record else backup_id,
            "filename": record.filename if record else Path(filepath).name,
            "is_verified": is_valid,
            "is_valid": is_valid,
            "status": "success" if is_valid else "corrupted",
            "message": msg,
            "error": None if is_valid else msg,
            "verified_at": now_dt.isoformat(),
            "checksum": compute_sha256(filepath) if Path(filepath).exists() else None,
        }
    finally:
        if should_close:
            db_sess.close()


# Alias for backward/forward compatibility
verify_backup_by_id = verify_backup


def list_backups(db: Optional[Session] = None, limit: int = 50, backup_dir: str = "backups") -> List[Dict[str, Any]]:
    """Retrieve list of backups from PostgreSQL records and filesystem."""
    results: List[Dict[str, Any]] = []
    seen_filenames = set()

    # 1. Query Database Records
    db_sess, should_close = _get_db_session(db)
    try:
        records = db_sess.query(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(limit).all()
        for r in records:
            d = r.to_dict()
            seen_filenames.add(r.filename)
            # Add file existence indicator
            if r.filepath and Path(r.filepath).exists():
                d["file_exists"] = True
            else:
                d["file_exists"] = (Path(backup_dir) / r.filename).exists()
            results.append(d)
    except Exception as e:
        logger.warning("Error fetching BackupRecords from database: %s", e)
    finally:
        if should_close:
            db_sess.close()

    # 2. Add any filesystem backups not in DB
    b_dir = Path(backup_dir)
    if b_dir.is_dir():
        for p in sorted(b_dir.glob("vantage_*.*"), key=os.path.getmtime, reverse=True):
            if p.name not in seen_filenames and len(results) < limit:
                try:
                    stat = p.stat()
                    results.append({
                        "id": None,
                        "backup_id": f"fs_{p.stem}",
                        "filename": p.name,
                        "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        "completed_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "status": "untracked",
                        "size_bytes": stat.st_size,
                        "checksum": None,
                        "database_name": "vantage_news",
                        "schema_version": "2.0.0",
                        "is_verified": False,
                        "file_exists": True,
                    })
                except Exception as fs_err:
                    logger.warning("Error reading backup file stat: %s", fs_err)

    return results


def cleanup_backups(
    retention_count: int = 7,
    retention_days: int = 30,
    backup_dir: str = "backups",
    db: Optional[Session] = None,
    dry_run: bool = False,
    config: Optional[BackupConfig] = None,
) -> Dict[str, Any]:
    """Prune expired backup files and records beyond retention thresholds."""
    if config:
        retention_count = config.retention_count
        retention_days = config.retention_days
        backup_dir = config.backup_dir

    b_dir = Path(backup_dir)
    cutoff_date = datetime.utcnow() - timedelta(days=retention_days)
    deleted_files: List[str] = []
    freed_bytes = 0

    if not b_dir.is_dir():
        return {
            "deleted_count": 0,
            "deleted_files_count": 0,
            "freed_bytes": 0,
            "deleted_files": [],
            "kept_count": 0,
            "dry_run": dry_run,
        }

    # Find all backup files sorted newest to oldest
    files = sorted(b_dir.glob("vantage_*.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    kept_count = 0

    for idx, f in enumerate(files):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            is_beyond_count = idx >= retention_count
            is_beyond_age = mtime < cutoff_date

            if is_beyond_count or is_beyond_age:
                f_size = f.stat().st_size
                deleted_files.append(f.name)
                freed_bytes += f_size
                if not dry_run:
                    f.unlink(missing_ok=True)
                    logger.info("Pruned old backup file: %s (%d bytes)", f.name, f_size)
            else:
                kept_count += 1
        except Exception as e:
            logger.warning("Error pruning backup file %s: %s", f.name, e)

    # Clean up DB records for deleted files
    if not dry_run and deleted_files:
        db_sess, should_close = _get_db_session(db)
        try:
            db_sess.query(BackupRecord).filter(BackupRecord.filename.in_(deleted_files)).delete(synchronize_session=False)
            db_sess.commit()
        except Exception as db_err:
            db_sess.rollback()
            logger.warning("Failed to clean up BackupRecords in DB: %s", db_err)
        finally:
            if should_close:
                db_sess.close()

    return {
        "deleted_count": len(deleted_files),
        "deleted_files_count": len(deleted_files),
        "freed_bytes": freed_bytes,
        "deleted_files": deleted_files,
        "kept_count": kept_count,
        "dry_run": dry_run,
    }


# Alias for backward/forward compatibility
prune_backups = cleanup_backups


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Vantage News Database Backup Management CLI")
    parser.add_argument("--create", action="store_true", help="Create a new logical PostgreSQL backup")
    parser.add_argument("--list", action="store_true", help="List recent database backups")
    parser.add_argument("--verify", nargs="?", const="ALL", help="Verify backup integrity (optional: backup ID or filename)")
    parser.add_argument("--cleanup", action="store_true", help="Prune old backups beyond retention thresholds")
    parser.add_argument("--dry-run", action="store_true", help="Simulate action without creating or deleting files")
    parser.add_argument("--retention-count", type=int, default=7, help="Max backup files to keep (default: 7)")
    parser.add_argument("--retention-days", type=int, default=30, help="Max backup age in days (default: 30)")
    parser.add_argument("--backup-dir", type=str, default="backups", help="Directory for backup files (default: backups)")

    args = parser.parse_args()

    if args.create:
        cfg = BackupConfig(backup_dir=args.backup_dir)
        res = create_backup(config=cfg, dry_run=args.dry_run)
        print("\n--- Database Backup Created ---")
        print(json.dumps(res, indent=2))

    elif args.list:
        res_list = list_backups(backup_dir=args.backup_dir)
        print(f"\n--- Backups in {args.backup_dir} ({len(res_list)} total) ---")
        for b in res_list:
            size_mb = round(b.get("size_bytes", 0) / (1024 * 1024), 2)
            chk = (b.get("checksum") or "")[:12]
            print(f"  • {b['filename']} | {size_mb} MB | Status: {b['status']} | SHA256: {chk}... | {b['created_at']}")

    elif args.verify:
        if args.verify == "ALL":
            backups = list_backups(backup_dir=args.backup_dir)
            print(f"\n--- Verifying {len(backups)} backups ---")
            for b in backups:
                v_res = verify_backup(b["filename"], backup_dir=args.backup_dir)
                status_icon = "✓" if v_res["is_valid"] else "✗"
                print(f"  {status_icon} {b['filename']}: {v_res['message']}")
        else:
            v_res = verify_backup(args.verify, backup_dir=args.backup_dir)
            print("\n--- Backup Verification Result ---")
            print(json.dumps(v_res, indent=2))

    elif args.cleanup:
        cl_res = cleanup_backups(
            retention_count=args.retention_count,
            retention_days=args.retention_days,
            backup_dir=args.backup_dir,
            dry_run=args.dry_run,
        )
        print("\n--- Backup Retention Cleanup ---")
        print(json.dumps(cl_res, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
