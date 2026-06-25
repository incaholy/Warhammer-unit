# Warhammer Unit Backend — Specification

A backend for storing Warhammer 40k unit datasheets in PostgreSQL, exposed through a FastAPI REST API.

The data divides into two halves:

- **The catalog** — the canonical datasheets (`units` and their weapons,
  profiles, abilities, keywords). These rarely change and are effectively
  read-only to normal users; you populate them by seed/admin. There is one
  shared copy of every datasheet.
- **Collections** — each user owns a set of pointers into the catalog with a
  quantity. Users don't create datasheets; they record *which* units (and how
  many of each) are in their army.

The end-to-end flow is **user → collection entry → unit (+ quantity)**:

```
users ──< collection_entries >── units  (the catalog)
                  │
                  └─ quantity
```

## Architecture overview

The project has three layers. Each layer only talks to the layer directly below it:

```
HTTP request
    │
    ▼
API layer        app/api/          FastAPI routers + request/response schemas
    │
    ▼
Service layer    app/core/services/  Business logic, validation, DB transactions
    │
    ▼
DB layer         app/core/db/      SQLModel models, engine/session, Alembic migrations
    │
    ▼
PostgreSQL
```

Rules:
- API routers never touch the database directly — they call services.
- Services never know about HTTP — they raise plain Python exceptions
  (`ValueError`, `LookupError`, etc.); the API layer translates those into
  HTTP status codes.
- Models are the single source of truth for the database schema; Alembic
  migrations are generated from them.

## DB layer (`app/core/db/`)

### Models (`models.py`)

| Model | Table | Half | Purpose |
|---|---|---|---|
| `Unit` | `units` | catalog | A datasheet's core stat line |
| `Weapon` | `weapons` | catalog | A weapon belonging to a unit |
| `WeaponProfile` | `weapon_profiles` | catalog | A firing/attack profile of a weapon (a weapon can have several, e.g. "standard" and "supercharge") |
| `Ability` | `abilities` | catalog | A named ability with a category (core, faction, datasheet) and description |
| `Keyword` | `keywords` | catalog | A keyword tag on a unit (e.g. INFANTRY, IMPERIUM); `keyword_type` separates normal vs faction keywords |
| `User` | `users` | collection | An account that owns a collection |
| `CollectionEntry` | `collection_entries` | collection | A pointer from a user to a unit, with how many they own |

Relationships:

```
# catalog (one-to-many, child holds the foreign key)
Unit ──< Weapon ──< WeaponProfile
Unit ──< Ability
Unit ──< Keyword

# collection (many-to-many via collection_entries, quantity on the join row)
User ──< CollectionEntry >── Unit
```

`CollectionEntry` carries `user_id`, `unit_id`, and `quantity`, with a
`UNIQUE(user_id, unit_id)` constraint (one row per unit type per user, so
"do I own this?" is one lookup and quantity is never split across rows) and a
`CHECK (quantity >= 1)` (quantity zero is a delete, not a row). `User` has a
unique `username`; no password field until auth lands (see below).

Unit stat line maps to the datasheet: `movement` (M), `toughness` (T),
`save` (Sv), `wounds` (W), `leadership` (Ld), `objective_control` (OC).

WeaponProfile maps to the weapon row: `range_inches`, `attacks` (string because
it can be dice notation like "D6"), `weapon_strength` (S), `armour_penetration`
(AP), `damage` (D), plus a skill stat (BS for ranged / WS for melee).

### Connection (`connection.py`)

Reads `DATABASE_URL` from the environment (loaded from `.env`), creates one
engine at import time, and hands out fresh `Session` objects via
`get_session()`.

### Migrations (`alembic/`)

Schema changes are made by editing `models.py`, then:

