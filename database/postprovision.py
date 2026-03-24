"""
postprovision.py — Run schema DDL + seed data against Fabric SQL Database

Executes schema.sql (DDL) and then loads seed data from CSVs using the
same logic as load_data.py. Designed to run as part of the azd postprovision hook.

Auth: DefaultAzureCredential (Azure CLI locally, managed identity deployed).

Usage:
    python database/postprovision.py
    python database/postprovision.py --schema-only
    python database/postprovision.py --seed-only
"""

import argparse
import logging
import os
import struct
import subprocess
import sys

import pyodbc
import requests
from azure.identity import DefaultAzureCredential

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("postprovision")

DRIVER = "ODBC Driver 18 for SQL Server"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------
def _get_connection_params() -> tuple[str, str]:
    """Resolve SQL_SERVER and SQL_DATABASE from env or azd."""
    sql_server = os.getenv("SQL_SERVER", "")
    sql_database = os.getenv("SQL_DATABASE", "")

    if not sql_server:
        try:
            result = subprocess.run(
                ["azd", "env", "get-value", "SQL_SERVER"],
                capture_output=True, text=True, check=True,
            )
            sql_server = result.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    if not sql_database:
        try:
            result = subprocess.run(
                ["azd", "env", "get-value", "SQL_DATABASE"],
                capture_output=True, text=True, check=True,
            )
            sql_database = result.stdout.strip()
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass

    if not sql_server or not sql_database:
        raise RuntimeError(
            "SQL_SERVER and SQL_DATABASE must be set (env vars or azd env). "
            "Run fabric_setup.py first."
        )

    return sql_server, sql_database


def _connect(sql_server: str, sql_database: str) -> pyodbc.Connection:
    """Open a pyodbc connection using Azure AD token auth."""
    log.info("Connecting to %s/%s", sql_server, sql_database)
    cred = DefaultAzureCredential()
    token = cred.get_token("https://database.windows.net/.default")
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn = pyodbc.connect(
        f"Driver={{{DRIVER}}};"
        f"Server={sql_server};"
        f"Database={sql_database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=60;",
        attrs_before={1256: token_struct},
    )
    conn.autocommit = True
    return conn


# ---------------------------------------------------------------------------
# Grant App Service managed identity access (Fabric workspace + SQL user)
# ---------------------------------------------------------------------------
FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"


