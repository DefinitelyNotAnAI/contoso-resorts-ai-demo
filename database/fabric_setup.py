"""
fabric_setup.py — Provision Fabric workspace + SQL Database via Fabric REST API

Creates a Fabric workspace (assigned to the F64 capacity) and a SQL Database
within it. Outputs the SQL_SERVER and SQL_DATABASE values for pyodbc connection.

Auth: DefaultAzureCredential (Azure CLI locally, managed identity deployed).

Usage:
    python database/fabric_setup.py --capacity-name <NAME> --location <REGION>
    python database/fabric_setup.py --help
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from typing import Any

import requests
from azure.identity import DefaultAzureCredential

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fabric_setup")

# ---------------------------------------------------------------------------
# Fabric REST API base
# ---------------------------------------------------------------------------
FABRIC_API = "https://api.fabric.microsoft.com/v1"
FABRIC_SCOPE = "https://api.fabric.microsoft.com/.default"

WORKSPACE_NAME = os.getenv("FABRIC_WORKSPACE_NAME", "contoso-resorts-ai")
DATABASE_NAME = os.getenv("FABRIC_DATABASE_NAME", "ContosoResortsDemo")


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------
def _get_token() -> str:
    """Acquire a Fabric API access token via DefaultAzureCredential."""
    cred = DefaultAzureCredential()
    token = cred.get_token(FABRIC_SCOPE)
    return token.token


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Fabric REST helpers
# ---------------------------------------------------------------------------
def _get_capacity_id(capacity_name: str) -> str:
    """Look up the Fabric capacity GUID by name via Fabric REST API."""
    resp = requests.get(f"{FABRIC_API}/capacities", headers=_headers())
    resp.raise_for_status()
    for cap in resp.json().get("value", []):
        if cap.get("displayName") == capacity_name:
            cap_id = cap["id"]
            log.info("Capacity ID: %s (state: %s)", cap_id, cap.get("state"))
            if cap.get("state") != "Active":
                raise RuntimeError(
                    f"Capacity '{capacity_name}' is {cap.get('state')}. "
                    "Resume it in the Azure portal before running setup."
                )
            return cap_id
    raise RuntimeError(f"Capacity '{capacity_name}' not found via Fabric API")


def list_workspaces() -> list[dict[str, Any]]:
    """List all Fabric workspaces accessible to the current user."""
    resp = requests.get(f"{FABRIC_API}/workspaces", headers=_headers())
    resp.raise_for_status()
    return resp.json().get("value", [])


def find_workspace(name: str) -> dict[str, Any] | None:
    """Find a workspace by display name."""
    for ws in list_workspaces():
        if ws.get("displayName") == name:
            return ws
    return None


def create_workspace(name: str, capacity_id: str) -> dict[str, Any]:
    """Create a Fabric workspace assigned to a capacity."""
    log.info("Creating workspace '%s' on capacity %s", name, capacity_id)
    resp = requests.post(
        f"{FABRIC_API}/workspaces",
        headers=_headers(),
        json={
            "displayName": name,
            "capacityId": capacity_id,
            "description": "Contoso Resorts AI demo — Fabric SQL Database + Data Agent",
        },
    )
    if resp.status_code == 409:
        log.info("Workspace '%s' already exists", name)
        ws = find_workspace(name)
        if ws:
            return ws
    resp.raise_for_status()
    return resp.json()


def list_items(workspace_id: str, item_type: str = "SQLDatabase") -> list[dict[str, Any]]:
    """List items of a given type in a workspace."""
    resp = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}/items",
        headers=_headers(),
        params={"type": item_type},
    )
    resp.raise_for_status()
    return resp.json().get("value", [])


def create_sql_database(workspace_id: str, name: str) -> dict[str, Any]:
    """Create a SQL Database in a Fabric workspace."""
    log.info("Creating SQL Database '%s' in workspace %s", name, workspace_id)
    resp = requests.post(
        f"{FABRIC_API}/workspaces/{workspace_id}/items",
        headers=_headers(),
        json={
            "displayName": name,
            "type": "SQLDatabase",
            "description": "Contoso Resorts demo database — 7 tables, 3 personas",
        },
    )
    if resp.status_code == 409:
        log.info("SQL Database '%s' already exists", name)
        for item in list_items(workspace_id, "SQLDatabase"):
            if item.get("displayName") == name:
                return item
    resp.raise_for_status()

    # For long-running operations, check the Location header
    if resp.status_code == 202:
        location = resp.headers.get("Location")
        if location:
            return _poll_operation(location)

    return resp.json()


def _poll_operation(url: str, timeout: int = 300) -> dict[str, Any]:
    """Poll a long-running Fabric operation until complete."""
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(url, headers=_headers())
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "")
            if status in ("Succeeded", "succeeded"):
                log.info("Operation completed")
                return data
            if status in ("Failed", "failed"):
                raise RuntimeError(f"Operation failed: {data}")
        time.sleep(5)
    raise TimeoutError(f"Operation timed out after {timeout}s")


def get_sql_connection_string(workspace_id: str, database_name: str) -> tuple[str, str]:
    """
    Get the actual SQL connection parameters from the Fabric REST API.

    Returns (sql_server, sql_database) tuple.
    Fabric SQL Database has its own unique server FQDN and database name
    (different from the workspace-based datawarehouse endpoint).
    """
    # Find the SQL Database item by name
    items = list_items(workspace_id, "SQLDatabase")
    db_item = None
    for item in items:
        if item.get("displayName") == database_name:
            db_item = item
            break

    if not db_item:
        raise RuntimeError(f"SQL Database '{database_name}' not found in workspace {workspace_id}")

    # Get detailed properties including connection info
    item_id = db_item["id"]
    resp = requests.get(
        f"{FABRIC_API}/workspaces/{workspace_id}/sqlDatabases/{item_id}",
        headers=_headers(),
    )
    resp.raise_for_status()
    props = resp.json().get("properties", {})

    sql_server = props.get("serverFqdn", "")
    sql_database = props.get("databaseName", "")

    if not sql_server or not sql_database:
        raise RuntimeError(
            f"Could not retrieve connection info for '{database_name}'. "
            f"Properties: {props}"
        )

    return sql_server, sql_database


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Provision Fabric workspace + SQL Database")
    parser.add_argument("--capacity-name", required=True, help="Fabric capacity resource name")
    parser.add_argument("--location", default="eastus2", help="Azure region")
    parser.add_argument("--workspace-name", default=WORKSPACE_NAME, help="Fabric workspace name")
    parser.add_argument("--database-name", default=DATABASE_NAME, help="SQL Database name")
    args = parser.parse_args()

    # 1. Look up capacity ID from ARM
    log.info("Looking up Fabric capacity '%s'...", args.capacity_name)
    capacity_id = _get_capacity_id(args.capacity_name)
    log.info("Capacity ID: %s", capacity_id)

    # 2. Create or find workspace
    ws = find_workspace(args.workspace_name)
    if ws:
        log.info("Workspace '%s' already exists (id=%s)", args.workspace_name, ws["id"])
    else:
        ws = create_workspace(args.workspace_name, capacity_id)
    workspace_id = ws["id"]
    log.info("Workspace ID: %s", workspace_id)

    # 3. Create or find SQL Database
    existing = [
        item for item in list_items(workspace_id, "SQLDatabase")
        if item.get("displayName") == args.database_name
    ]
    if existing:
        log.info("SQL Database '%s' already exists", args.database_name)
    else:
        create_sql_database(workspace_id, args.database_name)

    # 4. Output connection info
    sql_server, sql_database = get_sql_connection_string(workspace_id, args.database_name)
    log.info("SQL_SERVER  = %s", sql_server)
    log.info("SQL_DATABASE = %s", sql_database)

    # 5. Save to azd env (if available)
    try:
        subprocess.run(["azd", "env", "set", "SQL_SERVER", sql_server], check=True)
        subprocess.run(["azd", "env", "set", "SQL_DATABASE", sql_database], check=True)
        subprocess.run(["azd", "env", "set", "FABRIC_WORKSPACE_ID", workspace_id], check=True)
        log.info("Saved SQL_SERVER, SQL_DATABASE, FABRIC_WORKSPACE_ID to azd env")
    except (FileNotFoundError, subprocess.CalledProcessError):
        log.warning("azd not available — set SQL_SERVER and SQL_DATABASE manually")

    print(f"\n✅ Fabric setup complete")
    print(f"   Workspace: {args.workspace_name} ({workspace_id})")
    print(f"   Database:  {args.database_name}")
    print(f"   Server:    {sql_server}")


if __name__ == "__main__":
    main()
