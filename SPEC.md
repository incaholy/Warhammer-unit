# Warhammer Unit Backend — Specification

A backend for storing Warhammer 40k unit datasheets in PostgreSQL, exposed through a FastAPI REST API.

The data divides into two halves:

- **The catalog** — the canonical datasheets: `factions`, `subfactions`,
  `units`, and the `abilities`/`weapons` linked to each unit. These rarely
  change and are effectively read-only to normal users; you populate them by
  seed/admin. There is one shared copy of every datasheet.
- **User data** — what each user owns and builds, pointing into the catalog.
  Users don't create datasheets; they record two distinct things:
  - an **inventory** (`user_unit`) — the flat list of units a user physically
    owns, with an amount each ("I own 3 Intercessor squads"); and
  - named **armies** — buildable lists / rosters, each holding units with
    amounts ("this list uses 2 Intercessor squads").

  Inventory and armies are kept separate on purpose: the same physical model can
  appear in several army lists, and a list may include units the user hasn't
  bought yet, so "what I own" can't be derived by summing armies.

The end-to-end flow — armies and the inventory both hang directly off the user
and point into the catalog:

```
                ┌──< user_unit  >── units   (amount owned)
users ──────────┤                              (the catalog)
                └──< armies ──< army_units >── units (amount in list)
```

Both `user_unit` and `army_units` are amount-bearing joins into the catalog.
Armies are **independent** of inventory — an army may use units beyond what's
owned (aspirational lists) — but a read-only **shortfall** comparison can diff
an army against the inventory to report what the user still needs to buy.

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
| `Faction` | `factions` | catalog | A top-level army faction (e.g. Space Marines) |
| `Subfaction` | `subfactions` | catalog | A subfaction of one faction (e.g. Ultramarines) |
| `Unit` | `units` | catalog | A datasheet's core stat line |
| `Ability` | `abilities` | catalog | A named ability with a description |
| `Weapon` | `weapons` | catalog | A single weapon profile (ranged or melee) |
| `UnitAbility` | `unit_abilities` | catalog | Association row linking a unit to an ability |
| `UnitWeapon` | `unit_weapons` | catalog | Association row linking a unit to a weapon |
| `User` | `users` | user data | An account that owns armies and an inventory |
| `Army` | `armies` | user data | A named list/roster belonging to a user; holds units |
| `ArmyUnit` | `army_units` | user data | A pointer from an army to a unit, with how many are in the list |
| `UserUnit` | `user_unit` | user data | A pointer from a user to a unit, with how many they physically own (inventory) |

All tables use UUID primary keys and carry `created_at`/`updated_at` timestamps
(via a shared `TimestampMixin`), except the two association tables, which have a
composite primary key and no timestamps.

Relationships:

```
# catalog
Faction ──< Subfaction
Unit >── Faction          (faction_id, required)
Unit >── Subfaction       (subfaction_id, nullable — a per-unit restriction)
Unit >──< Ability         (many-to-many via unit_abilities)
Unit >──< Weapon          (many-to-many via unit_weapons)

# user data (army_units and user_unit are amount-bearing joins to the catalog)
User ──< Army ──< ArmyUnit >── Unit
User ──< UserUnit >── Unit
```

Catalog details:
- `Faction` — unique `name`.
- `Subfaction` — `faction_id` foreign key and a `name`, with
  `UNIQUE(faction_id, name)`.
- `Unit` — `unit_name`, a required `faction_id`, a nullable `subfaction_id`
  (null = available to any subfaction), the stat line, `points`, and a
  `keywords` list (stored as JSON). Abilities and weapons attach through the two
  association tables, so the same datasheet (e.g. a bolt rifle) can be shared
  across many units.
- `Ability` — `name` + `description`.
- `Weapon` — `name`, `category` (`CHECK category IN ('range','melee')`), a
  `keywords` list (JSON), a nullable `range_inches` (null = melee), and the
  weapon stats. One firing profile per row; a multi-profile weapon is several
  rows.

User-data details:
- `User` — unique `username` and unique `email`, plus `password_hash`.
- `Army` — `owner_user_id` foreign key, a `name`, optional `description`, a
  required `faction_id`, and a nullable `subfaction_id`. This is the unit that
  becomes a roster.
- `ArmyUnit` — carries `army_id`, `unit_id`, and `amount` (how many in the list),
  with `UNIQUE(army_id, unit_id)` (one row per unit type per army) and
  `CHECK (amount >= 0)`. Armies are independent of inventory — an `ArmyUnit` may
  reference a unit the user doesn't own, or exceed the owned amount.
- `UserUnit` — carries `owner_user_id`, `unit_id`, and `amount` (how many owned),
  with `UNIQUE(owner_user_id, unit_id)` and `CHECK (amount >= 0)`. This is the
  flat "what I own" inventory.

