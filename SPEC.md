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

Layout:

```
app/
  main.py                # FastAPI app (entry point)
  api/                   # routers + *_Create/*_Read schemas (one module per resource)
  core/
    services/            # one <Thing>Service per file (business logic)
    db/
      models.py          # SQLModel tables
      connection.py      # engine + session
      alembic/           # migrations (versions/ + env.py)
tests/                   # pytest suite (service tests; API tests to come)
Makefile                 # dev/db commands (see Development)
requirements.txt, requirements-dev.txt
```

## Development

Python 3.12 in a pyenv virtualenv named in `.python-version`
(`warhammer-unit-env`). `DATABASE_URL` must be set — it lives in `.env`, which is
gitignored. Common tasks run through the Makefile:

| Command | What it does |
|---|---|
| `make setup` | create the venv, install deps, create the DB, run migrations |
| `make install` / `make install-dev` | install runtime / dev+test dependencies |
| `make db-setup` | create the Postgres role + database (idempotent) |
| `make migrate` | apply Alembic migrations (`upgrade head`) |
| `make migrate-fresh` | drop, recreate, and re-migrate the dev DB (destructive) |
| `make test` | run the pytest suite |
| `uvicorn app.main:app --reload` | run the API locally |

Dependencies are pinned in `requirements.txt` (runtime) and
`requirements-dev.txt` (adds `pytest`/`pytest-cov`). Core stack: FastAPI,
SQLModel, SQLAlchemy, Alembic, psycopg2, Pydantic, python-dotenv.

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
  required `faction_id`, a nullable `subfaction_id`, and an optional
  `points_limit` (`CHECK (points_limit IS NULL OR points_limit >= 0)`). This is
  the unit that becomes a roster; its points *total* is computed from its units,
  not stored.
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

`get_engine()` builds one cached engine (one per process, via `lru_cache`) on
first use — not at import time — so importing this module (or anything that
imports it) never touches a database. The `DATABASE_URL` (e.g.
`postgresql+psycopg2://user:pass@host:port/db`) lives in `.env`, which is
gitignored; `get_engine()` calls `load_dotenv()` and reads it only when a session
is first requested, raising `RuntimeError` if it's unset. The engine is created
with `pool_pre_ping=True` so stale or dropped pooled connections are detected and
replaced. Both the app and Alembic (`alembic/env.py`) read the same
`DATABASE_URL`, so migrations and the running app always target one database.

Getting a session depends on the caller:

- **Requests** use `get_session()` as a FastAPI dependency
  (`Depends(get_session)`) — it yields a `Session` and closes it after the
  request.
- **Scripts / seed code** use `Session(get_engine())` directly.
- **Tests** build their own in-memory SQLite engine and inject the session, so
  they never call `get_session()` and never need `DATABASE_URL`.

This laziness is deliberate: services receive an injected session rather than
calling `get_session()` themselves, and tests import models/services without a
live database — the reason the engine can't be created at import time.

### Migrations (`app/core/db/alembic/`)

Schema changes are made by editing `models.py`, then autogenerating and applying
a migration:

```
alembic revision --autogenerate -m "describe the change"   # generate
make migrate         # apply (alembic upgrade head)
make migrate-fresh   # drop, recreate, and re-migrate the dev DB (destructive)
```

Migrations live in `app/core/db/alembic/versions/`; `env.py` loads `.env` and
points Alembic at the same `DATABASE_URL` as the app. Never edit the database
schema by hand.

## Service layer (`app/core/services/`)

One service class per aggregate root, named `service_<thing>.py` containing
`<Thing>Service`. Each service is given a `Session` (constructor injection) and
exposes CRUD methods.

| Service | Status | Methods |
|---|---|---|
| `UserService` | implemented (+ tests) | `create_user(username, email, password_hash)` (`ValueError` on duplicate username/email), `get_user(user_id)` |
| `UnitService` | implemented (+ tests) | units: `create_unit`, `get_unit`, `list_units`, `update_unit`, `delete_unit`, `create_weapon`, `create_ability`, `link_weapon`, `link_ability`; catalog reference: `list_factions`, `create_faction`, `create_subfaction` |
| `ArmyService` | implemented (+ tests) | `create_army`, `get_army`, `list_armies`, `update_army`, `delete_army`, `add_unit`, `set_amount`, `remove_unit`, `list_army_units`, `shortfall`, `points_total`, `validate` |
| `InventoryService` | implemented (+ tests) | `add_unit`, `set_amount`, `remove_unit`, `list_inventory` |

