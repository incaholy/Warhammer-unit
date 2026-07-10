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
| Operator scripts — seed catalog, make admin (+ Wahapedia scraper planned) | `scripts/` | ✅ |
| Dev tooling — Makefile, tests, `.env.example` | repo root | ✅ |
| Deployment — `Dockerfile` + `docker-compose.yml` (API + Postgres) | repo root | ✅ |

## Services

| Service | Responsibility | Status |
|---|---|---|
| `AuthService` | register (hash password) + authenticate (username/email + password) | ✅ implemented + tested |
| `UserService` | create / fetch users, grant/revoke admin | ✅ implemented + tested |
| `UnitService` | the catalog — full CRUD for units, weapons, abilities; factions/subfactions (+ link/unlink, filtering, counts) | ✅ implemented + tested |
| `InventoryService` | a user's owned units (`user_unit`) — add/set/remove/list | ✅ implemented + tested |
| `ArmyService` | armies + their units, plus `points_total`, `shortfall`, `validate` | ✅ implemented + tested |

## API surface

- **Public**: `POST /auth/register`, `POST /auth/login`, `GET /health`, catalog
  **reads** (`GET /units` + `GET /units/{id}`, `GET /factions`,
  `GET /factions/taxonomy`, `GET /weapons`, `GET /abilities`).
- **Authenticated (own data)**: `GET /me`, `/me/inventory/*`, `/me/armies/*`
  (armies ownership-checked → 404 for someone else's).
- **Admin only**: catalog **writes** — units (create/update/delete + link/unlink
  weapons & abilities), weapons & abilities (create/update/delete), factions
  (create), subfactions (create/delete); plus admin promotion (`PATCH /users/{id}`).

## Feature checklist

### Built ✅
- [x] Schema (11 tables) + Alembic migrations
- [x] Auth: register, login, JWT (`SECRET_KEY`, HS256), `GET /me`
- [x] Own-data routing (`/me/*`) — identity from token, not path
- [x] Army ownership check (`get_owned_army`) — stranger's `army_id` → 404
- [x] Admin-gated catalog writes; public catalog reads
- [x] Catalog CRUD: units (create/update/delete, link **and unlink** weapons/abilities), weapons & abilities (create/update/delete), factions (create), subfactions (create/delete)
- [x] Delete guard: deleting a referenced unit/subfaction → 409 (not a 500)
- [x] Input guard: `add_unit` (inventory + army) rejects amount < 1 (422 schema / 400 service)
- [x] Admin promotion via API (`PATCH /users/{id}` `{is_admin}`, admin-only)
- [x] Faction/subfaction name constraints (`FactionName` enum → 422; `FACTION_SUBFACTIONS` map → 400)
- [x] Catalog reads: `GET /units` (filter by faction/subfaction/`q` + `limit`/`offset` + `X-Total-Count`), `GET /units/{id}`, `GET /factions`, `GET /factions/taxonomy`, `GET /weapons`, `GET /abilities`
- [x] Inventory: upsert-add (201/200), set amount, remove, list
- [x] Armies: create/get/list/update/delete + units add (upsert 201/200) / set / remove
- [x] Roster: `points_limit`, computed `points_total`
- [x] Validation Tier 1 (points vs limit) + Tier 2 (faction / subfaction)
- [x] Shortfall (army vs inventory — what to buy)
- [x] Typed service errors (`NotFoundError` 404 / `ConflictError` 409 / `*ValidationError` 400 with `field`)
- [x] Containerization: `Dockerfile` (non-root user) + `docker-compose` (API + Postgres), migrations on start
- [x] CORS (env `ALLOWED_ORIGINS`), first-admin helper (`make create-admin`), seed script (`make seed`)
- [x] Test suite (202 tests) + Makefile + `.env.example`
- [x] Quality pass: whole-roadmap review + Improvements (should-fix / robustness / consistency / coverage) — see SPEC.md "Improvements"

### To build 🔨 (backlog)
- [ ] **Catalog scraper (Wahapedia)** — *change of plan for seeding.* Instead of
  hand-filling `datasheets.json`, `scripts/scrape_wahapedia.py` will scrape
  datasheets from Wahapedia (e.g. the White Scars page) into that JSON, then
  `make seed` loads it (**scrape → JSON → seed**). Includes polite fetching + a
  disk cache, mapping to our `FactionName`/`FACTION_SUBFACTIONS` taxonomy, and a
  parser test against a saved HTML fixture. *(Full plan: SPEC.md → "Scraping the
  catalog (Wahapedia)".)*
- [ ] **Frontend** — the "Muster" UI (Vite/React) hitting this API. Out of backend
  scope; the backend is **frontend-ready** (seed, CORS, typed errors, catalog reads
  with a total count all in place). See SPEC.md "Frontend integration".

### To add / harden ⚙️ (config & ops)
- [ ] Set a real `SECRET_KEY` in production — the app now refuses to start without it unless `APP_ENV=dev`, and `docker-compose` requires it; just provide the value at deploy-time.
- [ ] Point `ALLOWED_ORIGINS` at the frontend's real origin — deploy-time (the CORS middleware itself is built).
- [ ] Rate limiting on `/auth/*` (brute-force protection) — deferred; not yet needed. Plan in SPEC.md "Auth → Planned hardening".

## To fix 🐞
- [x] ~~`GET /units` advertised filters `list_units()` ignored~~ — **fixed** (filtering + pagination + `X-Total-Count`).
- [x] ~~SPEC service table omitted `AuthService`~~ — **fixed** (added to the table).
- [ ] `passlib` 1.7.4 emits a `crypt` `DeprecationWarning` (removed in Python 3.13) — harmless now; revisit on a passlib upgrade.

## Out of scope (not MVP)
- Datasheet **versioning** ("stats as of when I added it").
- **Wargear/loadout** modelling and points that scale with model count.
- List **sharing/export**, game/match tracking, multiplayer.
- **Validation Tier 3** (per-datasheet count limits) and **Tier 4** (detachments /
  force-org) — deferred; not yet needed. (Plans remain in SPEC.md's `validate`
  discussion if revived.)