def _get_azd_value(key: str) -> str:
    """Read a value from azd env, return empty string if not set."""
    try:
        result = subprocess.run(
            ["azd", "env", "get-value", key],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def grant_app_service_access(conn: pyodbc.Connection) -> None:
    """
    Add App Service managed identity to the Fabric workspace (Viewer)
    and create a SQL database user with read/write access.

    Reads APP_SERVICE_NAME, APP_SERVICE_PRINCIPAL_ID, FABRIC_WORKSPACE_ID
    from env or azd env. Skips silently if any value is missing.
    """
    app_name = os.getenv("APP_SERVICE_NAME") or _get_azd_value("APP_SERVICE_NAME")
    principal_id = os.getenv("APP_SERVICE_PRINCIPAL_ID") or _get_azd_value("APP_SERVICE_PRINCIPAL_ID")
    workspace_id = os.getenv("FABRIC_WORKSPACE_ID") or _get_azd_value("FABRIC_WORKSPACE_ID")

    if not app_name or not principal_id or not workspace_id:
        log.info("SKIP grant_app_service_access: APP_SERVICE_NAME/PRINCIPAL_ID/FABRIC_WORKSPACE_ID not set")
        return

    log.info("Granting SQL access to managed identity: %s (%s)", app_name, principal_id)

    # Step 1: Add managed identity as Fabric workspace Viewer via Fabric REST API
    # Required by Fabric SQL before any token-based login can succeed.
    try:
        cred = DefaultAzureCredential()
        fabric_token = cred.get_token(FABRIC_SCOPE).token
        resp = requests.post(
            f"{FABRIC_API}/workspaces/{workspace_id}/roleAssignments",
            headers={"Authorization": f"Bearer {fabric_token}", "Content-Type": "application/json"},
            json={"principal": {"id": principal_id, "type": "ServicePrincipal"}, "role": "Viewer"},
            timeout=30,
        )
        if resp.status_code in (200, 201):
            log.info("Workspace Viewer role assigned to %s", app_name)
        elif resp.status_code == 409:
            log.info("Workspace Viewer role already assigned to %s", app_name)
        else:
            log.warning("Workspace role assignment returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Fabric workspace role assignment skipped: %s", exc)

    # Step 2: Create SQL database user for the managed identity
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = '{app_name}') "
            f"BEGIN "
            f"  CREATE USER [{app_name}] FROM EXTERNAL PROVIDER WITH OBJECT_ID = '{principal_id}'; "
            f"  ALTER ROLE db_datareader ADD MEMBER [{app_name}]; "
            f"  ALTER ROLE db_datawriter ADD MEMBER [{app_name}]; "
            f"END"
        )
        log.info("SQL user created/verified: %s", app_name)
    except Exception as exc:
        log.warning("SQL user grant skipped: %s", exc)


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
def run_schema(conn: pyodbc.Connection) -> None:
    """Execute schema.sql against the database."""
    schema_path = os.path.join(SCRIPT_DIR, "schema.sql")
    log.info("Running schema DDL from %s", schema_path)

    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    # Split on GO statements (standard batch separator)
    # Also split on semicolons for Fabric SQL compatibility
    batches = []
    current_batch = []
    for line in schema_sql.split("\n"):
        stripped = line.strip().upper()
        if stripped == "GO":
            if current_batch:
                batches.append("\n".join(current_batch))
                current_batch = []
        else:
            current_batch.append(line)
    if current_batch:
        batches.append("\n".join(current_batch))

    cursor = conn.cursor()
    for i, batch in enumerate(batches, 1):
        # Strip comment-only lines to check if there's actual SQL
        sql_lines = [
            line for line in batch.split("\n")
            if line.strip() and not line.strip().startswith("--")
        ]
        if not sql_lines:
            continue
        try:
            cursor.execute(batch)
            log.info("Batch %d executed (%d chars)", i, len(batch.strip()))
        except Exception as exc:
            # Log but continue — some statements may fail on Fabric SQL
            log.warning("Batch %d warning: %s", i, exc)

    log.info("Schema DDL complete")


# ---------------------------------------------------------------------------
# Seed data (delegates to load_data.py)
# ---------------------------------------------------------------------------
def run_seed(sql_server: str, sql_database: str) -> None:
    """Run load_data.py --reset to seed all tables."""
    load_script = os.path.join(SCRIPT_DIR, "load_data.py")
    log.info("Running seed data from %s", load_script)

    env = os.environ.copy()
    env["SQL_SERVER"] = sql_server
    env["SQL_DATABASE"] = sql_database

    result = subprocess.run(
        [sys.executable, load_script, "--reset"],
        cwd=SCRIPT_DIR,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"load_data.py failed with exit code {result.returncode}")

    log.info("Seed data complete")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Post-provision: DDL + seed data")
    parser.add_argument("--schema-only", action="store_true", help="Run schema DDL only")
    parser.add_argument("--seed-only", action="store_true", help="Run seed data only")
    args = parser.parse_args()

    sql_server, sql_database = _get_connection_params()
    conn = _connect(sql_server, sql_database)

    try:
        if not args.seed_only:
            run_schema(conn)
        if not args.schema_only:
            run_seed(sql_server, sql_database)
        grant_app_service_access(conn)
    finally:
        conn.close()

    print(f"\n✅ Post-provision complete — {sql_server}/{sql_database}")


if __name__ == "__main__":
    main()
