#!/bin/bash
# One-time Cloudflare Tunnel setup for the voice calendar scheduler.
# Creates a named tunnel and optionally routes a DNS subdomain to it.
# Run once, then use ./scripts/run.sh --tunnel to start with the tunnel.
set -e

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG_FILE="$PROJECT_ROOT/.tunnel-config"

# ── Check prerequisites ──────────────────────────────────────────────
if ! command -v cloudflared &>/dev/null; then
    echo "cloudflared not found."
    echo "  brew install cloudflare/cloudflare/cloudflared"
    exit 1
fi

if [ -f "$CONFIG_FILE" ]; then
    echo "Tunnel already configured: $CONFIG_FILE"
    cat "$CONFIG_FILE"
    echo ""
    read -rp "Reconfigure? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 0
fi

# ── Authenticate (skip if cert already exists) ────────────────────────
CERT="$HOME/.cloudflared/cert.pem"
if [ -f "$CERT" ]; then
    echo "Cloudflare auth found ($CERT) — skipping login."
else
    echo "Opening browser to authenticate with Cloudflare..."
    cloudflared tunnel login
    if [ ! -f "$CERT" ]; then
        echo "Authentication failed — cert.pem not created."
        exit 1
    fi
fi

# ── Create tunnel ─────────────────────────────────────────────────────
read -rp "Tunnel name [voice-scheduler]: " TUNNEL_NAME
TUNNEL_NAME="${TUNNEL_NAME:-voice-scheduler}"

# Validate tunnel name (cloudflared only allows these characters)
if [[ "$TUNNEL_NAME" =~ [^a-zA-Z0-9_-] ]]; then
    echo "Tunnel name must contain only letters, numbers, hyphens, underscores."
    exit 1
fi

# Check if tunnel already exists (env var avoids shell injection in -c string)
EXISTING_ID=$(TUNNEL_TARGET="$TUNNEL_NAME" cloudflared tunnel list --output json 2>/dev/null \
    | python3 -c "
import sys, json, os
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)
tunnels = json.loads(raw)
target = os.environ['TUNNEL_TARGET']
for t in tunnels:
    if t['name'] == target:
        print(t['id'])
        break
" 2>/dev/null || true)

if [ -n "$EXISTING_ID" ]; then
    echo "Tunnel '$TUNNEL_NAME' already exists (ID: $EXISTING_ID)."
    TUNNEL_ID="$EXISTING_ID"
else
    echo "Creating tunnel '$TUNNEL_NAME'..."
    TUNNEL_ID=$(cloudflared tunnel create "$TUNNEL_NAME" 2>&1 \
        | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' \
        | head -1)
    if [ -z "$TUNNEL_ID" ]; then
        echo "Failed to create tunnel."
        exit 1
    fi
    echo "Created tunnel: $TUNNEL_ID"
fi

# ── Optional DNS routing ─────────────────────────────────────────────
echo ""
echo "Optional: route a subdomain to this tunnel."
echo "  Example: scheduler.chattychapters.com"
echo "  (Leave blank to skip — you can add DNS later.)"
read -rp "Subdomain (full hostname): " SUBDOMAIN

TUNNEL_URL=""
if [ -n "$SUBDOMAIN" ]; then
    echo "Routing $SUBDOMAIN → tunnel $TUNNEL_NAME..."
    cloudflared tunnel route dns "$TUNNEL_NAME" "$SUBDOMAIN" 2>&1 || true
    TUNNEL_URL="https://$SUBDOMAIN"
    echo "DNS configured: $TUNNEL_URL"
fi

# ── Save config ───────────────────────────────────────────────────────
cat > "$CONFIG_FILE" <<EOF
# Cloudflare Tunnel config — machine-specific, do not commit
TUNNEL_NAME='$TUNNEL_NAME'
TUNNEL_ID='$TUNNEL_ID'
TUNNEL_URL='$TUNNEL_URL'
EOF

echo ""
echo "Saved to $CONFIG_FILE"
echo ""
echo "Start the server with tunnel:"
echo "  ./scripts/run.sh --tunnel"