```
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

Never edit the database schema by hand.

## Service layer (`app/core/services/`)

One service class per aggregate root, named `service_<thing>.py` containing
`<Thing>Service`. Each service owns a session and exposes CRUD methods.

| Service | Status | Methods |
|---|---|---|
| `UnitService` | implemented | `create_unit`, `get_unit`, `list_units` (filter/limit/offset), `update_unit`, `delete_unit` |
| `WeaponService` | stub | `create_weapon` (to do), plus get/list/update/delete (to do) |
| `AbilityService` | planned | CRUD for abilities |
| `KeywordService` | planned | CRUD for keywords |
| `UserService` | planned | `create_user(username)` (`ValueError` on duplicate), `get_user(id)` |
| `CollectionService` | planned | `add_unit`, `set_quantity`, `remove_unit`, `list_collection` |

`CollectionService` behaviour:
- `add_unit(user_id, unit_id, quantity=1)` — validates the user and unit both
  exist (else `LookupError`), then **upserts**: increment quantity if an entry
  already exists, otherwise create one. Makes "add to collection" idempotent.
- `set_quantity(user_id, unit_id, quantity)` — absolute set; `quantity <= 0`
  raises `ValueError` (use `remove_unit` instead).
- `remove_unit(user_id, unit_id)` — delete the entry.
- `list_collection(user_id)` — returns entries joined with their `Unit`, so the
  caller gets each datasheet plus how many are owned.

Service conventions:
- "Not found" raises `LookupError` (don't return strings or `None` ambiguously).
- Bad input raises `ValueError` / `TypeError` with a descriptive message.
- Every write method ends with `commit()` + `refresh()` and returns the model.
- Services should take a `session` argument (`CollectionService(session)`)
  rather than calling `get_session()` in `__init__`, so a multi-table operation
  (check user, check unit, write the join) shares one transaction — and so
  tests can inject a test session.

## API layer (`app/api/`)

One router module per resource. Routers define their own request/response
schemas (`*_Create`, `*_Read`) so internal model fields aren't exposed
accidentally.

Planned routes:

**Catalog** — reads are public; writes are admin/seed only (see "Populating the
catalog"), not part of the normal user flow.

| Method | Path | Action | Status |
|---|---|---|---|
| POST | `/units` | create a unit (admin/seed) | done |
| GET | `/units/{id}` | get one unit (with weapons, abilities, keywords) | to do |
| GET | `/units` | list units, query params for faction/type filter, limit, offset | to do |
| PATCH | `/units/{id}` | update fields on a unit (admin) | to do |
| DELETE | `/units/{id}` | delete a unit (admin; cascade its children) | to do |
| POST | `/units/{id}/weapons` | add a weapon (with profiles) to a unit (admin) | to do |
| POST | `/units/{id}/abilities` | add an ability (admin) | to do |
| POST | `/units/{id}/keywords` | add a keyword (admin) | to do |

**Users & collections** — the user-facing flow.

| Method | Path | Action | Status |
|---|---|---|---|
| POST | `/users` | create a user | to do |
| GET | `/users/{id}` | get a user | to do |
| GET | `/users/{id}/collection` | list owned units with quantities (nested `Unit_Read` + `quantity`) | to do |
| POST | `/users/{id}/collection` | add a unit — body `{unit_id, quantity}`, upserts | to do |
| PATCH | `/users/{id}/collection/{unit_id}` | set absolute quantity | to do |
| DELETE | `/users/{id}/collection/{unit_id}` | remove a unit from the collection | to do |

`CollectionEntry_Read` embeds the existing `Unit_Read` and adds `quantity`, so
`GET /users/{id}/collection` returns each full datasheet alongside how many the
user owns.

Error mapping at the API layer:

| Service exception | HTTP status |
|---|---|
| `LookupError` (not found) | 404 |
| `ValueError` / `TypeError` (bad input) | 400 |
| Pydantic validation failure | 422 (FastAPI automatic) |

### App entry point (to do)

`app/main.py` creates the `FastAPI()` instance and includes the routers.
Run locally with:

```
uvicorn app.main:app --reload
```

## Populating the catalog

Users never create datasheets, so the catalog is filled out of band:

1. A **seed script** (`scripts/seed_datasheets.py`) that bulk-inserts datasheets
   from a JSON/CSV source. Run once, re-run when GW publishes a dataslate.
2. Or treat the catalog write routes (`POST /units`, etc.) as **admin-only**
   ingestion, gated once auth exists.

Start with the seed script. Editing a datasheet updates the single shared
`units` row, so every collection pointing at it sees the new stats
automatically — which is the desired behaviour. ("Stats as of when I added it"
would need a `version` on `Unit` plus a version stored on the entry; out of
scope for now.)

## Authentication (future)

The schema is auth-ready: a real `users` table and FK integrity on
`collection_entries`. For now endpoints take `user_id` as a path param and
there is no login. When auth lands, add `hashed_password` to `User`, an
`/auth` router (register/login → JWT), and a "current user" dependency that
replaces the `user_id` path param on collection routes — the collection logic
itself doesn't change.

## Testing

Tests live in `tests/`, mirroring the app structure:

```
tests/
  conftest.py            # fixtures: test engine, session, FastAPI TestClient
  services/
    test_service_unit.py
    test_service_weapon.py
    test_service_collection.py
  api/
    test_api_unit.py
    test_api_collection.py
```

Collection tests to cover: add-then-list, add-twice-increments-quantity,
`set_quantity` to a new value, remove, adding a nonexistent unit or user → 404,
and `set_quantity <= 0` → 400.

- Service tests hit a real database session pointed at SQLite in-memory (or a
  throwaway Postgres database) so SQL actually executes.
- API tests use FastAPI's `TestClient` with the service dependency overridden
  to use the test session.
- Run with `pytest`.

## Roadmap

1. Fix known bugs in `UnitService` (see CLAUDE.md "Known issues").
2. Add `app/main.py` and mount the unit router.
3. Set up `tests/` with fixtures; write tests for `UnitService` first.
4. Finish unit API routes (get, list, update, delete).
5. Implement `WeaponService` + `WeaponProfile` handling and routes.
6. Abilities and keywords services + routes.
7. Nested read schema: GET unit returns full datasheet (weapons with profiles, abilities, keywords).
8. **Collections**: add `User` + `CollectionEntry` models and a migration;
   build `UserService` and `CollectionService`; add the users/collection
   routes and `CollectionEntry_Read`; write the collection tests.
9. Seed script to load the real datasheet catalog.
10. Authentication: passwords + `/auth` router + current-user dependency.
