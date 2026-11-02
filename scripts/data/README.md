# scripts/data — the seed catalogs

Two scraped catalogs live in this directory, and they are tracked **differently**.
Which one you are holding decides whether you need to scrape at all.

| File | In git? | Scrape with | Seed with |
|---|---|---|---|
| `datasheets.json` (40k) | **yes** — committed, ~2.1 MB | `make scrape` | `make seed` |
| `killteam.json` (Kill Team) | **no** — gitignored | `make scrape-kt` | `make seed-kt` |

`scripts/data/cache/` holds the fetched Wahapedia HTML and is gitignored too. So a
fresh clone has no cache, and any scrape is a real network fetch rather than an
offline regeneration.

**`datasheets.json` ships full, not empty.** The committed copy holds 1,558 units,
2,235 weapons, 1,026 abilities and 4 factions — re-derive those four numbers by
taking `len()` of each top-level list in the file. A fresh clone can therefore run
`make seed` immediately, with no scrape. The seed stores **1,331** units out of the
1,558 rows: its natural key is `(faction, unit_name)`, and the remaining 227 rows
repeat a pair that is already loaded.

**`killteam.json` is the one that is genuinely absent.** It is gitignored, so you do
have to produce it before you can seed Kill Team: `make scrape-kt` writes it, then
`make seed-kt` loads it. Use `make scrape-kt-fresh` to re-request pages instead of
reading the cache, which is how a *changed* page is picked up — the cache has no
expiry. Unlike `make seed`, `seed_killteam` is re-runnable and refreshes changed rows.

## datasheets.json

`make seed` (→ `python -m scripts.seed_datasheets`) loads `datasheets.json` into the
catalog. It's **idempotent** — rows are matched by natural key and only created
if absent — so add datasheets over time and re-run; existing rows aren't
duplicated. You can enter datasheets via the admin API instead — both go through
the same validation.

All names must be **canonical**: factions from the `FactionName` enum
(`Imperium`, `Xenos`, `Chaos`, `Space Marines`) and subfactions from the
`FACTION_SUBFACTIONS` map in `app/core/db/models.py`. A weapon `category` must be
`range` or `melee`. Anything else is rejected by the service, same as the API.

## Shape

```json
{
  "factions": [
    { "name": "<faction>", "subfactions": ["<subfaction>", "..."] }
  ],
  "weapons": [
    {
      "name": "<weapon>", "category": "range | melee",
      "attacks": "<str, e.g. 2 or D6>", "weapon_skill": 0, "strength": 0,
      "armor_piercing": 0, "damage": "<str, e.g. 1 or D3>",
      "range_inches": 24, "keywords": ["<optional>"]
    }
  ],
  "abilities": [
    { "name": "<ability>", "description": "<text>" }
  ],
  "units": [
    {
      "unit_name": "<unit>",
      "faction": "<faction>", "subfaction": "<optional subfaction>",
      "movement": 0, "toughness": 0, "armor_save": 0, "wounds": 0,
      "leadership": 0, "objective_control": 0, "points": 0,
      "invulnerable_save": null,
      "keywords": ["<optional>"],
      "weapons": ["<weapon name>"], "abilities": ["<ability name>"]
    }
  ]
}
```

Notes:
- `range_inches` is `null` for melee weapons; `invulnerable_save` is `null` when
  the unit has no invuln.
- A unit's `weapons`/`abilities` reference entries by **name** from the
  top-level `weapons`/`abilities` lists (shared, so many units can link the same
  bolt rifle). Order of loading is handled for you: factions → subfactions →
  weapons → abilities → units → links.
