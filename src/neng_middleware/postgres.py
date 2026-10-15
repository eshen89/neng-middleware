"""Explicit transaction-local schema selection, safe with Supavisor pooling."""
import hashlib
import os
import re


def schema_name() -> str:
    schema = os.getenv("NENG_DB_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
        raise ValueError("NENG_DB_SCHEMA must be a simple lowercase PostgreSQL identifier")
    if os.getenv("NENG_DEPLOYMENT_MODE") == "gateway" and os.getenv("NENG_ENV") == "dev" and schema != "neng_dev":
        raise ValueError("Hosted dev must use the isolated neng_dev schema")
    return schema


def configure_schema(connection) -> None:
    # No public fallback: an absent dev table must fail, never select a production table.
    connection.execute("SELECT set_config('search_path', %s, true)", (schema_name(),))


def migration_lock(service: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{service}:{schema_name()}".encode()).digest()[:8],
                          "big", signed=True)
