import pytest
from fastapi.testclient import TestClient

from app.core.security import (
    get_allowed_cors_origins,
    is_safe_url,
    mask_secret,
    sanitize_search_query,
    sanitize_url,
    validate_slug,
)
from app.main import app


class TestUrlValidationAndSSRF:
    """Validate external URL security rules, rejecting SSRF targets and malicious schemes."""

    @pytest.mark.parametrize(
        "safe_url",
        [
            "https://news.google.com/rss/articles/CBMi",
            "https://www.reddit.com/r/technology/comments/12345/quantum_breakthrough/",
            "https://x.com/tech_insider/status/1835678901234567890",
            "http://example.com/news/story-123.html",
            "https://subdomain.domain.org/path?param=value&other=123",
        ],
    )
    def test_valid_safe_external_urls(self, safe_url: str):
        assert is_safe_url(safe_url) is True
        assert sanitize_url(safe_url) == safe_url

    @pytest.mark.parametrize(
        "dangerous_url",
        [
            # Localhost and loopback
            "http://localhost",
            "http://localhost:8000/api/topics",
            "http://127.0.0.1",
            "http://127.0.0.1:5432",
            "http://127.0.0.2:8080",
            "http://0.0.0.0:80",
            "http://[::1]/secret",
            # Decimal / integer IP representation for 127.0.0.1
            "http://2130706433",
            # Private RFC1918 networks
            "http://10.0.0.1/admin",
            "http://10.254.1.10:9000",
            "http://172.16.0.1/internal",
            "http://172.31.255.255",
            "http://192.168.1.1/router",
            "http://192.168.0.254:8080",
            # Cloud metadata endpoints (AWS, GCP, Azure, Alibaba)
            "http://169.254.169.254/latest/meta-data/",
            "http://metadata.google.internal/computeMetadata/v1/",
            "http://100.100.100.200/latest/meta-data/",
            # Internal domains
            "http://backend.internal:8000",
            "http://database.local",
            "http://redis.lan",
            # Malicious non-HTTP URI schemes (XSS, local file disclosure)
            "javascript:alert(document.cookie)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "file:///etc/passwd",
            "file://C:/Windows/System32/drivers/etc/hosts",
            "vbscript:msgbox(1)",
            # Empty or malformed
            "",
            "   ",
            "not-a-url",
            None,
        ],
    )
    def test_dangerous_and_internal_urls_rejected(self, dangerous_url: str):
        assert is_safe_url(dangerous_url) is False
        assert sanitize_url(dangerous_url) is None


class TestInputValidationAndSanitization:
    """Test slug validation, SQL LIKE wildcards escaping, and secret masking."""

    @pytest.mark.parametrize(
        "slug, expected",
        [
            ("quantum-computing", True),
            ("ai-agents-2026", True),
            ("tech-news-brief-v2", True),
            ("topic", True),
            ("INVALID_UPPERCASE", False),
            ("with space", False),
            ("../traversal", False),
            ("drop-table;--", False),
            ("slug--with--double--hyphen", False),
            ("", False),
        ],
    )
    def test_slug_validation(self, slug: str, expected: bool):
        assert validate_slug(slug) == expected

    def test_search_query_wildcard_escaping(self):
        # Escapes % and _ to prevent wildcard injection in SQL LIKE queries
        assert sanitize_search_query("100% genuine") == "100\\% genuine"
        assert sanitize_search_query("user_name_test") == "user\\_name\\_test"
        assert sanitize_search_query("normal search") == "normal search"
        assert sanitize_search_query("") == ""

    def test_secret_masking(self):
        assert mask_secret("sk-proj-1234567890abcdefghijklmn") == "sk-p...klmn"
        assert mask_secret("short") == "****"
        assert mask_secret(None) == "<empty>"


class TestSecurityHeadersAndCORS:
    """Verify security response headers and CORS origin restrictions."""

    def test_security_headers_present_on_endpoints(self):
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

        # Verify defensive security headers
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert "X-Process-Time" in response.headers

    def test_cors_origins_configuration(self):
        origins = get_allowed_cors_origins()
        assert isinstance(origins, list)
        assert len(origins) > 0
        assert "http://localhost:3000" in origins
