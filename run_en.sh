#!/usr/bin/env bash
# =============================================================================
#  Telegram Manager — Launch (English version)
# =============================================================================

VENV_DIR="venv"
SCRIPT="new_en.py"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Go to script directory ────────────────────────────────────────────────────
cd "$(dirname "$0")"

# ── Check venv ────────────────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}❌ Virtual environment '$VENV_DIR' not found.${RESET}"
    echo -e "   Run first: ${BOLD}./install.sh${RESET}"
    exit 1
fi

# ── Check main script ─────────────────────────────────────────────────────────
if [ ! -f "$SCRIPT" ]; then
    echo -e "${RED}❌ File $SCRIPT not found.${RESET}"
    exit 1
fi

# ── Activate venv ─────────────────────────────────────────────────────────────
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}======================================${RESET}"
echo -e "${BOLD}${GREEN}     🤖  TELEGRAM MANAGER  🤖         ${RESET}"
echo -e "${BOLD}${GREEN}======================================${RESET}"
echo -e "  Python : $(python --version 2>&1)"
echo -e "  Script : $SCRIPT"
echo -e "${BOLD}${GREEN}======================================${RESET}"
echo ""

# ── Launch ────────────────────────────────────────────────────────────────────
python "$SCRIPT"
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}  ✅  Program exited normally.${RESET}"
else
    echo -e "${RED}  ❌  Program exited with code $EXIT_CODE.${RESET}"
fi
echo ""
exit $EXIT_CODE
