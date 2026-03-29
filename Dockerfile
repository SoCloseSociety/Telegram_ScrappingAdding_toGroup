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

# Create data directories
RUN mkdir -p /app/data /app/campaigns

# Default port (overridable)
ENV PORT=8000
EXPOSE ${PORT}

CMD ["sh", "-c", "uvicorn dashboard.app:app --host 0.0.0.0 --port ${PORT}"]
