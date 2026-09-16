import ipaddress
import logging
import os
import re
from typing import List, Optional
import urllib.parse

logger = logging.getLogger("app.security")

# Regular expression for valid topic slug (alphanumeric and single hyphens)
SLUG_REGEX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Forbidden hosts / prefixes for SSRF mitigation
FORBIDDEN_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "[::1]",
    "metadata.google.internal",
    "metadata.internal",
}

# Cloud metadata IP networks
METADATA_NETWORKS = [
    ipaddress.ip_network("169.254.0.0/16"),       # Link-local / AWS/GCP/Azure metadata
    ipaddress.ip_network("100.100.100.200/32"),   # Alibaba Cloud metadata
    ipaddress.ip_network("fe80::/10"),            # IPv6 Link-local
    ipaddress.ip_network("fd00::/8"),             # IPv6 Unique Local Address
]


def is_ip_address(host: str) -> bool:
    """Check if the string is a valid IPv4 or IPv6 address."""
    clean_host = host.strip("[]")
    try:
        ipaddress.ip_address(clean_host)
        return True
    except ValueError:
        # Check integer/hex format representations (e.g. 2130706433)
        if clean_host.isdigit():
            try:
                ipaddress.ip_address(int(clean_host))
                return True
            except ValueError:
                pass
        return False


def is_private_or_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address belongs to private, loopback, link-local, or cloud metadata ranges."""
    if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local or ip_obj.is_multicast or ip_obj.is_reserved or ip_obj.is_unspecified:
        return True
    for net in METADATA_NETWORKS:
        if ip_obj in net:
            return True
    return False


def is_safe_url(url: Optional[str]) -> bool:
    """
    Validate that a URL is a safe, external HTTP/HTTPS URL and not an internal network or SSRF target.
    
    Checks:
    1. Scheme must strictly be 'http' or 'https'
    2. Must contain a valid hostname
    3. Hostname cannot be localhost or an internal alias
    4. Hostname / IP cannot belong to RFC1918 private subnets, loopback (127.0.0.0/8), link-local, or cloud metadata
    """
    if not url or not isinstance(url, str):
        return False

    url_str = url.strip()
    if not url_str or len(url_str) > 2048:
        return False

    try:
        parsed = urllib.parse.urlparse(url_str)
    except Exception:
        return False

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False

    if hostname in FORBIDDEN_HOSTNAMES:
        return False

    # Block local or internal top-level domains
    if hostname.endswith((".local", ".internal", ".lan", ".localhost", ".localdomain", ".intranet")):
        return False

    # Check if host is an IP address
    clean_host = hostname.strip("[]")
    if is_ip_address(clean_host):
        try:
            if clean_host.isdigit():
                ip_obj = ipaddress.ip_address(int(clean_host))
            else:
                ip_obj = ipaddress.ip_address(clean_host)
            if is_private_or_forbidden_ip(ip_obj):
                return False
        except ValueError:
            return False

    return True


def sanitize_url(url: Optional[str]) -> Optional[str]:
    """Return the sanitized URL string if safe, otherwise return None."""
    if is_safe_url(url):
        return url.strip()
    return None


def validate_slug(slug: Optional[str]) -> bool:
    """Validate that a slug conforms to URL-safe alphanumeric hyphen format."""
    if not slug or not isinstance(slug, str):
        return False
    clean_slug = slug.strip()
    if len(clean_slug) < 1 or len(clean_slug) > 100:
        return False
    return bool(SLUG_REGEX.match(clean_slug))


def sanitize_search_query(query: Optional[str]) -> str:
    """Sanitize user search queries, escaping SQL LIKE wildcard characters (% and _)."""
    if not query:
        return ""
    # Strip dangerous characters and escape SQL LIKE wildcards
    clean = query.strip()
    clean = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return clean


def mask_secret(secret: Optional[str], visible_chars: int = 4) -> str:
    """Mask sensitive secrets for safe logging or debug introspection."""
    if not secret:
        return "<empty>"
    sec_str = str(secret).strip()
    if len(sec_str) <= visible_chars * 2:
        return "****"
    return f"{sec_str[:visible_chars]}...{sec_str[-visible_chars:]}"


def get_allowed_cors_origins() -> List[str]:
    """Parse CORS allowed origins from environment variable or return secure defaults."""
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        return [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ]
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins if origins else ["http://localhost:3000"]