Deletes cascade down the ownership hierarchy and protect the catalog. Deleting a
`User` removes their armies and inventory; deleting an `Army` removes its
`army_units`; deleting a `Faction` removes its subfactions. The catalog-facing
foreign keys (`units`/`armies` → factions/subfactions, and the `unit_id` on
`army_units`/`user_unit`) use the default RESTRICT, so you can't delete a unit or
faction that's still referenced. Removing a `UserUnit` touches no army — a list
can keep referencing a unit the user just sold.

Unit stat line maps to the datasheet: `movement` (M), `toughness` (T),
`armor_save` (Sv), `wounds` (W), `invulnerable_save` (nullable Inv),
`leadership` (Ld), `objective_control` (OC), plus `points`.

Weapon maps to the weapon row: `range_inches` (nullable; null = melee),
`attacks` (string, because it can be dice notation like "D6"), `weapon_skill`
(BS for ranged / WS for melee), `strength` (S), `armor_piercing` (AP), `damage`
(string, D), and `category` (`range`/`melee`).

### Connection (`connection.py`)

`get_engine()` creates a single engine on first use (not at import time),
loading `.env` and reading `DATABASE_URL` only then — so importing the module
never requires a database. `get_session()` is a FastAPI-style dependency that
yields a `Session` and closes it afterwards.

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
| `UserService` | planned (tests written) | `create_user(username, email, password_hash)` (`ValueError` on duplicate username/email), `get_user(user_id)` |
| `UnitService` | planned (tests written) | catalog: `create_unit(...)`, `get_unit(unit_id)`, `list_units()` |
| `ArmyService` | planned (tests written) | `create_army`, `get_army`, `list_armies`, `delete_army`, `add_unit`, `set_amount`, `remove_unit`, `list_army_units`, `shortfall` |
| `InventoryService` | planned (tests written) | `add_unit`, `set_amount`, `remove_unit`, `list_inventory` |

`UserService`:
- `create_user(username, email, password_hash)` — `ValueError` if the username
  or email is already taken.
- `get_user(user_id)` — `LookupError` if not found.

`UnitService` manages the catalog:
- `create_unit(faction_id, unit_name, <stats>, points, invulnerable_save=None,
  subfaction_id=None, keywords=None)` — `LookupError` if the faction doesn't
  exist.
- `get_unit(unit_id)` — `LookupError` if not found.
- `list_units()` — all catalog units (filter/limit/offset later).

`ArmyService` manages a user's armies and the units inside them:
- `create_army(user_id, name, faction_id, subfaction_id=None, description=None)`
  — `LookupError` if the user or faction doesn't exist.
- `get_army(army_id)` / `list_armies(user_id)` / `delete_army(army_id)` —
  `LookupError` if the army doesn't exist; delete cascades to the army's
  `army_units`.
- `add_unit(army_id, unit_id, amount=1)` — validates the army and unit exist
  (else `LookupError`), then **upserts**: increment `amount` if the unit is
  already in the army, otherwise create the row. Does **not** check inventory —
  armies are aspirational.
- `set_amount(army_id, unit_id, amount)` — absolute set; `amount < 1` raises
  `ValueError` (use `remove_unit`).
- `remove_unit(army_id, unit_id)` — delete the entry.
- `list_army_units(army_id)` — the army's entries joined with their `Unit`.
- `shortfall(army_id)` — read-only diff of the army against its owner's
  inventory: for each unit, `need = max(0, amount_in_list - amount_owned)`.
  Returns the units that are short (with their owned/needed counts) so the
  caller can show "what you still need to buy."

`InventoryService` manages the units a user owns (the flat inventory), keyed on
`user_id`:
- `add_unit(user_id, unit_id, amount=1)` — validates the user and unit exist
  (else `LookupError`), then **upserts** the owned amount.
- `set_amount(user_id, unit_id, amount)` — absolute set; `amount < 1` raises
  `ValueError` (use `remove_unit`).
- `remove_unit(user_id, unit_id)` — delete the inventory entry.
- `list_inventory(user_id)` — entries joined with their `Unit`.

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
| POST | `/units` | create a unit (admin/seed) | to do |
| GET | `/units/{id}` | get one unit (stats, keywords, linked weapons + abilities) | to do |
| GET | `/units` | list units; query params for faction filter, limit, offset | to do |
| PATCH | `/units/{id}` | update fields on a unit (admin) | to do |
| DELETE | `/units/{id}` | delete a unit (admin) | to do |
| POST | `/units/{id}/weapons` | link a weapon to a unit (admin) | to do |
| POST | `/units/{id}/abilities` | link an ability to a unit (admin) | to do |
| GET | `/factions` | list factions with their subfactions | to do |
| POST | `/factions`, `/subfactions` | create catalog factions/subfactions (admin/seed) | to do |

**Users, inventory & armies** — the user-facing flow. Routes nest under the
user: the inventory and armies belong to a user, and a unit entry belongs to the
inventory or to an army.

