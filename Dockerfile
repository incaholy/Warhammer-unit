# API image. Install dependencies first (cached layer), then the app code.
FROM python:3.12-slim

WORKDIR /app

# Dependencies first so this layer is reused when only app code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Then the application (and the entrypoint that migrates before serving).
# docker-entrypoint.sh is committed executable (0755), so COPY preserves the bit.
COPY . .

# Run as a non-root user rather than root.
RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Entrypoint runs `alembic upgrade head`, then execs the command below.
# Bind to $PORT when the platform provides one (Render, Fly, Cloud Run, …),
# falling back to 8000 for local Compose. `sh -c` (JSON/exec form) is what
# expands ${PORT} at runtime; the generic entrypoint still `exec "$@"`s it.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
