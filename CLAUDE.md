# Warhammer Unit Backend

Backend for storing Warhammer 40k unit datasheets. FastAPI + SQLModel +
PostgreSQL + Alembic. Full architecture and roadmap are in SPEC.md — read it
before making structural changes.

The Kill Team part (a game tracker) is designed in KILLTEAM.md — read it before
working on any Kill Team code, and update it when a design decision changes. It is
kept separate from the 40k army list builder: `/kill-team` vs `/army-list`. Its
build order and status are the K entries in ROADMAP.md.

## Commands

```bash
uvicorn app.main:app --reload        # run the API
alembic revision --autogenerate -m "msg"   # generate a migration after editing models
alembic upgrade head                 # apply migrations
pytest                               # run tests
```

`DATABASE_URL` must be set (it lives in `.env`, which is gitignored).

## Layout

- `app/api/` — FastAPI routers, one module per resource, with `*_Create`/`*_Read` schemas
- `app/core/services/` — business logic, one `<Thing>Service` class per file
- `app/core/db/` — `models.py` and `models_killteam.py` (SQLModel tables), `columns.py`
  (shared column types and helpers), `connection.py` (engine/session), `alembic/`
  (migrations)

## Conventions

- Layering is strict: API → service → DB. Routers never touch the session;
  services never raise `HTTPException`.
- Services raise typed errors. The cross-cutting ones live in
  `app/core/services/errors.py` — `NotFoundError` (→404) and `ConflictError` for
  duplicates (→409). Each service defines its own `*ValidationError(ValueError)`
  in its own module (→400, with a `field`; e.g. `UnitValidationError` in
  `service_unit.py`). Every error inherits **two** bases: `CodedError` (the marker
  base in `app/core/errors.py`) and the builtin it maps to (`LookupError`/
  `ValueError`), and carries its own `code` (`ErrorCode`), `message`, and `field`.
  `app/main.py` registers **one** handler, against `CodedError`, so a new error
  class is mapped to its status automatically — there is no registry to update.
  Never raise `HTTPException` in a service.
- Every `*_Create`/`*_Update` schema inherits `WriteSchema` (`app/api/fields.py`),
  which forbids unknown keys, and bounds every string and int it declares — reusing
  `Name`, `Username`, `Stat`, `DiceValue`, `WeaponCategory` or `INT32_MAX` rather than restating a
  limit. A SQLModel `max_length` constrains the DDL, not the request schema, so an
  unbounded field is a 500 on Postgres that SQLite cannot see.
  `tests/test_api_write_bounds.py` enforces both over `openapi.json`.
- Schema changes go through `models.py` + an Alembic migration, never raw SQL.
- Keep stat names matching the datasheet terms used in `models.py`
  (`movement`, `toughness`, `armor_save`, `wounds`, `leadership`,
  `objective_control`).
