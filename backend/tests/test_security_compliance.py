from datetime import datetime, timedelta
import ipaddress
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.audit import record_audit_event
from app.core.security import (
    SecurityRole,
    authenticate_credentials,
    get_allowed_cors_origins,
    get_configured_keys_for_role,
    has_sufficient_role,
    is_private_or_forbidden_ip,
    is_safe_url,
    mask_secret,
    mask_sensitive_data,
    sanitize_dict_secrets,
    sanitize_search_query,
    sanitize_url,
    validate_slug,
)
from app.database import database
from app.database.cleanup_ops_history import cleanup_ops_history
from app.database.database import Base
from app.database.models import OperationalAlert, PipelineRun, SecurityAuditLog, Topic
from app.main import app

client = TestClient(app)


# ==========================================
# 1. RBAC Hierarchy & Authentication Tests
# ==========================================

class TestRBACHierarchy(unittest.TestCase):
    def test_role_hierarchy_precedence(self):
        # Public meets public, but not operator or admin
        self.assertTrue(has_sufficient_role(SecurityRole.PUBLIC, SecurityRole.PUBLIC))
        self.assertFalse(has_sufficient_role(SecurityRole.PUBLIC, SecurityRole.OPERATOR))
        self.assertFalse(has_sufficient_role(SecurityRole.PUBLIC, SecurityRole.ADMIN))
        self.assertFalse(has_sufficient_role(SecurityRole.PUBLIC, SecurityRole.SECURITY_ADMIN))

        # Operator meets operator and public, but not admin
        self.assertTrue(has_sufficient_role(SecurityRole.OPERATOR, SecurityRole.PUBLIC))
        self.assertTrue(has_sufficient_role(SecurityRole.OPERATOR, SecurityRole.OPERATOR))
        self.assertFalse(has_sufficient_role(SecurityRole.OPERATOR, SecurityRole.ADMIN))

        # Admin meets admin, operator, and public, but not security_admin
        self.assertTrue(has_sufficient_role(SecurityRole.ADMIN, SecurityRole.PUBLIC))
        self.assertTrue(has_sufficient_role(SecurityRole.ADMIN, SecurityRole.OPERATOR))
        self.assertTrue(has_sufficient_role(SecurityRole.ADMIN, SecurityRole.ADMIN))
        self.assertFalse(has_sufficient_role(SecurityRole.ADMIN, SecurityRole.SECURITY_ADMIN))

        # Security Admin meets all roles
        self.assertTrue(has_sufficient_role(SecurityRole.SECURITY_ADMIN, SecurityRole.PUBLIC))
        self.assertTrue(has_sufficient_role(SecurityRole.SECURITY_ADMIN, SecurityRole.OPERATOR))
        self.assertTrue(has_sufficient_role(SecurityRole.SECURITY_ADMIN, SecurityRole.ADMIN))
        self.assertTrue(has_sufficient_role(SecurityRole.SECURITY_ADMIN, SecurityRole.SECURITY_ADMIN))

    def test_dual_key_rotation_resolution(self):
        with patch.dict(os.environ, {
            "OPS_API_KEY": "primary_ops_key_123",
            "OPS_API_KEY_SECONDARY": "secondary_ops_key_456",
            "ADMIN_API_KEY": "primary_admin_key_789",
            "ADMIN_API_KEY_SECONDARY": "secondary_admin_key_abc",
        }):
            # Primary Ops Key
            role, actor = authenticate_credentials("primary_ops_key_123")
            self.assertEqual(role, SecurityRole.OPERATOR)
            self.assertEqual(actor, "operator_actor")

            # Secondary Rotation Ops Key
            role, actor = authenticate_credentials("secondary_ops_key_456")
            self.assertEqual(role, SecurityRole.OPERATOR)

            # Primary Admin Key
            role, actor = authenticate_credentials("primary_admin_key_789")
            self.assertEqual(role, SecurityRole.ADMIN)
            self.assertEqual(actor, "admin_actor")

            # Secondary Rotation Admin Key
            role, actor = authenticate_credentials("secondary_admin_key_abc")
            self.assertEqual(role, SecurityRole.ADMIN)

            # Invalid key
            role, actor = authenticate_credentials("invalid_random_key")
            self.assertEqual(role, SecurityRole.PUBLIC)
            self.assertEqual(actor, "unauthenticated")

            # Empty key
            role, actor = authenticate_credentials(None)
            self.assertEqual(role, SecurityRole.PUBLIC)
            self.assertEqual(actor, "anonymous")


