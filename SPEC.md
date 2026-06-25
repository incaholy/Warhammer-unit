# Warhammer Unit Backend — Specification

A backend for storing Warhammer 40k unit datasheets in PostgreSQL, exposed through a FastAPI REST API.

The data divides into two halves:

- **The catalog** — the canonical datasheets (`units` and their weapons,
  profiles, abilities, keywords). These rarely change and are effectively
  read-only to normal users; you populate them by seed/admin. There is one
  shared copy of every datasheet.
- **Collections** — user-owned data that points into the catalog. Users don't
  create datasheets; they organise *which* units (and how many of each) they
  field. A collection is structured as a hierarchy so it can grow into a
  roster / list builder: a user has one **collection**, the collection holds
  named **armies**, and each army holds **units with quantities**.

The end-to-end flow is **user → collection → army → unit (+ quantity)**:

```
users ──(1:1)── collections ──< armies ──< army_entries >── units  (the catalog)
                                                  │
                                                  └─ quantity
```

The unit↔army link is still a quantity-bearing join (one row per unit type per
army); the army and collection layers above it are what turn a flat "what I own"
list into named, buildable lists. Each army is effectively a roster.

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
| `User` | `users` | collection | An account that owns one collection |
| `Collection` | `collections` | collection | A user's top-level container; holds their armies |
| `Army` | `armies` | collection | A named list/roster belonging to a collection; holds units |
| `ArmyEntry` | `army_entries` | collection | A pointer from an army to a unit, with how many are in it |

Relationships:

```
# catalog (one-to-many, child holds the foreign key)
Unit ──< Weapon ──< WeaponProfile
Unit ──< Ability
Unit ──< Keyword

# collection (a hierarchy; army_entries is a quantity-bearing join to the catalog)
User ──(1:1)── Collection ──< Army ──< ArmyEntry >── Unit
```

Collection layer details:
- `User` — unique `username`; no password field until auth lands (see below).
- `Collection` — one per user (`user_id` unique foreign key, 1:1). It exists as
  its own table so collection-level data and multiple-collections-per-user
  remain easy to add later; for now it's just the container for armies.
- `Army` — `collection_id` foreign key, plus a `name` (and optionally `faction`
  / points limit later). This is the unit that becomes a roster.
- `ArmyEntry` — carries `army_id`, `unit_id`, and `quantity`, with a
  `UNIQUE(army_id, unit_id)` constraint (one row per unit type per army, so
  "is this unit in the army?" is one lookup and quantity is never split across
  rows) and a `CHECK (quantity >= 1)` (quantity zero is a delete, not a row).

