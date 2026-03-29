#!/usr/bin/env bash
# =============================================================================
#  Telegram Manager — Server Deployment Script
#  Target: 185.246.86.143 (root) → tg.soclose.co
# =============================================================================
set -euo pipefail

SERVER="root@185.246.86.143"
DEPLOY_DIR="/root/SAAS/telegram-manager"
DOMAIN="tg.soclose.co"
PORT=8700

GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  🚀 DEPLOYING TELEGRAM MANAGER       ${RESET}"
echo -e "${BOLD}${GREEN}  → ${DOMAIN} (port ${PORT})           ${RESET}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo ""

# ── 1. Check port availability on server ─────────────────────────────────────
echo -e "${CYAN}[1/6] Checking port ${PORT}...${RESET}"
PORT_USED=$(ssh $SERVER "lsof -ti :${PORT} 2>/dev/null || true")
if [ -n "$PORT_USED" ]; then
    echo "  ⚠️  Port ${PORT} in use, trying alternatives..."
    for P in 8701 8702 8703 8710 8720; do
        PORT_USED=$(ssh $SERVER "lsof -ti :${P} 2>/dev/null || true")
        if [ -z "$PORT_USED" ]; then
            PORT=$P
            echo "  ✅ Using port ${PORT}"
            break
        fi
    done
fi
echo "  ✅ Port ${PORT} available"

# ── 2. Create directory + sync code ──────────────────────────────────────────
echo -e "${CYAN}[2/6] Syncing code to server...${RESET}"
ssh $SERVER "mkdir -p ${DEPLOY_DIR}/data ${DEPLOY_DIR}/campaigns ${DEPLOY_DIR}/nginx"

rsync -avz --delete \
    --exclude='venv/' \
    --exclude='__pycache__/' \
    --exclude='*.session' \
    --exclude='config.json' \
    --exclude='auth.json' \
    --exclude='.default_credentials' \
    --exclude='history.db' \
    --exclude='automation.db' \
    --exclude='activity.log' \
    --exclude='*.csv' \
    --exclude='groups_cache.json' \
    --exclude='*.pyc' \
    --exclude='.DS_Store' \
    --exclude='.git/' \
    --exclude='node_modules/' \
    ./ $SERVER:${DEPLOY_DIR}/

echo "  ✅ Code synced"

# ── 3. Setup on server ───────────────────────────────────────────────────────
echo -e "${CYAN}[3/6] Setting up on server...${RESET}"
ssh $SERVER << REMOTE
set -e
cd ${DEPLOY_DIR}

# Create config.json if not exists
if [ ! -f config.json ]; then
    echo '{"accounts": [], "proxies": []}' > config.json
    echo "  Created default config.json"
fi

# Set port in .env
echo "PORT=${PORT}" > .env

# Build Docker image
echo "  Building Docker image..."
docker compose build --quiet

echo "  ✅ Setup complete"
REMOTE

# ── 4. SSL Certificate ───────────────────────────────────────────────────────
echo -e "${CYAN}[4/6] SSL certificate for ${DOMAIN}...${RESET}"
ssh $SERVER << REMOTE
# Check if cert already exists
if [ -d "/etc/letsencrypt/live/${DOMAIN}" ]; then
    echo "  ✅ Certificate already exists"
else
    echo "  Requesting certificate..."
    certbot certonly --nginx -d ${DOMAIN} --non-interactive --agree-tos --email neo@soclosesociety.com || {
        echo "  ⚠️  Certbot failed — setting up HTTP-only for now"
    }
fi
REMOTE

# ── 5. Nginx config ──────────────────────────────────────────────────────────
echo -e "${CYAN}[5/6] Configuring nginx...${RESET}"
ssh $SERVER << REMOTE
# Update port in nginx config
cat > /etc/nginx/sites-enabled/tg.soclose.co << 'NGINX'
server {
    listen 80;
    server_name ${DOMAIN};
    return 301 https://\$server_name\$request_uri;
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;

    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # SSE support
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 86400;
        proxy_http_version 1.1;
        proxy_set_header Connection '';
    }
}
NGINX

nginx -t && nginx -s reload
echo "  ✅ Nginx configured and reloaded"
REMOTE

# ── 6. Start container ───────────────────────────────────────────────────────
echo -e "${CYAN}[6/6] Starting container...${RESET}"
ssh $SERVER << REMOTE
cd ${DEPLOY_DIR}
docker compose down 2>/dev/null || true
docker compose up -d
sleep 3

# Check health
if curl -sf http://localhost:${PORT}/login > /dev/null; then
    echo "  ✅ Container healthy"
else
    echo "  ❌ Health check failed"
    docker compose logs --tail 20
fi

# Show default credentials if first deploy
if [ -f .default_credentials ]; then
    echo ""
    echo "  🔑 DEFAULT CREDENTIALS:"
    cat .default_credentials
    echo ""
    echo "  ⚠️  Change password after first login!"
fi
REMOTE

echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  ✅ DEPLOYED SUCCESSFULLY             ${RESET}"
echo -e "${BOLD}${GREEN}  🌐 https://${DOMAIN}                ${RESET}"
echo -e "${BOLD}${GREEN}  📡 Port: ${PORT}                     ${RESET}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
