import gzip
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import MagicMock, patch

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
BACKEND_DIR = PROJECT_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient
import pytest

from app.database.backup import (
    BackupConfig,
    acquire_backup_lock,
    compute_sha256,
    create_backup,
    generate_backup_filename,
    is_backup_running,
    list_backups,
    prune_backups,
    release_backup_lock,
    verify_backup_by_id,
    verify_backup_file,
)
from app.database import database
from app.database.models import BackupRecord
from app.database.restore import (
    RestoreConfig,
    parse_restore_target,
    restore_database,
)
from app.main import app
from app.workers.scheduler import BackgroundScheduler


@pytest.fixture
def temp_backup_dir():
    """Create a temporary directory for backups during testing."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


def get_db():
    return database.SessionLocal()


def test_backup_config_defaults(monkeypatch):
    """Verify BackupConfig defaults and environment overrides."""
    monkeypatch.delenv("BACKUP_ENABLED", raising=False)
    monkeypatch.delenv("BACKUP_DIR", raising=False)
    monkeypatch.delenv("BACKUP_RETENTION_COUNT", raising=False)
    
    cfg = BackupConfig()
    assert cfg.enabled is False
    assert cfg.backup_dir == "backups"
    assert cfg.retention_count == 7
    assert cfg.retention_days == 30
    assert cfg.compression is True
    assert cfg.verify_after_create is True

    monkeypatch.setenv("BACKUP_ENABLED", "true")
    monkeypatch.setenv("BACKUP_RETENTION_COUNT", "14")
    cfg_override = BackupConfig(
        enabled=True,
        retention_count=14,
    )
    assert cfg_override.enabled is True
    assert cfg_override.retention_count == 14


def test_compute_sha256_and_filename():
    """Verify SHA-256 calculation and filename format."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"Vantage news database dump test content")
        tmp_path = f.name

    try:
        sha = compute_sha256(tmp_path)
        assert len(sha) == 64
        assert sha == compute_sha256(tmp_path)  # Deterministic

        filename_gz = generate_backup_filename(database_name="vantage_news", compressed=True)
        assert filename_gz.startswith("vantage_backup_")
        assert filename_gz.endswith(".sql.gz")

        filename_raw = generate_backup_filename(database_name="vantage_news", compressed=False)
        assert filename_raw.endswith(".sql")
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def test_create_backup_dry_run(temp_backup_dir):
    """Verify dry-run mode returns success without writing dumps or DB records."""
    cfg = BackupConfig(backup_dir=temp_backup_dir)
    res = create_backup(config=cfg, dry_run=True)

    assert res["status"] in ("dry_run", "success")
    assert res["dry_run"] is True
    assert "backup_id" in res
    assert os.listdir(temp_backup_dir) == []

    db = get_db()
    try:
        assert db.query(BackupRecord).count() == 0
    finally:
        db.close()


def test_create_backup_success_mocked(temp_backup_dir, monkeypatch):
    """Verify successful backup creation, sha256 computation, and DB persistence."""
    cfg = BackupConfig(backup_dir=temp_backup_dir, compression=True, verify_after_create=True)

    # Mock subprocess.run to create a valid gzip file as pg_dump output
    def mock_run(cmd, *args, **kwargs):
        # find the output file from -f or write directly
        if "-f" in cmd:
            out_file = cmd[cmd.index("-f") + 1]
            with gzip.open(out_file, "wb") as f:
                f.write(b"CREATE TABLE test_table (id int); INSERT INTO test_table VALUES (1);")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_run)

    res = create_backup(config=cfg, dry_run=False)

    assert res["status"] in ("success", "verified")
    assert res["is_verified"] is True
    assert res["size_bytes"] > 0
    assert len(res["checksum"]) == 64
    assert os.path.exists(res["filepath"])

    # Verify DB record was saved
    db = get_db()
    try:
        rec = db.query(BackupRecord).filter_by(backup_id=res["backup_id"]).first()
        assert rec is not None
        assert rec.status in ("success", "verified")
        assert rec.is_verified is True
        assert rec.checksum == res["checksum"]
    finally:
        db.close()


def test_create_backup_failure_handling(temp_backup_dir, monkeypatch):
    """Verify failed backup logs error and records status='failed' without marking healthy."""
    cfg = BackupConfig(backup_dir=temp_backup_dir)

    def mock_failed_run(cmd, *args, **kwargs):
        return MagicMock(returncode=1, stdout="", stderr="pg_dump: connection to server failed: Connection refused")

    monkeypatch.setattr(subprocess, "run", mock_failed_run)

    with pytest.raises(RuntimeError, match="(?i)(backup failed|pg_dump failed)"):
        create_backup(config=cfg, dry_run=False)

    db = get_db()
    try:
        rec = db.query(BackupRecord).first()
        assert rec is not None
        assert rec.status == "failed"
        assert rec.is_verified is False
        assert "Connection refused" in rec.error_type
    finally:
        db.close()


def test_verify_backup_integrity_and_tampering(temp_backup_dir, monkeypatch):
    """Verify SHA-256 verification detects corrupted or tampered files."""
    test_file = Path(temp_backup_dir) / "vantage_backup_test.sql.gz"
    with gzip.open(test_file, "wb") as f:
        f.write(b"Valid PostgreSQL snapshot header and data;")

    sha = compute_sha256(str(test_file))
    size = test_file.stat().st_size

    db = get_db()
    try:
        rec = BackupRecord(
            backup_id="test_verify_id",
            filename="vantage_backup_test.sql.gz",
            status="success",
            is_verified=False,
            size_bytes=size,
            checksum=sha,
            database_name="vantage_news",
        )
        db.add(rec)
        db.commit()
    finally:
        db.close()

    cfg = BackupConfig(backup_dir=temp_backup_dir)

    # 1. Verify valid file
    res = verify_backup_by_id("test_verify_id", config=cfg)
    assert res["is_verified"] is True
    assert res["status"] == "success"

    # 2. Tamper with file
    with open(test_file, "ab") as f:
        f.write(b"CORRUPTED_BYTES_APPENDED")

    res_tampered = verify_backup_by_id("test_verify_id", config=cfg)
    assert res_tampered["is_verified"] is False
    assert res_tampered["status"] == "corrupted"
    assert "Checksum mismatch" in res_tampered["error"]


