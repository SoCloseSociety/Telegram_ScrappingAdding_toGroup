#!/usr/bin/env bash
# =============================================================================
#  Telegram Manager — Dashboard (FastAPI + HTMX)
# =============================================================================

VENV_DIR="venv"
PORT="${1:-8000}"

RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

cd "$(dirname "$0")"

# ── Check venv ────────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}❌ Environnement virtuel '$VENV_DIR' introuvable.${RESET}"
    echo -e "   Lancez d'abord : ${BOLD}./install.sh${RESET}"
    exit 1
fi

source "$VENV_DIR/bin/activate"

# ── Install dashboard deps if missing ─────────────────────────────────────────
python -c "import fastapi, uvicorn, jinja2" 2>/dev/null || {
    echo -e "${GREEN}  Installation des dépendances dashboard…${RESET}"
    pip install fastapi "uvicorn[standard]" jinja2 python-multipart -q
}

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}======================================${RESET}"
echo -e "${BOLD}${GREEN}   🌐  TELEGRAM MANAGER DASHBOARD     ${RESET}"
echo -e "${BOLD}${GREEN}======================================${RESET}"
echo -e "  URL    : ${BOLD}http://localhost:${PORT}${RESET}"
echo -e "  Stop   : Ctrl+C"
echo -e "${BOLD}${GREEN}======================================${RESET}"
echo ""

# ── Launch ────────────────────────────────────────────────────────────────────
# Open browser after a short delay
(sleep 1.5 && open "http://localhost:${PORT}") &

uvicorn dashboard.app:app --host 0.0.0.0 --port "$PORT" --reload
