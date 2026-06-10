# Warhammer Unit Backend — Specification

A backend for storing Warhammer 40k unit datasheets in PostgreSQL, exposed through a FastAPI REST API.

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

| Model | Table | Purpose |
|---|---|---|
| `Unit` | `units` | A datasheet's core stat line |
| `Weapon` | `weapons` | A weapon belonging to a unit |
| `WeaponProfile` | `weapon_profiles` | A firing/attack profile of a weapon (a weapon can have several, e.g. "standard" and "supercharge") |
| `Ability` | `abilities` | A named ability with a category (core, faction, datasheet) and description |
| `Keyword` | `keywords` | A keyword tag on a unit (e.g. INFANTRY, IMPERIUM); `keyword_type` separates normal vs faction keywords |

Relationships (all one-to-many, child holds the foreign key):

```
Unit ──< Weapon ──< WeaponProfile
Unit ──< Ability
Unit ──< Keyword
```

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

Service conventions:
- "Not found" raises `LookupError` (don't return strings or `None` ambiguously).
- Bad input raises `ValueError` / `TypeError` with a descriptive message.
- Every write method ends with `commit()` + `refresh()` and returns the model.

## API layer (`app/api/`)

One router module per resource. Routers define their own request/response
schemas (`*_Create`, `*_Read`) so internal model fields aren't exposed
accidentally.

Planned routes:

| Method | Path | Action | Status |
|---|---|---|---|
| POST | `/units` | create a unit | done |
| GET | `/units/{id}` | get one unit (with weapons, abilities, keywords) | to do |
| GET | `/units` | list units, query params for faction/type filter, limit, offset | to do |
| PATCH | `/units/{id}` | update fields on a unit | to do |
| DELETE | `/units/{id}` | delete a unit (cascade its children) | to do |
| POST | `/units/{id}/weapons` | add a weapon (with profiles) to a unit | to do |
| POST | `/units/{id}/abilities` | add an ability | to do |
| POST | `/units/{id}/keywords` | add a keyword | to do |

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

## Testing

Tests live in `tests/`, mirroring the app structure:

```
tests/
  conftest.py            # fixtures: test engine, session, FastAPI TestClient
  services/
    test_service_unit.py
    test_service_weapon.py
  api/
    test_api_unit.py
```

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
