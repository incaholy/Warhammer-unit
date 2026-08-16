# Warhammer Unit — backend

The backend for **Muster**, a Warhammer 40k army-list builder. It stores unit
datasheets (stats, weapons, abilities, keywords) and lets a user keep an
inventory of models they own and assemble armies from them, with points totals
and validation. FastAPI REST API over PostgreSQL.

The React frontend lives in a sibling repo (`warhammer_web`) and talks to this
API.

**Stack:** FastAPI · SQLModel · PostgreSQL · Alembic (migrations) · JWT auth
(OAuth2 password flow) · Python 3.12.

## Quick start

Prerequisites: **Python 3.12** (via [pyenv](https://github.com/pyenv/pyenv);
this repo pins the virtualenv `warhammer-unit-env` in `.python-version`), a
running **PostgreSQL**, and `make`.

```bash
cp .env.example .env          # then set DATABASE_URL and, outside dev, SECRET_KEY
make setup                    # install deps, create the DB role/database, run migrations
make run                      # serve on http://localhost:8000  (interactive docs at /docs)
```

Then in another shell:

```bash
make test                     # run the test suite
```

Optional next steps:

```bash
make create-admin USERNAME=<name>   # promote a user to admin (catalog writes are admin-only)
make seed                           # load the datasheet catalog from scripts/data/datasheets.json
```

`DATABASE_URL` is required and lives in `.env` (gitignored). In `dev` (the
default `APP_ENV`) a throwaway JWT key is allowed; any other environment
**requires** `SECRET_KEY`. See `.env.example` for every variable.

## Common commands

Everything is driven through the `Makefile` — run `make help` for the full list.

| Command | What it does |
|---|---|
| `make setup` | Install deps, create the DB, run migrations (one-shot bootstrap). |
| `make run` | Run the API locally with auto-reload. |
| `make test` | Run the test suite. |
| `make check` | Pre-PR gate: strict lint + format-check + tests. |
| `make lint` / `make format` | Read-only lint overview / auto-fix + format (mutating). |
| `make migrate` | Apply Alembic migrations up to head. |
| `make migrate-fresh` | Drop, recreate, and re-migrate — **destructive** (wipes data). |
| `make openapi` | Regenerate `openapi.json` from the app (no DB needed). |
| `make seed` / `make scrape` | Load the catalog / scrape datasheets into `scripts/data/`. |
| `make docker-up` / `make docker-down` | Run the app + Postgres via docker compose. |

After editing `models.py`, generate a migration with
`alembic revision --autogenerate -m "msg"`, then `make migrate`.

## Layout

```
app/
  api/               FastAPI routers, one module per resource (+ *_Create/*_Read schemas)
  core/
    services/        business logic, one <Thing>Service per file (typed errors, no HTTPException)
    db/              models.py (SQLModel tables), connection.py (engine/session), alembic/ (migrations)
tests/               pytest suite
scripts/             seed + scrape tooling
```

Layering is strict — **API → service → DB**: routers never touch the session,
services never raise `HTTPException` (they raise typed errors that `app/main.py`
maps to a normalized `{detail, code, field?}` response). See `CLAUDE.md` for the
conventions in full.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the target architecture and its guiding principles.
- [`SPEC.md`](SPEC.md) — the specification: data model, endpoints, and behavior.
- [`MVP.md`](MVP.md) — what the minimal product is and what's built.
- [`ROADMAP.md`](ROADMAP.md) — the delta from today's code to the target architecture, item by item.
- [`DEPLOY.md`](DEPLOY.md) — the fast, free path to putting the app online.
- [`DEPLOY-GCP.md`](DEPLOY-GCP.md) — the production-shaped deploy on Google Cloud.
- [`CODE-REVIEW.md`](CODE-REVIEW.md) — a full review of both repos (reasoning, not a live bug list).
- [`CLAUDE.md`](CLAUDE.md) — repo conventions and commands.
