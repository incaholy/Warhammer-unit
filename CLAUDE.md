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
- Services raise typed errors. The cross-cutting ones live in
  `app/core/services/errors.py` — `NotFoundError` (→404) and `ConflictError` for
  duplicates (→409). Each service defines its own `*ValidationError(ValueError)`
  in its own module (→400, with a `field`; e.g. `UnitValidationError` in
  `service_unit.py`). There is no shared base: every error inherits the builtin it
  maps to (`LookupError`/`ValueError`) and carries its own `code` (`ErrorCode`,
  from `app/core/errors.py`), `message`, and `field`. `app/main.py` registers a
  handler per concrete error class — **add a new service `*ValidationError` to the
  `_SERVICE_ERRORS` tuple there** or it falls through to a generic 500. Never
  raise `HTTPException` in a service.
- Schema changes go through `models.py` + an Alembic migration, never raw SQL.
- Keep stat names matching the datasheet terms used in `models.py`
  (`movement`, `toughness`, `armor_save`, `wounds`, `leadership`,
  `objective_control`).
