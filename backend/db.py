"""
db.py — SQL Database connection helper (Fabric SQL / Azure SQL)

Provides a pyodbc connection authenticated via Azure AD (DefaultAzureCredential).
No passwords, no connection strings with secrets — SFI compliant.

Works identically against Fabric SQL Database and Azure SQL Database.
Only the SQL_SERVER hostname differs between the two:
  - Fabric:    <unique-id>.database.fabric.microsoft.com,1433
  - Azure SQL: <server>.database.windows.net

Usage:
    from db import execute_query
    rows = await execute_query("SELECT TOP 5 * FROM dbo.Guests")
"""

import asyncio
import logging
import os
import struct
import threading
from typing import Any

import pyodbc
from azure.identity import DefaultAzureCredential

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (from environment / .env)
# ---------------------------------------------------------------------------
SQL_SERVER   = os.getenv("SQL_SERVER",   "")
SQL_DATABASE = os.getenv("SQL_DATABASE", "")
DRIVER       = "ODBC Driver 18 for SQL Server"

# Thread-local storage for connections (one connection per thread)
_local = threading.local()


# ---------------------------------------------------------------------------
# Azure AD token helper
# ---------------------------------------------------------------------------
def _get_token_struct() -> bytes:
    """Acquire an Azure AD access token for Azure SQL and pack it for pyodbc."""
    cred  = DefaultAzureCredential()
    token = cred.get_token("https://database.windows.net/.default")
    tb    = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(tb)}s", len(tb), tb)


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------
def _open_connection() -> pyodbc.Connection:
    """Open a new pyodbc connection using Azure AD token auth."""
    log.info("Opening SQL connection to %s/%s", SQL_SERVER, SQL_DATABASE)
    ts = _get_token_struct()
    conn = pyodbc.connect(
        f"Driver={{{DRIVER}}};"
        f"Server={SQL_SERVER};"
        f"Database={SQL_DATABASE};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=60;",
        attrs_before={1256: ts},
    )
    conn.autocommit = True   # read-only queries, autocommit is fine
    return conn


def _get_connection() -> pyodbc.Connection:
    """Return a thread-local connection, (re)opening if necessary."""
    conn = getattr(_local, "conn", None)
    try:
        if conn is not None:
            # Quick liveness check
            conn.cursor().execute("SELECT 1")
        else:
            conn = _open_connection()
            _local.conn = conn
    except Exception:
        log.warning("Connection lost — reconnecting (thread=%s)", threading.current_thread().name)
        conn = _open_connection()
        _local.conn = conn
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _execute_sync(sql: str) -> list[dict[str, Any]]:
    """Execute a SQL query synchronously and return rows as dicts."""
    conn   = _get_connection()
    cursor = conn.cursor()
    cursor.execute(sql)
    if cursor.description is None:
        return []
    columns = [col[0] for col in cursor.description]
    rows    = cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


def _execute_params_sync(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Execute a parameterized SQL query synchronously and return rows as dicts."""
    conn   = _get_connection()
    cursor = conn.cursor()
    cursor.execute(sql, params)
    if cursor.description is None:
        return []
    columns = [col[0] for col in cursor.description]
    rows    = cursor.fetchall()
    return [dict(zip(columns, row)) for row in rows]


async def execute_query(sql: str) -> list[dict[str, Any]]:
    """Execute a SQL query asynchronously (runs pyodbc in a thread)."""
    return await asyncio.to_thread(_execute_sync, sql)


async def execute_query_params(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Execute a parameterized SQL query asynchronously (runs pyodbc in a thread)."""
    return await asyncio.to_thread(_execute_params_sync, sql, params)


def check_health() -> dict[str, str]:
    """Validate database connectivity. Returns status dict."""
    try:
        rows = _execute_sync("SELECT DB_NAME() AS db")
        db   = rows[0]["db"] if rows else SQL_DATABASE
        return {"status": "healthy", "database": db, "server": SQL_SERVER}
    except Exception as exc:
        log.error("Health check failed: %s", exc)
        return {"status": "unhealthy", "error": str(exc), "server": SQL_SERVER}