A unit can appear in many armies and across many users' collections; deleting a
catalog unit is still a global admin action, while deleting an `ArmyEntry`,
`Army`, or `Collection` only affects that one user. Deletes cascade downward:
removing a collection removes its armies and their entries; removing an army
removes its entries (never the catalog units they point at).

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
| `UserService` | planned | `create_user(username)` (`ValueError` on duplicate, creates the user's `Collection`), `get_user(id)` |
| `CollectionService` | planned | `get_collection(user_id)`, `list_armies`, `create_army`, `rename_army`, `delete_army` |
| `ArmyService` | planned | `add_unit`, `set_quantity`, `remove_unit`, `list_army` |

`CollectionService` manages armies inside a user's collection:
- `get_collection(user_id)` — the collection with its armies (`LookupError` if
  the user/collection doesn't exist).
- `create_army(user_id, name)` — make a new army in that user's collection.
- `rename_army(army_id, name)` / `delete_army(army_id)` — `LookupError` if the
  army doesn't exist; delete cascades to the army's entries.

`ArmyService` manages the units inside one army:
- `add_unit(army_id, unit_id, quantity=1)` — validates the army and unit both
  exist (else `LookupError`), then **upserts**: increment quantity if an entry
  already exists, otherwise create one. Makes "add unit to army" idempotent.
- `set_quantity(army_id, unit_id, quantity)` — absolute set; `quantity <= 0`
  raises `ValueError` (use `remove_unit` instead).
- `remove_unit(army_id, unit_id)` — delete the entry.
- `list_army(army_id)` — returns the army's entries joined with their `Unit`, so
  the caller gets each datasheet plus how many are in the army.

Service conventions:
- "Not found" raises `LookupError` (don't return strings or `None` ambiguously).
- Bad input raises `ValueError` / `TypeError` with a descriptive message.
- Every write method ends with `commit()` + `refresh()` and returns the model.
- Services should take a `session` argument (`ArmyService(session)`)
  rather than calling `get_session()` in `__init__`, so a multi-table operation
  (check army, check unit, write the join) shares one transaction — and so
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

**Users, collections & armies** — the user-facing flow. Routes nest the
hierarchy: a collection belongs to a user, an army to a collection, a unit entry
to an army.

| Method | Path | Action | Status |
|---|---|---|---|
| POST | `/users` | create a user (and their empty collection) | to do |
| GET | `/users/{id}` | get a user | to do |
| GET | `/users/{id}/collection` | get the collection with a summary of its armies | to do |
| POST | `/users/{id}/collection/armies` | create an army — body `{name}` | to do |
| GET | `/users/{id}/collection/armies/{army_id}` | get one army with its units + quantities (nested `Unit_Read` + `quantity`) | to do |
| PATCH | `/users/{id}/collection/armies/{army_id}` | rename an army | to do |
| DELETE | `/users/{id}/collection/armies/{army_id}` | delete an army (cascade its entries) | to do |
| POST | `/users/{id}/collection/armies/{army_id}/units` | add a unit — body `{unit_id, quantity}`, upserts | to do |
| PATCH | `/users/{id}/collection/armies/{army_id}/units/{unit_id}` | set absolute quantity | to do |
| DELETE | `/users/{id}/collection/armies/{army_id}/units/{unit_id}` | remove a unit from the army | to do |

Read schemas nest the hierarchy:
- `ArmyEntry_Read` embeds the existing `Unit_Read` and adds `quantity`.
- `Army_Read` is `{id, name, entries: [ArmyEntry_Read]}` — a full roster.
- `Collection_Read` is `{id, armies: [Army_Summary]}`, where each army summary is
  just `{id, name, unit_count}` so the collection view stays light; you fetch a
  single army to get its full unit list.

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

The schema is auth-ready: a real `users` table and FK integrity through
`collections` → `armies` → `army_entries`. For now endpoints take `user_id` as
a path param and there is no login. When auth lands, add `hashed_password` to
`User`, an `/auth` router (register/login → JWT), and a "current user"
dependency that replaces the `user_id` path param on the collection routes — the
collection/army logic itself doesn't change.

## Testing

Tests live in `tests/`, mirroring the app structure:

```
tests/
  conftest.py            # fixtures: test engine, session, FastAPI TestClient
  services/
    test_service_unit.py
    test_service_weapon.py
    test_service_collection.py   # armies within a collection
    test_service_army.py         # units within an army
  api/
    test_api_unit.py
    test_api_collection.py
```

Collection/army tests to cover: create a user makes an empty collection; create
an army then list it under the collection; add-unit-then-list; add the same unit
twice increments quantity; `set_quantity` to a new value; remove a unit; delete
an army cascades its entries; adding a unit to a nonexistent army or a
nonexistent unit → 404; `set_quantity <= 0` → 400.

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
8. **Collections & armies**: add `User`, `Collection`, `Army`, and `ArmyEntry`
   models and a migration; build `UserService`, `CollectionService`, and
   `ArmyService`; add the nested users/collection/army routes and the
   `ArmyEntry_Read` / `Army_Read` / `Collection_Read` schemas; write the
   collection/army tests.
9. Seed script to load the real datasheet catalog.
10. Roster features on `Army`: faction, points limit/total, list validation.
11. Authentication: passwords + `/auth` router + current-user dependency.
