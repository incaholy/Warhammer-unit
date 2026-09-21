# Kill Team game tracker — design

The design reference for the Kill Team part of the backend: what it is, the
decisions behind it, and the order it is built in. Check new Kill Team work against
this document, and update it when a decision changes.

Branch: `fire-team` (cut from `roadmap`). See "Branching" at the end.

## Goal

Let a user play a game of **Kill Team (2024 edition)** with the app as the table's
bookkeeper: pick a kill team, build a roster, then track a game turn by turn —
turning points, CP, VP, each operative's wounds, order and activation.

The app **tracks**; the players **resolve**. Dice are physical and rules are applied
by the players. Encoding the rules themselves (a rules engine) is out of scope.

## Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Scope | Game tracker (catalog → roster → game) | Playable at the table without encoding every rule |
| 2 | Models | Separate `kt_*` tables; reuse shared infrastructure | Kill Team stats (APL, normal/crit damage) don't fit the 40k `Unit`/`Weapon` columns |
| 3 | Data source | Wahapedia, same two-stage scrape → seed as 40k | One pipeline shape to maintain |
| 4 | Edition | 2024 | Current edition |
| 5 | Players | One player per game; opponent's VP entered by hand | No sharing, permissions or live sync in v1 |
| 6 | Game stats | **Snapshot** operative stats into the game at creation | A re-scrape must not change a game in progress |
| 7 | Game updates | **Absolute values** (`wounds: 7`), never deltas | Retry-safe on a flaky table-side connection (same reasoning as R12) |
| 8 | History / undo | Current state in columns **plus** an append-only event log | Simple reads; undo reverts the last event. Not full event sourcing |
| 9 | Concurrent edits | `version` column, stale write → 409 | Two tabs can't silently overwrite each other; 409 handling already exists |
| 10 | Delivery | **Thin slice**: one or two kill teams through every phase, then widen | Model mistakes surface in week one, not after every team is seeded |
| 11 | Separation | Kill Team and the 40k army list builder are **two sections**: `/kill-team` and `/army-list` | Two games with different rules; neither's routes, models or views leak into the other |
| 12 | Finding a kill team | A flat **Kill Team faction** list (`KTFaction`), e.g. Tyranids → Raveners. No Imperium / Chaos / Xenos level, and no link to the 40k `Faction` → `Subfaction` lookup | The army name is what people look for; the 40k lookup puts the army on different levels (Tyranids is a subfaction, Space Marines a faction) and would tie the two games' faction lists together |
| 13 | Team rules | Rules belong to the **kill team** (`KillTeamRule`), not the faction | Two kill teams in the same faction can have different rules |
| 14 | Weapons and abilities | Belong to **one operative** (a plain FK), not shared through link tables the way 40k's `unit_weapons` / `unit_abilities` do | A datacard lists its own profiles, and the same weapon name on two operatives can carry different numbers; sharing would mean deduplicating on name *and* every stat |
| 15 | Weapon range | One non-null `range` column: the number from a printed `Range x` weapon rule when there is one, otherwise **1 for melee, 2 for range**, filled by a context-sensitive column default | A 2024 profile prints ATK/HIT/DMG/WR and no range — distance appears only as a weapon rule — so the column needs a defined meaning rather than a blank |

Build order and status are tracked in ROADMAP.md (K1–K6), not here.

## Separation from the 40k army list builder

Kill Team and the Warhammer 40k army list builder are separate parts of the app,
divided by path:

| | 40k army list builder | Kill Team |
|---|---|---|
| Frontend (`warhammer_web`) | `/army-list/...` | `/kill-team/...` |
| Backend catalog | existing routes, unchanged (`/api/v1/units`, `/factions`, …) | `/api/v1/kill-team/...` |
| Backend, user-owned | existing routes, unchanged (`/api/v1/me/armies`, `/me/inventory`) | `/api/v1/me/kill-team/...` |
| Models | `Unit`, `Weapon`, `Army`, … | `kt_*` tables |
| Faction lookup | `Faction` → `Subfaction` | `KTFaction` (flat, separate) |

- **Frontend:** the current 40k views (`/`, `/catalog`, `/inventory`, `/armies/...`,
  `/units/...`) move under `/army-list`, with redirects from the old paths so
  bookmarks keep working. This happens with the first Kill Team frontend view.