`UserService`:
- `create_user(username, email, password_hash)` — `ValueError` if the username
  or email is already taken.
- `get_user(user_id)` — `LookupError` if not found.

`UnitService` manages the catalog (units, factions, and their weapons/abilities):
- `create_unit(faction_id, unit_name, <stats>, points, invulnerable_save=None,
  subfaction_id=None, keywords=None)` — `LookupError` if the faction (or the
  given subfaction) doesn't exist.
- `get_unit(unit_id)` — `LookupError` if not found.
- `list_units()` — all catalog units (filter/limit/offset later).
- `update_unit(unit_id, **fields)` — partial update; `ValueError` on an unknown
  field, `LookupError` if the unit (or a new faction/subfaction) doesn't exist.
- `delete_unit(unit_id)` — `LookupError` if not found.
- `create_weapon(name, category, attacks, weapon_skill, strength,
  armor_piercing, damage, range_inches=None, keywords=None)` — create a weapon
  profile; `ValueError` if `category` isn't `range`/`melee` (`null` range = melee).
- `create_ability(name, description)` — create an ability.
- `link_weapon(unit_id, weapon_id)` / `link_ability(unit_id, ability_id)` —
  attach an existing weapon/ability to a unit (idempotent); `LookupError` if
  either doesn't exist.
- `list_factions()`; `create_faction(name)` (`ValueError` on duplicate);
  `create_subfaction(faction_id, name)` (`LookupError` on unknown faction,
  `ValueError` on duplicate for that faction).

`ArmyService` manages a user's armies and the units inside them:
- `create_army(user_id, name, faction_id, subfaction_id=None, description=None,
  points_limit=None)` — `LookupError` if the user or faction doesn't exist.
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
- `points_total(army_id)` — computed points cost: `sum(unit.points × amount)`
  over the army's units.