def test_backup_concurrency_lock():
    """Verify concurrent backup attempts are prevented by acquire_backup_lock."""
    release_backup_lock()
    assert is_backup_running() is False

    assert acquire_backup_lock() is True
    assert is_backup_running() is True

    # Second acquisition fails
    assert acquire_backup_lock() is False

    release_backup_lock()
    assert is_backup_running() is False


def test_prune_backups_retention(temp_backup_dir):
    """Verify retention cleanup prunes expired dumps and keeps configured count."""
    cfg = BackupConfig(backup_dir=temp_backup_dir, retention_count=2, retention_days=30)

    db = get_db()
    try:
        # Create 5 dummy backup records and files
        for i in range(5):
            fname = f"vantage_backup_dummy_{i}.sql.gz"
            fpath = Path(temp_backup_dir) / fname
            with open(fpath, "wb") as f:
                f.write(b"dummy dump")

            rec = BackupRecord(
                backup_id=f"dummy_{i}",
                filename=fname,
                status="success",
                size_bytes=10,
                checksum="dummy_sha",
            )
            db.add(rec)
        db.commit()
    finally:
        db.close()

    prune_res = prune_backups(config=cfg, dry_run=False)
    assert prune_res["deleted_files_count"] == 3
    assert prune_res["kept_count"] == 2

    # Verify only 2 files remain on disk
    remaining = os.listdir(temp_backup_dir)
    assert len(remaining) == 2


def test_restore_requires_confirm_and_safety(temp_backup_dir):
    """Verify restore refuses execution without explicit confirm flag."""
    dummy_dump = Path(temp_backup_dir) / "test_restore.sql.gz"
    with gzip.open(dummy_dump, "wb") as f:
        f.write(b"CREATE TABLE restore_test (id int);")

    cfg = RestoreConfig(backup_path=str(dummy_dump), confirm=False)

    # Without confirm
    with pytest.raises(RuntimeError, match="(?i)confirm"):
        restore_database(config=cfg)

    # Dry-run with confirm
    cfg_dry = RestoreConfig(backup_path=str(dummy_dump), confirm=True, dry_run=True)
    dry_res = restore_database(config=cfg_dry)
    assert dry_res["status"] == "success"
    assert dry_res["dry_run"] is True


def test_restore_success_mocked(temp_backup_dir, monkeypatch):
    """Verify restore executes pg_restore with safe environment and zero credential leak."""
    dummy_dump = Path(temp_backup_dir) / "test_restore.sql.gz"
    with gzip.open(dummy_dump, "wb") as f:
        f.write(b"CREATE TABLE restore_test (id int);")

    executed_cmds = []

    def mock_restore_run(cmd, *args, **kwargs):
        executed_cmds.append(cmd)
        # Ensure password is not in command line string
        assert not any("password" in str(arg).lower() for arg in cmd)
        # Ensure PGPASSWORD is in env
        assert "PGPASSWORD" in kwargs.get("env", {})
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", mock_restore_run)

    cfg = RestoreConfig(
        backup_path=str(dummy_dump),
        confirm=True,
        target_db="vantage_news_test",
    )
    res = restore_database(config=cfg)
    assert res["status"] == "success"
    assert len(executed_cmds) >= 1


def test_ops_api_backups_endpoints(temp_backup_dir, monkeypatch):
    """Verify GET /api/ops/backups and POST /api/ops/backups/create security guards."""
    client = TestClient(app)

    # 1. GET /api/ops/backups
    res = client.get("/api/ops/backups")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "retention_policy" in data
    assert "backups" in data

    # 2. POST create with dry_run
    post_res = client.post("/api/ops/backups/create?dry_run=true")
    assert post_res.status_code == 200
    assert post_res.json()["status"] == "success"


def test_scheduler_backup_fail_soft(monkeypatch):
    """Verify BackgroundScheduler fail-soft isolation during backup failures."""
    sched = BackgroundScheduler()

    # Mock BackupConfig enabled
    monkeypatch.setenv("BACKUP_ENABLED", "true")

    # Mock create_backup to raise exception
    def mock_broken_backup(*args, **kwargs):
        raise ConnectionError("PostgreSQL server unreachable for dump")

    monkeypatch.setattr("app.database.backup.create_backup", mock_broken_backup)

    # Should not raise exception
    res = sched.run_scheduled_backup()
    assert res is None
    assert sched.last_backup_status == "failed"
    assert "PostgreSQL server unreachable" in sched.last_backup_error

    status = sched.get_status()
    assert "backup_scheduler" in status
    assert status["backup_scheduler"]["last_backup_status"] == "failed"


def test_disaster_recovery_script_execution():
    """Verify scripts/disaster_recovery_check.py functions execute cleanly."""
    from scripts.disaster_recovery_check import (
        check_database_connectivity_and_schema,
        check_backup_subsystem,
        check_restore_tooling,
        check_operational_apis,
    )
    client = TestClient(app)

    assert check_database_connectivity_and_schema() is True
    assert check_backup_subsystem() is True
    assert check_restore_tooling() is True
    assert check_operational_apis(client) is True