- **Backend:** Kill Team routes get their own prefix. The existing 40k API routes
  are **not** renamed. That would be a breaking change for the frontend with no gain,
  and can be decided separately if it ever matters.
- Shared infrastructure (auth, errors, paging, transactions) is shared on purpose;
  the separation is between the two games, not their plumbing. Faction lists are
  **not** shared: each game groups its content its own way.

## What is reused as-is

- `TimestampMixin` (`app/core/db/models.py`)
- `Page` / `PageParams` / `paginate` (`app/api/pagination.py`)
- Coded errors — `NotFoundError`, `ConflictError`, per-service `*ValidationError`,
  all mapped by the single `CodedError` handler (`app/main.py`)
- One transaction per request (`get_session`, `app/core/db/connection.py`)
- Auth dependencies — `get_current_user`, `get_current_admin` (`app/api/deps.py`)
- `not_nullable_fields()` for PATCH null guards (`app/core/db/columns.py`)
- `fetch()` — cached, polite HTTP (`scripts/scrape_wahapedia.py`)
- Import-linter layering, the Postgres parity tier, and the `openapi.json` gate apply
  to new modules automatically.

## Data pipeline

- `scripts/scrape_wahapedia_kt.py`: reuses `fetch()`; **pure** parse functions for
  operatives, weapons, abilities, ploys, equipment, each tested against saved HTML in
  `tests/fixtures/`.
- Output goes to a **gitignored** path; the tracked file stays an empty template.
- `scripts/seed_killteam.py`: idempotent, natural-key upserts, `SeedError` / `_ref`
  pattern from `scripts/seed_datasheets.py`.
- `make scrape-kt`, `make seed-kt`.

**Input needed:** saved HTML for one or two kill teams and the universal equipment
page. The catalog tables are built before this (models first), and the pipeline fills
them.

## Catalog

Provisional tables — the migration is a **draft until `fire-team` merges**: a column
the saved pages show is wrong is fixed and the migration regenerated. Checked so far
against: Raveners.

