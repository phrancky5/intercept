# CAT Bridge startup script for Windows
# ========================================
# Run this on the Windows host where the radio's USB-serial cable is plugged in.
# The Docker container connects to this bridge over the network.
#
# Environment variables set HERE (Windows host):
#   CAT_BRIDGE_TOKEN  - Shared secret (MUST match the Docker container's CAT_BRIDGE_TOKEN)
#   ALLOWED_IPS       - Which IPs can connect (loopback + Docker networks + your LAN)
#   CAT_BRIDGE_HOST   - Bind address (0.0.0.0 = all interfaces)
#   CAT_BRIDGE_PORT   - HTTP API port (default 5060)
#   RIGCTLD_ENABLE    - "1" to enable rigctld relay for WSJT-X/Fldigi (default "1")
#   RIGCTLD_PORT      - rigctld TCP port (default 4532)
#   BRIDGE_TX_LOCK    - "1" to block PTT from rigctld clients (default "1")
#
# The Docker container needs DIFFERENT variables (set in docker-compose.yml):
#   CAT_BRIDGE_URL    - http://host.docker.internal:5060
#   CAT_BRIDGE_TOKEN  - Same value as here

# === REQUIRED: Shared authentication token ===
# This MUST match the CAT_BRIDGE_TOKEN in docker-compose.yml
$env:CAT_BRIDGE_TOKEN = "change-me"

# === IP Allowlist ===
# Who can connect to the bridge? Comma-separated IP addresses or CIDR ranges.
# Include:
#   127.0.0.1/8     - localhost (Windows)
#   ::1             - localhost (IPv6)
#   172.16.0.0/12   - Docker default bridge networks (172.16.x.x - 172.31.x.x)
#   192.168.1.0/24  - Your LAN (adjust to your network)
$env:ALLOWED_IPS = "127.0.0.1/8,::1,172.16.0.0/12,192.168.1.0/24"

# === Optional: Customize ports ===
# $env:CAT_BRIDGE_HOST = "0.0.0.0"    # Bind to all interfaces (default)
# $env:CAT_BRIDGE_PORT = "5060"       # HTTP API port (default)
# $env:RIGCTLD_PORT = "4532"          # rigctld TCP port for WSJT-X/Fldigi (default)

# === Optional: rigctld settings ===
# $env:RIGCTLD_ENABLE = "1"           # "0" to disable rigctld relay
# $env:BRIDGE_TX_LOCK = "1"           # "0" to allow PTT from rigctld clients
# $env:RIGCTLD_DEBUG = "1"            # "1" for verbose rigctld logging

# === Enable verbose bridge debugging ===
$env:CAT_BRIDGE_DEBUG = "1"

Write-Host "=============================================="
Write-Host "CAT Bridge starting on Windows host"
Write-Host "=============================================="
Write-Host "Token: $($env:CAT_BRIDGE_TOKEN.Substring(0,4))..." -ForegroundColor Cyan
Write-Host "Allowed IPs: $env:ALLOWED_IPS" -ForegroundColor Cyan
Write-Host ""
Write-Host "Docker container should have:" -ForegroundColor Yellow
Write-Host "  CAT_BRIDGE_URL=http://host.docker.internal:5060"
Write-Host "  CAT_BRIDGE_TOKEN=$env:CAT_BRIDGE_TOKEN"
Write-Host "=============================================="
Write-Host ""

python cat_bridge.py
