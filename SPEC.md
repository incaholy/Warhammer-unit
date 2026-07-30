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
gitignored; auth also reads `SECRET_KEY` (JWT signing — set a real one in
production) and optionally `ACCESS_TOKEN_EXPIRE_MINUTES` from there. Common tasks
run through the Makefile:

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
| `Faction` | `factions` | catalog | A top-level grouping / grand alliance (Imperium, Xenos, Chaos, Space Marines) |
| `Subfaction` | `subfactions` | catalog | The specific army beneath a faction (e.g. Xenos → Tyranids) |
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
- `Faction` — unique `name`, **restricted to the canonical `FactionName` enum**
  (`Imperium`, `Xenos`, `Chaos`, `Space Marines`; defined in `models.py`).
  Factions are a fixed, known set, so this prevents an accidental junk faction
  from a misspelling. The DB column stays a plain `str` — the enum lives in code,
  so changing the set needs no migration. The specific army (Tyranids, Necrons,
  Death Guard, …) is a `Subfaction`, not a faction.
- `Subfaction` — `faction_id` foreign key and a `name`, with
  `UNIQUE(faction_id, name)`. The name is **restricted to the armies allowed
  under its parent faction**, defined in the `FACTION_SUBFACTIONS` map in
  `models.py` (`faction → its armies`, e.g. `Xenos → (Aeldari, Necrons, Orks,
  …)`). `create_subfaction` rejects any name not listed under the given faction,
  which catches both misspellings and wrong-parent pairings (e.g. Ultramarines
  under Xenos). Extend the map to add an army; no migration needed (the DB column
  stays a `str`).
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
can keep referencing a unit the user just sold. (Deleting a referenced row is
guarded at the service layer to raise `ConflictError` → 409 rather than a raw
`IntegrityError` → 500; any `IntegrityError` that still slips through is caught
by the API-layer backstop as a generic 409.)

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
| `AuthService` | implemented (+ tests) | `register(username, email, password)` (hashes, delegates to `UserService`), `authenticate(identifier, password)` (username/email + password → `User` or `None`) |
| `UserService` | implemented (+ tests) | `create_user(username, email, password_hash)` (`ConflictError` on duplicate username/email), `get_user(user_id)` |
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
- `list_units(faction_id=None, subfaction_id=None, q=None, limit=50, offset=0)`
  — catalog units, optionally filtered (`faction_id`/`subfaction_id` exact,
  `q` = case-insensitive name search) and paged; ordered by name for stable
  paging.
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
- Bad input raises `ValueError` via the typed `*ValidationError`, with a
  descriptive message. Don't raise a bare `TypeError` for bad input — the API
  layer treats an unexpected `TypeError` as a bug (→ 500), not a client error.
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
| `app/api/auth.py` | `AuthService` | register / login (public) |
| `app/api/unit.py` | `UnitService` | catalog units |
| `app/api/faction.py` | `UnitService` (catalog) | catalog factions, subfactions, weapons & abilities |
| `app/api/user.py` | — (current user via JWT) | `GET /me` |
| `app/api/inventory.py` | `InventoryService` | the current user's inventory (`/me/inventory`) |
| `app/api/army.py` | `ArmyService` | the current user's armies (`/me/armies`) |

Success status codes follow REST conventions: `POST` create → **201**, `DELETE`
→ **204**, `GET`/`PATCH` → **200**. The inventory/army "add unit" `POST`s upsert,
so they return **201** when they create the row and **200** when they increment
an existing one.

Routes below are implemented. The **Backing method** column names the service
call each route makes.

Authentication: `/auth/register` and `/auth/login` are public; every `/me/*`
route requires a Bearer JWT (**401** without) and only touches the caller's own
data — identity comes from the token, never a path param. Catalog **reads** are
public; catalog **writes** require an admin (**403** otherwise).

**Catalog** — reads are public; writes require an **admin** (see
"Authentication & authorization" and "Populating the catalog").

| Method | Path | Action | Backing method | Status |
|---|---|---|---|---|
| POST | `/units` | create a unit (admin/seed) | `UnitService.create_unit` | done |
| GET | `/units/{unit_id}` | get one unit (stats, keywords, linked weapons + abilities) | `UnitService.get_unit` | done |
| GET | `/units` | list units; query params: `faction_id`, `subfaction_id`, `q` (name search), `limit` (1–200), `offset` | `UnitService.list_units` | done |
| PATCH | `/units/{unit_id}` | update fields on a unit (admin) | `UnitService.update_unit` | done |
| DELETE | `/units/{unit_id}` | delete a unit (admin) | `UnitService.delete_unit` | done |
| POST | `/units/{unit_id}/weapons` | link a weapon to a unit (admin) | `UnitService.link_weapon` | done |
| POST | `/units/{unit_id}/abilities` | link an ability to a unit (admin) | `UnitService.link_ability` | done |
| GET | `/factions` | list factions with their subfactions | `UnitService.list_factions` | done |
| POST | `/factions`, `/subfactions` | create catalog factions/subfactions (admin/seed) | `UnitService.create_faction` / `create_subfaction` | done |
| POST | `/weapons` | create a weapon (admin/seed) | `UnitService.create_weapon` | done |
| POST | `/abilities` | create an ability (admin/seed) | `UnitService.create_ability` | done |

**Auth & the current user's data** — `/auth/*` is public; every `/me/*` route
requires a JWT and acts on the token-holder's own data (a stranger's `{army_id}`
→ 404 via `get_owned_army`).

| Method | Path | Action | Backing method | Status |
|---|---|---|---|---|
| POST | `/auth/register` | register (public) — body `Register_Create` | `AuthService.register` | done |
| POST | `/auth/login` | login (public, OAuth2 form) — returns a `Token` | `AuthService.authenticate` | done |
| GET | `/me` | the current user | `get_current_user` | done |
| GET | `/me/inventory` | list owned units + amounts (nested `Unit_Read` + `amount`) | `InventoryService.list_inventory` | done |
| POST | `/me/inventory` | add an owned unit — body `InventoryAdd`, upserts | `InventoryService.add_unit` | done |
| PATCH | `/me/inventory/{unit_id}` | set absolute owned amount — body `AmountSet` | `InventoryService.set_amount` | done |
| DELETE | `/me/inventory/{unit_id}` | remove a unit from inventory | `InventoryService.remove_unit` | done |
| POST | `/me/armies` | create an army — body `Army_Create` | `ArmyService.create_army` | done |
| GET | `/me/armies` | list the user's armies | `ArmyService.list_armies` | done |
| GET | `/me/armies/{army_id}` | get one army with its units + amounts (nested `Unit_Read` + `amount`) | `get_owned_army` + `points_total` | done |
| PATCH | `/me/armies/{army_id}` | rename/update an army — body `Army_Update` | `ArmyService.update_army` | done |
| DELETE | `/me/armies/{army_id}` | delete an army (cascade its `army_units`) | `ArmyService.delete_army` | done |
| GET | `/me/armies/{army_id}/shortfall` | diff the army against inventory — units short and how many to buy | `ArmyService.shortfall` | done |
| GET | `/me/armies/{army_id}/validate` | check the list's legality (points vs limit, faction/subfaction) | `ArmyService.validate` | done |
| POST | `/me/armies/{army_id}/units` | add a unit — body `ArmyUnitAdd`, upserts | `ArmyService.add_unit` | done |
| PATCH | `/me/armies/{army_id}/units/{unit_id}` | set absolute amount — body `AmountSet` | `ArmyService.set_amount` | done |
| DELETE | `/me/armies/{army_id}/units/{unit_id}` | remove a unit from the army | `ArmyService.remove_unit` | done |

