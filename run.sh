#!/usr/bin/env bash
# =============================================================================
#  Telegram Manager — Lancement (version française)
# =============================================================================

VENV_DIR="venv"
SCRIPT="new.py"

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Aller dans le répertoire du script ────────────────────────────────────────
cd "$(dirname "$0")"

# ── Vérification du venv ──────────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}❌ Environnement virtuel '$VENV_DIR' introuvable.${RESET}"
    echo -e "   Lancez d'abord : ${BOLD}./install.sh${RESET}"
    exit 1
fi

# ── Vérification du script principal ─────────────────────────────────────────
if [ ! -f "$SCRIPT" ]; then
    echo -e "${RED}❌ Fichier $SCRIPT introuvable.${RESET}"
    exit 1
fi

# ── Activation du venv ────────────────────────────────────────────────────────
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ── Bannière ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}     🤖  TELEGRAM MANAGER  🤖         ${RESET}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo -e "  Python : $(python --version 2>&1)"
echo -e "  Script : $SCRIPT"
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo ""

# ── Lancement ─────────────────────────────────────────────────────────────────
python "$SCRIPT"
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}  ✅  Programme terminé normalement.${RESET}"
else
    echo -e "${RED}  ❌  Le programme s'est terminé avec le code $EXIT_CODE.${RESET}"
fi
echo ""
exit $EXIT_CODE
