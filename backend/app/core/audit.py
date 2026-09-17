from datetime import datetime
import json
import logging
from typing import Any, Dict, Optional

from app.core.security import mask_sensitive_data, sanitize_dict_secrets

logger = logging.getLogger("app.security.audit")


def record_audit_event(
    action: str,
    actor: str = "anonymous",
    role: str = "public",
    resource: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    status: str = "allowed",
    details: Optional[Dict[str, Any]] = None,
    error_message: Optional[str] = None,
    db: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Log a structured security audit event and persist it to the database.
    
    Guarantees:
    1. All details and error messages are strictly sanitized of secrets/credentials.
    2. Emits structured JSON log line.
    3. Persists to SecurityAuditLog table (gracefully catches and logs any DB error without raising).
    """
    now = datetime.utcnow()

    # Sanitize details and error messages
    clean_details = sanitize_dict_secrets(details) if details else {}
    clean_error = mask_sensitive_data(error_message) if error_message else None

    event_payload = {
        "timestamp": now.isoformat(),
        "action": action,
        "actor": actor,
        "role": role,
        "resource": resource,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "status": status,
        "details": clean_details,
        "error_message": clean_error,
    }

    # Emit structured log
    log_line = json.dumps(event_payload, default=str)
    if status in ("denied", "failed", "error"):
        logger.warning("[AUDIT_DENIED] %s", log_line)
    else:
        logger.info("[AUDIT_ALLOWED] %s", log_line)

    # Safe persistence to database
    should_close = False
    try:
        if db is None:
            from app.database import database
            if hasattr(database, "SessionLocal"):
                db_session = database.SessionLocal()
                should_close = True
            else:
                db_session = None
        else:
            db_session = db

        if db_session is not None:
            from app.database.models import SecurityAuditLog
            audit_record = SecurityAuditLog(
                timestamp=now,
                actor=actor,
                role=role,
                action=action,
                resource=resource,
                ip_address=ip_address,
                user_agent=user_agent,
                status=status,
                details_json=clean_details,
                error_message=clean_error,
            )
            db_session.add(audit_record)
            db_session.commit()
    except Exception as e:
        logger.warning("Could not persist security audit record to database: %s", e)
        if db_session is not None:
            try:
                db_session.rollback()
            except Exception:
                pass
    finally:
        if should_close and db_session is not None:
            try:
                db_session.close()
            except Exception:
                pass

    return event_payload
