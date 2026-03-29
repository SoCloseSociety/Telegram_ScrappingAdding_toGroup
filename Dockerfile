FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Create data directory
RUN mkdir -p /app/data /app/campaigns

# Data files live in /app/data (mounted as volume)
# Symlink them to where the app expects them
RUN ln -sf /app/data/config.json /app/config.json && \
    ln -sf /app/data/auth.json /app/auth.json && \
    ln -sf /app/data/history.db /app/history.db && \
    ln -sf /app/data/automation.db /app/automation.db && \
    ln -sf /app/data/activity.log /app/activity.log && \
    ln -sf /app/data/groups_cache.json /app/groups_cache.json

ENV PORT=8700
EXPOSE ${PORT}

# Entrypoint: symlink sessions + CSV from data dir, then start
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
