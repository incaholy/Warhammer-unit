# MVP — Warhammer Unit Backend

A tracking doc for the minimal viable product: what the parts are, what's built,
what still needs building, and what needs fixing. Architecture and route details
live in [SPEC.md](SPEC.md); this file is the feature/status checklist.

## What it is

A backend for a Warhammer 40k **army-list builder**. Three ideas:

- a shared, read-only **catalog** of datasheets (factions → subfactions → units,
  with weapons + abilities);
- each user's **inventory** — the models they physically own; and
- each user's **armies** — buildable, points-costed, rules-checked rosters.

Everything is behind JWT auth: users see only their own data; the catalog is
admin-curated.

## The core loop (definition of "viable")

`register → log in → browse the catalog → record what you own → build army lists
→ check points / shortfall / validity`. **This loop works today.**

## Parts of the system

| Part | Where | Status |
|---|---|---|
| DB layer — SQLModel tables + Alembic migrations | `app/core/db/` | ✅ |
| Services — business logic (session-injected) | `app/core/services/` | ✅ |
| API — FastAPI routers + request/response schemas | `app/api/` | ✅ |
| Security — hashing, JWT, current-user/admin deps | `app/core/security.py` | ✅ |
| Dev tooling — Makefile, tests, `.env.example` | repo root | ✅ |
| Deployment — `Dockerfile` + `docker-compose.yml` (API + Postgres) | repo root | 🔨 planned |

## Services

| Service | Responsibility | Status |
|---|---|---|
| `AuthService` | register (hash password) + authenticate (username/email + password) | ✅ implemented + tested |
| `UserService` | create / fetch users | ✅ implemented + tested |
| `UnitService` | the catalog — units, factions, subfactions, weapons, abilities (+ linking) | ✅ implemented + tested |
| `InventoryService` | a user's owned units (`user_unit`) — add/set/remove/list | ✅ implemented + tested |
| `ArmyService` | armies + their units, plus `points_total`, `shortfall`, `validate` | ✅ implemented + tested |

## API surface

- **Public**: `POST /auth/register`, `POST /auth/login`, `GET /health`, catalog
  **reads** (`GET /units`, `GET /units/{id}`, `GET /factions`).