| Method | Path | Action | Status |
|---|---|---|---|
| POST | `/users` | create a user | to do |
| GET | `/users/{id}` | get a user | to do |
| GET | `/users/{id}/inventory` | list owned units + amounts (nested `Unit_Read` + `amount`) | to do |
| POST | `/users/{id}/inventory` | add an owned unit — body `{unit_id, amount}`, upserts | to do |
| PATCH | `/users/{id}/inventory/{unit_id}` | set absolute owned amount | to do |
| DELETE | `/users/{id}/inventory/{unit_id}` | remove a unit from inventory | to do |
| POST | `/users/{id}/armies` | create an army — body `{name, faction_id, subfaction_id?, description?}` | to do |
| GET | `/users/{id}/armies` | list the user's armies | to do |
| GET | `/users/{id}/armies/{army_id}` | get one army with its units + amounts (nested `Unit_Read` + `amount`) | to do |
| PATCH | `/users/{id}/armies/{army_id}` | rename/update an army | to do |
| DELETE | `/users/{id}/armies/{army_id}` | delete an army (cascade its `army_units`) | to do |
| GET | `/users/{id}/armies/{army_id}/shortfall` | diff the army against inventory — units short and how many to buy | to do |
| POST | `/users/{id}/armies/{army_id}/units` | add a unit — body `{unit_id, amount}`, upserts | to do |
| PATCH | `/users/{id}/armies/{army_id}/units/{unit_id}` | set absolute amount | to do |
| DELETE | `/users/{id}/armies/{army_id}/units/{unit_id}` | remove a unit from the army | to do |

Read schemas nest the hierarchy:
- `UserUnit_Read` and `ArmyUnit_Read` both embed the existing `Unit_Read` and add
  `amount`.
- `Army_Read` is `{id, name, faction_id, units: [ArmyUnit_Read]}` — a full roster.
- `User_Read` is the account (`{id, username, email}`); the inventory and armies
  are fetched via their own routes, or summarized as `{..., army_count,
  inventory_count}`.
- `Shortfall_Read` is a list of `{unit: Unit_Read, in_list, owned, need}` rows.

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
`units` row, so every army and inventory entry pointing at it sees the new stats
automatically — which is the desired behaviour. ("Stats as of when I added it"
would need a `version` on `Unit` plus a version stored on the entry; out of
scope for now.)

## Authentication (future)

The schema is auth-ready: a real `users` table with a `password_hash` column,
and FK integrity from `armies` / `user_unit` down to the user. For now endpoints
take `user_id` as a path param and there is no login. When auth lands, add an
`/auth` router (register/login → JWT) and a "current user" dependency that
replaces the `user_id` path param on the user routes — the army/inventory logic
itself doesn't change.

## Testing

Tests live in a flat `tests/` directory:

```
tests/
  conftest.py            # in-memory SQLite engine + session fixture + object factories
  test_service_user.py
  test_service_unit.py
  test_service_army.py
  test_service_inventory.py
  # test_api_*.py — added once the API layer exists
```

Service tests to cover: create a user (duplicate username/email → `ValueError`,
missing → `LookupError`); create a catalog unit (unknown faction → `LookupError`),
get/list units; create an army (unknown user → `LookupError`), list and delete
armies; add a unit to an army or to inventory, add the same unit twice increments
the amount, set the amount to a new value, remove a unit; `set_amount < 1` →
`ValueError`; adding a unit to a nonexistent army/user or a nonexistent unit →
`LookupError`; an army may reference a unit the user doesn't own (no rejection);
deleting an army cascades its `army_units`; deleting an inventory entry leaves
armies untouched; `shortfall` reports `need = max(0, in_list - owned)` and is
empty when inventory covers the list.

- Service tests run against an in-memory SQLite database (one fresh schema per
  test, foreign keys enforced) so SQL actually executes.
- API tests (later) will use FastAPI's `TestClient` with the service dependency
  overridden to use the test session.
- Run with `pytest` (or `make test`).

## Roadmap

1. ✓ Full schema in `models.py` (factions, subfactions, units, abilities,
   weapons, users, armies, inventory) + an initial Alembic migration.
2. ✓ Service tests written (red) for `UserService` / `UnitService` /
   `ArmyService` / `InventoryService`.
3. Implement the four services to turn the tests green — session-injected,
   raising `LookupError` / `ValueError` per the contracts.
4. Make `connection.py` lazy so importing a service doesn't require
   `DATABASE_URL` (the engine/URL check moves into `get_session()`).
5. Add `app/main.py` and the API routers (units, users, inventory, armies),
   with `*_Create`/`*_Read` schemas and the service-exception → HTTP mapping.
6. API tests with FastAPI's `TestClient`.
7. Seed script to load the real datasheet catalog.
8. Roster features on `Army`: points limit/total, list validation, the
   `shortfall` endpoint.
9. Authentication: `/auth` router (register/login → JWT) + current-user dependency.
