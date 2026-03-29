#!/bin/sh
# Symlink all data files from /app/data/ to /app/ (working directory)
# This allows Telethon to find .session files and the app to find config/DB files

for f in /app/data/*; do
    base=$(basename "$f")
    # Don't overwrite app code files
    if [ ! -d "/app/$base" ] && [ "$base" != "entrypoint.sh" ]; then
        ln -sf "$f" "/app/$base" 2>/dev/null || true
    fi
done

echo "  Data files linked: $(ls /app/data/ | wc -l) files"

exec uvicorn dashboard.app:app --host 0.0.0.0 --port ${PORT:-8700}
