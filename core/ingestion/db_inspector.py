"""
core/ingestion/db_inspector.py
─────────────────────────────────────────────────────────────────────────────
Live Database Isolation Inspector (OmniGraph v2)

Replaces static config-file parsing. Connects to the target database
using a scan-scoped read-only credential to determine the exact
default transaction isolation level configured on the server.
"""

import os
import logging
from typing import Optional
from core.schema import DbIsolationLevel

logger = logging.getLogger(__name__)

def fetch_postgres_isolation(db_url: str) -> Optional[DbIsolationLevel]:
    """
    Connects to a PostgreSQL database and queries the default transaction isolation.
    Requires 'psycopg2' or 'pg8000'.
    """
    try:
        import psycopg2
    except ImportError:
        logger.warning("psycopg2 not installed. Cannot perform live DB isolation check.")
        return None

    try:
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("SHOW default_transaction_isolation;")
        result = cur.fetchone()[0].upper()
        cur.close()
        conn.close()

        # Map Postgres string to Enum
        mapping = {
            "READ UNCOMMITTED": DbIsolationLevel.READ_UNCOMMITTED,
            "READ COMMITTED": DbIsolationLevel.READ_COMMITTED,
            "REPEATABLE READ": DbIsolationLevel.REPEATABLE_READ,
            "SERIALIZABLE": DbIsolationLevel.SERIALIZABLE,
        }
        return mapping.get(result, DbIsolationLevel.UNKNOWN)

    except Exception as e:
        logger.error(f"Failed to connect to DB for isolation check: {e}")
        return None


def resolve_database_isolation(db_type: str, fallback: DbIsolationLevel = DbIsolationLevel.READ_COMMITTED) -> DbIsolationLevel:
    """
    Attempts to determine the true isolation level.
    1. Checks if a scan-time URL is provided via env var.
    2. Falls back to the real-world default for the engine if connection fails/is missing.
    """
    db_url = os.environ.get("OMNIGRAPH_SCAN_DB_URL")
    
    if db_url and db_type.lower() == "postgres":
        live_level = fetch_postgres_isolation(db_url)
        if live_level:
            return live_level

    # V2 Policy: Under-suppressing is safer than over-suppressing.
    # If we can't prove it's SERIALIZABLE, assume READ COMMITTED.
    return fallback
