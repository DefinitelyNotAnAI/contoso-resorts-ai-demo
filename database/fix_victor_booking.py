import os
import pyodbc
import struct
from dotenv import load_dotenv
from azure.identity import DefaultAzureCredential

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

server = os.getenv("SQL_SERVER", "").split(",")[0]
database = os.getenv("SQL_DATABASE", "")
port = "1433"
if "," in os.getenv("SQL_SERVER", ""):
    port = os.getenv("SQL_SERVER", "").split(",")[1]

print(f"Connecting to: {server}/{database}")

credential = DefaultAzureCredential()
token = credential.get_token("https://database.windows.net/.default")
token_bytes = token.token.encode("utf-16-le")
token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

conn = pyodbc.connect(
    f"DRIVER={{ODBC Driver 18 for SQL Server}};SERVER={server},{port};DATABASE={database}",
    attrs_before={1256: token_struct},
)
cursor = conn.cursor()

# Fix BK-0003003: change from P-003 (Orlando) to P-004 (New York)
cursor.execute(
    "UPDATE Bookings SET PropertyID='P-004', RoomNumber='412', RatePerNight=289.00, TotalAmount=1156.00 WHERE BookingID='BK-0003003'"
)
conn.commit()
print(f"Rows updated: {cursor.rowcount}")

cursor.execute(
    "SELECT BookingID, PropertyID, RoomNumber, RatePerNight, TotalAmount, Status FROM Bookings WHERE BookingID='BK-0003003'"
)
row = cursor.fetchone()
print(f"BK-0003003: PropertyID={row[1]}, Room={row[2]}, Rate={row[3]}, Total={row[4]}, Status={row[5]}")
conn.close()
