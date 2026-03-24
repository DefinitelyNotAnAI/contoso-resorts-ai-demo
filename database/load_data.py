#!/usr/bin/env python3
"""
load_data.py — Bulk-load Contoso Resorts seed data into Azure SQL Database

Merges bulk-generated CSVs (from generate_data.py) with hand-authored persona
seed files, then inserts into Azure SQL Database via pyodbc using Azure AD
token authentication (DefaultAzureCredential — no passwords, no connection strings
with secrets).

Prerequisites:
  pip install pyodbc azure-identity python-dotenv
  ODBC Driver 18 for SQL Server must be installed.
  Run 'az login' locally, or deploy with managed identity.

Usage:
  python database/load_data.py              # load (skip existing rows)
  python database/load_data.py --reset      # truncate tables then reload
  python database/load_data.py --dry-run    # validate CSVs, no DB writes

Environment (from .env or shell):
  SQL_SERVER    e.g. <workspace-id>.datawarehouse.fabric.microsoft.com or <server>.database.windows.net
  SQL_DATABASE  e.g. ContosoResortsDemo
"""

import argparse
import csv
import os
import struct
import sys
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env if present (no hard dependency on python-dotenv)
# ---------------------------------------------------------------------------
def _load_dotenv() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Table definitions: load order respects FK constraints
# ---------------------------------------------------------------------------
SEED_DIR = Path(__file__).parent / "seed"

# (table_name, [bulk_csv, ...persona_csvs...], [column_names...])
# Properties are seeded by schema.sql — not loaded here.
TABLES: list[tuple[str, list[str], list[str]]] = [
    (
        "dbo.Guests",
        ["guests.csv", "persona_dana_guest.csv", "persona_anne_guest.csv", "persona_victor_guest.csv"],
        ["GuestID", "FirstName", "LastName", "Email", "Phone",
         "HomeCity", "Country", "LoyaltyTier", "LoyaltyPoints", "MemberSince", "Preferences"],
    ),
    (
        "dbo.Experiences",
        ["experiences.csv"],
        ["ExperienceID", "PropertyID", "Name", "Category", "Description", "Price", "Duration", "Available"],
    ),
    (
        "dbo.Bookings",
        ["bookings.csv", "persona_dana_bookings.csv", "persona_anne_bookings.csv", "persona_victor_bookings.csv"],
        ["BookingID", "GuestID", "PropertyID", "CheckIn", "CheckOut",
         "RoomType", "RoomNumber", "RatePerNight", "TotalAmount", "Status", "SpecialRequests", "BookedDate"],
    ),
    (
        "dbo.Inventory",
        ["inventory.csv"],
        ["PropertyID", "Date", "RoomType", "TotalRooms", "BookedRooms", "Available"],
    ),
    (
        "dbo.Surveys",
        ["surveys.csv", "persona_dana_surveys.csv", "persona_anne_surveys.csv", "persona_victor_surveys.csv"],
        ["SurveyID", "GuestID", "BookingID", "PropertyID",
         "OverallRating", "NPS", "Cleanliness", "Service", "FoodBeverage",
         "Spa", "Activities", "FreeText", "SubmittedDate"],
    ),
    (
        "dbo.ServiceRequests",
        ["service_requests.csv",
         "persona_dana_service_requests.csv",
         "persona_anne_service_requests.csv",
         "persona_victor_service_requests.csv"],
        ["RequestID", "GuestID", "BookingID", "PropertyID",
         "RequestedDate", "Department", "Category", "Description",
         "Priority", "Status", "AssignedTo", "CompletedDate",
         "ResponseMinutes", "ResolutionNotes", "GuestSatisfied"],
    ),
]

# Reverse order for truncation (child tables first)
TRUNCATE_ORDER = ["dbo.ServiceRequests", "dbo.Surveys", "dbo.Inventory", "dbo.Bookings", "dbo.Experiences", "dbo.Guests"]

BATCH_SIZE = 500  # rows per executemany call


# ---------------------------------------------------------------------------
# Read and merge CSVs for a table
# ---------------------------------------------------------------------------
def load_csv_rows(csv_files: list[str]) -> list[dict[str, Any]]:
    rows: list[dict] = []
    for filename in csv_files:
        path = SEED_DIR / filename
        if not path.exists():
            log.warning(f"  Seed file not found (skipping): {path.name}")
            continue
        with open(path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            file_rows = list(reader)
            log.info(f"  Read {len(file_rows):>6,} rows from {filename}")
            rows.extend(file_rows)
    return rows


def coerce_row(row: dict, columns: list[str]) -> tuple:
    """Return a tuple of values in column order, None for empty strings.

    Type coercions applied so pyodbc fast_executemany works correctly:
    - Empty string → None
    - ISO datetime strings with 'T' separator → datetime objects (DATETIME2)
    - Pure integer strings → int  (handles BIT "0"/"1", INT columns)
    - Float strings → float
    - Everything else → string
    """
    values = []
    for col in columns:
        val = row.get(col, "")
        if val == "" or val is None:
            values.append(None)
        elif isinstance(val, str) and "T" in val:
            # ISO 8601 datetime: "2023-07-09T19:45:00" → datetime object
            try:
                values.append(datetime.fromisoformat(val))
            except ValueError:
                values.append(val)
        else:
            # Try numeric coercion for BIT/INT/FLOAT columns
            try:
                if "." in str(val):
                    values.append(float(val))
                else:
                    values.append(int(val))
            except (ValueError, TypeError):
                values.append(val)
    return tuple(values)


# ---------------------------------------------------------------------------
# Azure AD token auth for Azure SQL Database (no password)
# ---------------------------------------------------------------------------
def get_token_bytes() -> bytes:
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError:
        log.error("azure-identity not installed. Run: pip install azure-identity")
        sys.exit(1)

    log.info("Acquiring Azure AD token via DefaultAzureCredential...")
    credential = DefaultAzureCredential()
    token = credential.get_token("https://database.windows.net/.default").token

    # Encode token as required by ODBC Driver 18 (UTF-16-LE with null bytes)
    token_bytes = token.encode("utf-16-le")
    return struct.pack("<I", len(token_bytes)) + token_bytes


def get_connection(server: str, database: str):
    try:
        import pyodbc
    except ImportError:
        log.error("pyodbc not installed. Run: pip install pyodbc")
        sys.exit(1)

    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER={server};"
        f"DATABASE={database};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )

    token_bytes = get_token_bytes()
    SQL_COPT_SS_ACCESS_TOKEN = 1256

    log.info(f"Connecting to {server} / {database}...")
    conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_bytes})
    conn.autocommit = False
    return conn


