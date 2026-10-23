# Warhammer Unit — backend

The backend for **Muster**. It serves two games, kept apart in the API and in the
client (`/army-list` vs `/kill-team`):

- **Warhammer 40k army lists.** Unit datasheets (stats, weapons, abilities,
  keywords), an inventory of the models a user owns, and armies assembled from
  that inventory, with points totals and validation.
- **Kill Team 2024.** A read-only operative catalog, a player's own rosters, and a
  turn-by-turn game tracker — turning points, activations, ploys, equipment, and an
  undo. Designed in [`KILLTEAM.md`](KILLTEAM.md).

Kill Team is the larger half by surface area. Of the 72 operations in
`openapi.json`, 29 are Kill Team; of the 26 SQLModel tables, 15 are the `kt_*`
tables in `app/core/db/models_killteam.py`. Both counted at commit `753f552`.

FastAPI REST API over PostgreSQL.

The React frontend lives in a sibling repo. On GitHub it is
**`incaholy/warhammer_unit_web`**; `warhammer_web` is only the directory name it is
usually cloned into locally.

**Stack:** FastAPI · SQLModel · PostgreSQL · Alembic (migrations) · JWT auth
(OAuth2 password flow) · Python 3.12.

## Quick start

Prerequisites: **Python 3.12** (via [pyenv](https://github.com/pyenv/pyenv);
this repo pins the virtualenv `warhammer-unit-env` in `.python-version`), a
running **PostgreSQL**, and `make`.

```bash
cp .env.example .env          # then set DATABASE_URL and, outside dev, SECRET_KEY
make setup                    # install runtime deps, create the DB role/database, run migrations
make install-dev              # dev tooling: ruff, import-linter, the scrapers
make run                      # serve on http://localhost:8000  (interactive docs at /docs)
```

Then in another shell:

```bash
make test                     # run the test suite
```

Optional next steps:

```bash
make create-admin USERNAME=<name>   # promote a user to admin (catalog writes are admin-only)
make seed                           # load the 40k catalog from scripts/data/datasheets.json (committed)
make scrape-kt && make seed-kt      # scrape, then load the Kill Team catalog (killteam.json is gitignored)
```

`make setup` installs `requirements.txt` only. `ruff` and `import-linter` live in
`requirements-dev.txt`, so `make check` and `make lint-imports` both fail until
`make install-dev` has run. The scrapers (`beautifulsoup4`, `lxml`) are dev deps too.

`DATABASE_URL` is required and lives in `.env` (gitignored). In `dev` (the
default `APP_ENV`) a throwaway JWT key is allowed; any other environment
**requires** `SECRET_KEY`. See `.env.example` for every variable.

## Common commands

Everything is driven through the `Makefile` — run `make help` for the full list.

| Command | What it does |
|---|---|
| `make setup` | Install runtime deps, create the DB, run migrations (one-shot bootstrap). |
| `make install-dev` | Install `requirements-dev.txt` — ruff, import-linter, the scrapers. |
| `make run` | Run the API locally with auto-reload. |
| `make test` | Run the test suite (fast in-memory SQLite tier). |
| `make docker-test` | Run the suite against real Postgres (the parity tier — schema built from the migrations). |
| `make check` | Pre-PR gate: strict lint + format-check + tests. |
| `make lint` / `make format` | Read-only lint overview / auto-fix + format (mutating). |
| `make lint-imports` | Check the layering contracts in `.importlinter` (also part of `make check`). |
| `make migrate` | Apply Alembic migrations up to head. |
| `make migrate-fresh` | Drop, recreate, and re-migrate — **destructive** (wipes data). |
| `make openapi` | Regenerate `openapi.json` from the app (no DB needed). |
| `make seed` / `make scrape` | Load the 40k catalog / re-scrape it into `scripts/data/datasheets.json`. |
| `make seed-kt` / `make scrape-kt` | Load the Kill Team catalog / scrape it into `scripts/data/killteam.json`. |
| `make scrape-kt-fresh` | Re-request every Kill Team page — the scraper cache has no expiry, so this is how a changed page is picked up. |
| `make docker-up` / `make docker-down` | Run the app + Postgres via docker compose. |

After editing `models.py`, generate a migration with
`alembic revision --autogenerate -m "msg"`, then `make migrate`.

**Test tiers.** `make test` runs the fast in-memory SQLite tier. To run the same
suite against Postgres (the parity tier — schema built by the real migrations,
catching length/timestamp/native-type divergences), set `TEST_DATABASE_URL` to a
**throwaway** database and run `pytest`, or use `make docker-test` (containerized).
The parity setup drops and rebuilds the schema, so it refuses any `TEST_DATABASE_URL`
whose name lacks `test` or that matches `DATABASE_URL`. See `.env.example`.

## Layout

```
app/
  api/               FastAPI routers, one module per resource (+ *_Create/*_Read schemas)
                     40k:        unit.py faction.py inventory.py army.py user.py auth.py
                     Kill Team:  killteam.py        the read-only catalog
                                 killteam_roster.py a player's own rosters
                                 killteam_game.py   the turn-by-turn game tracker
  core/
    services/        business logic, one <Thing>Service per file (typed errors, no HTTPException)
    db/              models.py + models_killteam.py (SQLModel tables), columns.py (shared
                     column types), connection.py (engine/session), alembic/ (migrations)
  observability.py   request-id middleware, JSON log formatter, DSN-gated Sentry
tests/               pytest suite (35 modules)
scripts/             seed + scrape tooling, for both 40k and Kill Team
```

Layering is strict — **API → service → DB**: routers never touch the session,
services never raise `HTTPException` (they raise typed errors that `app/main.py`
maps to one normalized error shape: `{detail, code, errors}` on every error body,
plus `field` on a field-specific failure and `request_id` whenever the request
carries one). [`docs/api/conventions.md`](docs/api/conventions.md) states that
shape in full, and `CLAUDE.md` has the repo conventions.

## Documentation

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — the target architecture and its guiding principles.
- [`SPEC.md`](SPEC.md) — the specification: data model, endpoints, and behavior.
- [`docs/api/conventions.md`](docs/api/conventions.md) — the cross-repo API contract:
  the error shape, the pagination envelope, auth, versioning, optimistic concurrency.
  The only human-readable statement of those; `openapi.json` is the machine half.
- [`MVP.md`](MVP.md) — what the minimal product is and what's built.
- [`ROADMAP.md`](ROADMAP.md) — the delta from today's code to the target architecture, item by item.
- [`DEPLOY.md`](DEPLOY.md) — the fast, free path to putting the app online.
- [`DEPLOY-GCP.md`](DEPLOY-GCP.md) — the production-shaped deploy on Google Cloud.
- [`CODE-REVIEW.md`](CODE-REVIEW.md) — a full review of both repos (reasoning, not a live bug list).
- [`KILLTEAM.md`](KILLTEAM.md) — the Kill Team 2024 tracker: catalog → roster → game,
  and the numbered decisions behind it.
- [`CLAUDE.md`](CLAUDE.md) — repo conventions and commands.