# ==========================================
# 2. SSRF Multi-Layer Protection Tests
# ==========================================

class TestSSRFDefenses(unittest.TestCase):
    def test_forbidden_schemes(self):
        self.assertFalse(is_safe_url("file:///etc/passwd"))
        self.assertFalse(is_safe_url("gopher://127.0.0.1:70/"))
        self.assertFalse(is_safe_url("dict://127.0.0.1:11211/stat"))
        self.assertFalse(is_safe_url("ftp://example.com/file.txt"))
        self.assertFalse(is_safe_url("data:text/html,<script>alert(1)</script>"))
        self.assertFalse(is_safe_url("javascript:alert(1)"))

    def test_loopback_and_localhost(self):
        self.assertFalse(is_safe_url("http://localhost:8000/api"))
        self.assertFalse(is_safe_url("http://127.0.0.1:8000/metrics"))
        self.assertFalse(is_safe_url("http://0.0.0.0/"))
        self.assertFalse(is_safe_url("http://[::1]:8080/"))
        self.assertFalse(is_safe_url("http://localhost.localdomain/"))

    def test_private_subnets_rfc1918(self):
        self.assertFalse(is_safe_url("http://10.0.0.1/admin"))
        self.assertFalse(is_safe_url("http://10.254.0.1/secrets"))
        self.assertFalse(is_safe_url("http://172.16.0.1/status"))
        self.assertFalse(is_safe_url("http://172.31.255.255/"))
        self.assertFalse(is_safe_url("http://192.168.1.1/router"))
        self.assertFalse(is_safe_url("http://192.168.0.100:8080/"))

    def test_cloud_metadata_endpoints(self):
        # AWS/GCP/Azure link-local metadata
        self.assertFalse(is_safe_url("http://169.254.169.254/latest/meta-data/"))
        self.assertFalse(is_safe_url("http://169.254.169.254/computeMetadata/v1/"))
        self.assertFalse(is_safe_url("http://metadata.google.internal/computeMetadata/v1/"))
        self.assertFalse(is_safe_url("http://metadata.internal/"))
        self.assertFalse(is_safe_url("http://instance-data/"))
        # Alibaba Cloud metadata
        self.assertFalse(is_safe_url("http://100.100.100.200/latest/meta-data/"))

    def test_obfuscated_ip_representations(self):
        # Decimal integer IP representations
        # 2130706433 == 127.0.0.1
        self.assertFalse(is_safe_url("http://2130706433/"))
        # 2852039166 == 169.254.169.254
        self.assertFalse(is_safe_url("http://2852039166/"))
        # 0x7f000001 == 127.0.0.1 (hex)
        self.assertFalse(is_safe_url("http://0x7f000001/"))
        # Octal notation: 0177.0.0.1 == 127.0.0.1
        self.assertFalse(is_safe_url("http://0177.0.0.1/"))
        # Hex notation dotted: 0x7f.0.0.1 == 127.0.0.1
        self.assertFalse(is_safe_url("http://0x7f.0.0.1/"))

    def test_embedded_credentials_blocked(self):
        self.assertFalse(is_safe_url("http://admin:password@news.google.com/rss"))
        self.assertFalse(is_safe_url("https://user:secret@bbc.com/world"))

    def test_valid_public_urls_permitted(self):
        self.assertTrue(is_safe_url("https://news.google.com/rss/search?q=AI"))
        self.assertTrue(is_safe_url("https://www.reuters.com/technology/"))
        self.assertTrue(is_safe_url("http://feeds.bbci.co.uk/news/rss.xml"))
        self.assertEqual(
            sanitize_url("https://news.google.com/rss"),
            "https://news.google.com/rss",
        )
        self.assertIsNone(sanitize_url("http://169.254.169.254/meta-data"))