Request schemas (`*_Create` plus the small add/patch bodies):
- `Unit_Create` — `{faction_id, unit_name, movement, toughness, armor_save,
  wounds, leadership, objective_control, points, invulnerable_save?,
  subfaction_id?, keywords?}`.
- `Faction_Create` — `{name}`, where `name` is a `FactionName` enum value
  (`Imperium`/`Xenos`/`Chaos`/`Space Marines`); anything else is rejected with
  **422**, and the allowed values are published in the OpenAPI schema. The
  service (`create_faction`) re-checks the same set, so the seed/direct-session
  path is guarded too (→ 400). `Subfaction_Create` — `{faction_id, name}`, where
  `name` must be an army allowed under that `faction_id` (see the
  `FACTION_SUBFACTIONS` map). Unlike faction, this can't be a schema-level enum
  (the valid set depends on the chosen faction), so `create_subfaction` validates
  it in the service and rejects a bad pairing with **400**.
- `Weapon_Create` — `{name, category, attacks, weapon_skill, strength,
  armor_piercing, damage, range_inches?, keywords?}`; `Ability_Create` —
  `{name, description}`.
- `Register_Create` — `{username, email, password}` (the `/auth/register` body;
  the server hashes the password, never accepting a client-supplied hash). Login
  uses the OAuth2 password form and returns a `Token` — `{access_token,
  token_type}`.
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

Error mapping at the API layer (see "Custom service errors" for the typed
hierarchy). Only messages the service author wrote (the typed `ServiceError`s)
reach the client; any *unexpected* exception is logged server-side and returned
as a generic body with no internals — never `str(exc)` of an arbitrary builtin:

| Service exception | HTTP status |
|---|---|
| `NotFoundError` (⊂ `LookupError`) | 404 |
| `ConflictError` (⊂ `ValueError`) — duplicate | 409 |
| `*ValidationError` (⊂ `ValueError`) — carries `field` | 400 |
| Pydantic validation failure | 422 (FastAPI automatic) |
| `IntegrityError` (DB-constraint backstop) | 409 — logged, generic body |
| any other unhandled exception | 500 — logged with traceback, generic body |

There are deliberately **no** catch-all `ValueError`/`TypeError`/`LookupError`
handlers: those builtins are raised throughout the stdlib and third-party libs,
so returning their raw message would leak internals, and a `TypeError` (almost
always a bug) would be mislabelled a client `400` instead of a server `500`.

### App entry point (`app/main.py`)

`app/main.py` builds the `FastAPI()` instance, mounts every router
(`app.include_router(...)`), registers the `ServiceError` → HTTP handler, an
`IntegrityError` → 409 backstop, and a catch-all handler that logs unexpected
exceptions and returns a generic 500, and exposes a `GET /health` liveness
check. Run locally with `uvicorn app.main:app --reload` (or `make run`).

### Planned additions (frontend-readiness)

These are small, additive, non-breaking endpoints the frontend/admin UI will
need. **Effort: all S** unless noted.

- **`GET /weapons` and `GET /abilities`** — the catalog is admin-curated and
  linking a weapon to a unit (`POST /units/{id}/weapons`) needs a `weapon_id`,
  but there's currently no way to *list* weapons/abilities to pick from.
  *Plan:* add `list_weapons()` / `list_abilities()` to `UnitService` (a plain
  `select(...)`), then `GET /weapons` / `GET /abilities` routes returning
  `list[Weapon_Read]` / `list[Ability_Read]` (public reads, like the rest of the
  catalog). Mirrors the existing `list_factions` / `GET /factions`.
- **`GET /factions/taxonomy`** — the allowed **faction names** are already in the
  OpenAPI schema (the `FactionName` enum on `Faction_Create`), but the allowed
  **subfactions per faction** live only in the service-side `FACTION_SUBFACTIONS`
  map, so an admin UI has no way to render a subfaction dropdown.
  *Plan:* a read route returning `{faction_name: [allowed subfactions]}` straight
  from the map (no DB) — e.g. `{"Xenos": ["Aeldari", "Necrons", …], …}`. Public.
- **`X-Total-Count` header on `GET /units`** — the list is paged but returns no
  total, so the catalog view can't show "showing 20 of 137."
  *Plan:* add a `UnitService.count_units(**filters)` (a `select(func.count())`
  with the same `where` clauses as `list_units`) and set an `X-Total-Count`
  response header in the route. Chosen over an `{items, total}` envelope because
  it keeps the bare-list body **non-breaking** (see "Custom service errors" for
  the same no-envelope stance).

### Catalog administration

**Implemented (roadmap 19–23).** Admin catalog CRUD is now complete: units,
weapons, and abilities are fully editable, subfactions can be deleted, wrongly-
linked weapons/abilities can be unlinked, and deleting a referenced row returns a
clean **409** instead of a 500. These are all admin-gated routes. A **shared
delete guard** underpins the deletes — `Unit`,
`Subfaction`, and `Faction` are referenced by RESTRICT foreign keys, so before
deleting one, check for references and raise `ConflictError` (→ 409) instead of
letting a raw `IntegrityError` surface as a 500. Weapons/abilities need no guard:
their link rows (`unit_weapons`/`unit_abilities`) cascade.

- **Fix `delete_unit` (bug)** *(S)* — guard against `ArmyUnit`/`UserUnit`
  references → `ConflictError`. Today deleting an in-use unit 500s. No route change
  (409 flows through the `ServiceError` handler).
- **Unlink weapon/ability** *(S)* — `unlink_weapon(unit_id, weapon_id)` /
  `unlink_ability(unit_id, ability_id)` on `UnitService` (idempotent), exposed as
  `DELETE /units/{id}/weapons/{weapon_id}` and `.../abilities/{ability_id}` → 204.
  Mirrors the existing `POST` link routes.
- **Editable weapons + abilities** *(M)* — `update_weapon`/`delete_weapon` and
  `update_ability`/`delete_ability` (partial update; bad `category` or unknown
  field → `UnitValidationError`; 404 if missing), exposed as `PATCH`/`DELETE
  /weapons/{id}` and `/abilities/{id}` (new all-optional `Weapon_Update` /
  `Ability_Update`). The high-value gap — stat/description typos are otherwise
  permanent.
