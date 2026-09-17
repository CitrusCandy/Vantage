import enum
import hmac
import ipaddress
import logging
import os
import re
import socket
from typing import Any, Dict, List, Optional, Set, Tuple
import urllib.parse

from fastapi import Depends, Header, HTTPException, Request, status

logger = logging.getLogger("app.security")

# Regular expression for valid topic slug (alphanumeric and single hyphens)
SLUG_REGEX = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# ==========================================
# 1. RBAC Roles & Hierarchy
# ==========================================

class SecurityRole(str, enum.Enum):
    PUBLIC = "public"
    OPERATOR = "operator"
    ADMIN = "admin"
    SECURITY_ADMIN = "security_admin"


# Role hierarchy mapping (a role inherits all rights of lower roles)
ROLE_HIERARCHY: Dict[SecurityRole, int] = {
    SecurityRole.PUBLIC: 0,
    SecurityRole.OPERATOR: 1,
    SecurityRole.ADMIN: 2,
    SecurityRole.SECURITY_ADMIN: 3,
}


def has_sufficient_role(user_role: SecurityRole, required_role: SecurityRole) -> bool:
    """Check if the user's role meets or exceeds the required privilege level."""
    return ROLE_HIERARCHY.get(user_role, 0) >= ROLE_HIERARCHY.get(required_role, 0)


# ==========================================
# 2. Key Management & Rotation
# ==========================================

def _constant_time_compare(val_a: Optional[str], val_b: Optional[str]) -> bool:
    """Perform constant-time string comparison to prevent timing attacks."""
    if val_a is None or val_b is None:
        return False
    if not isinstance(val_a, str) or not isinstance(val_b, str):
        return False
    return hmac.compare_digest(val_a.encode("utf-8"), val_b.encode("utf-8"))


def get_configured_keys_for_role(role: SecurityRole) -> List[str]:
    """
    Retrieve primary and secondary (rotation) keys configured for a role.
    Supports seamless dual-key rotation without service downtime.
    """
    keys: List[str] = []
    if role == SecurityRole.OPERATOR:
        primary = os.getenv("OPS_API_KEY", "").strip()
        secondary = os.getenv("OPS_API_KEY_SECONDARY", "").strip()
        if primary:
            keys.append(primary)
        if secondary:
            keys.append(secondary)
    elif role == SecurityRole.ADMIN:
        primary = os.getenv("ADMIN_API_KEY", "").strip()
        secondary = os.getenv("ADMIN_API_KEY_SECONDARY", "").strip()
        if primary:
            keys.append(primary)
        if secondary:
            keys.append(secondary)
    elif role == SecurityRole.SECURITY_ADMIN:
        primary = os.getenv("SECURITY_API_KEY", "").strip()
        secondary = os.getenv("SECURITY_API_KEY_SECONDARY", "").strip()
        if primary:
            keys.append(primary)
        if secondary:
            keys.append(secondary)
    return keys