# ==========================================
# 3. Secret Redaction & Masking Tests
# ==========================================

class TestSecretMasking(unittest.TestCase):
    def test_mask_secret(self):
        self.assertEqual(mask_secret(""), "<empty>")
        self.assertEqual(mask_secret(None), "<empty>")
        self.assertEqual(mask_secret("short"), "****")
        self.assertEqual(mask_secret("sk-1234567890abcdef"), "sk-1...cdef")

    def test_mask_sensitive_data(self):
        # OpenAI keys
        raw_openai = "Error calling provider with key sk-proj-1234567890abcdef12345678"
        self.assertIn("[REDACTED_API_KEY]", mask_sensitive_data(raw_openai))
        self.assertNotIn("1234567890abcdef", mask_sensitive_data(raw_openai))

        # Bearer tokens
        raw_bearer = "Authorization: Bearer mySecretToken1234567890"
        self.assertIn("[REDACTED]", mask_sensitive_data(raw_bearer))
        self.assertNotIn("mySecretToken", mask_sensitive_data(raw_bearer))

        # Database URLs with passwords
        raw_db = "Connecting to postgresql://postgres:superSecretPass123@db.prod.internal:5432/vantage"
        self.assertIn("postgresql://postgres:****@", mask_sensitive_data(raw_db))
        self.assertNotIn("superSecretPass123", mask_sensitive_data(raw_db))

        # Redis URLs
        raw_redis = "Connecting to redis://:mypassword123@redis.prod:6379/0"
        self.assertIn("redis://:****@", mask_sensitive_data(raw_redis))
        self.assertNotIn("mypassword123", mask_sensitive_data(raw_redis))

    def test_sanitize_dict_secrets(self):
        payload = {
            "user": "test_operator",
            "api_key": "sk-sensitive-key-1234567890",
            "password": "myDatabasePassword",
            "nested": {
                "secret": "topSecretValue",
                "normal_field": "public_data",
            },
            "list_items": [
                {"token": "secret_token_1"},
                {"name": "harmless_item"},
            ],
        }
        sanitized = sanitize_dict_secrets(payload)
        self.assertEqual(sanitized["api_key"], "[REDACTED]")
        self.assertEqual(sanitized["password"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["secret"], "[REDACTED]")
        self.assertEqual(sanitized["nested"]["normal_field"], "public_data")
        self.assertEqual(sanitized["list_items"][0]["token"], "[REDACTED]")
        self.assertEqual(sanitized["list_items"][1]["name"], "harmless_item")


# ==========================================
# 4. HTTP Security Headers & Middleware Tests
# ==========================================

class TestSecurityHeadersAndLimits(unittest.TestCase):
    def test_security_headers_present(self):
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)

        # Verify all mandatory security headers
        headers = response.headers
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertEqual(headers.get("Cross-Origin-Opener-Policy"), "same-origin")
        self.assertEqual(headers.get("Cross-Origin-Resource-Policy"), "same-origin")
        self.assertIn("max-age=31536000", headers.get("Strict-Transport-Security", ""))
        self.assertIn("default-src 'self'", headers.get("Content-Security-Policy", ""))
        self.assertIn("camera=()", headers.get("Permissions-Policy", ""))
        self.assertIn("X-Process-Time", headers)

    def test_oversized_payload_rejected_413(self):
        # 3MB payload exceeds 2MB limit
        large_body = "A" * (3 * 1024 * 1024)
        response = client.post(
            "/api/ops/alerts/evaluate",
            data=large_body,
            headers={"Content-Length": str(len(large_body))},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("Payload too large", response.json()["detail"])

    def test_cors_origins(self):
        origins = get_allowed_cors_origins()
        self.assertIsInstance(origins, list)
        self.assertTrue(len(origins) > 0)


# ==========================================
# 5. Endpoint RBAC Protection Tests
# ==========================================

class TestEndpointRBAC(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {
            "ENVIRONMENT": "production",
            "OPS_API_KEY": "ops_secret_key_123",
            "OPS_API_KEY_SECONDARY": "ops_rot_key_456",
            "ADMIN_API_KEY": "admin_secret_key_789",
            "ADMIN_API_KEY_SECONDARY": "admin_rot_key_abc",
        })
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    def test_public_endpoints_accessible_without_auth(self):
        # /health
        res = client.get("/health")
        self.assertEqual(res.status_code, 200)

        # /ready
        res = client.get("/ready")
        self.assertIn(res.status_code, (200, 503))

        # /metrics
        res = client.get("/metrics")
        self.assertEqual(res.status_code, 200)

        # /api/topics
        res = client.get("/api/topics")
        self.assertEqual(res.status_code, 200)

    def test_operator_endpoint_requires_auth(self):
        # Missing auth -> 401
        res = client.post("/api/ops/alerts/evaluate")
        self.assertEqual(res.status_code, 401)

        # Invalid key -> 401 Unauthorized
        res = client.post("/api/ops/alerts/evaluate", headers={"X-API-Key": "wrong_key"})
        self.assertEqual(res.status_code, 401)

        # Valid primary Ops key -> 200

        res = client.post("/api/ops/alerts/evaluate", headers={"X-Ops-Key": "ops_secret_key_123"})
        self.assertEqual(res.status_code, 200)

        # Valid secondary rotation Ops key -> 200
        res = client.post("/api/ops/alerts/evaluate", headers={"X-API-Key": "ops_rot_key_456"})
        self.assertEqual(res.status_code, 200)

        # Bearer token format -> 200
        res = client.post("/api/ops/alerts/evaluate", headers={"Authorization": "Bearer ops_secret_key_123"})
        self.assertEqual(res.status_code, 200)

    def test_admin_endpoint_privilege_boundary(self):
        # Operator key accessing Admin endpoint -> 403 Forbidden
        res = client.post("/api/ops/resilience/reset", headers={"X-Ops-Key": "ops_secret_key_123"})
        self.assertEqual(res.status_code, 403)

        # Admin key accessing Admin endpoint -> 200 OK
        res = client.post("/api/ops/resilience/reset", headers={"X-Admin-Key": "admin_secret_key_789"})
        self.assertEqual(res.status_code, 200)

        # Secondary Admin rotation key -> 200 OK
        res = client.post("/api/ops/resilience/reset", headers={"X-API-Key": "admin_rot_key_abc"})
        self.assertEqual(res.status_code, 200)


# ==========================================
# 6. Security Audit Logging Tests
# ==========================================

class TestSecurityAuditLogging(unittest.TestCase):
    def test_record_audit_event(self):
        session = database.SessionLocal()
        try:
            event = record_audit_event(
                action="test_security_event",
                actor="test_admin_user",
                role="admin",
                resource="/api/ops/test",
                ip_address="192.0.2.1",
                user_agent="VantageSecurityTest/1.0",
                status="allowed",
                details={"api_key": "sk-secret-1234567890", "info": "safe_data"},
                error_message="Error with token bearer secretToken123456789",
                db=session,
            )
            self.assertEqual(event["action"], "test_security_event")
            self.assertEqual(event["actor"], "test_admin_user")
            self.assertEqual(event["details"]["api_key"], "[REDACTED]")
            self.assertEqual(event["details"]["info"], "safe_data")
            self.assertIn("[REDACTED]", event["error_message"])

            # Verify persisted in DB
            db_record = session.query(SecurityAuditLog).filter(
                SecurityAuditLog.action == "test_security_event"
            ).first()
            self.assertIsNotNone(db_record)
            self.assertEqual(db_record.actor, "test_admin_user")
            self.assertEqual(db_record.details_json.get("api_key"), "[REDACTED]")
        finally:
            session.close()

    def test_get_audit_logs_endpoint(self):
        with patch.dict(os.environ, {
            "ENVIRONMENT": "production",
            "OPS_API_KEY": "ops_secret_key_123",
        }):
            res = client.get(
                "/api/ops/audit-logs",
                headers={"X-Ops-Key": "ops_secret_key_123"},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("audit_logs", data)
            self.assertIsInstance(data["audit_logs"], list)


# ==========================================
# 7. Data Retention & Privacy Pruning Tests
# ==========================================

class TestDataRetention(unittest.TestCase):
    def test_cleanup_ops_and_audit_history(self):
        session = database.SessionLocal()
        try:
            now = datetime.utcnow()
            old_time = now - timedelta(days=200)

            # Insert expired pipeline run
            old_run = PipelineRun(
                topic_slug="test-expired-topic",
                pipeline_type="discourse_pipeline",
                status="success",
                started_at=old_time,
            )
            session.add(old_run)

            # Insert expired audit log
            old_audit = SecurityAuditLog(
                timestamp=old_time,
                actor="old_actor",
                role="operator",
                action="old_action",
                status="allowed",
            )
            session.add(old_audit)

            # Insert active alert (must NEVER be pruned)
            active_alert = OperationalAlert(
                alert_id="retention_test:alert",
                alert_type="retention_test",
                severity="warning",
                component="retention",
                status="active",
                message="Active alert should remain",
                first_seen=old_time,
                last_seen=old_time,
            )
            session.add(active_alert)
            session.commit()

            # Execute dry run
            dry_res = cleanup_ops_history(
                ops_retention_days=30,
                alert_retention_days=90,
                audit_retention_days=180,
                dry_run=True,
                db=session,
            )
            self.assertTrue(dry_res["pipeline_runs_pruned"] >= 1)
            self.assertTrue(dry_res["security_audit_logs_pruned"] >= 1)

            # Execute real cleanup
            real_res = cleanup_ops_history(
                ops_retention_days=30,
                alert_retention_days=90,
                audit_retention_days=180,
                dry_run=False,
                db=session,
            )
            self.assertTrue(real_res["pipeline_runs_pruned"] >= 1)
            self.assertTrue(real_res["security_audit_logs_pruned"] >= 1)

            # Confirm active alert was preserved
            remaining_alert = session.query(OperationalAlert).filter(
                OperationalAlert.alert_id == "retention_test:alert"
            ).first()
            self.assertIsNotNone(remaining_alert)
            self.assertEqual(remaining_alert.status, "active")

            # Cleanup test alert
            session.delete(remaining_alert)
            session.commit()
        finally:
            session.close()


# ==========================================
# 8. Input Validation & Slug / Query Sanitization
# ==========================================

class TestInputValidation(unittest.TestCase):
    def test_slug_validation(self):
        self.assertTrue(validate_slug("ai-breakthrough-2026"))
        self.assertTrue(validate_slug("quantum-computing"))
        self.assertTrue(validate_slug("spacex-starship-v3"))
        self.assertFalse(validate_slug(""))
        self.assertFalse(validate_slug("AI Breakthrough"))  # spaces and uppercase
        self.assertFalse(validate_slug("topic/../traversal"))
        self.assertFalse(validate_slug("topic;DROP TABLE topics;--"))
        self.assertFalse(validate_slug("a" * 150))  # too long

    def test_search_query_sanitization(self):
        self.assertEqual(sanitize_search_query("AI & Robotics"), "AI & Robotics")
        self.assertEqual(sanitize_search_query("100% discount"), "100\\% discount")
        self.assertEqual(sanitize_search_query("test_variable"), "test\\_variable")
        self.assertEqual(sanitize_search_query(""), "")