- **Delete a subfaction** *(S)* — `delete_subfaction(id)` (guarded) +
  `DELETE /subfactions/{id}` → 204. No `PATCH`: the name is map-constrained, so
  delete-and-recreate covers a mistake.
- **Factions stay create + list only** — the four `FactionName` values are fixed
  and can't be mistyped (422 blocks that), so edit/delete would add a
  guarded-delete failure mode for no real use case. Out of scope unless needed.

## Custom service errors

**Implemented.** Services raise a typed hierarchy from
`app/core/services/errors.py` instead of bare builtins, so errors carry the
offending **field** and a **duplicate** gets its own **409** (rather than being
lumped into 400). It is **backward-compatible**: each custom error subclasses the
builtin it replaces (`NotFoundError(LookupError)`; the `ValueError` family), and a
single `ServiceError` handler in `app/main.py` maps them by their `status_code` —
chosen over the builtin `LookupError`/`ValueError` handlers because `ServiceError`
precedes those in each subclass's MRO. The builtin handlers stay as fallbacks.
Mirrors `attention-api`.

Two families.

**Shared errors** — cross-cutting, carry `message`, an optional `field`, and a
`status_code`:

- `NotFoundError(LookupError)` — a row doesn't exist. **→ 404** (already, via the
  existing `LookupError` handler, since it subclasses it).
