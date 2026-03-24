#!/bin/bash
# startup.sh — App Service startup script
# Installs ODBC Driver 18 (auto-detects Ubuntu vs Debian) and launches uvicorn

echo "=== Installing ODBC Driver 18 for SQL Server ==="

if dpkg -l | grep -q msodbcsql18; then
    echo "ODBC Driver 18 already installed — skipping"
else
    # Detect distro: Ubuntu (jammy/focal) vs Debian (bookworm/bullseye)
    DISTRO_ID=$(grep '^ID=' /etc/os-release | cut -d= -f2 | tr -d '"')
    DISTRO_VERSION=$(grep '^VERSION_CODENAME=' /etc/os-release | cut -d= -f2 | tr -d '"')
    echo "Detected distro: $DISTRO_ID $DISTRO_VERSION"

    curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg

    if [ "$DISTRO_ID" = "ubuntu" ]; then
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/ubuntu/22.04/prod jammy main" > /etc/apt/sources.list.d/mssql-release.list
    else
        # Debian fallback (bookworm / bullseye)
        echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" > /etc/apt/sources.list.d/mssql-release.list
    fi

    apt-get update -qq
    ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
    echo "ODBC Driver 18 installed"
fi

echo "=== Starting Contoso Resorts AI backend ==="
cd /home/site/wwwroot/backend
exec python -m uvicorn api:app --host 0.0.0.0 --port 8000
