# API image. Install dependencies first (cached layer), then the app code.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first so this layer is reused when only app code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the application (and the entrypoint that migrates before serving).
COPY . .
RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

# Entrypoint runs `alembic upgrade head`, then execs the command below.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