- `ConflictError(ValueError)` — a uniqueness clash: duplicate `username`/`email`,
  duplicate faction name, duplicate subfaction-for-faction. Wants **→ 409**; needs
  its own handler to get 409, else falls back to 400 (it's a `ValueError`).

(Ownership on `/me/armies/{id}` intentionally stays a **404** through
`get_owned_army` to hide existence rather than a 403, so there's no
`ForbiddenError` in the hierarchy today — add one if a case ever needs to reveal
"exists but not yours.")

**Per-service validation errors** — one `ValueError` subclass per service,
constructed as `(field, message)`, rendering `"{field}: {message}"` and exposing
`.field`. They map to **400** (bad request), and their handler adds the offending
`field` to the response body. We keep them at **400**, not 422: 422 is reserved
for FastAPI's *request-shape* validation (a malformed body), whereas these are
semantically-invalid-but-well-formed requests (a business rule failed).

- `UserValidationError` — registration/account rules (empty/oversized fields).
- `UnitValidationError` — catalog input across `UnitService` (unknown updatable
  field; `category` not `range`/`melee`; a faction name outside `FactionName`; a
  subfaction not allowed under its faction).
- `ArmyValidationError` — roster input (`amount < 1` on set, `points_limit < 0`).
- `InventoryValidationError` — inventory input (`amount < 1` on set).

**Naming principle — name an error by how it's *handled*, not where it's raised.**
`attention-api` mixes both styles on purpose, and so do we:

- **Generic** for cross-cutting failures nothing branches on: a `NotFoundError`
  is a not-found regardless of resource (its `message` names the row). Minting
  `UnitNotFoundError`/`ArmyNotFoundError` that all become an identical 404 is
  class-proliferation for no gain.
- **Resource-named** for validation, grouped per service (`UnitValidationError`,
  …) — this is attention's `MessageValidationError` shape, and the `.field`
  carries the specifics.
- **Rule-named** (like attention's `ChallengeAlreadyExistsError`) *only* when a
  failure needs its own status, its own handler, or the frontend must react
  differently. Here every duplicate starts as a generic `ConflictError` (→ 409);
  split out `DuplicateFactionError(ConflictError)` *later* only if the UI must
  tell one duplicate from another. Add specificity when the handling diverges,
  not before.

**API layer.** A single `@app.exception_handler(ServiceError)` in `app/main.py`
builds the response from `exc.status_code` and `exc.field`: the body is
`{"detail": message, "field": field?}` — close to FastAPI's default, i.e. **no
`{data, meta}` envelope** (that was intentionally deferred). It's picked over the
builtin `LookupError`/`ValueError` handlers because Starlette matches handlers by
walking the exception's MRO, where `ServiceError` sits ahead of them. Those
builtin handlers remain as fallbacks for any un-migrated raise.

**Status codes.** `NotFoundError` → 404, `ConflictError` → 409, and the
`*ValidationError` family → 400 (with `field`). Validation stays 400, not 422: 422
is reserved for FastAPI's request-shape validation (a malformed body), whereas
these are well-formed requests that fail a business rule.

**Done.** All four services now raise the typed errors; `errors.py` +
the handler are in place, and the test suite covers the 404/409/400 mapping and
the `field` payload.

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

## Seeding the catalog

**Implemented (machinery); data source is changing.** Migrations create tables,
not rows, so the catalog ships **empty**. `scripts/seed_datasheets.py` bulk-loads
it from `scripts/data/datasheets.json` — which **ships empty**. The seed machinery
is unchanged; what's changing is *how `datasheets.json` gets populated*: rather than
hand-filled, it will be **generated by a scraper** that pulls datasheets from
Wahapedia (see "Scraping the catalog"). The admin API remains available for ad-hoc
edits. The JSON schema is documented in `scripts/data/README.md`.

- **Script** — `scripts/seed_datasheets.py`, run out of band (not an HTTP route).
  It opens a session directly (`Session(get_engine())`, the "scripts" path from
  `connection.py`) and drives the **service layer** (`UnitService`), not raw SQL,
  so the same foreign-key checks, `CHECK` constraints, and validation that guard
  the API also guard the seed. (Matches the "schema changes go through
  models/migrations, never raw SQL" convention.)
- **Data source** — a checked-in `scripts/data/datasheets.json` describing the
  hierarchy: factions → subfactions, shared weapons/abilities, then units with
  their stat line, `points`, `keywords`, and the weapons/abilities they link.
  Keeping the data in a file (not inline in code) makes a new GW dataslate a data
  edit, not a code change.
- **Idempotency** — safe to run repeatedly. The services raise on duplicates
  (`create_faction` → `ValueError`; `UNIQUE(faction_id, name)` on subfactions;
  etc.), so the seed does **get-or-create by natural key**: faction by `name`,
  subfaction by `(faction, name)`, unit by `(faction, unit_name)`, weapon/ability
  by `name`. Existing rows are updated (or skipped), never duplicated — re-running
  after a dataslate patches stats in place, which every army/inventory entry then
  sees (datasheets are shared — see "Populating the catalog").
- **Order of operations** — respects the foreign keys: factions → subfactions →
  weapons + abilities → units → link weapons/abilities to units.
- **Running it** — `make seed` (a thin wrapper over
  `python -m scripts.seed_datasheets`), needing only `DATABASE_URL`. In the
  container world it's a one-shot:
  `docker compose run --rm api python -m scripts.seed_datasheets`. Because the
  script uses a direct session, it needs **no admin user and no auth** — seeding
  is separate from the admin-gated HTTP write routes (those are for ad-hoc edits
  once the app is running).
- **Scope** — ship a **small starter dataset** (a couple of factions and a
  handful of units) so dev, tests, demos, and the frontend have real data
  immediately; entering the full GW catalog is a later data-entry effort, not a
  code one.

## Scraping the catalog (Wahapedia)

**v1 built.** Datasheet content is **scraped from Wahapedia**
(`scripts/scrape_wahapedia.py`, `make scrape`) instead of hand-entered. The primary
source is a faction's **collated datasheets page** — e.g.
`https://wahapedia.ru/wh40k10ed/factions/space-marines/datasheets.html` — which
lists *every* datasheet for a faction on one page (one fetch covers a whole
faction). The scraper emits the *same* `scripts/data/datasheets.json` the seed
consumes, so seeding is a decoupled two-stage pipeline:
**`make scrape` → `datasheets.json` → `make seed` → DB**. Keeping the scrape
separate means the output is reviewable, re-seedable offline, and there's **no live
scraping at deploy**.

**Status.** v1 extracts, per datasheet: **name, the six-stat line (M/T/Sv/W/Ld/OC),
and chapter → subfaction** (verified: parses 276 Space Marine units across 11
chapters, and the output seeds cleanly + idempotently). Confirmed the page is
server-rendered (BeautifulSoup + lxml; no headless browser). It also parses **weapons** (ranged + melee, per-profile rows; inline `.kwb2`
keywords split from the name; AP stored as magnitude; shared weapons deduped and
linked by name — 431 on the SM page), **points** (minimum-size cost from the static
`.PriceTag` table — the `dsPointy` box is JS-filled/empty, but `.PriceTag` is in the
HTML; enhancement/stratagem prices are excluded by requiring an "N models" row),
**unit keywords** (title-cased; the "olKW" column, Cyrillic-`С` class gotcha noted),
and **abilities** (name + description, scoped to the ABILITIES section — the
overloaded `.dsAbility` blocks for composition/points and bare faction/core
references are excluded; 194 on the SM page). So the **full datasheet** is scraped,
and all of it seeds cleanly. **All 23 Wahapedia factions are configured** → our 4
factions + 33 subfactions (≈1558 datasheets scraped). **Remaining limits**: only the
first stat profile of a multi-profile datasheet is read, and same-named units under
one faction collapse on the seed's natural key — now ≈227/1558, because generic
units (Chaos Spawn, Cultists, …) recur across a faction's subfactions and our seed
keys units on `(faction, unit_name)`; keying on `(faction, subfaction, unit_name)`
would keep them (a seed follow-up). The scraped `datasheets.json` and the
`scripts/data/cache/` HTML are **not committed** (GW IP —
personal/dev use); run `make scrape` locally to (re)generate.

**Ground rules (do these first).** Wahapedia is a fan reference built on Games
Workshop's IP. Before scraping: read its `robots.txt` and terms; scrape **politely**
— a low request rate (≈1 request / 2–5 s), a descriptive `User-Agent`, and a
**disk cache** of fetched HTML (e.g. `scripts/data/cache/`) so re-runs don't re-hit
the site. Treat the result as personal/dev use, not redistribution.

**Pieces.**
- **`scripts/scrape_wahapedia.py`** — fetch a faction's collated datasheets page,
  parse every datasheet, and write `datasheets.json`.
- **Page sources** — the **collated `/{faction}/datasheets.html`** is the primary
  target: one request yields all of a faction's datasheets. The per-subfaction
  landing pages (e.g. `.../space-marines/white-scars`) are a *secondary* source,
  used only to tag which datasheets are chapter-specific (see "Normalize").
- **Fetch** — `httpx`/`requests` + the disk cache above; one collated page per
  faction keeps the request count tiny. Inspect the page first: Wahapedia is largely
  server-rendered, so `beautifulsoup4` + `lxml` should suffice; fall back to a
  headless browser (Playwright) *only* if the datasheet content turns out to be
  JS-rendered.
- **Parse → our schema** — map each datasheet card to the seed JSON:
  - stat line `M/T/Sv/W/Ld/OC` (+ optional `Inv`) → `movement`, `toughness`,
    `armor_save`, `wounds`, `leadership`, `objective_control`, `invulnerable_save`
    (strip `"`/`+`; keep `attacks`/`damage` as **strings** for dice like `D6`);
  - Ranged/Melee weapon tables → `Weapon` rows (`category` range/melee, `attacks`,
    `weapon_skill` [BS/WS], `strength`, `armor_piercing`, `damage`, `range_inches`,
    `keywords`);
  - abilities → `Ability` rows (name + text); the keywords line → the unit's
    `keywords`.
- **Normalize to our taxonomy** — the `FACTIONS` map keys a Wahapedia slug to
  `(our faction, fixed subfaction | None)`. There are **two modes**:
  - **Space Marines** (`("Space Marines", None)`) — the one page that mixes armies:
    most datasheets are faction-wide → `subfaction = null` (our model's "any
    subfaction"), and chapter-specific ones get their chapter from the datasheet's
    color-theme code (`CHSA` → Salamanders), via `CHAPTER_CODES`.
  - **Every other faction page** (e.g. `("Xenos", "Tyranids")`) — a whole Wahapedia
    "faction" is a single one of *our* subfactions, so **every** datasheet on the
    page gets that fixed subfaction. (Our four factions are Imperium/Xenos/Chaos/
    Space Marines; the rest — Tyranids, Necrons, Death Guard, … — are subfactions.)
  Adding a faction is one line in `FACTIONS`. The subfaction must be in
  `FACTION_SUBFACTIONS` or the seed rejects it, exactly as `create_subfaction` does.

**Decisions to make.**
- **Points** *(done)* — **minimum-size** points, parsed from the static `.PriceTag`
  table (the `dsPointy` box is JS-filled, but `.PriceTag` values are in the HTML).
  Only rows with an "N models" label count, so enhancement/stratagem prices are
  excluded. Storing per-size points would be a later model change.
- **Fail loud** — validate the scraped JSON against the seed schema before writing,
  so a Wahapedia layout change errors instead of seeding garbage.

**Testing.** Parse against a **saved HTML fixture** checked into `tests/fixtures/`
(no network in tests); only the fetch layer touches the site, exercised manually.

**Dependencies.** Add `httpx` (or `requests`) + `beautifulsoup4` + `lxml` to
`requirements-dev.txt` — this is operator tooling, not runtime. Playwright only if
JS-rendering forces it.

## Authentication & authorization

Implemented. Users register and log in for a JWT; `/me/*` routes are token-scoped
to the caller, and catalog writes require an admin. The pieces:

**Passwords.** Hashing lives in `app/core/security.py`: `hash_password` /
`verify_password` using **bcrypt via `passlib`**. The server always hashes a
**raw password** — a client-supplied hash is never accepted or stored.

**Tokens.** Login returns a **JWT** (`create_access_token` / decode, also in
`security.py`): subject = the user's id, algorithm **HS256**, signed with a
`SECRET_KEY` env var and expiring after `ACCESS_TOKEN_EXPIRE_MINUTES`.

**Token storage (accepted decision).** The frontend stores the JWT in the
browser's `localStorage` (`src/api/client.ts`) and sends it as a `Bearer`
header. Tradeoff: `localStorage` is readable by JavaScript, so any XSS on the
page could exfiltrate the token; the alternative — an `httpOnly` cookie — is not
JS-readable but adds CSRF exposure and complexity. **We accept `localStorage`
for now** because it's the standard SPA pattern, auth is a `Bearer` header (no
cookies → no CSRF surface), this is a small/demo app with limited blast radius,
and tokens expire (`ACCESS_TOKEN_EXPIRE_MINUTES`). Defense-in-depth that keeps
it acceptable: a modest token lifetime, standard XSS hygiene (React escapes by
default; avoid `dangerouslySetInnerHTML`; a CSP header would help), and no
sensitive data beyond the account. **Revisit** — move to an `httpOnly` cookie
with CSRF protection (and add refresh/revocation, roadmap L5) if the app starts
handling sensitive data, grows a real user base, or adds features that widen the
XSS surface.

**Dependencies:** `passlib`, `bcrypt`, `python-jose`, `cryptography`.

**Routing.**
- The `/auth` router: `register` (creates a user, hashing the password) and
  `login` (verifies username/email + password, returns a JWT).
- A `get_current_user` dependency decodes the JWT into the `User`. The
  user-scoped routes are `/me/...` (Option A) — `/me`, `/me/inventory`,
  `/me/armies/...` — taking the id from the token, not the path.

**Authorization** (distinct from authentication):
- **Own-data only** — a user may read/write only *their own* armies and
  inventory; the current-user dependency, not a path param, decides whose data
  is touched. Inventory is fully scoped by the token (`/me/inventory`). The
  nested `{army_id}` routes use a `get_owned_army` dependency that loads the army
  and returns **404** if it isn't the current user's — so a stranger's `army_id`
  reveals nothing (404 hides existence rather than 403).
- **Admin role** — catalog writes (`POST/PATCH/DELETE /units`, `/factions`,
  `/subfactions`, `/weapons`, `/abilities`) require an admin, enforced by the
  `get_current_admin` dependency (**403** otherwise). Admin status is the
  `User.is_admin` boolean (default `false`). Because it defaults false, the
  **first admin** is made out of band (see the bootstrap helper below).

### Planned hardening

- **First-admin bootstrap helper** *(Effort: S)* — replace the manual
  `UPDATE users SET is_admin = true` with a script + `make` target.
  *Plan:* `scripts/make_admin.py` opens a direct session (`Session(get_engine())`,
  the scripts path from `connection.py`), looks the user up by username, sets
  `is_admin = True`, and commits; `LookupError` if the user doesn't exist. Wrap in
  `make create-admin USERNAME=<name>` (not `USER=`, which collides with the shell's
  login-name env var). No auth/HTTP — it's an operator action, like the seed
  script. This unblocks catalog seeding/management. **(Built — roadmap 14.)**
- **Admin promotion via API** *(Built — roadmap 22)* — the *first* admin is
  bootstrapped out of band (above); an existing admin promotes/demotes others via
  `UserService.set_admin(user_id, is_admin)` (`NotFoundError` if missing) behind an
  admin-only `PATCH /users/{id}` `{is_admin}`. Returns `User_Read` (which
  includes `is_admin`). Last-admin-lockout protection was left out for now.
- **Rate limiting on `/auth/*`** *(Effort: M)* — brute-force protection on
  `register`/`login`. *Plan:* add `slowapi` (a Starlette-friendly limiter), a
  keyed-by-IP limit (e.g. 5/min) on the two auth routes, and a 429 handler. Deferred
  — it's a hardening step, not a core-loop blocker; revisit before public deploy.
- **Real `SECRET_KEY` in production** *(Effort: S)* — a dev default is in place;
  production must set a random `SECRET_KEY` (`python -c "import secrets;
  print(secrets.token_urlsafe(32))"`) via the environment. Belongs to the deploy
  checklist, not code.

## Frontend integration

**Planned — not yet built.** How the "Muster" browser UI (Vite/React) will talk
to this API. The connection choice is **Option B: relative URLs + a proxy**, so
CORS is a fallback, not the primary mechanism. Auth is a **Bearer JWT in the
`Authorization` header** (not cookies), so there's no CSRF/`SameSite` concern.

- **Repo layout** *(Effort: S)* — the frontend lives in a `frontend/` subfolder
  (monorepo); the Python backend stays at the repo root untouched. Add `frontend/`
  to `.dockerignore` so it never bloats the API image; the frontend gets its own
  build.
- **Dev — Vite proxy** *(Effort: S)* — the frontend calls relative paths
  (`fetch("/units")`); `vite.config.js`'s `server.proxy` forwards to
  `http://localhost:8000`, so the browser sees one origin and needs no CORS.
- **Prod — reverse proxy** *(Effort: M)* — a small Caddy/nginx compose service
  serves the built static files and forwards `/api` (or the API paths) to the
  `api` service; still one origin, still no CORS.
- **CORS fallback** *(Effort: S)* — add `CORSMiddleware` in `app/main.py` with an
  **env-driven allow-list** (`ALLOWED_ORIGINS`, comma-separated; never `*`), for
  the case where the frontend is genuinely hosted on another origin. `Authorization`
  header allowed; no credentials/cookies. This is the one piece to add on the
  backend before a cross-origin frontend can call it.
- **Typed client from OpenAPI** *(Effort: S)* — generate the frontend's API types
  from `/openapi.json` (e.g. `openapi-typescript`) so `Unit_Read`/`Army_Read`
  stay in sync with `models.py`; a rename on the backend surfaces as a TS error.

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

## Deployment & containerization

**Implemented.** Alongside the bare-metal path (`make run` /
`uvicorn app.main:app --reload` against a locally-installed Postgres from
`make db-setup`), the app now ships a container stack that brings the API and its
database up together with one command (`make docker-up`), keeping dev / test /
prod in parity. Mirrors the sibling `attention-api` layout.

Pieces (all at the repo root):

- **`Dockerfile`** — package the API into an image. Base `python:3.12-slim`,
  `COPY requirements.txt` + `pip install --no-cache-dir -r requirements.txt`
  first (so the dependency layer caches), then `COPY . .`, `EXPOSE 8000`, and
  `CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]`.
  Bind `0.0.0.0`, not `127.0.0.1`, so the port is reachable from outside the
  container.
- **`.dockerignore`** — keep the build context small and secrets out of the
  image: exclude `.venv`/virtualenvs, `__pycache__`, `.pytest_cache`, `.git`,
  and **`.env`** (env comes in at runtime, never baked into the image).
- **`docker-compose.yml`** — the batteries-included local stack, two services:
  - `db` — `postgres:16`, a named volume `postgres-data` for persistence, and
    `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` from the environment; a
    healthcheck (`pg_isready`) so the API can wait on it.
  - `api` — `build: .`, `depends_on: db` (condition `service_healthy`),
    `ports: 8000:8000`, and a `DATABASE_URL` that targets the compose DB by
    **service name** (`postgresql+psycopg2://…@db:5432/…`), not `localhost`.
  `make run` stays the fast bare-metal path; compose is the one-command path.
- **`docker-compose.test.yml`** — an overlay that points the suite at a
  throwaway Postgres (a separate `POSTGRES_DB`, no persistent volume) so
  integration tests run against real Postgres without touching dev data. (The
  current pytest suite uses in-memory SQLite and needs none of this; this is the
  Postgres-parity option for later.)

Cross-cutting concerns:

- **Migrations** — the image ships no schema. `docker-entrypoint.sh` runs
  `alembic upgrade head` on container start (after the `db` healthcheck passes),
  then `exec`s the container command (uvicorn) — so the schema is current before
  traffic is served. Migrations are never baked into the image build.
- **Config** — `DATABASE_URL`, `SECRET_KEY`, and `ACCESS_TOKEN_EXPIRE_MINUTES`
  come from the environment (compose injects them with sensible `${VAR:-default}`
  fallbacks; `.env` stays gitignored and out of the image via `.dockerignore`).
  A real `SECRET_KEY` is required in any non-local run.
- **Makefile** — `make docker-build`, `make docker-up` (build + compose up),
  `make docker-down`, and `make docker-test` (run the suite in a container against
  the throwaway Postgres) sit alongside the bare-metal targets.

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
7. ✓ `POST /weapons` and `POST /abilities` routes (the catalog is fully
   enterable over HTTP), and the upsert `POST`s return 201 on create / 200 on
   increment.
8. Seed script to bulk-load the datasheet catalog — deferred; now tracked in
   "Remaining work" below (it's the top frontend prerequisite). See "Seeding the
   catalog."
9. ✓ Roster features on `Army`: `points_limit` + computed `points_total`, and
   list validation — Tier 1 (points vs limit) and Tier 2 (faction/subfaction).
   Datasheet count limits and detachment rules remain a later follow-up.
10. ✓ Authentication & authorization: `app/core/security.py` (bcrypt + JWT), the
    auth deps, an `/auth` router (register/login), `/me/*` own-data routes with
    `get_owned_army` ownership, and admin-gated catalog writes (`User.is_admin`).
11. ✓ Deployment & containerization: `Dockerfile`, `.dockerignore`,
    `docker-entrypoint.sh` (migrate-then-serve), `docker-compose.yml`
    (API + Postgres, healthcheck) + a `docker-compose.test.yml` overlay, and
    `make docker-*` targets — see "Deployment & containerization."
12. ✓ Custom service errors: a typed exception hierarchy in
    `app/core/services/errors.py` (`NotFoundError`, `ConflictError`, and
    per-service `*ValidationError`) replacing the builtin `LookupError`/`ValueError`
    across all services; a single `ServiceError` handler maps them (409 for
    duplicates, `field` on validation) — see "Custom service errors."

### Remaining work — ordered by ease of implementation

Steps 1–12 above are the build history. What's left, easiest first, each linking
to the section with its plan. The **S** items are small, additive, and mostly
non-breaking; do them to reach "frontend-ready," then the **M**/**L** items.

13. ✓ **CORS** — `CORSMiddleware` in `app/main.py`, allow-list from the
    `ALLOWED_ORIGINS` env var (off when unset). See "Frontend integration."
14. ✓ **First-admin bootstrap helper** — `scripts/make_admin.py` (a testable
    `promote(session, username)` + a CLI) and `make create-admin USERNAME=<name>`;
    unblocks catalog seeding/management. See "Authentication & authorization →
    Planned hardening."
15. ✓ **`GET /weapons` + `GET /abilities`** — list routes so the admin UI can
    pick weapons/abilities to link (`list_weapons`/`list_abilities` on
    `UnitService`). See "API layer → Planned additions."
16. ✓ **`GET /factions/taxonomy`** — exposes `FACTION_SUBFACTIONS` for subfaction
    dropdowns. See "API layer → Planned additions."
17. ✓ **`X-Total-Count` on `GET /units`** — `UnitService.count_units` sets the
    total header for the catalog's "N results" count. See "API layer → Planned
    additions."
18. ✓ **Seed script** — `scripts/seed_datasheets.py` (get-or-create, idempotent) +
    `make seed`, loading `scripts/data/datasheets.json`. The JSON ships **empty**;
    datasheet content is operator-supplied (JSON or admin API). See "Seeding the
    catalog."
19. ✓ **Fix `delete_unit` 500** — guards against `ArmyUnit`/`UserUnit` references
    (`_unit_is_referenced`) → `ConflictError` (409). See "API layer → Catalog
    administration."
20. ✓ **Unlink weapon/ability** — idempotent `unlink_weapon`/`unlink_ability` +
    `DELETE /units/{id}/weapons/{weapon_id}` and `.../abilities/{ability_id}` → 204.
    See "API layer → Catalog administration."
21. ✓ **Delete a subfaction** — guarded `delete_subfaction` (units/armies →
    `ConflictError` 409) + `DELETE /subfactions/{id}` → 204. See "API layer →
    Catalog administration."
22. ✓ **Admin promotion via API** — `UserService.set_admin` + admin-only
    `PATCH /users/{id}` `{is_admin}` → `User_Read` (includes `is_admin`). See
    "Authentication & authorization → Planned hardening."
23. ✓ **Editable weapons + abilities** — `update_weapon`/`delete_weapon`,
    `update_ability`/`delete_ability` + `PATCH`/`DELETE /weapons/{id}` and
    `/abilities/{id}` (all-optional `Weapon_Update`/`Ability_Update`; links cascade,
    so no delete guard). See "API layer → Catalog administration."
24. ✓ **Catalog scraper (Wahapedia)** — `scripts/scrape_wahapedia.py` + `make scrape`
    scrape a faction's collated `datasheets.html` → the **full datasheet** (stats,
    chapter→subfaction, weapons, min-size points, keywords, abilities) →
    `datasheets.json` for `make seed` (cached/polite fetch, synthetic-fixture parser
    tests). Remaining polish: multi-profile datasheets (first profile only),
    per-size points. See "Scraping the catalog (Wahapedia)."
25. **(L) Frontend** — the "Muster" Vite/React UI. Out of backend scope; the
    items above are its prerequisites. See "Frontend integration."

**Deploy checklist (not code):** set a real `SECRET_KEY`; point `ALLOWED_ORIGINS`
at the frontend's real origin.

## Improvements

Findings from the whole-roadmap quality review (2026-07). The **Should-fix** items
are real correctness/security holes; the rest are hardening and cleanup. (Three
bigger refactors from the review are intentionally *not* tracked here: DRYing the
`InventoryService`/`ArmyService` junction logic, a composite FK to enforce
unit/army subfaction-belongs-to-faction at the DB level, and lazy `DATABASE_URL`
parsing in the Makefile.)

### Should-fix 🐞
- [x] **`add_unit` accepts 0/negative amounts** — *fixed:* `Field(default=1, ge=1)`
  on `InventoryAdd`/`ArmyUnitAdd` (→ 422 at the API) **and** an `amount >= 1` guard
  in both services' `add_unit` raising `*ValidationError("amount", …)` (→ 400 for
  direct callers). +4 tests.
- [x] **`make docker-test` is broken** — *fixed:* stopped ignoring `tests/` in
  `.dockerignore` (pytest already ships in `requirements.txt`, so that was the only
  gap). Not yet live-verified — needs a `make docker-test` run once the Docker
  daemon is up.
- [x] **`SECRET_KEY` fails open** — *fixed:* the throwaway default is now allowed
  only when `APP_ENV=dev` (default); any other env with `SECRET_KEY` unset raises at
  startup (`_resolve_secret_key`). `docker-compose.yml` uses `${SECRET_KEY:?…}`
  (compose refuses to start without it — verified) and defaults containers to
  `APP_ENV=production`. `.env.example` documents `APP_ENV`. +3 tests.

### Robustness ⚠️
- [x] **Guard roster `Unit` lookups** — *fixed:* `shortfall`/`points_total`/
  `validate` route their catalog lookups through a new `_unit_or_404` helper, so a
  dangling `ArmyUnit` raises `NotFoundError` (404) instead of `AttributeError` (500).
  +1 test.
- [x] **Seed script error handling** — *fixed:* a `SeedError` + `_ref` helper turn
  unknown faction/subfaction/weapon/ability references and missing unit fields into
  clear messages naming the offending record; `main()` catches read/JSON/service
  errors and exits non-zero via `_fail` (no raw traceback). +2 tests.
- [x] **Dockerfile hardening** — *fixed:* creates a non-root `appuser` (uid 1000,
  owns `/app`) and switches to it with `USER`; dropped the redundant `chmod`
  (`docker-entrypoint.sh` is committed `100755`, and `COPY` preserves the bit). Not
  live-verified (Docker daemon down).
- [x] **Upsert 201-vs-200 scan** — *fixed:* `InventoryService`/`ArmyService.add_unit`
  now return `(entry, created)`, so the routes pick 201 vs 200 from the flag instead
  of scanning a full `list_*` (one fewer query, no read-then-write race). Callers +
  tests updated to unpack (and assert the flag).

### Consistency / cleanup 🧹
- [x] **Remove dead `ForbiddenError`** — *fixed:* dropped the unused class from
  `errors.py` and its SPEC references (error-map table, shared-errors list, status
  codes, roadmap 12). Ownership stays 404 via `get_owned_army`; re-add if a real
  403 case appears.
- [x] **Stale docstrings** — *fixed:* `models.py` now points at SPEC.md ("DB
  layer") instead of the nonexistent `test_units.md`; the four `test_service_*.py`
  TDD preambles + outdated contracts are replaced with concise one-line summaries.
- [x] **Error-taxonomy consistency** — *fixed:* `get_owned_army` and
  `make_admin.promote` now raise `NotFoundError` instead of stdlib `LookupError`
  (still → 404; the make_admin test asserts the typed error).
- [x] **Merge the duplicate service factory** — *fixed:* `faction.py` now imports
  `get_unit_service` from `unit.py` instead of defining an identical
  `get_catalog_service`; removed the now-unused `Session`/`get_session` imports.

### Test coverage 🧪
- [x] **Service-level gaps** — *fixed:* added ~20 `UnitService` tests covering
  `unlink_weapon` (incl. not-linked), `update_weapon`/`update_ability` (unknown
  field, bad category), `delete_weapon`/`delete_ability` (404), `list_weapons`/
  `list_abilities`, `count_units` (filters), `delete_subfaction` (success/404/409),
  and the `link_*` not-found error paths.
- [x] **Verify the delete-CASCADE** — *fixed:* two tests link a weapon/ability to a
  unit, delete it (succeeds despite the reference), and assert the unit no longer
  lists it — confirming `unit_weapons`/`unit_abilities` cascade.
- [x] **`validate()` combined issues** — *fixed:* a test where one over-costed,
  wrong-faction unit trips both `over_points` (Tier 1) and `wrong_faction` (Tier 2)
  at once, asserting both kinds present, `ok` False, and the right `points_total`.
- [x] **Fix conftest fragility** — *fixed:* `auth_client`/`admin_client` each build
  their own `TestClient` via a `_authed_client` helper (depending on `client` only
  for the session-override lifecycle), so a single test can use both. Added a
  regression test asserting they're independent.

## Backend next steps (post-MVP backlog)

The backend is **MVP-complete and conformant** (verified by a multi-agent audit).
The items below are forward-looking next steps, ordered by value and dependency,
distilled from a 3-agent forward-looking review (roadmap/backlog,
frontend-driven needs, and technical-debt/hardening). Nothing here is a
correctness hole in the shipped MVP — those are tracked under "Improvements"
above; these are the things to build *next*. Effort tags are **S/M/L**.

### Tier 0 — Frontend unblockers

Three tiny, additive serializer/query changes that immediately unblock the
frontend. **None needs a database migration** — all three underlying columns
already exist on the tables; the work is exposing them.

| ID | Change | Where | Effort |
|---|---|---|---|
| FE1 | Add `created_at` (optionally `updated_at`) to `Army_Read` so the frontend can show the army's "Created" date | `app/api/army.py` (the column exists via `TimestampMixin` in `models.py` and is in the initial migration; the serializer already uses `from_attributes`, so declaring the field populates it). Also add `created_at` to the frontend `types.ts` | S |
| FE2 | Add a `q` filter to `GET /me/inventory` so inventory search is server-side, not client-only | `app/api/inventory.py` + `service_inventory.py.list_inventory`, mirroring the case-insensitive `ilike` + count already in `GET /units` (`service_unit._apply_unit_filters`) | S |
| FE3 | Add `is_admin` to the `/me` response to unblock admin-UI gating | `User_Read` in `app/api/user.py`. Safe: `/me` is self-only (identity from the JWT), so it reveals only the caller's own admin status and grants nothing — authorization stays enforced server-side by `get_current_admin` on every write | S |
| BUG1 | **Catalog only shows 25 units on the deployed site** (found 2026-07). Not a DB issue: the catalog paginates 25/page by design (`CatalogView.PAGE_SIZE`), and the Prev/Next pager only renders when `total > PAGE_SIZE`. `total` comes from the `X-Total-Count` response header, but the API's `CORSMiddleware` omits `expose_headers`, so cross-origin (Firebase → Cloud Run) the browser can't read the header → `total` defaults to `0` → pager is hidden → stuck on the first 25. Works locally because the Vite proxy makes it same-origin. **Fix:** add `expose_headers=["X-Total-Count"]` to `CORSMiddleware`; if the symptom persists, confirm the header round-trips and that `units.ts`/`client.ts` parse it | `app/main.py` (CORS `expose_headers`); verify `src/api/units.ts`/`client.ts` in the web repo | S |

### Tier 1 — Make the scrape→seed pipeline honest & robust

The seed currently contradicts its own SPEC promise (it skips existing rows
rather than patching them) and same-named units collapse. These fix that, and
harden the concurrent "add unit" path.

| ID | Change | Where | Effort |
|---|---|---|---|
| H1 | **Seed upsert** — update mutable fields on existing rows (stat line, `points`, `keywords`, `invulnerable_save`) instead of skipping them, so re-seeding truly "patches stats in place" as SPEC claims. Add an update branch per entity and an "updated" count bucket | `scripts/seed_datasheets.py` (create-only today); update `tests/test_seed.py` accordingly | M |
| C | *(fold into H1)* Change the unit seed natural key from `(faction, unit_name)` to `(faction, subfaction, unit_name)`, stopping the ~227-unit collapse (same-named generic units — Chaos Spawn, Cultists — merging across subfactions; ~1558 scraped → ~1331 seeded) | `scripts/seed_datasheets.py` | S |
| H2 | Catch `IntegrityError` on concurrent `add_unit` and convert to `ConflictError`/merge; add a catch-all `IntegrityError` → 409 handler. Today two simultaneous "add unit" requests leak a raw 500 | `service_army.py` + `service_inventory.py`; handler in `app/main.py` | M |

### Tier 2 — Scaling (before real traffic)

N+1 query patterns and missing observability will bite once there's load. Do
these before opening the API to real traffic.

| ID | Change | Where | Effort |
|---|---|---|---|
| H3 + M1 | Eager-load weapons/abilities with `selectinload` on the list endpoints, and batch `ArmyService`'s per-entry `session.get`. Removes the N+1 that makes `GET /units?limit=200` fire ~400 queries and `GET /me/armies` walk the catalog subtree per army | `service_unit.list_units` (and the army list); batch the `session.get` in `points_total`/`shortfall`/`validate` | M |
| Q1 | **Extend the N+1 fix beyond `/units`** (found 2026-07). `Unit_Read` nests `weapons` + `abilities`, so *every* list that serializes units lazy-loads them per row: `GET /units` (`1+2N`), `GET /me/inventory` (each entry's `unit` + its weapons/abilities), and `GET /me/armies` — which is worst: it serializes `Army_Read.units` (lazy) **and** re-queries the same units in `points_total`. Options: `selectinload` the chains; compute `points_total` with a single `SUM(amount*points)` aggregate instead of a per-entry loop; and/or split `Army_Read` into a **summary** schema (list — no `units`) vs a **detail** schema (`GET /{id}` — units eager-loaded), the classic list-vs-detail split. Coordinated FE change (the web app reads `army.units`) | `app/api/unit.py`, `app/api/inventory.py`, `app/api/army.py`, `service_army.points_total` | M |
| M5 | Wire the already-installed `sentry-sdk` (no-op when `SENTRY_DSN` unset), add basic structured logging, and a sanitized catch-all `Exception` → 500 handler so internals never leak | `app/main.py` | S/M |

> **Revisit: relationship-loading strategy.** The loading strategy across the
> `*_Read` schemas needs a deliberate pass — everything defaults to **lazy**
> today, which is what creates the N+1s above. Decide *per relationship* which
> strategy each endpoint should use — **lazy** (fine for single-object detail),
> eager **`selectinload`** (collections / many-to-many) or **`joinedload`**
> (many-to-one), and **`noload`/`raiseload`** to forbid a relationship from
> loading (raiseload is useful in tests to catch accidental lazy loads). Also
> choose *where* to set it: per-query `.options(...)` (flexible, preferred) vs a
> model-level `Relationship(sa_relationship_kwargs={"lazy": ...})` default.

### Tier 3 — Small hardening wins

Independent, low-risk improvements, each landable on its own.

| ID | Change | Where | Effort |
|---|---|---|---|
| M4 | Replace `passlib` 1.7.4 with direct `bcrypt` calls (keep `hash_password`/`verify_password` as the seam) to unblock Python 3.13 and drop the `crypt` `DeprecationWarning` | `app/core/security.py` | S/M |
| L2 | **Scraper "fail loud"** — validate the assembled JSON against the seed schema (and cross-check factions/subfactions against `FactionName`/`FACTION_SUBFACTIONS`) *before* writing, so a Wahapedia layout change errors instead of seeding garbage | `scrape_wahapedia.py` `main()` | S/M |
| L3 | Add a deep readiness probe (`/health/ready` or `?deep=1`) that runs `SELECT 1` | `app/main.py` | S |
| D1 | **Docs (code review):** give `README.md` a real front door — what the project is, how to run it — linking `SPEC.md`, `MVP.md`, `DEPLOY.md`. Currently 2 lines | `README.md` | S |

### Tier 4 — Deferred / deploy-time / cleanup

Not code changes (or intentionally deferred), grouped by kind.

- **Deploy-time (not code)** — set a real `SECRET_KEY`; point `ALLOWED_ORIGINS`
  at the frontend origin; run `make docker-test` and verify the Dockerfile
  hardening once the Docker daemon is available.
- **Deferred hardening** — rate-limit `/auth/*` (slowapi, IP-keyed 5/min, 429)
  before public deploy (L1); consolidate config into a `pydantic-settings`
  `Settings` class (L4); shorten JWT lifetime + add refresh/revocation (L5);
  move migration-on-start to a one-shot job for multi-replica deploys (L6).
  (JWT-in-`localStorage` is now a documented **accepted decision** — see
  "Authentication & authorization → Token storage".)
- **Email deliverability check** *(builds on the EmailStr format check, now in place)* — turn on the "can this domain
  actually receive mail?" check: a custom validator calling
  `email_validator.validate_email(value, check_deliverability=True)` (Pydantic's
  `EmailStr` disables this by default), so registration does a live DNS/MX
  lookup on the domain. Deferred on purpose — it adds a network call + latency
  to signup and can reject legitimate users when DNS is flaky.
- **Out of scope (unchanged)** — datasheet versioning; wargear/loadout +
  model-count points scaling; multi-profile datasheet parsing; per-size points;
  Validation Tier 3 (per-datasheet count limits) & Tier 4 (detachments /
  force-org).
- **Stale-doc cleanup** — remove `CLAUDE.md`'s "Known issues" section (all 6
  already fixed); fix `MVP.md`'s "202 tests" → 209; reconcile stale SPEC prose
  (the "API tests to come" note, the "Wahapedia scraper planned" note, the
  present-tense "delete_unit 500s" line, and the "Planned additions
  (frontend-readiness)" header whose items are all done); correct `CLAUDE.md`'s
  stat-name shorthand "save" → "armor_save".

### Test-coverage gaps

New behavior above wants matching tests: the concurrency/upsert race
(H2); seed update-in-place (H1); query-count/N+1 regression guards (H3/M1);
scraper assembly + fail-loud (L2); and expired-token handling.

### Production-readiness

The top ops gap is **logging/observability** (M5) — nothing surfaces errors
today. Behind it: a deep healthcheck (L3), a single typed `Settings` (L4), and
JWT lifetime/revocation (L5). Address these before treating a deploy as
production-grade.
