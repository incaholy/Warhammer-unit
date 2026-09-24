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
| 16 | Composition | A kill team's composition is **budgeted selection lists** (`KTSelectionList` → `KTSelectionOption`), not a headcount with a leader. An option carries its `cost` in selections, how many `models` it fields, and its own `max_selections` cap | The pages never say "leader": they say "1 X operative", then "4 X operatives selected from the following list". A budget of 1 over one option *is* required; over several it is "choose one". Weighted costs (Brood Brother's Magus counts as two selections) and pairs ("2 PSYCHIC FAMILIAR … still counts as one selection") cannot be expressed by a count and a flag |

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

- `scripts/scrape_wahapedia_kt.py`: reuses `fetch()`; **pure** parse functions, each
  tested against a **synthetic** fixture in `tests/fixtures/` — one reproducing the
  DOM structure with invented names, as `wahapedia_datasheets.html` does, so no
  scraped content is committed to a public repo.
- Output goes to `scripts/data/killteam.json`, which is **gitignored**. The 40k
  equivalent ended up tracked; this one is not.
- `scripts/seed_killteam.py`: idempotent, natural-key upserts, `SeedError` / `_ref`
  pattern from `scripts/seed_datasheets.py`.
- `make scrape-kt`, `make seed-kt`.

**Which teams: discovered, not configured.** The site's nav is one small standalone
file (`nav.html`, assembled by JS, which is why a page's own HTML does not contain
it), and its "Kill Teams" dropdown carries every team with its faction and slug — 48
teams across 22 factions. `parse_nav` reads it, so there is no hand-maintained list
to drift, and a team added later appears on the next run. Two grouping levels are in
that markup and only one is ours: `FactionHeader` (Imperium / Chaos / Xenos /
**Aeldari**) is dropped, `factionGroup_KT` is the `KTFaction`. That asymmetry is also
the proof of decision #12 — 40k files Aeldari as a subfaction *under* Xenos, where
Kill Team makes it an alliance above Craftworlds, Corsairs and Harlequins.

Labels are normalised on the way through (curly quotes to straight, `&nbsp;`
stripped) as a rule rather than a per-faction exception list: the nav writes
"T’au Empire" where we store "T'au Empire", and two spellings would seed two rows.

**Still needed from a browser:** the per-team pages carry the stats, and fetching
them is what 403s intermittently, so save Raveners, one other kill team, and the
universal equipment page into `tests/fixtures/` when it is time to write those
parsers. `nav.html` needs no save — it fetches reliably.

## Catalog

Provisional tables — the migration is a **draft until `fire-team` merges**: a column
the saved pages show is wrong is fixed and the migration regenerated. Checked so far
against: Raveners.