# ---------------------------------------------------------------------------
# Core operations
# ---------------------------------------------------------------------------
def truncate_tables(conn, dry_run: bool) -> None:
    log.info("Truncating tables (reverse FK order)...")
    cursor = conn.cursor()
    for table in TRUNCATE_ORDER:
        if dry_run:
            log.info(f"  [dry-run] TRUNCATE TABLE {table}")
            continue
        log.info(f"  Truncating {table}...")
        cursor.execute(f"DELETE FROM {table}")  # DELETE instead of TRUNCATE to respect FKs
    if not dry_run:
        conn.commit()
    cursor.close()


def load_table(
    conn,
    table: str,
    csv_files: list[str],
    columns: list[str],
    dry_run: bool,
) -> int:
    rows = load_csv_rows(csv_files)
    if not rows:
        log.warning(f"  No rows to load for {table}")
        return 0

    placeholders = ", ".join(["?"] * len(columns))
    col_list = ", ".join(f"[{c}]" for c in columns)
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"

    if dry_run:
        log.info(f"  [dry-run] Would INSERT {len(rows):,} rows into {table}")
        # Validate all rows can be coerced without error
        for row in rows:
            coerce_row(row, columns)
        return len(rows)

    cursor = conn.cursor()
    cursor.fast_executemany = True  # bulk-upload path (avoids row-by-row round trips)
    inserted = 0
    try:
        for i in range(0, len(rows), BATCH_SIZE):
            batch = [coerce_row(r, columns) for r in rows[i : i + BATCH_SIZE]]
            cursor.executemany(sql, batch)
            conn.commit()
            inserted += len(batch)
        log.info(f"  Inserted {inserted:,} rows into {table}")
    except Exception as e:
        conn.rollback()
        log.error(f"  FAILED loading {table}: {e}")
        raise
    finally:
        cursor.close()

    return inserted


def verify_counts(conn) -> None:
    import pyodbc
    log.info("\nRow count verification:")
    cursor = conn.cursor()
    for table, _, _ in TABLES:
        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        count = cursor.fetchone()[0]
        log.info(f"  {table:<25} {count:>7,} rows")
    cursor.execute("SELECT COUNT(*) FROM dbo.Properties")
    count = cursor.fetchone()[0]
    log.info(f"  {'dbo.Properties':<25} {count:>7,} rows")
    cursor.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="Load Contoso Resorts seed data into Azure SQL Database")
    parser.add_argument("--reset",   action="store_true", help="Truncate all tables before loading")
    parser.add_argument("--dry-run", action="store_true", help="Validate CSVs without writing to DB")
    args = parser.parse_args()

    _load_dotenv()

    server   = os.environ.get("SQL_SERVER", "")
    database = os.environ.get("SQL_DATABASE", "")

    log.info("=" * 60)
    log.info("Contoso Resorts — Azure SQL Database Loader")
    log.info(f"  Server  : {server or '(not set)'}")
    log.info(f"  Database: {database or '(not set)'}")
    log.info(f"  Mode    : {'DRY RUN' if args.dry_run else 'RESET + LOAD' if args.reset else 'LOAD'}")
    log.info("=" * 60)

    if args.dry_run:
        log.info("\nDry-run mode: validating CSVs only (no DB connection)\n")
        total = 0
        for table, csv_files, columns in TABLES:
            log.info(f"\n{table}")
            total += load_table(None, table, csv_files, columns, dry_run=True)
        log.info(f"\nTotal rows validated: {total:,}")
        log.info("Dry run complete — no data written.")
        return

    if not server or not database:
        log.error(
            "SQL_SERVER and SQL_DATABASE must be set.\n"
            "  Copy .env.example to .env and fill in your Azure SQL values."
        )
        sys.exit(1)

    conn = get_connection(server, database)
    try:
        if args.reset:
            truncate_tables(conn, dry_run=False)

        total = 0
        for table, csv_files, columns in TABLES:
            log.info(f"\n{table}")
            total += load_table(conn, table, csv_files, columns, dry_run=False)

        log.info(f"\nTotal rows inserted: {total:,}")
        verify_counts(conn)
        log.info("\nLoad complete.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
