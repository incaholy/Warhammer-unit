# Warhammer Unit Backend

Backend for storing Warhammer 40k unit datasheets. FastAPI + SQLModel +
PostgreSQL + Alembic. Full architecture and roadmap are in SPEC.md — read it
before making structural changes.

## Commands

```bash
uvicorn app.main:app --reload        # run the API (once app/main.py exists)
alembic revision --autogenerate -m "msg"   # generate a migration after editing models
alembic upgrade head                 # apply migrations
pytest                               # run tests (once tests/ exists)
```

`DATABASE_URL` must be set (it lives in `.env`, which is gitignored).

## Layout

- `app/api/` — FastAPI routers, one module per resource, with `*_Create`/`*_Read` schemas
- `app/core/services/` — business logic, one `<Thing>Service` class per file
- `app/core/db/` — `models.py` (SQLModel tables), `connection.py` (engine/session), `alembic/` (migrations)

## Conventions

- Layering is strict: API → service → DB. Routers never touch the session;
  services never raise `HTTPException`.
- Services raise the typed errors from `app/core/services/errors.py`:
  `NotFoundError` (→404), `ConflictError` for duplicates (→409), and a per-service
  `*ValidationError(field, msg)` for bad input (→400, with the `field`). Each
  subclasses the builtin it replaces (`LookupError`/`ValueError`), which the
  `ServiceError` handler in `app/main.py` maps. Never raise `HTTPException` in a
  service.
- Schema changes go through `models.py` + an Alembic migration, never raw SQL.
- Keep stat names matching the datasheet terms used in `models.py`
  (`movement`, `toughness`, `save`, `wounds`, `leadership`,
  `objective_control`).