| Table | Holds |
|---|---|
| `KTFaction` | name (unique) — the Kill Team faction list (see "Factions") |
| `KillTeam` | name; FK `KTFaction`. **No operative count** — see decision #16 |
| `KillTeamRule` | name, text; FK kill team — team-wide rules (e.g. Raveners' Burrow, Tunnel, Predatory Instincts) |
| `KTOperative` | name, APL, move, save, wounds, keywords; FK kill team. Whether a roster may take it, and how often, belongs to the list offering it |
| `KTSelectionList` | label (as printed), `budget` in selections, `position`, `restriction_text`; FK kill team |
| `KTSelectionOption` | `cost` (default 1), `models` (default 1), `max_selections` (null = no limit), `loadout_options` (**display only**); `kill_team_id` + composite FKs to its list and operative |
| `KTSelectionRestriction` | `keyword`, `max_operatives`; FK **kill team** — a team-wide cap on a SET of operatives (Deathwatch: up to one GRAVIS) |
| `KTWeapon` | name, `category` (`range`/`melee`, the same two values as the 40k column), `range` (decision #15), attacks, hit, normal damage, crit damage, weapon rules (JSON); FK operative. A name is unique **per category** — one weapon can print both profiles |
| `KTAbility` | name, text (includes unique actions); FK operative |
| `KTPloy` | name, `kind` (`strategy`/`firefight`), CP cost (default 1 — the pages print none), text; FK kill team, **or null for a ploy every team can use** (Command Re-roll) |
| `KTEquipment` | name, text; FK kill team, **or null for the universal list** (see "Equipment"). No cost column — equipment is selected up to an allowance, not bought |

- `app/core/services/service_killteam.py`, `app/api/killteam.py`.
- Routes under `/api/v1/kill-team/...`; public read, admin write (same policy as the
  40k catalog).

### Ploys

Two kinds, as the pages divide them: `strategy` and `firefight`. A ploy belongs to
one kill team, with one exception — **Command Re-roll, which every kill team can
use**. That is a `kill_team_id` of NULL: one row to correct rather than a copy per
team, and a team's ploy list reads as "its own, plus the universal ones". Deleting a
kill team takes its own ploys and leaves the universal rows alone.

NULL costs one constraint. Postgres treats two NULLs as distinct, so
`UNIQUE(kill_team_id, name)` would accept a second "Command Re-roll"; a **partial
unique index** on `name` where `kill_team_id IS NULL` is what refuses it. The same
applies to universal equipment when that lands.

### Equipment

Same ownership shape as ploys: a team's own equipment carries its `kill_team_id`,
the universal list is stored once with NULL, and a partial unique index keeps the
universal names unique. Deleting a kill team takes its own equipment and leaves the
universal list.

Two rules recorded here because they belong to the **roster** (K4), not the catalog
entry:

- **An option cannot be selected more than once in a game.** That is
  `UNIQUE(roster_id, equipment_id)` on the roster's equipment, not something the
  catalog can express.
- **The allowance is 4 pieces**, and *some kill teams get more*. Deliberately not a
  column yet: when K4 enforces it, either a constant with a per-team override or a
  `KillTeam.equipment_limit` column defaulting to 4, decided once the pages show how
  the exceptions are worded.

Equipment whose effect is a weapon keeps its profile in the description for now,
since `KTWeapon` belongs to an operative. If K2's pages show profiles worth
structuring, the upgrade path is a nullable `KTWeapon.equipment_id` alongside a
nullable `operative_id`, with a check that exactly one owner is set — a migration,
not a redesign.

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

### Composition: budgeted selection lists

A page states what a roster may contain as **lists**, each with a budget:

    Raveners   list 1  budget 1   [Ravener Prime]
               list 2  budget 4   [Felltalon, Tremorscythe, Venomspitter, Warrior, Wrecker]
                                  each max 1, except Warrior (no limit)

Team size is per team — across the 48 teams the totals run from 2 to 14, with 10 the
most common — so nothing about size is hardcoded.

| Rule | Where it lives | Example |
|---|---|---|
| Selections a list may spend | `KTSelectionList.budget` | Raveners' second list: 4 |
| Which operatives it offers | `KTSelectionOption` rows | the five specialists |
| An operative that must be taken | a budget-1 list with one option | the Ravener Prime |
| Choose one of several | a budget-1 list with several options | Angel of Death's sergeants |
| An option costing two selections | `KTSelectionOption.cost` | Brood Brother's Magus |
| Two models for one selection | `KTSelectionOption.models` | "2 PSYCHIC FAMILIAR … still counts as one selection" |
| A cap on repeats | `KTSelectionOption.max_selections` | 1 per specialist, NULL for Warriors |
| A team-wide cap on a **set** of operatives | `KTSelectionRestriction` (keyword + limit), on the kill team | Deathwatch: "up to one GRAVIS operative" |

The cap sits on the **option**, not the operative: it is stated by the list, and two
lists can offer the same operative on different terms.

**An option cannot offer another kill team's operative.** Two plain foreign keys only
promise "some list" and "some operative", so the option carries `kill_team_id` and
reaches both parents through composite foreign keys — a mismatch is refused by the
database rather than by a service remembering to check (verified against Postgres with
raw SQL). The cost is one denormalised column, a redundant unique key on each parent
for the composite keys to target, and an `overlaps=` annotation on the relationships,
since two of them write that column by design.

Three of the 48 teams use weighted costs (Blooded, Brood Brother, Pathfinders); `cost`
and `models` are what make them expressible rather than exceptional.

**Keyword caps are the other set-scoped rule, and they are common: 13 of the 48 teams
state one.** Deathwatch prints "can only include each operative on this list once, and
can only include up to one GRAVIS operative" — two rules in one sentence, and only the
first is per-option. Several entries carry GRAVIS, so capping each at one still allows
two, which is why `KTSelectionRestriction` exists rather than a note in
`restriction_text`: unstructured, `validate` would approve illegal rosters for a
quarter of the teams. It is evaluated against `KTOperative.keywords`, which the catalog
already holds, and a list may carry several caps (Inquisitorial Agent states three).

**The restriction sentence is read, not just stored.** It carries two clauses, and both
become data:

- *"Other than CREMATOR and WARRIOR operatives, your kill team can only include each
  operative on this list once"* → `max_selections = 1` on every option except those
  whose datacard carries an exempted **keyword**. 39 of the 42 sentences cap repeats and
  38 carry an exemption clause; across the teams that is 252 options capped and 168 left
  free.
- *"Your kill team can only include up to two GUNNER operatives"* →
  `KTSelectionRestriction`, scoped to the **kill team**. The two clauses have different
  scopes and the sentence says so: the repeat clause reads "each operative on *this
  list*", the cap reads "your *kill team*". Brood Brother proves it — its BROODCOVEN cap
  matches the Magus, Patriarch and Primus, which a *different* list offers than the one
  the sentence follows, so a list-scoped cap could never have applied. 12 of the parsed
  teams state one, and a team may carry several. **Matched by words, not strings**: the pages disagree about what counts as
  one keyword — Battleclade's datacards print "COMBAT, SERVITOR" (two, comma-separated)
  while Pathfinders prints "WEAPONS EXPERT" (one), and both are capped by a sentence
  naming the phrase. An operative counts towards a cap when every word of the phrase
  appears among the words of its keywords. A cap matching no operative on the page
  raises: the phrase was misread, and a cap that never applies would leave `validate`
  approving rosters it should refuse.

A number word the parser does not know raises rather than defaulting, since a silently
wrong cap approves illegal rosters.

**Loadouts are display only.** `loadout_options` keeps the printed variants for an
entry ("with flamer and gun butt", "with webber and gun butt") and **nothing validates
them**. Wyrmblade prints three `GUNNER with …` lines, and 12 of the 48 teams repeat an
operative that way: one operative with a weapon choice, so it is ONE option. Which
weapons a roster took is recorded at K4 from the operative's own profiles and
snapshotted into a game at K5. Which combinations are *legal* is a rule, and decision
#1 leaves rules to the players — so the catalog says what an operative can use and
stops there.

`restriction_text` on a list likewise keeps the sentence its caps were read from, for a
human to check the parse against.

**Parsing it.** The section is one `ul.redTriangle`, three levels deep: a list, its
operatives, and each operative's printed loadouts. Three shapes appear across the 48
teams — a numbered line ("4 RAVENER operatives selected from the following list"), a
fixed roster ("Every ELUCIDIAN STARSTRIDER operative in the following list: 1 X, 1 Y",
budget = the sum), and an unnumbered line naming one operative ("BOSS NOB operative
with one of the following options", budget 1). A count on a LINE is a budget; a count on
an ENTRY is models. Counts are read from the leading number only: "XV26 Stealth
Battlesuit" would otherwise read as 26 operatives.

A list line can be **nested**: Blades of Khaine prints its second list inside the
leader line, and Hunter Clade wraps one in a `div` inside the same `ul`, so it is
neither a direct child nor a descendant of another line. Both are read as their own
lists, and an entry belongs to its *nearest* enclosing list.

46 of the 48 teams parse. Two raise rather than guess, and both are handled per team when
K6 widens:

- **Inquisitorial Agent** prints "5 INQUISITORIAL AGENT operatives selected from the
  list above, or REQUISITIONED operatives from one group" — it reuses another list's
  options and adds requisitioned groups.
- **Hunter Clade** prints "WARRIOR SICARIAN *", which matches both the Infiltrator and
  the Ruststalker Warrior; the footnote is what tells a human which. An ambiguous entry
  raises **even when the parser is only asking "is this an operative?"** — a lenient
  "no" would drop it as though it were a weapon loadout, which is how it silently
  vanished before.

Also roster-level rather than catalog (K4): an equipment option cannot be selected
twice in one game, and the allowance is 4 pieces with some teams allowed more.

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