- `validate(army_id)` — read-only legality check returning a
  `ValidationReport{ok, points_total, points_limit, issues}`. Each issue has a
  `kind`, a `detail`, and the offending `unit` where relevant:
  - `over_points` — `points_limit` is set and the total exceeds it (Tier 1).
  - `wrong_faction` — a unit's `faction_id` ≠ the army's (Tier 2).
  - `wrong_subfaction` — a unit's `subfaction_id` restriction ≠ the army's
    `subfaction_id` (Tier 2; a unit's `null` subfaction = usable by any).
  `ok` is true when there are no issues. Datasheet count limits and detachment
  rules are out of scope for now.

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

One router module per resource; each is backed by one service and defines its
own request/response schemas (`*_Create`, `*_Read`) so internal model fields
aren't exposed accidentally. All ids in paths and schemas are UUIDs.

Router modules (each mounted in `app/main.py` with `app.include_router(...)`):

| Module | Backing service | Resource |
|---|---|---|
| `app/api/unit.py` | `UnitService` | catalog units |
| `app/api/faction.py` | `UnitService` (catalog) | catalog factions, subfactions, weapons & abilities |
| `app/api/user.py` | `UserService` | users |
| `app/api/inventory.py` | `InventoryService` | a user's inventory (`user_unit`) |
| `app/api/army.py` | `ArmyService` | a user's armies and their units |

Success status codes follow REST conventions: `POST` create → **201**, `DELETE`
→ **204**, `GET`/`PATCH` → **200**. The inventory/army "add unit" `POST`s upsert,
so they return **201** when they create the row and **200** when they increment
an existing one.

Routes below are implemented. The **Backing method** column names the service
call each route makes.

**Catalog** — reads are public; writes are admin/seed only (see "Populating the
catalog"), not part of the normal user flow.

| Method | Path | Action | Backing method | Status |
|---|---|---|---|---|
| POST | `/units` | create a unit (admin/seed) | `UnitService.create_unit` | done |
| GET | `/units/{unit_id}` | get one unit (stats, keywords, linked weapons + abilities) | `UnitService.get_unit` | done |
| GET | `/units` | list units; query params for faction filter, limit, offset | `UnitService.list_units` | done |
| PATCH | `/units/{unit_id}` | update fields on a unit (admin) | `UnitService.update_unit` | done |
| DELETE | `/units/{unit_id}` | delete a unit (admin) | `UnitService.delete_unit` | done |
| POST | `/units/{unit_id}/weapons` | link a weapon to a unit (admin) | `UnitService.link_weapon` | done |
| POST | `/units/{unit_id}/abilities` | link an ability to a unit (admin) | `UnitService.link_ability` | done |
| GET | `/factions` | list factions with their subfactions | `UnitService.list_factions` | done |
| POST | `/factions`, `/subfactions` | create catalog factions/subfactions (admin/seed) | `UnitService.create_faction` / `create_subfaction` | done |
| POST | `/weapons` | create a weapon (admin/seed) | `UnitService.create_weapon` | done |
| POST | `/abilities` | create an ability (admin/seed) | `UnitService.create_ability` | done |

**Users, inventory & armies** — the user-facing flow. Routes nest under the
user: the inventory and armies belong to a user, and a unit entry belongs to the
inventory or to an army.

| Method | Path | Action | Backing method | Status |
|---|---|---|---|---|
| POST | `/users` | create a user | `UserService.create_user` | done |
| GET | `/users/{user_id}` | get a user | `UserService.get_user` | done |
| GET | `/users/{user_id}/inventory` | list owned units + amounts (nested `Unit_Read` + `amount`) | `InventoryService.list_inventory` | done |
| POST | `/users/{user_id}/inventory` | add an owned unit — body `InventoryAdd`, upserts | `InventoryService.add_unit` | done |
| PATCH | `/users/{user_id}/inventory/{unit_id}` | set absolute owned amount — body `AmountSet` | `InventoryService.set_amount` | done |
| DELETE | `/users/{user_id}/inventory/{unit_id}` | remove a unit from inventory | `InventoryService.remove_unit` | done |
| POST | `/users/{user_id}/armies` | create an army — body `Army_Create` | `ArmyService.create_army` | done |
| GET | `/users/{user_id}/armies` | list the user's armies | `ArmyService.list_armies` | done |
| GET | `/users/{user_id}/armies/{army_id}` | get one army with its units + amounts (nested `Unit_Read` + `amount`) | `ArmyService.get_army` + `list_army_units` | done |
| PATCH | `/users/{user_id}/armies/{army_id}` | rename/update an army — body `Army_Update` | `ArmyService.update_army` | done |
| DELETE | `/users/{user_id}/armies/{army_id}` | delete an army (cascade its `army_units`) | `ArmyService.delete_army` | done |
| GET | `/users/{user_id}/armies/{army_id}/shortfall` | diff the army against inventory — units short and how many to buy | `ArmyService.shortfall` | done |
| GET | `/users/{user_id}/armies/{army_id}/validate` | check the list's legality (points vs limit, faction/subfaction) | `ArmyService.validate` | done |
| POST | `/users/{user_id}/armies/{army_id}/units` | add a unit — body `ArmyUnitAdd`, upserts | `ArmyService.add_unit` | done |
| PATCH | `/users/{user_id}/armies/{army_id}/units/{unit_id}` | set absolute amount — body `AmountSet` | `ArmyService.set_amount` | done |
| DELETE | `/users/{user_id}/armies/{army_id}/units/{unit_id}` | remove a unit from the army | `ArmyService.remove_unit` | done |

Request schemas (`*_Create` plus the small add/patch bodies):
- `Unit_Create` — `{faction_id, unit_name, movement, toughness, armor_save,
  wounds, leadership, objective_control, points, invulnerable_save?,
  subfaction_id?, keywords?}`.
- `Faction_Create` — `{name}`; `Subfaction_Create` — `{faction_id, name}`.
- `Weapon_Create` — `{name, category, attacks, weapon_skill, strength,
  armor_piercing, damage, range_inches?, keywords?}`; `Ability_Create` —
  `{name, description}`.
- `User_Create` — `{username, email, password_hash}`. The `password_hash` field
  is a temporary placeholder: once auth lands this becomes `{username, email,
  password}` and the server hashes it, never accepting a client-supplied hash
  (see "Authentication & authorization").
- `Army_Create` — `{name, faction_id, subfaction_id?, description?, points_limit?}`;
  `Army_Update` — the same fields, all optional.
- `InventoryAdd` / `ArmyUnitAdd` — `{unit_id, amount}` (`amount` defaults to 1).
- `AmountSet` (the `PATCH` set-amount body) — `{amount}`.

Read schemas nest the hierarchy:
- `Unit_Read` — the catalog datasheet: `{id, unit_name, faction_id,
  subfaction_id, movement, toughness, armor_save, wounds, invulnerable_save,
  leadership, objective_control, points, keywords, weapons: [...],
  abilities: [...]}`.
- `Faction_Read` — `{id, name, subfactions: [{id, name}]}`.
- `Weapon_Read` — `{id, name, category, keywords, range_inches, attacks,
  weapon_skill, strength, armor_piercing, damage}`; `Ability_Read` —
  `{id, name, description}`. (These are also the shapes embedded in `Unit_Read`.)
- `UserUnit_Read` and `ArmyUnit_Read` both embed `Unit_Read` and add `amount`.
- `Army_Read` is `{id, name, faction_id, subfaction_id, points_limit,
  points_total, units: [ArmyUnit_Read]}` — a full roster (`points_total` is
  computed).
- `User_Read` is `{id, username, email}`. A user's armies and inventory are
  fetched via their own list routes, not embedded here.
- `Shortfall_Read` is a list of `{unit: Unit_Read, in_list, owned, need}` rows.
- `Validation_Read` is `{ok, points_total, points_limit, issues: [{kind, detail,
  unit: Unit_Read?}]}` — the `validate` report.

Error mapping at the API layer:

| Service exception | HTTP status |
|---|---|
| `LookupError` (not found) | 404 |
| `ValueError` / `TypeError` (bad input) | 400 |
| Pydantic validation failure | 422 (FastAPI automatic) |

### App entry point (`app/main.py`)

`app/main.py` exists as a bare-bones app: the `FastAPI()` instance plus a
`GET /health` liveness check. Still to add — the routers
(`app.include_router(...)`) and the service-exception → HTTP handlers. Run
locally with:

```
uvicorn app.main:app --reload
```

## Populating the catalog

Users never create datasheets, so the catalog is filled out of band:

1. A **seed script** (`scripts/seed_datasheets.py`) that bulk-inserts datasheets
   from a JSON/CSV source. Run once, re-run when GW publishes a dataslate.
2. Or treat the catalog write routes (`POST /units`, etc.) as **admin-only**
   ingestion, gated by the admin role introduced in "Authentication &
   authorization."

Start with the seed script. Editing a datasheet updates the single shared
`units` row, so every army and inventory entry pointing at it sees the new stats
automatically — which is the desired behaviour. ("Stats as of when I added it"
would need a `version` on `Unit` plus a version stored on the entry; out of
scope for now.)

## Authentication & authorization (future)

The schema is auth-ready: a real `users` table with a `password_hash` column,
and FK integrity from `armies` / `user_unit` down to the user. For now endpoints
take `user_id` as a path param and there is no login.

**Passwords.** Hashing lives in a dedicated `app/core/security.py`:
`hash_password` / `verify_password` using **bcrypt via `passlib`**. The server
always hashes a **raw password** — a client-supplied hash is never accepted or
stored. (Until auth lands, `User_Create.password_hash` is a temporary
placeholder.)

**Tokens.** Login returns a **JWT** (`create_access_token` / decode, also in
`security.py`): subject = the user's id, algorithm **HS256**, signed with a
`SECRET_KEY` env var and expiring after `ACCESS_TOKEN_EXPIRE_MINUTES`.

**Dependencies to add:** `passlib`, `bcrypt`, `python-jose`, `cryptography`.

**When auth lands:**
- Add an `/auth` router (register → hash the password; login → verify and return
  a JWT).
- Add a "current user" dependency that decodes the JWT and replaces the
  `user_id` path param on the user routes — the army/inventory logic itself
  doesn't change.

**Authorization** (distinct from authentication):
- **Own-data only** — a user may read/write only *their own* armies and
  inventory; the current-user dependency, not a path param, decides whose data
  is touched.
- **Admin role** — catalog writes (`POST/PATCH/DELETE /units`, `/factions`,
  etc.) require an admin; see "Populating the catalog."

## Testing

Tests live in a flat `tests/` directory:

```
tests/
  conftest.py            # in-memory SQLite engine + session fixture + object factories
  test_service_user.py
  test_service_unit.py
  test_service_army.py
  test_service_inventory.py
  # test_api_*.py — added with the API layer (see below)
```

`conftest.py` provides the `session` fixture plus object factories
(`make_user`, `make_faction`, `make_subfaction`, `make_unit`, `make_army`) that
build valid rows so each test only spells out what it cares about.

The **service tests are implemented and green.** They cover: create a user
(duplicate username/email → `ValueError`, missing → `LookupError`); create a
catalog unit (unknown faction → `LookupError`), get/list units; create an army
(unknown user → `LookupError`), list and delete armies; add a unit to an army or
to inventory, add the same unit twice increments the amount, set the amount to a
new value, remove a unit; `set_amount < 1` → `ValueError`; adding a unit to a
nonexistent army/user or a nonexistent unit → `LookupError`; an army may
reference a unit the user doesn't own (no rejection); deleting an army cascades
its `army_units`; deleting an inventory entry leaves armies untouched;
`shortfall` reports `need = max(0, in_list - owned)` and is empty when inventory
covers the list.

API tests come with the API layer — one file per router (`test_api_user.py`,
`test_api_unit.py`, `test_api_inventory.py`, `test_api_army.py`), plus
`test_api_auth.py` and authorization tests once auth lands (a user can't read
another user's armies; a non-admin can't write the catalog).

- Service tests run against an in-memory SQLite database (one fresh schema per
  test, foreign keys enforced) so SQL actually executes. This is equivalent to
  Postgres for our schema (UUIDs, JSON, CHECKs, and cascades all behave), though
  Postgres-specific behavior isn't exercised.
- API tests use FastAPI's `TestClient` with the `get_session` dependency
  overridden (`app.dependency_overrides[get_session] = ...`) so routers run
  against the test session.
- Run with `pytest` (or `make test`); add `--cov` for coverage (`pytest-cov`
  is installed).

## Roadmap

1. ✓ Full schema in `models.py` (factions, subfactions, units, abilities,
   weapons, users, armies, inventory) + an initial Alembic migration.
2. ✓ Service tests written for `UserService` / `UnitService` / `ArmyService` /
   `InventoryService`.
3. ✓ Implemented the four services (session-injected, `LookupError` /
   `ValueError` per the contracts) — the service suite is green.
4. ✓ Made `connection.py` lazy (engine created on first use; imports no longer
   require `DATABASE_URL`).
5. ✓ API routers (units, faction, users, inventory, armies) mounted on
   `app/main.py`, with `*_Create`/`*_Read` schemas, the `get_session`
   dependency, and the service-exception → HTTP handlers. The previously
   "to add" service methods (`update_unit`, `delete_unit`, `link_weapon`,
   `link_ability`, `create_faction`, `create_subfaction`, `update_army`) were
   added.
6. ✓ API tests with FastAPI's `TestClient` (dependency-overridden session), one
   file per router.
7. Add `POST /weapons` and `POST /abilities` routes (backed by
   `create_weapon`/`create_ability`) so the full catalog is enterable over
   HTTP. Also settle the upsert `POST`s' 201-vs-200 status (currently always
   201).
8. Seed script to load the real datasheet catalog.
9. Roster features on `Army`: `points_limit` + computed `points_total`, and
   list validation — Tier 1 (points vs limit) and Tier 2 (faction/subfaction).
   Datasheet count limits and detachment rules are a later follow-up.
10. Authentication & authorization: `app/core/security.py` (bcrypt hashing +
    JWT), the `passlib`/`bcrypt`/`python-jose`/`cryptography` deps, an `/auth`
    router, a current-user dependency (own-data enforcement), and an admin role
    for catalog writes.
