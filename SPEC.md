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
can keep referencing a unit the user just sold. (Deleting a referenced row will be
guarded at the service layer to raise `ConflictError` → 409 rather than a raw
`IntegrityError` → 500 — planned; see "API layer → Catalog administration.")

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
hierarchy; the plain builtins remain as fallbacks):

| Service exception | HTTP status |
|---|---|
| `NotFoundError` (⊂ `LookupError`) | 404 |
| `ConflictError` (⊂ `ValueError`) — duplicate | 409 |
| `*ValidationError` (⊂ `ValueError`) — carries `field` | 400 |
| `ForbiddenError` | 403 |
| bare `LookupError` (fallback) | 404 |
| bare `ValueError` / `TypeError` (fallback) | 400 |
| Pydantic validation failure | 422 (FastAPI automatic) |

### App entry point (`app/main.py`)

`app/main.py` builds the `FastAPI()` instance, mounts every router
(`app.include_router(...)`), registers the `ServiceError` → HTTP handler (plus
the builtin fallbacks), and exposes a `GET /health` liveness check. Run locally
with `uvicorn app.main:app --reload` (or `make run`).

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
- `ForbiddenError(Exception)` — an ownership/permission violation. **→ 403** via a
  dedicated handler. (Ownership on `/me/armies/{id}` stays a **404** through
  `get_owned_army` to hide existence; `ForbiddenError` is for the cases where
  revealing "exists but not yours" is acceptable.)

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

**Status codes.** `NotFoundError` → 404, `ConflictError` → 409,
`ForbiddenError` → 403, and the `*ValidationError` family → 400 (with `field`).
Validation stays 400, not 422: 422 is reserved for FastAPI's request-shape
validation (a malformed body), whereas these are well-formed requests that fail a
business rule.

**Done.** All four services now raise the typed errors; `errors.py` +
the handler are in place, and the test suite covers the 404/409/400 mapping and
the `field` payload. `ForbiddenError` is defined but not yet raised (ownership on
`/me/armies/{id}` still 404s through `get_owned_army`).

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

**Implemented (machinery); data is operator-supplied.** Migrations create tables,
not rows, so the catalog ships **empty**. `scripts/seed_datasheets.py` bulk-loads
it from `scripts/data/datasheets.json` — but that file **ships empty**: datasheet
content is entered by the operator (fill the JSON, or add units via the admin API;
both go through the same validation). The schema is documented in
`scripts/data/README.md`. This realizes the plan below.

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

## Authentication & authorization

Implemented. Users register and log in for a JWT; `/me/*` routes are token-scoped
to the caller, and catalog writes require an admin. The pieces:

**Passwords.** Hashing lives in `app/core/security.py`: `hash_password` /
`verify_password` using **bcrypt via `passlib`**. The server always hashes a
**raw password** — a client-supplied hash is never accepted or stored.

**Tokens.** Login returns a **JWT** (`create_access_token` / decode, also in
`security.py`): subject = the user's id, algorithm **HS256**, signed with a
`SECRET_KEY` env var and expiring after `ACCESS_TOKEN_EXPIRE_MINUTES`.

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
  admin-only `PATCH /users/{id}` `{is_admin}`. Returns `UserAdmin_Read` (like
  `User_Read` but surfacing `is_admin`, since `User_Read` deliberately hides it).
  Last-admin-lockout protection was left out for now.
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
    `app/core/services/errors.py` (`NotFoundError`, `ConflictError`,
    `ForbiddenError`, and per-service `*ValidationError`) replacing the builtin
    `LookupError`/`ValueError` across all services; a single `ServiceError`
    handler maps them (409 for duplicates, `field` on validation) — see "Custom
    service errors."

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
    `PATCH /users/{id}` `{is_admin}` → `UserAdmin_Read` (surfaces `is_admin`). See
    "Authentication & authorization → Planned hardening."
23. ✓ **Editable weapons + abilities** — `update_weapon`/`delete_weapon`,
    `update_ability`/`delete_ability` + `PATCH`/`DELETE /weapons/{id}` and
    `/abilities/{id}` (all-optional `Weapon_Update`/`Ability_Update`; links cascade,
    so no delete guard). See "API layer → Catalog administration."