- **Authenticated (own data)**: `GET /me`, `/me/inventory/*`, `/me/armies/*`
  (armies ownership-checked → 404 for someone else's).
- **Admin only**: catalog **writes** (`/units`, `/factions`, `/subfactions`,
  `/weapons`, `/abilities`).

## Feature checklist

### Built ✅
- [x] Schema (11 tables) + Alembic migrations
- [x] Auth: register, login, JWT (`SECRET_KEY`, HS256), `GET /me`
- [x] Own-data routing (`/me/*`) — identity from token, not path
- [x] Army ownership check (`get_owned_army`) — stranger's `army_id` → 404
- [x] Admin-gated catalog writes; public catalog reads
- [x] Catalog CRUD: units, factions, subfactions, weapons, abilities + link weapon/ability
- [x] Inventory: upsert-add (201/200), set amount, remove, list
- [x] Armies: create/get/list/update/delete + units add (upsert 201/200) / set / remove
- [x] Roster: `points_limit`, computed `points_total`
- [x] Validation Tier 1 (points vs limit) + Tier 2 (faction / subfaction)
- [x] Shortfall (army vs inventory — what to buy)
- [x] Test suite (120 tests) + Makefile + `.env.example`

### To build 🔨 (backlog)
- [ ] **Seed script** to bulk-load a real datasheet catalog (`scripts/seed_datasheets.py`) — *deferred; the catalog is fully enterable via the admin API in the meantime.*
- [ ] **First-admin bootstrap helper** — a `make`/CLI target to set `is_admin`, instead of a manual `UPDATE users SET is_admin = true`.
- [x] **Catalog listing filters + pagination** — `GET /units` narrows and pages
  results instead of returning the entire catalog. *(Built: bare-list response
  kept; envelope with `total` deferred — see note at end of block.)* By layer:
  - **Service** — widen `UnitService.list_units()` from no-args to
    `list_units(faction_id=None, subfaction_id=None, q=None, limit=50, offset=0)`.
    Build the `select(Unit)` conditionally: `.where(Unit.faction_id == faction_id)`
    and `.where(Unit.subfaction_id == subfaction_id)` when given (exact match),
    `.where(Unit.unit_name.ilike(f"%{q}%"))` for a case-insensitive name search,
    then `.offset(offset).limit(limit)`.
  - **API** — add query params to the `GET /units` route: `faction_id: UUID | None`,
    `subfaction_id: UUID | None`, `q: str | None` (name search), `limit: int = 50`
    (clamp to a max, e.g. 200), `offset: int = 0`; pass them straight to the service.
  - **Response-shape decision** — either keep the bare `list[Unit_Read]` (simplest)
    or switch to an envelope `{items: [...], total, limit, offset}`. The Muster
    catalog view shows a "N results" count and a faction sidebar, so it wants a
    **total** and the applied filters → lean envelope. Pick one and apply it
    consistently (only `GET /units` needs it; `GET /factions` is small enough to
    stay a bare list).
  - **Filters to support (MVP)** — `faction_id` is the must-have (the catalog is
    browsed by faction). `q` name-search and `subfaction_id` are cheap add-ons.
    A `keyword` filter (units having a given keyword) is a nice-to-have but needs
    a JSON containment query, so defer it.
  - **Tests** — filter by faction returns only that faction's units; `q` narrows
    by name; `limit`/`offset` page correctly (e.g. 3 units, `limit=2` → 2 then 1);
    no params returns the first page.
  - **SPEC** — this makes `GET /units`'s advertised "faction filter, limit, offset"
    real; update the route/description to match the final param names + response
    shape. (Resolves the *To fix* mismatch below.)
- [ ] **Validation Tier 3** — per-datasheet count limits ("0-1 per army", "max 3", epic hero once). Needs new `Unit` fields (`max_per_army`, `is_epic_hero`) + a migration.
- [ ] **Validation Tier 4** — detachments / force-org rules (larger; edition-specific).
- [ ] **Deployment & containerization** — make the app run anywhere, not just on
  a dev laptop. *(Full spec: SPEC.md → "Deployment & containerization.")*
  - **`Dockerfile`** — `python:3.12-slim`, install requirements, run
    `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
  - **`docker-compose.yml`** — `api` + `db` (`postgres:16`, named volume,
    healthcheck); `DATABASE_URL` targets the `db` service, not `localhost`.
  - **`.dockerignore`** — keep caches/`.env` out of the build context.
  - **Migrations on start** — run `alembic upgrade head` before uvicorn serves.
  - **Makefile** — add `docker-build` / `docker-up` / `docker-down` targets.
- [ ] **Custom service errors** — replace the builtin `LookupError`/`ValueError`
  with a typed hierarchy so errors carry a **field** and the right status.
  *(Full spec: SPEC.md → "Custom service errors.")*
  - **`app/core/services/errors.py`** — shared `NotFoundError(LookupError)` (404),
    `ConflictError(ValueError)` (409, duplicates), `ForbiddenError(Exception)`
    (403); plus per-service `ValueError` subclasses `UserValidationError`,
    `UnitValidationError`, `ArmyValidationError`, `InventoryValidationError`,
    each built as `(field, message)`.
  - **Backward-compatible** — every custom error subclasses the builtin it
    replaces, so the current `main.py` handlers keep working; new handlers just
    add the field + 409/422. Body stays `{"detail", "field"?}` — **no** `{data,
    meta}` envelope.
  - **Migration** — per service, swap `raise LookupError` → `NotFoundError` and
    `raise ValueError` → the typed `*ValidationError` / `ConflictError`.
- [ ] **Frontend** — the "Muster" UI (Vite/React) hitting this API. Out of backend scope; tracked here for the product view.

### To add / harden ⚙️ (config & ops)
- [ ] Set a real `SECRET_KEY` in production (a dev default is in place).
- [ ] CORS config once a browser frontend calls the API.
- [ ] Rate limiting on `/auth/*` (brute-force protection).

## To fix 🐞
- [x] ~~`GET /units` advertises `faction filter, limit, offset` but `list_units()` takes no args~~ — **fixed**: filtering + pagination implemented with `faction_id`/`subfaction_id`/`q`/`limit`/`offset`, SPEC synced, 5 tests added.
- [ ] SPEC's Service-layer table lists four services and omits `AuthService` — doc drift; add it.
- [ ] `passlib` 1.7.4 emits a `crypt` `DeprecationWarning` (removed in Python 3.13) — harmless now; revisit on a passlib upgrade.

## Out of scope (not MVP)
- Datasheet **versioning** ("stats as of when I added it").
- **Wargear/loadout** modelling and points that scale with model count.
- List **sharing/export**, game/match tracking, multiplayer.
