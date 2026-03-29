#!/usr/bin/env bash
# =============================================================================
#  Telegram Manager — Script d'installation
# =============================================================================
set -euo pipefail

VENV_DIR="venv"
REQUIRED_PYTHON_MAJOR=3
REQUIRED_PYTHON_MINOR=9
CONFIG_FILE="config.json"

# ── Couleurs ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

ok()   { echo -e "${GREEN}  ✅  $*${RESET}"; }
info() { echo -e "${CYAN}  ℹ️   $*${RESET}"; }
warn() { echo -e "${YELLOW}  ⚠️   $*${RESET}"; }
err()  { echo -e "${RED}  ❌  $*${RESET}"; exit 1; }

# ── Bannière ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║      🤖  TELEGRAM MANAGER  🤖        ║${RESET}"
echo -e "${BOLD}${CYAN}║          Script d'installation        ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════╝${RESET}"
echo ""

# ── 1. Vérification Python ────────────────────────────────────────────────────
info "Vérification de Python..."

PYTHON=""
for cmd in python3 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        MAJOR=${VER%%.*}
        MINOR=${VER##*.}
        if [ "$MAJOR" -ge "$REQUIRED_PYTHON_MAJOR" ] && [ "$MINOR" -ge "$REQUIRED_PYTHON_MINOR" ]; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    err "Python ${REQUIRED_PYTHON_MAJOR}.${REQUIRED_PYTHON_MINOR}+ est requis mais introuvable.
       Installez Python depuis https://www.python.org/downloads/"
fi

PY_VERSION=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
ok "Python $PY_VERSION trouvé → $PYTHON"

# ── 2. Environnement virtuel ──────────────────────────────────────────────────
info "Création de l'environnement virtuel dans ./$VENV_DIR/ ..."

if [ -d "$VENV_DIR" ]; then
    warn "Dossier $VENV_DIR déjà existant — réutilisation."
else
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Environnement virtuel créé."
fi

# Activer le venv
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Environnement virtuel activé."

# ── 3. Mise à jour de pip ─────────────────────────────────────────────────────
info "Mise à jour de pip..."
pip install --upgrade pip --quiet
ok "pip à jour."

# ── 4. Installation des dépendances ──────────────────────────────────────────
info "Installation des dépendances depuis requirements.txt..."
pip install -r requirements.txt --quiet
ok "Dépendances installées."

# Vérification des imports critiques
"$VENV_DIR/bin/python" -c "import telethon, colorama, socks" 2>/dev/null \
    && ok "Import des modules OK (telethon, colorama, PySocks)." \
    || warn "Un ou plusieurs modules n'ont pas pu être importés — vérifiez manuellement."

# tqdm est optionnel
"$VENV_DIR/bin/python" -c "import tqdm" 2>/dev/null \
    && ok "tqdm disponible (barres de progression activées)." \
    || warn "tqdm absent — les barres de progression seront désactivées (non bloquant)."

# ── 5. Création du config.json si absent ─────────────────────────────────────
if [ ! -f "$CONFIG_FILE" ]; then
    info "Création du fichier $CONFIG_FILE par défaut..."
    cat > "$CONFIG_FILE" << 'EOF'
{
    "accounts": [],
    "proxies": []
}
EOF
    ok "$CONFIG_FILE créé."
else
    ok "$CONFIG_FILE existant conservé."
fi

# ── 6. Rendre les scripts exécutables ────────────────────────────────────────
for script in run.sh run_en.sh run_dashboard.sh; do
    if [ -f "$script" ]; then
        chmod +x "$script"
        ok "$script rendu exécutable."
    fi
done

# ── 7. Résumé ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}   ✅  Installation terminée !         ${RESET}"
echo -e "${BOLD}${GREEN}══════════════════════════════════════${RESET}"
echo ""
echo -e "  Pour lancer le programme :"
echo -e "  ${BOLD}  ./run.sh${RESET}            — CLI (français, 63 options)"
echo -e "  ${BOLD}  ./run_en.sh${RESET}         — CLI (anglais)"
echo -e "  ${BOLD}  ./run_dashboard.sh${RESET}  — Dashboard web (http://localhost:8000)"
echo ""
echo -e "  Avant le premier lancement :"
echo -e "  ${CYAN}  1. Obtenez vos clés API sur https://my.telegram.org/auth${RESET}"
echo -e "  ${CYAN}  2. Lancez :  python connect_account.py${RESET}"
echo -e "  ${CYAN}     (ou ./run.sh puis option 8)${RESET}"
echo ""
