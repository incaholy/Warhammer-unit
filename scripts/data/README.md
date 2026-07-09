# datasheets.json — the seed catalog

`make seed` (→ `python -m scripts.seed_datasheets`) loads this file into the
catalog. It's **idempotent** — rows are matched by natural key and only created
if absent — so add datasheets over time and re-run; existing rows aren't
duplicated. The file ships **empty**; fill it in (or enter datasheets via the
admin API instead — both go through the same validation).

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