| Table | Holds |
|---|---|
| `KTFaction` | name (unique) — the Kill Team faction list (see "Factions") |
| `KillTeam` | name, **operative count**; FK `KTFaction` |
| `KillTeamRule` | name, text; FK kill team — team-wide rules (e.g. Raveners' Burrow, Tunnel, Predatory Instincts) |
| `KTOperative` | name, APL, move, save, wounds, keywords, **required**, **max per roster** (null = no limit); FK kill team |
| `KTWeapon` | name, `category` (`range`/`melee`, the same two values as the 40k column), `range` (decision #15), attacks, hit, normal damage, crit damage, weapon rules (JSON); FK operative |
| `KTAbility` | name, text (includes unique actions); FK operative |
| `KTPloy` | name, strategy or firefight, CP cost, text; FK kill team. The Raveners page shows no cost, so the column takes a default when the page gives none |
| `KTEquipment` | name, text; FK kill team, or null for universal |

- `app/core/services/service_killteam.py`, `app/api/killteam.py`.
- Routes under `/api/v1/kill-team/...`; public read, admin write (same policy as the
  40k catalog).

### The datacard

An operative owns its weapons and abilities (decision #14), so deleting one takes
them with it, and deleting a kill team cascades through operatives to both.

`range` (decision #15) is filled by a **context-sensitive column default**: one
column, a value that depends on the row's `category`. SQL's `DEFAULT` cannot do that
— and a SQLModel `model_validator` cannot either, because table models skip
validation — so the default is a callable on the column that reads the row's
`category` at insert. A `server_default` of 1 backs it for a raw-SQL insert that
names no `range`, and `CHECK (range >= 1)` holds whatever the caller passes, since a
default only fires when the column is omitted.

### Factions

Kill teams are grouped by a flat faction list of their own, one level deep:

    Tyranids (KTFaction) → Raveners (KillTeam)

- Just the faction name: no Imperium / Chaos / Xenos grouping above it, and no link
  to the 40k `Faction` / `Subfaction` tables.
- Faction names come from **one place in code**: the scraper's config maps each kill
  team's page to its faction name, the way `FACTIONS` does in
  `scripts/scrape_wahapedia.py`. The name is never read off the page, so a typo can
  only happen once, in that map, where it's visible in review.
- A table rather than a text column: the name is unique, so the seed finds the
  existing row instead of creating a duplicate, filters use an id like the rest of
  the API, and a rename is one row.
- `KillTeam.faction_id` is required. The seed script finds or creates the faction by
  name.
- `GET /api/v1/kill-team/factions` lists them (paged); the kill team list takes a
  `?faction_id=` filter, and the frontend can group the list by faction.
- Factions are for **finding** a team only. Rules are never attached to a faction:
  they live on `KillTeamRule`, per kill team, since two teams in the same faction can
  have different rules.

## Roster

Mirrors `Army`.

- `KTRoster` (owner, kill team, name), `KTRosterOperative` (with an `amount`, like
  `ArmyUnit`), `KTRosterEquipment`.
- Under `/api/v1/me/kill-team/rosters/...`; add is create-only (409 on repeat),
  PATCH for changes.
- `GET .../validate` **reports** composition problems rather than blocking saves;
  harden later, team by team.

### Roster limits

Every kill team limits what a roster may contain. Three kinds of limit, all stored as
**data** on the catalog (columns above), not written as code per team:

| Limit | Where it lives | Example |
|---|---|---|
| **Team size**: there is *always* a set number of operatives | `KillTeam.operative_count` | Raveners: 5 (1 Prime + 4 others) |
| **Required**: some operatives must be in the roster | `KTOperative.required` | Raveners: the Ravener Prime (leader) |
| **Per-operative cap**: how many of the same operative may be taken | `KTOperative.max_per_roster` | Raveners: `1` for each specialist; `null` for Warriors, which can be taken without restriction (still bounded by team size) |

`validate` checks all three and reports each problem with the operative it concerns.
The per-operative cap is checked against `KTRosterOperative.amount`; team size against
the sum of amounts, reporting a roster that is over **or under** size. When a game starts, each copy becomes its own `KTGameOperative`,
since every copy tracks its own wounds and order.

This is roster bookkeeping, not rules implementation: the app counts what is in the
roster against limits read from the data, the same way it checks wounds against a
maximum during a game. Some kill teams have selection rules these three columns
can't express (e.g. "choose one of these options"); those are recorded when the
fixtures show them, and handled as a later addition rather than guessed at now.

## Game tracker

| Table | Holds |
|---|---|
| `KTGame` | owner, roster, opponent name, status (setup / in progress / finished), turning point (1–4), phase (strategy / firefight), initiative, CP, VP by source, opponent VP, **markers** (JSON list), `version` |
| `KTGameOperative` | **snapshot** of the operative's stats, current wounds, order (engage / conceal), activated this TP, **status** (reserve / on board / incapacitated), **tokens** (JSON list) |
| `KTGameEvent` | append-only: type, turning point, payload (JSON) |

Service-enforced bookkeeping (→ 400 `VALIDATION`), not rules:

- wounds within `0..max`; `0` sets status to incapacitated
- one activation per operative per turning point; advancing clears activations
- CP never negative; no turning point past 4

**Tokens, markers and reserve.** Teams put tokens on operatives (Raveners: Poison,
Heightened Senses, …) and markers on the killzone (Raveners: Tunnel markers 0–4), and
some operatives start off the board (Raveners' Burrow). The tracker records these
generically: `tokens` is a list of token names on an operative, `markers` a list of
team-level markers on the game, and `status = reserve` an operative not yet on the
board. It records that they exist; the players apply what they do. No team-specific
columns, so a new team needs no migration.

Endpoints under `/api/v1/me/kill-team/games/...`:

- `POST` (from a roster), `GET` list / detail
- `PATCH .../operatives/{id}` — wounds, order, activated
- `POST .../advance` — next phase / turning point; the server applies resets
- `PATCH /{id}` — CP, VP
- `POST .../undo` — revert the last event

## Frontend

Catalog browse → roster builder → game screen. The game screen is phone-first for
table-side use: large touch targets, one card per operative, turning point / CP / VP
always visible.

## Branching

`fire-team` was cut from `roadmap`. Merge PR #4 with **"Create a merge commit"** and
nothing further is needed. If it is squashed or rebased instead, move this branch
before opening its PR:

    git fetch origin
    git rebase --onto origin/main roadmap fire-team

## Out of scope for v1

- Rules engine (dice resolution, weapon-rule automation)
- Two-player shared games and live sync
- Killzone / terrain modelling
- Editions other than 2024
