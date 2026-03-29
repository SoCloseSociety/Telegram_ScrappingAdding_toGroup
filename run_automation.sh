#!/usr/bin/env bash
# =============================================================================
#  Telegram Manager — Automation Engine
# =============================================================================
VENV_DIR="venv"
cd "$(dirname "$0")"

if [ ! -d "$VENV_DIR" ]; then
    echo "❌ Environnement virtuel introuvable. Lancez ./install.sh"
    exit 1
fi

source "$VENV_DIR/bin/activate"

echo ""
echo -e "\033[1;32m═══════════════════════════════════════\033[0m"
echo -e "\033[1;32m  🤖 AUTOMATION ENGINE                 \033[0m"
echo -e "\033[1;32m═══════════════════════════════════════\033[0m"
echo ""

python automation.py "$@"