24. **(L) Frontend** — the "Muster" Vite/React UI. Out of backend scope; the
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
- [ ] **`add_unit` accepts 0/negative amounts** — `InventoryAdd.amount` /
  `ArmyUnitAdd.amount` are `int = 1` with no lower bound, and neither service's
  `add_unit` guards it (unlike `set_amount`). A 0/negative slips to the DB CHECK as
  a raw 500, or decrements an existing row below 1. *Fix:* `Field(default=1, ge=1)`
  on both schemas **and** an `amount >= 1` check in `InventoryService.add_unit` /
  `ArmyService.add_unit` (raising the service's `*ValidationError("amount", …)`).
- [ ] **`make docker-test` is broken** — `.dockerignore` excludes `tests/`, but
  `docker-compose.test.yml` runs `pytest tests/` inside that image, so it finds
  nothing. *Fix:* stop ignoring `tests/` (or build a test image that includes them).
- [ ] **`SECRET_KEY` fails open** — `app/core/security.py` defaults it to
  `"dev-secret-change-me"`, so a prod deploy that forgets to set it ships a
  publicly-known JWT signing key (forgeable admin tokens). *Fix:* fail fast at
  startup when unset outside local dev; use `${SECRET_KEY:?…}` in compose.

### Robustness ⚠️
- [ ] **Guard roster `Unit` lookups** — `ArmyService.shortfall`/`points_total`/
  `validate` call `session.get(Unit, …)` without a None check, so a dangling
  `ArmyUnit` yields `AttributeError` → 500. *Fix:* fetch into a local and skip/raise
  `NotFoundError`.
- [ ] **Seed script error handling** — `scripts/seed_datasheets.py` `KeyError`s when
  a unit references an unknown weapon/faction, and gives a raw traceback on malformed
  JSON or a mid-file failure. *Fix:* friendly messages + a clean non-zero exit.
- [ ] **Dockerfile hardening** — the image runs as root; add a non-root `USER`. Drop
  the redundant `chmod +x docker-entrypoint.sh` (the file is already committed `755`).
- [ ] **Upsert 201-vs-200 scan** — the inventory/army "add unit" routes run a full
  `list_*` and scan it in Python to choose 201 vs 200 (extra round-trip, racy under
  concurrency). *Fix:* return a `created` flag from `add_unit`.

### Consistency / cleanup 🧹
- [ ] **Remove dead `ForbiddenError`** — defined in `errors.py`, never raised, and
  (unlike the other leaves) subclasses no builtin, so the incremental-migration
  handler wouldn't even map it. Drop it, or wire it into an ownership path + handler.
- [ ] **Stale docstrings** — `models.py:10` references `app/core/db/test_units.md`
  (doesn't exist); the four `tests/test_service_*.py` files still open with "these
  fail until `<service>` exists" TDD preambles + outdated contracts. Update/remove.
- [ ] **Error-taxonomy consistency** — `get_owned_army` (`app/api/army.py`) and
  `scripts/make_admin.py:promote` raise stdlib `LookupError`; switch to
  `NotFoundError` to match the rest of the codebase.
- [ ] **Merge the duplicate service factory** — `get_unit_service` (unit.py) and
  `get_catalog_service` (faction.py) both `return UnitService(session)`; share one.

### Test coverage 🧪
- [ ] **Service-level gaps** — add `UnitService` tests for `unlink_weapon`/
  `unlink_ability` (incl. the not-linked path), `update_weapon`/`update_ability`
  (unknown field, bad category), `delete_weapon`/`delete_ability` (404), `count_units`
  (with filters), `delete_subfaction` (success/404/409), and `link_*` error paths —
  most are currently API-only or untested.
- [ ] **Verify the delete-CASCADE** — no test links a weapon/ability to a unit,
  deletes it, and asserts the unit no longer lists it (the "links cascade, no guard
  needed" claim is unverified).
- [ ] **`validate()` combined issues** — add a test with multiple simultaneous issue
  kinds and one pinning the `ok`/tier semantics.
- [ ] **Fix conftest fragility** — `auth_client`/`admin_client` share one
  `TestClient` (mutating its `Authorization` header), so a single test can't use both.
  Give each its own client.
