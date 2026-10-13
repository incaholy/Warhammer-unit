# MVP — Muster


Written for someone reading the project cold. It states what Muster is, what works
today, what "finished" means, and what is deliberately absent. Every count in it was
measured rather than carried forward; where something is broken or missing, it says so.
This is the only MVP document — there is deliberately no second one to drift from it.

**Scope note.** Muster is two repositories. This one, `warhammer_unit`, is the API; the
web client is [`warhammer_web`][web]. The API had shipped both halves of the product
before this document widened the MVP, and the client is three of its five slices in — so
each has work left, listed under [What "finished" means](#what-finished-means). This
document tracks the *product's* MVP rather than either repo's, because an API nobody can
use is not a viable product.

**On the jargon.** This is a document about two tabletop games, so it uses their
words. Every one of them is defined in the [glossary](#glossary) at the end — nothing
in here assumes you have played either game.

[web]: https://github.com/incaholy/warhammer_unit_web

<!-- The client's repository is `warhammer_unit_web`; its working directory is `warhammer_web`. -->

---

## What it is

A collection and game manager for two Warhammer tabletop games, built around one
idea: **the app records what is true, and the players apply the rules.**

| | |
|---|---|
| **Army List** | Warhammer 40,000. A read-only catalog of datasheets, a record of the models you physically own, and points-costed army lists checked against a limit — including what you would still have to buy. |
| **Kill Team** | Kill Team 2024. The team catalog, rosters built from it, and a battle tracker that follows a game turn by turn with an undoable log. |

The two are deliberately separate sections rather than one merged app: different
games, different rules, different data. They share only authentication and the
engineering underneath.

**Who it is for.** One player managing their own collection — not a store, a
tournament organiser or a league. A collection, an army list and a roster belong to
one user and are visible only to them; the catalogs are shared and read-only. The one
thing two people share is a **Kill Team battle**: both players track the same game,
each with their own team, operatives, command points and victory points (K8, K9).

## What "the app records, the players apply" means

This is the project's governing constraint, and most of what follows is downstream of
it. Muster is a **tracker, not a rules engine**. It will tell you that a roster has
six operatives and that the page says to pick six; it will not refuse to save a
seventh. It stores what the printed page says and leaves judgement to the people at
the table.

That is a scope decision, not an unfinished feature, and it is why several things a
reviewer might expect are absent on purpose — see
[Deliberate non-features](#deliberate-non-features).

## Tech stack

| | |
|---|---|
| **API** | Python 3.12, FastAPI 0.128, Pydantic 2.12 |
| **Data** | PostgreSQL via SQLAlchemy 2.0 + SQLModel 0.0.31; Alembic 1.18 for migrations |
| **Auth** | JWT (`python-jose`), password hashing via `passlib`/`bcrypt` |
| **Tests** | pytest 8.3 — two tiers, SQLite and real Postgres |
| **Quality** | ruff (lint + format), `import-linter` for layering, Sentry for errors |
| **Contract** | `openapi.json` is committed, and CI fails if it drifts from the app |
| **Client** | React 19 + TypeScript, Vite, TanStack Query, zod ([`warhammer_web`][web]) |

The runtime dependency list is deliberately small: FastAPI, SQLModel, Alembic,
Pydantic, a driver, a JWT library, a hasher. No task queue, no cache, no ORM layer of
our own.

## Project structure

```
app/
  main.py              the app, and ONE error handler for every typed error
  observability.py     request ids, structured logs, Sentry
  api/                 one router per resource; never touches a DB session
    auth.py  user.py
    faction.py  unit.py  inventory.py  army.py        40k
    killteam.py  killteam_roster.py  killteam_game.py Kill Team
    deps.py  errors.py  fields.py  pagination.py      shared route plumbing
  core/
    services/          one <Thing>Service per file; all the business logic
      service_auth.py  service_user.py
      service_unit.py  service_inventory.py  service_army.py
      service_killteam.py  service_killteam_roster.py  service_killteam_game.py
      errors.py        NotFoundError, ConflictError — the cross-cutting ones
    db/
      models.py            40k + shared tables (11)
      models_killteam.py   Kill Team tables (15)
      columns.py           shared column types and helpers
      connection.py        engine and session
      alembic/             4 migrations
    errors.py          CodedError, the marker base every typed error inherits
    security.py        hashing and tokens
scripts/
  scrape_wahapedia.py     40k catalog → datasheets.json
  scrape_wahapedia_kt.py  Kill Team catalog → killteam.json
  seed_datasheets.py      load the 40k catalog
  seed_killteam.py        load the Kill Team catalog (upsert + withdraw sweep)
  make_admin.py
tests/                 34 modules, two tiers
```

**The layering is strict and enforced, not aspirational**: API → service → DB, one
direction. A router never opens a session; a service never raises `HTTPException`.
Two `import-linter` contracts fail CI if either rule is broken.

Errors follow from that. A service raises a typed error that inherits both
`CodedError` and the Python builtin it maps to, and `app/main.py` registers **one**
handler against `CodedError` — so a new error class gets the right status
automatically and there is no registry to keep in sync.

## Data model

Two trees sharing a `users` table.

**Army List (11 tables)**

```
Faction ─→ Subfaction
   └─→ Unit ─→ (unit_weapons) ─→ Weapon
         └──→ (unit_abilities) ─→ Ability

User ─→ Army ─→ ArmyUnit ─→ Unit        a points-costed list
  └──→ UserUnit ─→ Unit                 the models you own
```

**Kill Team (15 tables)** — three layers, each only depending on the one above.

```
CATALOG (read-only, public)
  KTFaction ─→ KillTeam ─→ KTOperative ─→ KTWeapon
                   │            └───────→ KTAbility
                   ├─→ KillTeamRule
                   ├─→ KTPloy        (also team-less: universal ploys)
                   ├─→ KTEquipment   (also team-less: universal equipment)
                   └─→ KTSelectionRule   the printed composition, as text

ROSTER (one player's)
  KTRoster ─→ KTRosterOperative ─→ KTOperative

GAME (one battle, self-contained)
  KTGame ─→ KTGameOperative ─→ (datacard SNAPSHOT, not a reference)
     ├───→ KTGameEquipment
     └───→ KTGameEvent          append-only log; undo walks it back
```


**A game copies everything it needs.** Starting a battle snapshots every datacard,
the team's rules and both ploy lists into the game's own rows. After that the game
reads nothing from the catalog. So a catalog correction cannot change a battle in
progress, a finished game still shows the datacard as it was played, and the whole
battle screen is one request.

**Ownership is structural, not checked.** A roster row and a game row carry
`owner_user_id` and are held by three-column composite foreign keys. A route that
forgot to check ownership could not land the row — the database would refuse it. The
check exists too, for the error message, but it is not what makes it safe.

A shared battle does not relax this, which is the part worth looking at: the leg grows
from three columns to four so it names the game's **side** instead of the game (#67).
The guarantee gets stronger — an operative must belong to a side that is playing its
kill team, where today it need only belong to the game's team.

**A withdrawn catalog row is flagged, not deleted.** When the source drops a
datasheet, the seed marks it. Catalog reads hide it so a picker never offers it;
roster and game reads still resolve it, so a roster built last month still renders.
Deleting would leave anything already rostered permanently broken.

### Every table, and what it holds

**Army List and shared — 11 tables.**

| Table | Holds | Notable |
|---|---|---|
| `users` | username, email, bcrypt hash, admin flag | the only table both halves share |
| `factions` | the top-level armies | name is globally unique |
| `subfactions` | narrower groupings | `UNIQUE(faction_id, name)`; cascades from its faction |
| `units` | one datasheet: the stat line, points, keywords | `points` is a **single** integer — the minimum-size price, which is why R13 exists |
| `weapons` | a weapon profile, reusable across units | range is nullable because melee prints none |
| `abilities` | a named ability and its text | shared across units rather than copied |
| `unit_weapons` | which weapons a unit may take | association table, no id of its own |
| `unit_abilities` | which abilities a unit has | association table, no id of its own |
| `user_unit` | your inventory — how many of a unit you own | `UNIQUE(owner_user_id, unit_id)`; the count is of whole units |
| `armies` | one army list: name, faction, optional points limit | `points_total` is **not** stored; it is computed per request |
| `army_units` | which units are in a list, and how many | `UNIQUE(army_id, unit_id)` — one row per datasheet, which is what makes two differently-sized squads unrepresentable |

**Kill Team — 15 tables**, in three layers.

*Catalog (seeded, read-only, public):*

| Table | Holds | Notable |
|---|---|---|
| `kt_factions` | the top-level groupings | |
| `kt_kill_teams` | a team such as Raveners — the root the rest hangs off | **no operative count and no composition columns**; composition is rows |
| `kt_kill_team_rules` | a team-wide rule, with its printed group | `UNIQUE(kill_team_id, name)` |
| `kt_operatives` | one datacard: APL, move, save, wounds, keywords | `availability` marks a datacard a roster may not take, only a battle |
| `kt_weapons` | a weapon profile on a datacard | `UNIQUE(operative_id, name, category)` — the same name can be both ranged and melee |
| `kt_abilities` | an ability or unique action | `UNIQUE(operative_id, name)` |
| `kt_ploys` | a CP-priced option | `kill_team_id` is **nullable**: NULL means universal, available to every team |
| `kt_equipment` | gear selectable for a battle | also nullable `kill_team_id`, with a partial unique index for the universal rows |
| `kt_selection_rules` | the printed composition, one row per printed line | `{position, depth, kind, text}` and nothing else — no budget, no caps, nothing derived (#44) |

*Roster (one player's):*

| Table | Holds | Notable |
|---|---|---|
| `kt_rosters` | a roster: a name, a fixed kill team | `UNIQUE(owner_user_id, name)`; no points, because Kill Team does not cost in points |
| `kt_roster_operatives` | one row per **individual** operative | no count, so two identical models are two rows you can tell apart (#50). Two composite FKs |

*Game (one battle, self-contained):*

| Table | Holds | Notable |
|---|---|---|
| `kt_games` | the battle: status, turning point, phase, initiative, CP, VP, and the snapshotted team reference | 20 columns, **8 check constraints**, and the `version` that locks every write in the whole subtree |
| `kt_game_operatives` | a **snapshot** of a datacard plus what is true of it now | 24 columns and **10 checks** — the most constrained table in the schema. Snapshot and live state side by side |
| `kt_game_equipment` | equipment taken for this battle, text copied | plain single-column FKs, because universal equipment has a NULL team and offers no unique pair to point at |
| `kt_game_events` | one row per thing that happened, append-only | `UNIQUE(game_id, sequence)` is the only ordering guarantee; `undone_by` is a self-reference |

### What the trees don't show

Five things the diagrams cannot express, each of which a reviewer would otherwise
have to read the models to find:

**Who writes each table.** Nine of the fifteen Kill Team tables and seven of the
eleven Army List tables are **seeded** — on the 40k side five written directly and the
two association tables populated through `UnitService`'s link calls. That leaves ten
tables a user's own requests write: `users`, `user_unit`, `armies` and `army_units`,
and the six Kill Team roster and game tables. A game or roster never writes a catalog table (#21). So "read-only" is a
property of the writer, not a permission: there is no Kill Team catalog write route at
all, and the 40k catalog's admin routes exist but would be overwritten by the next
seed.

**The `withdrawn` flag covers eight tables, and one is deliberately exempt.** Every
Kill Team catalog table carries it except `kt_selection_rules`, because a composition
is replaced as a whole and so already loses what the source dropped (#55). The 40k
catalog has no such flag anywhere — its seed is create-only and has no concept of a
row going away, which is part of why that seed is on the hardening list.

**Sixteen columns are JSON** (JSONB on Postgres, plain JSON on SQLite) — two on the
40k side and fourteen on the Kill Team side: keyword and rule lists, the whole datacard
snapshot on `kt_game_operatives`, the VP-by-source document and both reference
snapshots on `kt_games`, and every event's payload. They are used where the shape is a list or a document the
database never queries into — and one consequence is load-bearing: SQLAlchemy stores
Python `None` as the JSON value `null` rather than SQL NULL, so a `NOT NULL` column
cannot catch it and the service checks instead.

**Composite foreign keys live on exactly three tables** — `kt_roster_operatives`,
`kt_games` and `kt_game_operatives` — carrying five constraints between them. They are
what makes ownership structural rather than checked, and the redundant-looking unique
constraints on `kt_rosters` and `kt_games` exist only to give those keys a provably
unique parent to point at.

**Every table carries `created_at` and `updated_at`** from one mixin, with
`server_default` and `onupdate` set in the DDL — except the two 40k association
tables, which have no columns of their own beyond the pair they join.

**And what has no table yet:** the 40k composition and its points tiers (R13), and the
game side (K8). Those are the three tables the MVP still adds.

## API surface

**49 paths, 72 operations today**, every one of them below. **Fifteen need no token**:
`/health`, register and login, the six Kill Team catalog reads, and six 40k catalog
reads — factions, subfactions, `/taxonomy`, weapons, abilities and a unit by id. The
other 57 require a JWT, and a user sees only their own rows.

| Area | Operations | |
|---|---|---|
| Kill Team games | 15 | create from a roster, the battle writes, advance, undo |
| 40k catalog | 24 | factions, subfactions, units, weapons, abilities — admin-writable, public to read |
| 40k armies | 10 | CRUD, add/remove units, points validation, shortfall |
| Kill Team rosters | 8 | CRUD, add/remove/move operatives |
| Kill Team catalog | 6 | **public — no token at all** |
| shared | 5 | register, login, `/me`, the admin grant, `/health` |
| 40k inventory | 4 | what you own |

Conventions that hold across all of it: every path is prefixed `/api/v1` except
`/health`, which sits at the root so a probe need not know the API version; one
pagination envelope (`{items, total, limit, offset}`, `total` in the body so
cross-origin JS can read it), one error shape
(`{detail, code, field?, request_id, errors[]}`), and a cross-user row answering
**404 rather than 403** so an id never reveals whether it exists.

Each section below ends with the endpoints that do not exist yet but will once the
MVP is finished. Those are marked **planned**, and where a route's shape follows from
a decision rather than being named by one, it says so — an invented path presented as
settled would be the worst kind of thing for this document to contain.

### Shared — 5 operations

| | |
|---|---|
| `POST /auth/register` | create an account — username, email, password |
| `POST /auth/login` | OAuth2 password flow; returns a JWT, two-day default lifetime |
| `GET /me` | the authenticated user |
| `PATCH /users/{user_id}` | grant or revoke admin. **Admin only** |
| `GET /health` | liveness. Touches nothing, needs no token |

### Army List — Warhammer 40,000, 38 operations

**The catalog (24).** Public to read, admin to write. This is reference data scraped
from the source, not user content.

| | |
|---|---|
| `GET /factions` · `POST /factions` | the top-level armies |
| `GET /subfactions` · `POST /subfactions` · `DELETE /subfactions/{subfaction_id}` | narrower groupings inside a faction |
| `GET /taxonomy` | the subfaction **names** allowed under each faction, from a module constant rather than the database. No ids, so a picker built on it cannot reference a row |
| `GET /units` | paged, filterable by faction, subfaction, free text, and whether you own it. Serves anonymous callers, but `openapi.json` declares the bearer scheme because the `owned` dependency pulls it in — so a generated client treats it as authenticated |
| `GET /units/facets` | per-faction counts for a filter sidebar, without fetching the units |
| `GET /units/{unit_id}` · `POST /units` · `PATCH /units/{unit_id}` · `DELETE /units/{unit_id}` | one datasheet; delete answers 409 if an army or inventory references it |
| `POST /units/{unit_id}/weapons` · `DELETE /units/{unit_id}/weapons/{weapon_id}` | link and unlink a weapon profile |
| `POST /units/{unit_id}/abilities` · `DELETE /units/{unit_id}/abilities/{ability_id}` | link and unlink an ability |
| `GET /weapons` · `POST /weapons` · `PATCH /weapons/{weapon_id}` · `DELETE /weapons/{weapon_id}` | weapon profiles |
| `GET /abilities` · `POST /abilities` · `PATCH /abilities/{ability_id}` · `DELETE /abilities/{ability_id}` | abilities |

**Your inventory (4).** What you physically own.

| | |
|---|---|
| `GET /me/inventory` | paged, with each unit's datasheet |
| `POST /me/inventory` | record a unit as owned. **Create-only** — a repeat answers 409, because an incrementing add is not retry-safe |
| `PATCH /me/inventory/{unit_id}` | set the owned count to an absolute value |
| `DELETE /me/inventory/{unit_id}` | stop owning it. Leaves any army that uses it alone |

**Your army lists (10).**

| | |
|---|---|
| `GET /me/armies` · `POST /me/armies` | your lists; a list names a faction and an optional points limit |
| `GET /me/armies/{army_id}` | the list whole, with every entry's datasheet and the running points total |
| `PATCH /me/armies/{army_id}` · `DELETE /me/armies/{army_id}` | rename, re-faction, re-limit; delete cascades to its entries only |
| `POST /me/armies/{army_id}/units` | add a unit. Create-only, same reasoning as the inventory |
| `PATCH /me/armies/{army_id}/units/{unit_id}` | set how many, absolutely |
| `DELETE /me/armies/{army_id}/units/{unit_id}` | drop it from the list |
| `GET /me/armies/{army_id}/validate` | reports the total against the limit, plus wrong-faction and wrong-subfaction entries. **Reports; refuses nothing** |
| `GET /me/armies/{army_id}/shortfall` | what the list needs that the inventory does not have — "what you still have to buy" |

**Planned — R13.**

| | |
|---|---|
| `GET /units/{unit_id}` | *changed, not added:* the response gains the datasheet's printed composition, so the catalog and the list both get it from a read they already make |
| `POST /me/armies/{army_id}/units` | *changed:* the body gains the model count, so a squad is added at the size actually fielded |
| `PATCH /me/armies/{army_id}/units/{row_id}` | *rekeyed and repurposed.* Keyed by row, not unit: one list may hold two differently-sized squads of one datasheet. It sets the model count; R13 retires the squad count |
| `DELETE /me/armies/{army_id}/units/{row_id}` | *rekeyed,* same reason |

The composition gets no route of its own: it rides on the unit. R13's reasoning names
the rekeying and the retirement of the squad count; the exact field name in the body
is not yet settled.

### Kill Team — 29 operations

**The catalog (6).** The only part of the API that needs **no token at all**, because
the printed rules reference is useful before you have an account.

| | |
|---|---|
| `GET /kill-team/factions` | the top-level groupings |
| `GET /kill-team/teams` | all 48 teams, paged and filterable |
| `GET /kill-team/teams/{kill_team_id}` | one team whole — its datacards, rules, ploys, equipment and composition |
| `GET /kill-team/teams/{kill_team_id}/composition` | the printed composition alone, unpaginated |
| `GET /kill-team/operatives` | datacards across teams, for a picker |
| `GET /kill-team/universal` | the ploys and equipment no single team owns |

**Your rosters (8).**

| | |
|---|---|
| `GET /me/kill-team/rosters` · `POST /me/kill-team/rosters` | your rosters; the kill team is fixed at creation and the name is unique per owner |
| `GET /me/kill-team/rosters/{roster_id}` | the roster with every operative's datacard resolved live, plus both ploy lists |
| `PATCH /me/kill-team/rosters/{roster_id}` · `DELETE /me/kill-team/rosters/{roster_id}` | rename or re-describe; delete answers 409 if a game was played from it |
| `POST /me/kill-team/rosters/{roster_id}/operatives` | add one operative — one row per miniature, so two identical models are two rows |
| `PATCH /me/kill-team/rosters/{roster_id}/operatives/{row_id}` | move it in the printed order |
| `DELETE /me/kill-team/rosters/{roster_id}/operatives/{row_id}` | remove that one |

**Your games (15).** A game snapshots everything it needs at creation and then reads
nothing from the catalog.

| | |
|---|---|
| `GET /me/kill-team/games` | your games, newest first, lean — no snapshots |
| `POST /me/kill-team/games` | start a battle from one of your rosters, copying every datacard, the team's rules and both ploy lists |
| `GET /me/kill-team/games/{game_id}` | the battle whole: operatives, equipment, reference and the event log, in one request |
| `PATCH /me/kill-team/games/{game_id}` | the nine fields `_UPDATABLE` holds: `opponent_name`, `status`, `initiative`, `command_points`, `victory_points`, `opponent_victory_points`, `markers`, `ploys_used`, `choices`. Takes an **optional** `version`, and answers 409 when one is sent and the game has moved on |
| `DELETE /me/kill-team/games/{game_id}` | delete the game and its rows; the roster and catalog are untouched |
| `POST /me/kill-team/games/{game_id}/operatives` | field a datacard a rule or a piece of equipment granted mid-battle |
| `PATCH /me/kill-team/games/{game_id}/operatives/{row_id}` | wounds, order, tokens, actions used. Zero wounds incapacitates |
| `POST /me/kill-team/games/{game_id}/operatives/{row_id}/activate` | stamp the activation with the game's own turning point — not a value the client sends |
| `POST /me/kill-team/games/{game_id}/operatives/{row_id}/transform` | re-snapshot the row's datacard in place, keeping its id, tokens and position |
| `POST /me/kill-team/games/{game_id}/equipment` | take a piece into this battle, with its text copied |
| `PATCH /me/kill-team/games/{game_id}/equipment/{row_id}` | reveal it |
| `DELETE /me/kill-team/games/{game_id}/equipment/{row_id}` | put it back; the event carries the whole row so undo can restore it |
| `POST /me/kill-team/games/{game_id}/ploys` | spend a ploy by name — logs it and deducts its cost in **one** event, so undo reverses both |
| `POST /me/kill-team/games/{game_id}/advance` | next phase, next turning point, or finish. The server applies the resets |
| `POST /me/kill-team/games/{game_id}/undo` | revert the newest standing event and append a compensating one. The only way to reopen a finished game |

**Planned — K8, the second side.**

| | |
|---|---|
| `POST /me/kill-team/games/{game_id}/sides/{side}/ploys` | *replaces* `POST .../ploys` — deducts that side's command points. **Named by #67** |
| `PATCH /me/kill-team/games/{game_id}/sides/{side}` | that side's CP, VP, ploys used and per-game choices, which move off the game onto the side. *Shape follows from #67; not named by it* |
| `POST /me/kill-team/games/{game_id}/sides/{side}/operatives` | field a datacard on that side. *Follows from #67.* The row-keyed operative routes above need no change, since a row id already identifies one operative |
| `POST /me/kill-team/games/{game_id}/sides/{side}/equipment` | take a piece for that side. *Follows from #67* |

A side row carries `occupant_user_id`, nullable (#74), written in K8 and unread until
K9. NULL means no account holds that side, which is the ordinary case: an opponent
across the table who has not installed anything. The row is created once that side's
kill team is known, not at game creation, so a game can be in a state where one side
exists and the other has not been described yet.

How the opponent's side is established — a second roster, a hand-filled side, or a
separate route — is **not yet decided**.

**Planned — K9, a second player.**

| | |
|---|---|
| `GET /me/kill-team/games/{game_id}/events?since={sequence}` | the events after that sequence, with the game's current `version`. How both clients converge. **Named by #72** |
| `POST /me/kill-team/games/join` | redeem an invite code, sent in the **body** so it never reaches an access log. **Named by #71** |
| `POST /me/kill-team/games/{game_id}/invite` | mint a code and its expiry; writing a new one revokes the old, because there is only one slot. *Follows from #71; not named by it* |

No route widens who may read a game, but **two** predicates change, not one (#75).
Thirteen nested operations reach the game through one dependency, where "owner" becomes
"owner or participant" and the 404-rather-than-403 behaviour is unchanged. The listing
is the second: `GET /me/kill-team/games` filters the owner inside `list_games` and never
calls that dependency, so without widening it too, a game you are playing would be
missing from the list of your games.

## Current state

Measured on this branch (`main` @ `366f918`):

| | |
|---|---|
| Tests | **712 passing, 1 skipped** (SQLite tier); **713** on the Postgres parity tier |
| Test modules | 34 |
| Tables | 26 — 11 Army List and shared, 15 Kill Team |
| Migrations | 4, and CI fails if they disagree with the models |
| API | 49 paths, 72 operations, published as a committed `openapi.json` |
| Recorded decisions | **73**, in [`KILLTEAM.md`](KILLTEAM.md), each with its reasoning — 66 at `366f918`, plus the seven this branch adds for shared play |
| CI gates | 7 — see below |
| Catalog data | ~1,331 40k units; all 48 Kill Team teams parse and seed |

**Both halves of the backend shipped.** The Army List API and the Kill Team API are
merged and tested at the commit above. "Feature-complete" was true when that commit was
cut and is not any more — widening the MVP to shared play and honest points added three
backend slices, which are in the table under [What "finished" means](#what-finished-means).

**Seven CI gates**, every one blocking: `ruff check`, `ruff format --check`,
`lint-imports` (the layering contracts), a check that the committed `openapi.json`
matches the app, pytest on SQLite, `alembic check` against a freshly migrated
database, and pytest again on real Postgres.

That last pair is the one worth explaining. The default test tier builds its schema
with `create_all` from the models, which is fast but means those tests validate the
*models* rather than the shipped schema. The parity tier runs the real migrations
against Postgres, so the two together catch a class of bug — an unbounded column, a
dialect-specific query — that neither catches alone.

**The client is three slices of five — on the `kill-team-ui` branch, not `main`.**
That branch is 70 commits ahead of the client's `main`, which carries no Kill Team UI
at all: no route, view, query or type. A reader who clones `main` sees the 40k half
only. On [`warhammer_web`][web]'s `kill-team-ui`: the two-section
shell, the public Kill Team catalog, and the roster builder are done; the battle
screen is not. So the API can do things no screen can yet reach.

## How a player uses it

**Army List.** Browse the catalog by faction. Record the models you own. Create an
army with a points limit, add units to it, and the app reports the running total,
whether the list fits, and the *shortfall* — which units the list needs that your
inventory does not have. It never refuses a list; it tells you what is true about it.

Each entry also shows the datasheet's **printed composition** — the models the unit is
made of, worded as the page words it — and prices the size you actually chose rather
than the cheapest one. The composition is shown and never applied: a squad at a size
the page does not print is still saved, and the list says so instead of refusing it.

**Kill Team.** Browse the kill teams and their datacards, with each team's printed
composition shown as printed. Build a roster: one row per operative, so two identical
models are two rows you can tell apart — "which of my two Warriors is on 4 wounds?"
is a question the app can answer. Then start a battle from that roster and track it:
wounds, order, tokens, activations, command points, victory points, equipment
revealed mid-game, ploys spent. Every change is one event in an append-only log, and
**undo** walks it back one step at a time.

Both sides of the table are tracked, not just yours. You can fill the opponent's side
in by hand, or send them a time-limited invite code and let them keep their own side
from their own phone — the same game, each side picking up the other's changes
within a few seconds (the mechanism is [below](#how-two-players-stay-in-sync)).

## How the project is operated

**Catalog data** is scraped, not hand-entered, and the two stages are separate so a
scrape never writes to the database:

```
scrape_wahapedia_kt.py  →  scripts/data/killteam.json  →  seed_killteam.py  →  DB
```

The Kill Team seed is an **upsert with a withdrawal sweep**: a row whose payload
changed is rewritten, and a row the source no longer mentions is flagged rather than
deleted. The sweep is scoped to the parents actually present in the payload, so a
partial scrape cannot mass-flag a catalog it simply did not mention.

**Schema changes** go through the models and a migration, never raw SQL:

```
edit models.py  →  alembic revision --autogenerate  →  alembic upgrade head
                →  alembic check   (CI fails if they disagree)
```

**Tests** run in two tiers, as above: `pytest` for SQLite, and `pytest` with
`TEST_DATABASE_URL` set for the migration-built Postgres tier.

**Deploy** is documented in [`DEPLOY.md`](DEPLOY.md) and
[`DEPLOY-GCP.md`](DEPLOY-GCP.md). `DATABASE_URL` is required; it lives in `.env`,
which is not committed.

## What "finished" means

The MVP is finished when a player can do the whole of both loops in a browser — with
both sides of a Kill Team battle tracked and an army list priced for the size actually
fielded. Most of the API is there and the client is further behind, but the API is no
longer only waiting on the client: three of the five remaining slices are its own.

**Remaining for the product:**

| | Where | |
|---|---|---|
| The battle screen's state half | `warhammer_web` KF4 | create a game, activations, wounds, order, tokens, turning point, CP/VP. Carries the only genuinely new problem left: the game's `version` can 409 on any write, and a phone and a laptop at the same table is the normal case |
| The battle screen's reference half | `warhammer_web` KF5 | the team tab, equipment reveal, ploys, transform, the undo log |
| The second side of the table | this repo, **K8** | `kt_game_sides`, so a game holds two kill teams with their own operatives, equipment, CP and VP. No second user yet — one person still operates the app, which is what makes an account-less opponent representable |
| A second player in the session | this repo, **K9** | a time-limited invite code, two authorization predicates widened from owner to participant, and both clients converging by polling the event log. The column that says who occupies a side lands in K8 (#74), because adding it later is a migration with a backfill |
| Unit composition and honest points | this repo, **R13** | the datasheet's printed composition stored as its own words, and points that follow the model count instead of billing every squad at its cheapest legal size |

### How two players stay in sync

Both phones read the same game, and the game's own **event log** is what keeps them
agreed — not a live connection (decision #72).

Nearly every mutation appends exactly one event, carrying a sequence number that is
monotonic within the game. So a client never asks "what is the state now?"; it asks what
happened after the last number it saw — a cursor read over a log that already exists and
already drives undo:

```
GET /me/kill-team/games/{id}/events?since=47
  →  { events: [ 48, 49, 50 ], version: 51 }
```

Each client polls that while the battle screen is actually in front of someone, and backs
off when it is not. The answer is usually empty; when it is not, it is two or three rows.

**"Nearly" is doing work in that first sentence.** Two mutations write no event at all —
creating a game and deleting one — so #81 gives both one, because a log that is the only
convergence channel makes anything missing from it invisible. Deletion was the harmful
case: a guest's first notice would be a 404 on the next poll, with nothing to explain it.
A write that sends a value already stored also records nothing, which stays deliberate
(#58): an empty event would spend the next undo reverting nothing.

**Writes stay ordinary `PATCH` and `POST` calls carrying absolute values** rather than
deltas (#7). Every one bumps the game's `version` — but bumping is universal and refusing
is not, which the earlier version of this section elided. Measured against the contract:
of the twelve nested writes, **two** accept a `version` and it is optional on both,
**four** answer 422 to an extra key, and **six** declare no request body, so a `version`
sent to them is silently discarded with a 200. So #79 moves it into an `If-Match`-style
header, required on all twelve, which reaches the bodiless ones without reshaping four
schemas.

**A 409 means re-read and re-decide, not re-read and retry** (#78). The difference is not
stylistic. You read at version 12 with an operative on 5 wounds and tap 4; your opponent
sets it to 1; your write is refused; you re-read and retry your 4, and their write is
gone with neither of you told. A blind retry discards the conflict the version check just
detected — the same defect #70 rejected per-player undo over. So the screen owes you the
comparison and a person chooses.

One thing this does not solve, named rather than hidden (#80): a single `version` on the
game means two sides writing **disjoint** fields still collide — your wounds against
their command points. A version per side would fix it and cannot be done without
re-solving `KTGameEvent.sequence`, whose uniqueness holds only because every writer
touches the shared row.

**Why not WebSockets.** Mostly because the catch-up endpoint above is mandatory
whatever the transport: Cloud Run caps a single request at 60 minutes and a Kill Team
game runs longer, phones sleep, and venue wifi is venue wifi. So a socket would be an
addition to polling rather than an alternative to it. It would also need
infrastructure this stack deliberately does not have — the service runs at
`--max-instances 5` with no session affinity, so two sockets land on different
instances and an in-process broadcast reaches only one of them, which is fixable only
with a pub/sub backplane. Meanwhile `--min-instances 0` means an open socket pins and
bills a container for the whole game, which is what scale-to-zero was chosen to avoid.
What all of that would buy is nought to three seconds, against an opponent sitting
across the table. If the latency ever genuinely matters, server-sent events come
first: writes stay POSTs and the stream carries only a sequence number, so this
endpoint remains both the fetch and the fallback.

**Hardening, also in this repo** — separate from the three feature slices in the table
above, and none of it blocking a feature:

- **M4** — `passlib` imports Python's `crypt`, **removed in 3.13**. Works today on
  3.12; blocks an interpreter upgrade. Moving to `bcrypt` directly is small.
- **H1 + C** — the 40k seed is **create-only**, so editing a stat in `datasheets.json`
  and re-running does nothing. The Kill Team seed already upserts; porting it is small.
  The same pass should rekey the seed on `(faction, subfaction, unit_name)`, which is
  what collapses 1,558 scraped rows to roughly 1,331 stored ones.
- **L2** — the scraper **writes before it validates**, so a malformed parse reaches
  the JSON.
- **L1** — no rate limiting exists anywhere. A prerequisite for K9's invite code.
- **L3** — a **deep readiness probe** is deliberately condition-gated. Build it when a
  load balancer or an orchestrator arrives, not before.
- Roughly 20 places where a test exists but cannot fail, found by mutation testing
  and **not yet written down anywhere**. The count is a recollection, not a record,
  which makes it the least trustworthy figure in this document.

**Closed since the earlier backlog**, named because `SPEC.md` and `CODE-REVIEW.md` still
cite the IDs: **FE1–FE3** (frontend unblockers), **H2** (`IntegrityError` → 409 is
registered in `app/main.py`), **H3 + M1** (`selectinload` on the list paths), **M3**
(`EmailStr` on register), **M5** (Sentry, request ids, structured logs) and **L7** (the
last-admin guard). The stale-doc cleanup that entry also listed is done.

## What success looks like

The section above lists work. This one is the test, because a list of work items
cannot tell you when to stop. Each of these is checkable, and all of them must hold
**without a terminal, a database client, or a second machine**:

| | |
|---|---|
| **The 40k loop closes** | A new user registers, records the models they own, and builds an army list — start to finish in a browser. Each entry MUST show the datasheet's printed composition. The list MUST price the squad size chosen, not the cheapest. It MUST report the total, whether the list fits its limit, and which units are still to buy. |
| **The Kill Team loop closes** | A player MUST be able to go catalog → roster → battle for any of the 48 teams. A full four-turning-point game MUST track wounds, order, tokens, activations, CP, VP, equipment revealed mid-game and ploys spent. |
| **Two players, two devices, one game** | Both sides MUST be tracked, each player holding their own. Each client MUST pick up the other's changes within seconds, without a reload. |
| **The game is reconstructable** | The event log MUST account for every change and name who made it. Undo MUST reverse changes one step at a time. |
| **A mistake is cheap** | A premature finish MUST cost one undo. It MUST NOT need a reopen path, a support request or a database edit. |
| **A re-scrape is safe** | Updating the catalog MUST NOT change a game in progress. A finished game MUST still show the datacards as they were played. |
| **The gates stay green** | All seven CI gates MUST pass. That includes the Postgres tier, which is built by real migrations rather than from the models. |

Rows four, five and six are the ones worth arguing about, because they are the
project's distinguishing claims rather than table stakes. A tracker that cannot
reconstruct the game, or that needs an administrator to undo a fat-fingered tap, is a
notebook with extra steps.

The honest criterion no checklist covers: it gets used at an actual table, in
preference to a notebook. Everything above is necessary for that and none of it is
sufficient.

## Deliberate non-features

Absent on purpose. Each is a decision with reasoning recorded in
[`KILLTEAM.md`](KILLTEAM.md) or [`SPEC.md`](SPEC.md), not a gap:

- **No rules engine.** The app never decides whether a play is legal. Dice are
  physical; judgement is the players'. (#1)
- **No legality enforcement on a roster or an army list.** Composition is stored as the
  *printed words* — one row per line, with its indent — and nothing is derived from it:
  no budget, no caps, and no model count on the catalog's composition itself. It
  describes; it never refuses. (#28, #44) R13's model count is a different thing wearing
  the same words: it sits on the player's **army-list row**, recording the size they
  chose so it can be priced, and it is never read back as a limit.
- **No equipment allowance enforced.** A game reports the allowance beside what has
  been taken, and accepts the fifth piece. A client that hard-coded "4" to block it
  would be holding a rule of the game. (#57)
- **No Tac Ops or archetype gating.** A chosen Tac Op is a free-form per-game note.
  Gating a choice by archetype would be applying a rule. (#59)
- **No redo.** Undo walks the event log back one step; there is no forward. (#58)
- **No catalog editing from the app.** The Kill Team catalog has no write route at
  all — every path under it declares only `get`. An admin edit would be undone by the
  next seed anyway. (#21, #49)

## Known gaps and accepted trade-offs

Stated plainly because a reviewer should not have to find them:

- **The 40k catalog's provenance is unresolved.** `datasheets.json` is scraped
  third-party data and it **is committed** — 2,112,565 bytes, tracked on every
  branch. `.gitignore` records the state rather than the intent: it excludes the Kill
  Team scrape because it is "a copy of someone else's content", and notes that "the
  40k equivalent (`scripts/data/datasheets.json`) ended up committed". So the open
  question is not whether that data stays in the project; it is already published.
  The create-only seed above is downstream of it.
- **The two test tiers are asymmetric.** Only one of the 713 tests is tier-gated, so
  on SQLite 712 of them validate the models rather than the shipped schema. The
  parity tier exists precisely for that, and it runs in CI — but a developer running
  `pytest` locally is testing the weaker thing.
- **No end-to-end tests.** The API is tested thoroughly and the client is tested
  thoroughly; nothing exercises the two together. The committed `openapi.json` and
  the generated client types are what stand in for that, and they catch a contract
  *shape* change rather than a behavioural one.
- **`points_total` is understated today, which is worse than approximate.** It computes
  `SUM(squads × minimum-size price)`, because the scraper reads each datasheet's
  `(model count, points)` pairs and keeps only the cheapest — so a ten-model squad is
  billed at the five-model cost. R13 fixes it. The fix is blocked by a uniqueness
  constraint that makes two differently-sized squads of one datasheet unrepresentable,
  which is why it is a slice rather than a column.
- **The 40k catalog holds no composition at all.** The scraper locates the composition
  block on each page specifically in order to skip it, so the thing a list builder most
  obviously needs is the one thing not captured.
- **No deploy automation.** Deployment is documented and manual.
- **Mutation testing is manual.** It has found real defects — tests that passed for
  the wrong reason, in both repos — but it is run by hand rather than in CI.

## Security, dependencies and risks

**What is stored about a user.** A username, an email address and a bcrypt hash of
their password. Nothing else — no name, no location, no payment details, no analytics
identity. The only other personal data in the database is what a user typed in
themselves: their army lists, their rosters and their games.

**How access works.** Password login issues a JWT (HS256) with a **two-day** default
lifetime. There is no refresh token and no revocation list, so a leaked token is valid
until it expires — short lifetimes are the only mitigation, and two days is a
convenience choice rather than a security one. Reads and writes are scoped to the
authenticated user, and a row belonging to someone else answers **404 rather than
403**, so an id never discloses whether it exists. Ownership is also enforced by the
schema itself, not only by the service — see the data model above.

**What is not protected, stated plainly:**

- **There is no rate limiting anywhere in this service.** Not on login, not on
  registration, not on the Kill Team invite code that K9 introduces. For the invite
  code this is a named prerequisite rather than an oversight (decision #71), because a
  short code with no attempt limiting is the one place it would really bite.
- **No audit log of authentication.** Game events record their actor; logins do not.
- Error reporting sends no personal data — `send_default_pii` is left at its default of
  off, and reports carry a request id as a tag — but that is a default, not an
  assertion that has been tested.

**The one dependency worth naming.** Every datasheet and every datacard in the product
comes from **one third-party site**, Wahapedia: 1,331 40k units and all 48 kill teams.
A markup change, a block, or that site going away stops the pipeline for both halves at
once, and there is no second source and no cached fallback beyond whatever JSON is on
disk. The two-stage split (scrape to a file, seed from the file) limits the damage — a
broken scrape cannot corrupt a working database — but it does not replace the source.

**Dated risk.** `passlib` imports Python's `crypt`, which was **removed in 3.13**. The
service runs on 3.12 and is fine today, but this is a deadline rather than a chore: it
blocks every future interpreter upgrade until it is done.

**Capacity, for honesty rather than because it is close.** `DEPLOY-GCP.md` works the
arithmetic: `--max-instances 5` with SQLAlchemy's default pool is up to 75 connections,
against roughly 25 on a `db-f1-micro`. So the first real traffic spike would fail as
`FATAL: too many connections` while the database sat nearly idle. Nothing about a
single-player app makes that likely; it is written down because the number is knowable
in advance.

## Out of scope

Not planned, as distinct from not finished:

- **Leagues, tournaments, a public profile, a shared collection.** Two players share
  a *battle* (K8, K9) and nothing else: a collection, an army list and a roster stay
  one user's.
- **Wargear and loadout modelling**, datasheet versioning, list export or sharing.
- **40k match tracking.** The Kill Team tracker exists because Kill Team's game state
  is small enough to model honestly; a 40k battle is not.
- **Offline use, mobile apps, internationalisation.**
- **A rules engine**, in any form. See above — this one is load-bearing.

---

## Glossary

The game terms this document uses, in the order you meet them. Both games are Games
Workshop's; these are plain-language definitions, not rules.

| Term | |
|---|---|
| **Warhammer 40,000** ("40k") | The large-scale tabletop game. Dozens of models per side, played from army lists costed in points. Muster's **Army List** half serves this. |
| **Kill Team** | The small-scale game in the same setting. Roughly six models per side, played over four rounds. Muster's **Kill Team** half serves this. The 2024 edition is the one modelled. |
| **faction** / **subfaction** | The army a model belongs to, and a narrower grouping inside it. |
| **datasheet** (40k) | The printed page for one unit: its stats, the weapons it may take, its abilities. Read-only reference data in Muster. |
| **datacard** (Kill Team) | The Kill Team equivalent, for a single model rather than a unit. |
| **unit** (40k) | One or more identical models bought and fielded together. |
| **operative** (Kill Team) | A single model. One operative is one miniature — which is why a roster holds one row per operative rather than a count. |
| **kill team** (lowercase) | A named group of operatives you may choose from, such as Raveners. 48 of them exist. |
| **army list** (40k) | A chosen set of units, costed in points against a limit agreed before the game. |
| **roster** (Kill Team) | The Kill Team equivalent: the operatives you have picked for a team. No points — Kill Team composes by rules printed per team instead. |
| **turning point** | One round of a Kill Team game. There are four, after which the game ends. |
| **command points** (CP) | A per-player currency spent during a game, mostly on ploys. |
| **victory points** (VP) | The score. Whoever has more at the end wins. |
| **ploy** | A one-off effect a player buys with CP during a game. Some belong to a kill team, others are universal to every team. |
| **equipment** | Gear chosen for one battle rather than kept on the roster. Some is revealed mid-game. |
| **Tac Op** | A secret objective a player picks at the start of a game. Muster records the choice as free text and nothing more (decision #59). |
| **archetype** | A label on a kill team that would normally restrict which Tac Ops it may pick. Not captured, and deliberately not enforced. |

## Where to read more

| | |
|---|---|
| [`README.md`](README.md) | how to run it |
| [`SPEC.md`](SPEC.md) | the full architecture and every route |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | the structural rules, each with a status |
| [`KILLTEAM.md`](KILLTEAM.md) | the Kill Team design — 73 numbered decisions |
| [`ROADMAP.md`](ROADMAP.md) | build order and the remaining backlog |
| [`CODE-REVIEW.md`](CODE-REVIEW.md) | a dated correctness review, with outcomes |
