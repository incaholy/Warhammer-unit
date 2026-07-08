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

## Known issues (fix before building on top)

- `service_unit.py`: `create_unit` calls `self.session.commit(new_unit)` —
  `commit()` takes no arguments.
- `service_unit.py`: `list_units` builds `statment` but filters/offset/limit
  assign to `statement` (typo means filters are silently dropped, and the
  filtered path crashes with NameError).
- `service_unit.py`: `delete_unit` has `self.session.commit` without `()` —
  the delete is never committed.
- `service_unit.py`: `get_unit` returns the string "unit does not exist"
  instead of raising `LookupError`.
- `service_unit.py`: `update_unit` error messages use `"{field}"` without the
  `f` prefix.
- `app/api/unit.py`: router exists but there is no `app/main.py` mounting it,
  so the API can't be started yet.