def extract_token_from_request(
    request: Request,
    x_api_key: Optional[str] = None,
    x_ops_key: Optional[str] = None,
    x_admin_key: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Tuple[Optional[str], str]:
    """
    Extract credential token and header source from request headers or parameters.
    Returns (token_value, source_name).
    """
    if x_admin_key and x_admin_key.strip():
        return x_admin_key.strip(), "X-Admin-Key"
    if x_ops_key and x_ops_key.strip():
        return x_ops_key.strip(), "X-Ops-Key"
    if x_api_key and x_api_key.strip():
        return x_api_key.strip(), "X-API-Key"

    if authorization and authorization.strip():
        auth_val = authorization.strip()
        if auth_val.lower().startswith("bearer "):
            return auth_val[7:].strip(), "Authorization (Bearer)"
        return auth_val, "Authorization"

    # Fallback to query param for token if present (dev/testing inspection only)
    token_param = request.query_params.get("api_key")
    if token_param and token_param.strip():
        return token_param.strip(), "QueryParam"

    return None, "None"


def authenticate_credentials(provided_key: Optional[str]) -> Tuple[SecurityRole, str]:
    """
    Authenticate an incoming token against configured keys across roles.
    Returns (authenticated_role, actor_identifier).
    """
    if not provided_key:
        return SecurityRole.PUBLIC, "anonymous"

    # 1. Check Security Admin keys
    sec_keys = get_configured_keys_for_role(SecurityRole.SECURITY_ADMIN)
    for k in sec_keys:
        if _constant_time_compare(provided_key, k):
            return SecurityRole.SECURITY_ADMIN, "security_admin_actor"

    # 2. Check Admin keys
    admin_keys = get_configured_keys_for_role(SecurityRole.ADMIN)
    for k in admin_keys:
        if _constant_time_compare(provided_key, k):
            return SecurityRole.ADMIN, "admin_actor"

    # 3. Check Operator keys
    ops_keys = get_configured_keys_for_role(SecurityRole.OPERATOR)
    for k in ops_keys:
        if _constant_time_compare(provided_key, k):
            return SecurityRole.OPERATOR, "operator_actor"

    return SecurityRole.PUBLIC, "unauthenticated"


# ==========================================
# 3. FastAPI RBAC Dependencies
# ==========================================

def require_role(required_role: SecurityRole):
    """
    FastAPI dependency factory enforcing Role-Based Access Control (RBAC).
    
    If no keys are configured in the environment (e.g. initial dev/local environment),
    access is permitted with a warning log, unless ENVIRONMENT is explicitly set to production.
    In production with configured keys, strict constant-time matching is enforced.
    """
    async def _role_guard(
        request: Request,
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
        x_ops_key: Optional[str] = Header(default=None, alias="X-Ops-Key"),
        x_admin_key: Optional[str] = Header(default=None, alias="X-Admin-Key"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> SecurityRole:
        # Public endpoints require no authentication
        if required_role == SecurityRole.PUBLIC:
            request.state.actor = "anonymous"
            request.state.role = SecurityRole.PUBLIC
            return SecurityRole.PUBLIC

        provided_token, source = extract_token_from_request(
            request=request,
            x_api_key=x_api_key,
            x_ops_key=x_ops_key,
            x_admin_key=x_admin_key,
            authorization=authorization,
        )

        # Check if environment is production or keys are configured
        env = os.getenv("ENVIRONMENT", "development").lower()
        configured_for_required = get_configured_keys_for_role(required_role)
        all_ops_keys = get_configured_keys_for_role(SecurityRole.OPERATOR)
        all_admin_keys = get_configured_keys_for_role(SecurityRole.ADMIN)
        has_any_keys = bool(configured_for_required or all_ops_keys or all_admin_keys)

        # In dev mode with no keys configured at all, permit access as operator/admin
        if not has_any_keys and env != "production":
            request.state.actor = "dev_default_actor"
            request.state.role = required_role
            return required_role

        if not provided_token:
            from app.core.audit import record_audit_event
            record_audit_event(
                action="auth_failure",
                actor="anonymous",
                role="public",
                resource=str(request.url.path),
                ip_address=request.client.host if request.client else "unknown",
                user_agent=request.headers.get("user-agent", "unknown"),
                status="denied",
                error_message=f"Missing credentials for role '{required_role.value}'",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Ops-Key header for operational controls",
                headers={"WWW-Authenticate": "Bearer"},
            )

        authenticated_role, actor = authenticate_credentials(provided_token)

        # If key was invalid / unrecognized
        if authenticated_role == SecurityRole.PUBLIC:
            from app.core.audit import record_audit_event
            record_audit_event(
                action="auth_failure",
                actor="unauthenticated",
                role="public",
                resource=str(request.url.path),
                ip_address=request.client.host if request.client else "unknown",
                user_agent=request.headers.get("user-agent", "unknown"),
                status="denied",
                error_message=f"Invalid credentials supplied for role '{required_role.value}'",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing X-Ops-Key header for operational controls",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not has_sufficient_role(authenticated_role, required_role):
            from app.core.audit import record_audit_event
            record_audit_event(
                action="authorization_denied",
                actor=actor,
                role=authenticated_role.value,
                resource=str(request.url.path),
                ip_address=request.client.host if request.client else "unknown",
                user_agent=request.headers.get("user-agent", "unknown"),
                status="denied",
                error_message=f"Role '{authenticated_role.value}' does not satisfy required '{required_role.value}'",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient privileges. Required: '{required_role.value}', provided role: '{authenticated_role.value}'",
            )

        request.state.actor = actor
        request.state.role = authenticated_role
        return authenticated_role


    return _role_guard


# Convenient predefined dependency instances
require_public = require_role(SecurityRole.PUBLIC)
require_operator = require_role(SecurityRole.OPERATOR)
require_admin = require_role(SecurityRole.ADMIN)
require_security_admin = require_role(SecurityRole.SECURITY_ADMIN)


# ==========================================
# 4. Multi-Layer SSRF Protections
# ==========================================

# Forbidden hosts / prefixes for SSRF mitigation
FORBIDDEN_HOSTNAMES: Set[str] = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "[::1]",
    "metadata.google.internal",
    "metadata.internal",
    "instance-data",
    "kubernetes.default",
    "kubernetes.default.svc",
}

# Cloud metadata and internal IP networks (IPv4 & IPv6)
METADATA_NETWORKS: List[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),           # Current network (only valid as source)
    ipaddress.ip_network("10.0.0.0/8"),          # RFC 1918 Private
    ipaddress.ip_network("100.64.0.0/10"),       # Carrier-grade NAT
    ipaddress.ip_network("127.0.0.0/8"),         # Loopback
    ipaddress.ip_network("169.254.0.0/16"),      # Link-local / AWS/GCP/Azure metadata
    ipaddress.ip_network("172.16.0.0/12"),       # RFC 1918 Private
    ipaddress.ip_network("192.0.0.0/24"),        # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),        # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),      # RFC 1918 Private
    ipaddress.ip_network("198.18.0.0/15"),       # Benchmark Testing
    ipaddress.ip_network("198.51.100.0/24"),     # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),      # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),         # Multicast
    ipaddress.ip_network("240.0.0.0/4"),         # Reserved / Future use
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
    ipaddress.ip_network("::1/128"),             # IPv6 Loopback
    ipaddress.ip_network("::/128"),              # IPv6 Unspecified
    ipaddress.ip_network("fe80::/10"),           # IPv6 Link-local
    ipaddress.ip_network("fd00::/8"),            # IPv6 Unique Local Address (ULA)
    ipaddress.ip_network("fc00::/7"),            # IPv6 Unique Local
]


def _decode_nonstandard_ip(host_str: str) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """
    Attempt to decode decimal, octal, hex, or standard IP representations.
    Handles evasions like 2130706433 (127.0.0.1) or 0177.0.0.1 or 0x7f.0.0.1.
    """
    clean = host_str.strip("[]").strip()
    if not clean:
        return None

    # Standard IP check
    try:
        return ipaddress.ip_address(clean)
    except ValueError:
        pass

    # Pure integer decimal representation (e.g. 2130706433)
    if clean.isdigit():
        try:
            val = int(clean)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except (ValueError, OverflowError):
            pass

    # Hexadecimal integer representation (e.g. 0x7f000001)
    if clean.lower().startswith("0x"):
        try:
            val = int(clean, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except (ValueError, OverflowError):
            pass

    # Dotted octal/hex segments (e.g. 0177.0.0.1 or 0x7f.0.0.1)
    parts = clean.split(".")
    if len(parts) == 4:
        try:
            bytes_list = []
            for p in parts:
                p_str = p.strip()
                if p_str.lower().startswith("0x"):
                    num = int(p_str, 16)
                elif p_str.startswith("0") and len(p_str) > 1:
                    num = int(p_str, 8)
                else:
                    num = int(p_str, 10)
                if 0 <= num <= 255:
                    bytes_list.append(num)
                else:
                    return None
            if len(bytes_list) == 4:
                return ipaddress.IPv4Address(bytes(bytes_list))
        except (ValueError, OverflowError):
            pass

    return None


def is_private_or_forbidden_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Check if an IP address belongs to private, loopback, link-local, multicast, or cloud metadata ranges."""
    if (
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    ):
        return True

    for net in METADATA_NETWORKS:
        if ip_obj in net:
            return True
    return False


def is_safe_url(url: Optional[str], check_dns: bool = False) -> bool:
    """
    Validate that a URL is a safe, external HTTP/HTTPS URL and not an internal network or SSRF target.
    
    Checks:
    1. Scheme must strictly be 'http' or 'https'
    2. Must not contain embedded credentials (user:pass@host)
    3. Hostname cannot be localhost, internal alias, or cloud metadata domain
    4. Hostname / IP (in decimal, octal, hex, or standard format) cannot belong to private, loopback, or metadata subnets
    5. Optionally resolves DNS to prevent DNS rebinding to internal IP ranges
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

    # Block embedded credentials (e.g. http://admin:secret@victim.com)
    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False

    if hostname in FORBIDDEN_HOSTNAMES:
        return False

    # Block local or internal top-level domains
    if hostname.endswith((
        ".local",
        ".internal",
        ".lan",
        ".localhost",
        ".localdomain",
        ".intranet",
        ".corp",
        ".home",
        ".arpa",
    )):
        return False

    # Check for IP address formats (decimal, octal, hex, standard)
    decoded_ip = _decode_nonstandard_ip(hostname)
    if decoded_ip is not None:
        if is_private_or_forbidden_ip(decoded_ip):
            return False

    # Optional DNS resolution pre-flight check
    if check_dns:
        try:
            resolved_ips = socket.getaddrinfo(hostname, None)
            for item in resolved_ips:
                sockaddr = item[4]
                ip_str = sockaddr[0]
                ip_obj = ipaddress.ip_address(ip_str)
                if is_private_or_forbidden_ip(ip_obj):
                    return False
        except (socket.gaierror, ValueError, OSError):
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
    clean = query.strip()
    clean = clean.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return clean


# ==========================================
# 5. Secret Masking & Redaction
# ==========================================

def mask_secret(secret: Optional[str], visible_chars: int = 4) -> str:
    """Mask sensitive secrets for safe logging or debug introspection."""
    if not secret:
        return "<empty>"
    sec_str = str(secret).strip()
    if len(sec_str) <= visible_chars * 2:
        return "****"
    return f"{sec_str[:visible_chars]}...{sec_str[-visible_chars:]}"


def mask_sensitive_data(text_val: Optional[str]) -> str:
    """Mask tokens, passwords, cookies, API keys, and connection strings from arbitrary text."""
    if not text_val or not isinstance(text_val, str):
        return ""
    masked = text_val
    # 1. Mask specific high-entropy credential patterns first
    masked = re.sub(r"sk-[a-zA-Z0-9_\-]{20,}", "[REDACTED_API_KEY]", masked)
    masked = re.sub(r"ghp_[a-zA-Z0-9]{20,}", "[REDACTED_TOKEN]", masked)
    masked = re.sub(r"eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}", "[REDACTED_JWT]", masked)
    masked = re.sub(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{8,}", r"\1[REDACTED]", masked)
    masked = re.sub(r"postgres(?:ql)?://([^:]+):([^@]+)@", r"postgresql://\1:****@", masked)
    masked = re.sub(r"redis://:([^@]+)@", r"redis://:****@", masked)
    # 2. Mask generic key/secret/password assignments
    masked = re.sub(
        r"(?i)(token|password|secret|key|cookie|apikey|api_key)[=:\s]+['\"]?[a-zA-Z0-9_\-\.]{8,}['\"]?",
        r"\1: [REDACTED]",
        masked,
    )
    return masked



def sanitize_dict_secrets(data: Any) -> Any:
    """Recursively mask sensitive keys in nested dictionaries and lists."""
    sensitive_keys = {
        "password",
        "secret",
        "token",
        "api_key",
        "apikey",
        "ops_api_key",
        "admin_api_key",
        "security_api_key",
        "authorization",
        "cookie",
        "session",
        "private_key",
    }
    if isinstance(data, dict):
        sanitized = {}
        for k, v in data.items():
            if str(k).lower() in sensitive_keys:
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, (dict, list)):
                sanitized[k] = sanitize_dict_secrets(v)
            elif isinstance(v, str):
                sanitized[k] = mask_sensitive_data(v)
            else:
                sanitized[k] = v
        return sanitized
    elif isinstance(data, list):
        return [sanitize_dict_secrets(item) for item in data]
    elif isinstance(data, str):
        return mask_sensitive_data(data)
    return data


# ==========================================
# 6. CORS Configuration
# ==========================================

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
