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
| 6 | Game stats | **Snapshot** operative stats into the game at creation — widened to the whole datacard by decision #22 | A re-scrape must not change a game in progress |
| 7 | Game updates | **Absolute values** (`wounds: 7`), never deltas | Retry-safe on a flaky table-side connection (same reasoning as R12) |
| 8 | History / undo | Current state in columns **plus** an append-only event log | Simple reads; undo reverts the last event. Not full event sourcing |
| 9 | Concurrent edits | `version` column, stale write → 409 | Two tabs can't silently overwrite each other; 409 handling already exists |
| 10 | Delivery | **Thin slice**: one or two kill teams through every phase, then widen | Model mistakes surface in week one, not after every team is seeded |
| 11 | Separation | Kill Team and the 40k army list builder are **two sections**: `/kill-team` and `/army-list` | Two games with different rules; neither's routes, models or views leak into the other |
| 12 | Finding a kill team | A flat **Kill Team faction** list (`KTFaction`), e.g. Tyranids → Raveners. No Imperium / Chaos / Xenos level, and no link to the 40k `Faction` → `Subfaction` lookup | The army name is what people look for; the 40k lookup puts the army on different levels (Tyranids is a subfaction, Space Marines a faction) and would tie the two games' faction lists together |
| 13 | Team rules | Rules belong to the **kill team** (`KillTeamRule`), not the faction, and `kill_team_id` is NOT NULL — unlike a ploy or a piece of equipment, a rule is never universal | Two kill teams in the same faction can have different rules. And the same rule NAME is printed by several teams — "Astartes" by seven of them, "Rifles" by two — so each is that team's own row, never one shared rule. Anything that lists rules has to say whose they are, which is why the FK cannot be null and a rule read carries its kill team |
| 14 | Weapons and abilities | Belong to **one operative** (a plain FK), not shared through link tables the way 40k's `unit_weapons` / `unit_abilities` do | A datacard lists its own profiles, and the same weapon name on two operatives can carry different numbers; sharing would mean deduplicating on name *and* every stat |
| 15 | Weapon range | One non-null `range` column: the number from a printed `Range x` weapon rule when there is one, otherwise **1 for melee, 2 for range**, filled by a context-sensitive column default | A 2024 profile prints ATK/HIT/DMG/WR and no range — distance appears only as a weapon rule — so the column needs a defined meaning rather than a blank |
| 16 | Composition | **Retired by #44** — composition is text now, and a budget is one of the numbers that went with it. A kill team's composition is **budgeted selection lists** (`KTSelectionList` → `KTSelectionOption`), not a headcount with a leader. Descriptive, not enforced — see #28. An option carries its `cost` in selections, how many `models` it fields, and its own `max_selections` cap | The pages never say "leader": they say "1 X operative", then "4 X operatives selected from the following list". A budget of 1 over one option *is* required; over several it is "choose one". Weighted costs (Brood Brother's Magus counts as two selections) and pairs ("2 PSYCHIC FAMILIAR … still counts as one selection") cannot be expressed by a count and a flag |
| 17 | Equipment | Equipment is picked **per game**, not per roster: `KTGameEquipment` with `UNIQUE(game_id, equipment_id)`, and the allowance (4 pieces, more for some teams) is checked there | The rules choose equipment for each battle and reveal it during play. On the roster it would make a roster mean "a roster for one battle", and re-playing the same team would mean a second roster |
| 18 | A game's operatives | The set is **not fixed at creation**. Two operations change it during a battle: **add** (`source = equipment` or `rule`) and **transform** (one datacard becomes another) | Two teams need it already. MUTOID VERMIN equipment adds four Gellerpox vermin "for the battle", and Chaos Cult's Mutation turns a Devotee into a Mutant and then a Torment. Both are per-battle, so neither belongs on a roster |
| 19 | Transform | **In place**: the row keeps its id, tokens, order and board status, its catalog pointer moves, and its datacard is re-snapshotted (stats, weapons and abilities — decision #22). The event log carries the history | It is the same miniature on the table, so the game screen needs nothing new. A second row plus `replaces_id` would give lineage and cheap undo, at the cost of a status the UI must filter everywhere |
| 20 | Which operatives a roster may take | `KTOperative.availability`: `roster` (the default) or `in_battle`. **Built**, derived from the page: an operative named by a CONDITIONAL composition block is `in_battle` | The vermin and Chaos Cult's Mutant / Torment are real datacards that **no selection list offers**, and that is correct — they arrive mid-battle. The flag is set from a CONDITIONAL composition block, which only Gellerpox print, so it covers the three vermin and NOT Chaos Cult's two: those are named in rule prose, which is too loose to store as a column (a word-union match hits 80 of the 454 operatives), so they stay `roster` and the honest test for rosterability is "does some list offer it". Setting the flag structurally, instead of the scraper's guard inferring it from rule text, and it is the list the game screen's "add an operative" picker needs |
| 21 | The catalog | **Reference data, read-only**: public read and no write route at all (unlike the 40k catalog's admin write), and a game or roster NEVER writes to a `kt_*` catalog table. Everything that changes during play — wounds, order, activation, tokens, actions used, equipment, CP, VP — lives on the game's own rows | The scrape is someone else's content and the single source of truth for what an operative *can do*. A player editing it would change every other game and roster that reads it, and the next `make seed-kt` would silently undo the edit anyway |
| 22 | What a game snapshots | The **whole datacard**, not just the stat line: stats, weapon profiles, abilities and the text of the equipment taken, copied into the game as display-only JSON. Extends decision #6 | #6's reason applies to all of it, and the seed now REWRITES a catalog row when the source changes it (create-once would have been safe). Reading profiles live would let a balance update change a weapon mid-battle. It also makes a game self-contained: it keeps working if the catalog later drops that operative |
| 23 | Actions | `KTGameOperative.actions_used`: a list of `{name, turning_point}` entries, appended as players mark them | Unique actions are abilities on the datacard, and their limits differ (once per battle, once per turning point). Recording *what was used and when* lets the screen show both without the tracker knowing any rule — decision #1 again, and the same shape as `tokens` |
| 24 | Reference at the table | A game also snapshots its **team's** reference: the kill team's rules, its own ploys and the universal ones. With #22's datacards and equipment text, a battle needs **no catalog request** once it has started, and the game screen can show any datacard in full as a reference | A player reads the printed card mid-battle — every weapon profile, every ability, the ploys they might spend CP on. Leaving those in the catalog would mean a screen that mixes live and snapshotted data, and a re-scrape changing a ploy's wording mid-game. It is static for the game's whole life, so it is embedded in the game detail response and never invalidates |
| 25 | Print order | **Every child of a kill team carries `position` and is ordered by it** — operatives, rules, ploys, equipment, weapons, abilities and selection rules. Since #44 there is no exception: a restriction is a selection rule with a printed position like any other | Rows have no order of their own. The stored order matched the page by luck -- the seed inserts in page order -- and a `VACUUM FULL` reorders the heap, while a page that REORDERS its profiles changed no row's data, so the seed reported "nothing changed" and kept serving the old order for good. Sorting by name is not a datacard either: a card lists ranged profiles then melee, and one name can appear in both (Sanctifiers' brazier), so alphabetical splits one weapon across the list. Not unique per parent for weapons and abilities, deliberately: the seed rewrites those positions IN PLACE, and a unique constraint would collide with whichever row has not moved yet. `KTSelectionRule` is the exception and keeps `UNIQUE(kill_team_id, position)`, because a composition is replaced as a whole rather than rewritten, so no row ever moves while a sibling still holds its old position (the delete is flushed before the inserts). Sorting by name was tried for the four team-level collections and was wrong three ways: the page prints the LEADER first (not the alphabetically first operative, in 46 of 48 teams), Strategy Ploys before Firefight Ploys (which ordering by `kind` reverses, since "firefight" sorts first), and a rule beside the rule that refers to it (Raveners' Burrow and Tunnel). Once a team's chosen options became rules with a `group` (#27), alphabetical order also interleaved the three Aspects |
| 26 | List columns | `keywords` and `weapon_rules` are `JSONB` on Postgres and plain `JSON` on SQLite, through one shared `STRING_LIST` type (`loadout_options` was a third until #44 removed the options table) | Same shape, same reason, in one place. `json` cannot carry a GIN index, so a "which operatives have this keyword?" filter would have no way to be indexed — and a keyword is exactly what a catalog gets filtered by. Done while the migration was still an unmerged draft, so it cost nothing |
| 27 | Chosen options | A team's **grouped selectable options** are `KillTeamRule` rows carrying the page section as a nullable `group` (NULL = an always-on faction rule) | Blades of Khaine print their whole mechanic as three "… Aspect Techniques" sections and Exodite Dragon Masters theirs as three "… Upgrade" sections: 30 named blocks the scraper read nothing of, because it read only "Faction Rules" and only a bare `h3`. They are not ploys -- not one of the 30 prints a CP cost -- and `KillTeamRule` is already name-and-text belonging to a team. The group is the missing fact: "THE SLICING HURRICANE" means nothing without "Dire Avenger", which is what says who may take it. Which option a player CHOSE is a per-game record, and belongs with K5 beside the Accursed Gift |
| 28 | Composition is DESCRIPTION | The catalog states what a page prints and **never decides legality**. Completed by #44, which removed the structure this decision had already stopped trusting. A save is never blocked, and since #44 there is nothing to report either: the printed rule is stored as the page worded it and no limit is derived from it, so a roster has nothing to be checked against | Decision #1 already leaves rules to the players, and roster legality is a rule. Trying to encode it produced a list no roster could satisfy (Battleclade), a cost the column cannot hold (Kommandos' half selection), and six shapes no column fits — caps over a subset of options, caps keyed on a loadout, mutual exclusion, per-item caps, per-battle limits, a selection spent on a ploy discount. Each new team brings another. We never had correct legality, only numbers that looked authoritative, which is worse than none. The membership half — WHICH operatives a list offers — is a page fact and stays |
| 29 | What a budget counts | **Retired by #44** — there is no budget to count. `KTSelectionList.shape`: `budgeted` (the line prints its number), `single` (unnumbered, an implicit 1) or `fixed` ("Every X operative in the following list"). On a `fixed` line the number counts **models**, everywhere else **selections** | The parser already worked the shape out to compute the budget and then threw it away — so the one number whose unit changes was indistinguishable from the others. Two lists are `fixed` (Elucidian Starstrider and Gellerpox Infected, both budget 9 over 6–7 selections), and reading them as selections is how a counter reports "7 of 9" for a roster the page states as nine models. Stored rather than re-derived, because the label is prose |
| 30 | Composition notes | The footnotes and callouts printed around a composition are their own `KTSelectionRule` rows, `kind = 'note'`, in printed order — a `KillTeam.composition_notes` column until #44. A sentence is a `kind = 'restriction'` row. Footnote MARKERS are still dropped from the bullets they mark | One field used to hold four things: the repeat clause, a footnote body, a designer's-note box and (Kasrkin) a glossary aside — 342 characters and six sentences — attached to whichever list happened to precede them in the DOM. 13 teams stored a body on a list, 3 of them the wrong one, and 7 where no marker survived to say which entries it was about — figures from before this split, and not recomputable now. Today: 29 notes over 20 teams. The page marks the boundaries itself, which makes the split exact rather than a guess: a `sup` or `span.ast` marker STARTS a note, and a `Corner25` callout is a note of its own. Notes go on the team because that is where the page prints them — under the whole composition — and it is the only attachment that is never wrong. Caps are read from the sentence AND its notes, since Brood Brother state their BROODCOVEN cap in a footnote |
| 31 | Separation by kill team | A kill team's operatives, rules, ploys and equipment are **its own rows**. Two teams in one faction share nothing, the same name on two teams is two rows, and nothing in the catalog can mix two teams' rows — a composition is text belonging to one team (#44), where it used to be options held to their team by composite foreign keys | The faction is only a way to FIND a team (#12); the kill team is the unit of play. It is also what the source does: Inquisitorial Agent's page carries its own copies of the Sister of Silence and Tempestus Scion datacards it can requisition, rather than pointing at those teams. So a cross-team composition is not expressible as options, and never should be — if requisition is ever modelled (K6) it has to be a reference to another kill TEAM, not a shared operative row |
| 32 | An entry nobody can resolve | **Retired by #44** — nothing is resolved in a composition, so no entry can be unresolvable. Ambiguity loses that ENTRY, not the team: the entry is dropped, recorded in the payload's `unresolved_entries`, and reported by both `make scrape-kt` and `make seed-kt`. A line whose own name is ambiguous keeps its label and budget with **no options**. A line the parser does not recognise at all still raises | Failing the whole team cost Hunter Clade's 14 datacards, 51 weapons, 8 ploys, 4 equipment and a rule, all of which parse, over one entry its own footnote disambiguates — and left the catalog at 46 of 48 teams with nothing saying so. This is not a return to the silence that once dropped a real entry: the payload carries the gap and both tools print it, and the operative involved also shows up in the "no list offers this datacard" warning. All 48 teams now seed |
| 33 | Requisition groups | **Retired by #44** — a group's heading and lines are text like any other (#44). A team may print an "… Requisition" section, one `h2` per ally, each a composition tree of the ordinary shape. Its lines become selection lists on the requisitioning team, carrying `requisition_source` — the group's heading. Lists that carry one are **alternatives** to each other and to the line that points at them ("REQUISITIONED operatives from one group"), not further lists to spend on | Only Inquisitorial Agent prints one, and it is why that team was unusable: its main line points at the groups, so **seven of its eighteen datacards were offered by nothing**. The groups' operatives are the ones the page prints as its own rows — Sister of Silence and Tempestus Scion are not kill teams, which is exactly why their datacards are there (#31). Reading those two groups makes every one of its datacards fieldable |
| 34 | Requisitioning another team | **Retired by #44** — there is no reference to link, so whether an ally has a page stops mattering. When the ally IS a kill team, its operatives live on its own page and are **never resolved against the requisitioning one**. The group keeps its printed label and budget, offers nothing, and points at that team through `KTSelectionList.from_kill_team_id` — resolved in a second seed pass, since the payload's order is the site's | Resolving them locally is not merely fruitless, it is wrong: the resolver's last-word rule matched Death Korps' "TROOPER" to Inquisitorial Agent's Tempestus Scion Trooper. A reference rather than copies, because Death Korps' operatives stay Death Korps' (#31) — copying ~50 rows would duplicate data that already exists and create a refresh question. Four groups reference a team (Death Korps, Exaction Squad, Imperial Navy Breacher, Kasrkin); the other two carry their own options |
| 35 | Resolving an entry | **Narrowed by #44** — a composition entry is no longer resolved at all; this is now only how `parse_in_battle_operatives` names the datacards a conditional block grants, for `availability` (#20). The page's own **link** decides first: a composition entry usually carries `<a class="kwbOne" href="…#Sicarian-Infiltrator-Warrior">`, the site's link to that datacard. The six text rules run only where there is no link, which is 26 of the 470 entries that resolve | Measured over all 48 pages: **444 anchors, 444 unique matches, no misses and no ambiguity** — better evidence than any text heuristic, and it would have prevented Void-dancer Troupe's line offering a `Player` where the page requires the `Lead Player`. 296 items carry none, and not because the site hides anything: it links a keyword on its FIRST appearance only, so Blooded's three repeated "GUNNER with …" variants are styled and unlinked, and a phrase naming no single card — Hunter Clade's "WARRIOR SICARIAN", which should read "WARRIOR RUSTSTALKER" — is left unlinked because the site's own linker could not tell either |
| 36 | Loadout lists | **Retired by #44** — a loadout bullet is kept at its own depth rather than filtered out. An item in a `ul.redEmptyCircle2` is a **loadout, never an operative**, and is not resolved at all | 149 such items across the 48 pages and not one is an operative, so the class is a structural answer where resolution was a guess. It also closes a hazard decision #35 opens: a loadout line that happens to link a datacard would otherwise be read as an entry offering it. `redCircle2` is deliberately NOT used this way — of its 455 items in the composition trees, 387 resolve to an operative and 68 do not, so it narrows the question without answering it |
| 37 | Eliminating within a list | **Retired by #44** — nothing resolves, so there is nothing to eliminate between. When an entry matches several datacards equally, candidates that **another entry of the same list already claimed** are removed. If exactly one is left, it is the answer; otherwise the entry stays unresolved and reported | A list does not offer the same operative twice under two names. Hunter Clade print "WARRIOR INFILTRATOR" — which the page LINKS to the Infiltrator Warrior (#35) — and then "WARRIOR SICARIAN", matching both Sicarian Warriors, because the page should read "WARRIOR RUSTSTALKER". With the Infiltrator claimed, only the Ruststalker remains, which is how a human reads it and what the entry's own Ruststalker loadouts confirm. The surviving candidate goes back through the SAME reader rather than a second code path, so an entry that still does not resolve stays unresolved. Every entry is read before any elimination, so an eliminated one keeps its printed position |
| 38 | A line pointing at another list | **Retired by #44** — the line is stored as the text it is, with no reference column. When a line says "selected from the list above" it carries `same_options_as_id` — the list whose options it offers — and no options of its own. Checked BEFORE the label is read as an operative name, because it is not one | Inquisitorial Agent print one, and reading it as a name matched nine Agent datacards and reported an ambiguity that was never a naming problem. The options are stated once, on the list that prints them, and the line still carries its own budget because the page states one. Positions are unique within a team, so this resolves in the same seed pass — unlike a requisition pointing at another TEAM (#34) |
| 39 | Where a composition's loose text lives | The element whose text belongs to a composition is the list's **parent** — or its grandparent when the list is that parent's sole element child. Never further, and never merely because the parent held no text of its own | 47 of the 48 pages put the composition `ul` inside a `div.BreakInsideAvoid`, and 43 pages print a sentence there. Hunter Clade wrap theirs in a `div.Columns2` first, so reading only the parent made that team print **no restriction at all**: it lost its repeat clause, three keyword caps (DIKTAT, SURVEYOR, SICARIAN), four capped options and a footnote, and no warning could report it, because every check runs on the sentence this step returns. The sole-child test is what keeps the climb from reading a NEIGHBOUR's prose as this team's rule — Exodite Dragon Masters' grandparent prints 284 characters of another column's actions and Elucidian Starstrider's is the whole page body, and both genuinely print no restriction. Blades of Khaine climbs too and finds nothing, which is harmless. Identity, not equality: two empty `div`s compare equal in BeautifulSoup |
| 40 | A requisition group's own restriction | **Narrowed by #44** — a group's sentence and notes are still read, as rules of their own; there are no caps left to mint or withhold. A group's sentence, caps and notes are read exactly as the main composition's are, and the caps and notes belong to the **team** wherever on the page they are printed. A keyword capped in two places keeps the **tighter** number. No cap is minted from a group whose ally is a kill team of its own | The group reader built its lines and read nothing else: five of Inquisitorial Agent's six groups lost their sentence, three lost their notes, a cap was lost, and its Tempestus Scion Gunner, Medic and Vox-Operator were stored with no repeat limit where the page prints one. A known team's cap names a keyword no datacard on THIS page can carry — Exaction Squad cap SUBDUCTOR and their operatives live on their own page (#34) — so minting it trips the "this cap matches no operative" check and loses the whole team, and the row would be inert even if it did not. The sentence is stored and only the derived cap withheld, which is all #28 asks. The tighter number because `UNIQUE(kill_team_id, keyword)` holds one row, the same trade as a loadout-keyed cap (#28) |
| 41 | Which list a restriction caps | **Retired by #44** — a restriction is its own row and caps nothing. The last list that **offers something**, not the last one printed. When no list in the tree offers anything the sentence stays on the last line and caps nothing | Printed under the whole composition and saying "this list", so it belongs to the last one — right on 47 of the 48 teams; Inquisitorial Agent is the only one whose sentence would land on a list offering nothing. Inquisitorial Agent's composition ENDS on a cross-reference naming no options of its own (#38), so the rule was applied to an empty set and its nine Agents were stored with no repeat limit at all. Storing the sentence beside the options it governs puts the rule and its effect on one row, rather than making every consumer join them. The fallback is what a known team's group relies on (#40) |
| 42 | Counting ambiguity | **Retired by #44** — the gate it describes is gone with the structure it guarded. A line with no options raises only when **that line** produced no ambiguity — counted per line, never over the whole tree | `unresolved` accumulates across the composition, so one ambiguous entry anywhere above silenced the parser's loudest check for every line below it: the same unreadable line raised or was stored as a label with no options depending on an unrelated line printed earlier. The elimination rule (#37) fills `unresolved` by design, so the check switched itself off precisely when the parser was already struggling. The gate on the whole composition still reads the whole list, because there the question really is whether the composition came out empty |
| 43 | A reference printed first | **Retired by #44** — no name is read out of any line. A cross-reference at position 0 points at nothing, so it is not stored as a reference — and an operative name is **never** read out of a line that points at another list, even when it resolves cleanly | Such a line fell through to the very inline name resolution #38 exists to prevent: a label resolving to exactly one datacard made the parser invent an option the page never offered, with `same_options_as` unset and nothing in `unresolved` to say so. It now raises, so `scrape` names the team in `skipped` — a reference printed first is a page shape the parser does not understand, and saying so is the honest answer. A label resolving AMBIGUOUSLY is still stored with its budget and reported (#32) |
| 44 | Composition is TEXT | One table, `KTSelectionRule` = {`position`, `depth`, `kind`, `text`}, holding the page's own words in the page's own order. `kind` is `heading`, `line`, `restriction` or `note`; `depth` is the page's indent. Nothing is derived: no budget, no cost, no models, no repeat cap, no keyword cap, and no entry resolved against a datacard. Replaces `KTSelectionList`, `KTSelectionOption` and `KTSelectionRestriction`, and retires ten decisions with them | #28 already said the catalog DESCRIBES composition and never decides legality. Keeping the columns anyway was enforcement machinery with no enforcer, and every problem in this area came from that gap: a budget summing to 43 selections for a team that fields 7; a cap on COMBAT SERVITOR matching none of the seven operatives carrying it, because the datacard prints the phrase as two keywords; two conditional clauses stored at opposite strictnesses because a column had to pick one; four rows that looked identical while meaning four different things. Text is exact where a column had to approximate. A tracker meant to allow CUSTOM games should not imply a restriction it will not apply, and a roster offers every operative of its kill team (K4) with these rows read beside it. 593 structured rows became 893 text rows — more rows, every one of them something the page printed |
| 45 | Why `depth` and not nested entries | Each printed bullet is its own row carrying how far it is indented, rather than a line owning a list of entry strings | Both flatter shapes lie about the page. An `entries` list per line made "Servo-claw; meltagun" a sibling of "AUTO-PROXY SERVITOR" instead of a choice FOR the COMBAT SERVITOR above it — wrong on 45 of the 85 lines that have entries. Keeping the old `redEmptyCircle2` filter instead dropped 157 printed lines and left two lines promising "one of the following options:" and listing none. Read in `position` order, indenting by `depth`, and the page's section is back: 740 bullets across the 48 composition trees, 114 / 490 / 136 by depth |
| 46 | Naming a parent in a read | A read carries the parent's **id**, never a copy of its name: a kill team summary is {`id`, `name`, `faction_id`} | What `Unit_Read` already does, and the frontend already resolves it — `CatalogView.tsx` builds a `Map<UUID, string>` from the `/factions` listing, which it needs anyway for its filter rail. One way to name a parent across the whole API, and a rename cannot leave a stale copy in a cached response |
| 47 | What a team detail carries | Everything nested: rules, ploys, equipment, operatives with their weapons and abilities, and the selection rules. Measured over the served responses: 16 KB (Plague Marines) to 46 KB (Inquisitorial Agent), median 24 KB, each in nine queries flat | Forced by there being no standalone operative route — nested is the only way to reach a datacard. It is also the 40k pattern (`Unit_Read` nests weapons and abilities even in its paged list), and #24 wants a game to snapshot the team's rules, ploys and every datacard anyway, so a game gets its snapshot in one call. Abilities and weapons are the bulk of the bytes and the only cut that would change the size class — and they are what the page is for |
| 48 | Reading the rows no team owns | One route, `/kill-team/universal`, carrying both the universal ploy and the universal equipment list | They are one concept — the rows with `kill_team_id` NULL — and one thing a client wants: fetch once, keep for the session, use with every team. Folding them into each team detail instead would add 8 KB to every response (a third again on a median team) and re-send it on every team view, to save a call that happens once per GAME |
| 49 | Proving the catalog takes no writes | A test over `app.openapi()` asserting that no path under the CATALOG's own prefix (`/api/v1/kill-team`) declares any method but `get`. Prefix-anchored, not a substring match: `/api/v1/me/kill-team/rosters` also contains "kill-team" and is supposed to write | #21 says there is no write route at all, and until now nothing checked it. One assertion that cannot be outgrown by new routes, rather than a 405 test per path — the likely failure is a later slice mirroring the 40k routers, which are ~80% write code, and adding a `POST` nobody questions |
| 50 | A roster row is ONE operative | `KTRosterOperative` carries no `amount`: taking two Warriors is two rows, each with its own `position`. So **no** `UNIQUE(roster_id, operative_id)`, add APPENDS rather than 409ing on a repeat, and a row is addressed by its own id rather than by the operative it points at — three deliberate divergences from the `ArmyUnit` it otherwise mirrors | An `amount` works in 40k because a unit is a GROUP you move and shoot as one. A Kill Team operative is an individual: it activates, takes wounds, holds its order and its tokens separately, and in a game each one will have its own stats to track. `{operative: Warrior, amount: 2}` cannot answer "which of my two Warriors is on 4 wounds?". Two decisions already made need it too: #19 transforms an operative IN PLACE, keeping its id, tokens, order and board status while its catalog pointer moves — which is only expressible if a row is one operative — and #22 snapshots the whole datacard per operative, which is one snapshot per row under this shape and a duplicating loop under an `amount`. The roster row and the game row correspond one to one, so K5 copies rather than expands |
| 51 | `availability` is information, not a constraint | A roster may take ANY operative of its kill team. `availability = in_battle` is read by the game screen's "add an operative" picker (#20) and by a frontend deciding what to grey out; no route refuses a row because of it | The catalog describes and never enforces (#28, #44), and a roster-level refusal would be the catalog applying a rule after all. It is also unnecessary: a session service creates its own per-operative record, which is where an operative's stats are tracked (#50), so whether a datacard arrives by roster or mid-battle is a question that record answers rather than the roster |
| 52 | What a roster read carries | The roster's operatives with their datacards embedded, plus the team's rules, the team's ploys, the universal ploys, the team's equipment and the universal equipment list. The two equipment lists are the pool a game CHOOSES from -- the choice itself stays per game (#17), so there is no `KTRosterEquipment` | It is what a player has in front of them while building and then playing: the datacards they will field and every rule, ploy and piece of kit that applies. The cost is stated rather than discovered: measured over all 48 teams, a ten-operative roster read is 24-42 KB, median 31, in thirteen queries flat (ten for an empty roster) -- larger than the team detail it overlaps (16-46 KB, median 24) for 44 of the 48, since it repeats that team's rules, ploys and equipment and adds 7.8 KB of universal rows. Accepted deliberately: the alternative is a roster that carries ids and makes every client join against a team detail it may not have fetched |
| 53 | `position` on a roster row is a sort HINT | `move_operative` sets a position absolutely and shifts nothing, so the values need be neither contiguous nor distinct: moving the third row to 0 leaves two rows at 0 and vacates 2. No `UNIQUE(roster_id, position)`; every reader breaks a tie with `id` | An absolute set is retry-safe and one query, where renumbering is an UPDATE across every row of the roster per move. Ties are the cheaper half of that trade and cost nothing as long as the ORDER is still total, so `KTRoster.operatives` and `list_roster_operatives` both order by `(position, id)` -- they are two paths to the same rows and must not disagree. This is the one `position` in the schema a PLAYER sets: the catalog's are assigned sequentially by the scraper (#25), so a tie there would be a seed defect rather than something a request can cause, and those relationships need no tie-breaker. Note #50 rules out `UNIQUE(roster_id, operative_id)` and says nothing about `position` -- a distinction worth keeping straight, since conflating them reads as though ties were already decided |

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
- `scripts/seed_killteam.py`: natural-key upserts that also **refresh** a row the
  source changed, `SeedError` / `_ref` pattern from `scripts/seed_datasheets.py`. One
  exception: a team's **composition is replaced as a whole**, because a selection
  list's identity is its print position, so a page that gains or reorders a line
  shifts every list rather than changing one. Rows the source *removes* elsewhere are
  left behind until K4 decides how rosters referencing them are handled.
- `make scrape-kt`, `make seed-kt`. The page cache has no expiry, so
  `make scrape-kt-fresh` is what picks up a **changed** page.
- A team whose page the parsers cannot read is listed in the payload's `skipped` and
  reported by both tools rather than silently missing. Nothing is skipped today — all 48
  parse — so this is a guard against a page changing.

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

**Fetching, as it turned out:** the per-team pages 403 intermittently, so `fetch`
caches every page to `scripts/data/cache/` and the parsers run against that. The cache
has no expiry, which is why `make scrape-kt-fresh` exists. The committed fixtures are
**synthetic** — real DOM structure, invented names — so no scraped content is in the
repo, and all 48 pages have been parsed from the cache rather than from saved fixtures.

### What the scraper decides

Content decisions that live in code rather than in a numbered row, listed because a
reader of the tables would not guess them:

- **A page names each operative twice.** The composition says `FELLTALON`, the datacard
  says "Ravener Felltalon", so `resolve_operative` matches them with an ordered ladder of
  six rules, each needing a unique winner. They are the fallback for an entry the page
  printed no link for (decision #35). Since #44 a composition entry is not resolved at
  all, so this runs for one caller only: naming the datacards a CONDITIONAL block grants,
  which is what `availability` is set from (#20). The tie-break direction depends on which
  side carries the extra words, and an AMBIGUOUS entry raises rather than guessing.
- **There is nothing left for `composition_warnings` to check.** It reported four kinds
  of problem — an unresolvable entry, a budget no roster could satisfy, a datacard no list
  offered, a label naming a card its list did not offer — and every one of them read a
  structure #44 removed. A mis-read composition used to be silent and plausible; a
  mis-read line is now just the line, printed wrong. The function is gone with the
  checks.
- **Universal rows come from two discovered pages**, not from a team: the ploys from the
  core rules (Command Re-roll) and the equipment from the universal equipment page, both
  found through the nav.
- **Hidden duplicates are stripped**: `div.tooltip_templates` holds a second copy of every
  ploy on some pages, which collided with `UNIQUE(kill_team_id, name)`.
- **`p.ShowFluff` is dropped** from every rule, ploy and equipment entry — it is prose
  about the faction, and the tracker shows rules.
- **Text is normalised once, in `_clean`**: curly quotes, `&nbsp;`, the site's `<KY>`
  keyword markers, and the space that joining inline elements leaves before punctuation.
- **Equipment names keep their printed quantity prefix** (`1X AMMO CACHE`), because that is
  the name the page gives; there is no quantity column.
- **Keywords are upper-cased**, and a unique action keeps its AP cost at the front of its
  own text rather than in a column.
- **The seed refuses a payload that names the same thing twice**, section by section: every
  natural key is a unique constraint, so a repeat would silently overwrite rather than add.
- **An ABSENT `selection_rules` key leaves a composition alone; an empty list clears it.**
  A partial payload is not a statement that a team has no composition.
- **One transaction per run, rolled back on failure**, so a team that fails leaves nothing.
- **Deleting a faction that still has kill teams is refused** — a faction is a grouping,
  and losing one by accident must not take its teams.
- **A run that parses nothing refuses to overwrite `killteam.json`**, which is gitignored
  and therefore unrecoverable.

## Catalog

Provisional tables — the migration is a **draft until `fire-team` merges**: a column
the pages show is wrong is fixed and the migration regenerated, which has happened five
times. Checked against all 48 team pages: all 48 parse and seed, with no warnings.

| Table | Holds |
|---|---|
| `KTFaction` | name (unique) — the Kill Team faction list (see "Factions") |
| `KillTeam` | name; FK `KTFaction`. **No operative count** and no composition columns — the composition is `KTSelectionRule` rows (decision #44) |
| `KillTeamRule` | name, `description`, nullable `group` (decision #27), `position`; FK kill team — **never NULL**, so a rule always names its team — team-wide rules (e.g. Raveners' Burrow, Tunnel, Predatory Instincts), and the grouped options two teams choose from |
| `KTOperative` | name, APL, move, save, wounds, keywords (JSONB, decision #26), `position`, `availability` (decision #20); FK kill team. Whether a roster may take it, and how often, belongs to the list offering it. `availability = in_battle` marks the three Gellerpox datacards a CONDITIONAL block grants; it is **not** a test for "no list offers this" — Chaos Cult's Chaos Mutant and Chaos Torment are offered by no list either and stay `roster` |
| `KTSelectionRule` | `position` (across the whole composition), `depth` (the page's indent), `kind` (`heading`/`line`/`restriction`/`note`), `text`; FK kill team. The composition as printed — nothing derived (decisions #44, #45) |
| `KTWeapon` | name, `category` (`range`/`melee`, the same two values as the 40k column), `range` (decision #15), attacks, hit, normal damage, crit damage, weapon rules (JSONB, decision #26), `position` (print order, decision #25); FK operative. A name is unique **per category** — one weapon can print both profiles |
| `KTAbility` | name, `description` (includes unique actions), `position` (print order, decision #25); FK operative |
| `KTPloy` | name, `kind` (`strategy`/`firefight`), `cp_cost` (default 1 — **most** pages print none; the core rules and Blades of Khaine print theirs), `description`, `position`; FK kill team, **or null for a ploy every team can use** (Command Re-roll) |
| `KTEquipment` | name, `description`, `position`; FK kill team, **or null for the universal list** (see "Equipment"). No cost column — equipment is selected up to an allowance, not bought |

Built in K3: `app/core/services/service_killteam.py` and `app/api/killteam.py`.

- `app/core/services/service_killteam.py`, `app/api/killteam.py`.
- Routes under `/api/v1/kill-team/...`, **read-only**: public read and no writes at
  all, unlike the 40k catalog's admin write. An admin edit would be silently undone by
  the next `make seed-kt`, which rewrites a row whenever the payload differs.

### Ploys

Two kinds, as the pages divide them: `strategy` and `firefight`. A ploy belongs to
one kill team, with one exception — **Command Re-roll, which every kill team can
use**. That is a `kill_team_id` of NULL: one row to correct rather than a copy per
team, and a team's ploy list reads as "its own, plus the universal ones". Deleting a
kill team takes its own ploys and leaves the universal rows alone.

NULL costs one constraint. Postgres treats two NULLs as distinct, so
`UNIQUE(kill_team_id, name)` would accept a second "Command Re-roll"; a **partial
unique index** on `name` where `kill_team_id IS NULL` is what refuses it. The same
applies to universal equipment, which the payload already carries (11 rows).

### Equipment

Same ownership shape as ploys: a team's own equipment carries its `kill_team_id`,
the universal list is stored once with NULL, and a partial unique index keeps the
universal names unique. Deleting a kill team takes its own equipment and leaves the
universal list.

Two rules recorded here because they belong to the **game** (decision #17), not the
catalog entry:

- **An option cannot be selected more than once in a game.** That is
  `UNIQUE(game_id, equipment_id)` on `KTGameEquipment`, not something the catalog can
  express.
- **The allowance is 4 pieces**, and *some kill teams get more*. Deliberately not a
  column yet: when K5 enforces it, either a constant with a per-team override or a
  `KillTeam.equipment_limit` column defaulting to 4, decided once the pages show how
  the exceptions are worded.

Equipment is chosen for each battle and **revealed during** it, which is why it sits on
the game rather than the roster, and why revealing a piece can change what is on the
table — see "Operatives that join or change during a battle".

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
- Faction names come from **one place**: the site's own nav, read by `parse_nav`
  (see "Which teams: discovered, not configured"). Unlike the 40k scraper, which has a
  hand-written `FACTIONS` map, there is no list here to drift from the site — and
  normalising labels on the way through is what keeps one faction from seeding twice.
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

- `KTRoster` (owner, kill team, name) and `KTRosterOperative` — **one row per
  operative**, no `amount` (decision #50), because each one has its own stats to track
  once a game starts. **No `KTRosterEquipment`**: equipment is chosen per GAME, not per
  roster (decision #17), so a roster that held it would mean "a roster for one battle".
- Under `/api/v1/me/kill-team/rosters/...`. **Add APPENDS** — a repeat is a second
  operative, not a 409 — and a row is addressed by its own id, since two rows may name
  the same operative (decision #50). That is where this stops mirroring `ArmyUnit`,
  whose add is create-only because an incrementing `amount` is not retry-safe; there is
  no `amount` here, so a retried add is a real outcome rather than a double-applied one.
  Ownership is checked the way `Army` checks it — a roster you do not own 404s rather
  than 403s, so its existence is not disclosed.
- **No `validate`.** A roster offers every operative of its kill team and nothing narrows
  that: composition is the page's own words shown beside the roster, never a rule the
  catalog applies (decisions #28, #44). So there is nothing to report, and that is what
  lets a CUSTOM game be built. If legality checking is ever wanted it is a feature of its
  own, decided then, not a column inherited from the catalog.
- No points, so none of `Army`'s `points_limit`, `points_total` or `/shortfall` has an
  equivalent.
- A roster may take **any** operative of its kill team: `availability` informs a picker,
  it does not refuse a row (decision #51).
- A roster read carries its operatives with their datacards, the team's rules and ploys,
  the universal ploys, and both equipment lists as the pool a GAME chooses from
  (decisions #52, #17).

### Composition: the page's own words

A page states what a roster may contain as an indented list, and the catalog stores it as
exactly that — one `KTSelectionRule` row per printed thing, in printed order, with the
indent kept (decisions #44, #45). Battleclade's section, as stored:

    pos depth kind         text
      0     0 line         1 BATTLECLADE TECHNOARCHEOLOGIST operative
      1     0 line         1 BATTLECLADE SERVITOR UNDERSEER operative
      2     0 line         8 BATTLECLADE operatives selected from the following list:
      3     1 line         AUTO-PROXY SERVITOR
      4     1 line         SERVITOR BREACHER
      5     1 line         COMBAT SERVITOR with one of the following options:
      6     2 line           Servo-claw; incendine igniter
      7     2 line           Servo-claw; meltagun
      8     2 line           Servo-claw; phosphor blaster
      9     1 line         GUN SERVITOR with heavy arc rifle and augmetic claw
     10     1 line         GUN SERVITOR with heavy bolter and augmetic claw
     11     1 line         TECHNOMEDIC SERVITOR
     12     0 restriction  Other than COMBAT SERVITOR operatives, your kill team can …

Read in `position` order, indenting by `depth`, and the page's section is back. Across the
48 teams: **893 rules** — 810 bullets, 48 restriction sentences, 29 notes and 6 requisition
headings, at depths 203 / 546 / 144.

**Nothing is derived, and that is the point.** There is no budget, no cost, no models
count, no repeat cap, no keyword cap, and no bullet resolved against a datacard. The
catalog describes composition and never decides legality (decision #28), and until #44 it
carried the apparatus anyway — which is where every problem in this area came from:

| What was stored | What went wrong |
|---|---|
| a `budget` per list | summed to 43 selections for a team that fields 7, because some lists are alternatives |
| a cap per keyword | `COMBAT SERVITOR` matched none of the seven operatives carrying it — the datacard prints the phrase as two keywords |
| `max_selections` per option | two conditional clauses had to be stored at opposite strictnesses, because a column cannot hold "unless you took none of these" |
| three reference columns | four rows read identically (`budget 5, 0 options`) while meaning four different things |

Text is exact where a column had to approximate. And a tracker meant to allow **custom
games** should not imply a restriction it will not apply: a roster offers every operative
of its kill team (K4), with these rows read beside it.

What a reader still gets, because the page prints it: which operatives a line offers, how
many, which loadouts each may take, what the repeat rule is and what the footnotes say —
all of it as the page worded it, none of it as a number the catalog invented.

Equipment rules are game-level rather than catalog or roster (decision #17): an option
cannot be selected twice in one game, and the allowance is 4 pieces with some teams
allowed more.

## Game tracker

**The catalog is read-only; a game owns everything that changes** (decision #21). The
scraped tables answer "what can this operative do?" and nothing in a game or a roster
writes to them: a player editing a datacard would change every other game reading it,
and the next `make seed-kt` would rewrite it back. So a game COPIES what it needs
(decision #22) and then only ever writes its own rows.

That copy is what the game screen reads, which has three consequences worth stating:
a balance update to the source cannot change a battle in progress, a finished game
still shows the datacard as it was played, and a game survives the catalog dropping an
operative — the open question the seed leaves for K4 is about **rosters**, not games.

**The datacard is reference, and reference is part of the game** (decision #24). At the
table a player reads the card itself: the stat line, every weapon profile with its
ATK / HIT / DMG and weapon rules, the abilities and unique actions in full, the
keywords. So the game carries all of it, together with the team's rules, its ploys and
the universal ones, and the text of the equipment taken. Between them a battle needs no
catalog request at all — which also means one payload, no request per card opened, and
nothing that can change under the players mid-game. Which ploys have been spent is the
team-level twin of `actions_used`: `ploys_used` keeps `{name, turning_point}` entries, so
the CP column has a readable history beside it.

| Table | Holds |
|---|---|
| `KTGame` | owner, roster, opponent name, status (setup / in progress / finished), turning point (1–4), phase (strategy / firefight), initiative, CP, VP by source, opponent VP, **markers** and `ploys_used` (JSON lists), the team's snapshotted `rules` and `ploys` (decision #24), `version` |
| `KTGameOperative` | **snapshot** of the whole datacard (decision #22): stats, plus `weapons` and `abilities` as display-only JSON. Then the state: current wounds, order (engage / conceal), activated this TP, **status** (reserve / on board / incapacitated), **tokens** and `actions_used` (JSON lists). Plus the catalog operative it is a snapshot of, `source` (roster / equipment / rule) and `added_in_turning_point` (NULL for the roster's own) |
| `KTGameEquipment` | the pieces picked for **this battle** (decision #17): FK game, FK equipment, its `text` snapshotted like an operative's, `revealed` — `UNIQUE(game_id, equipment_id)` |
| `KTGameEvent` | append-only: type, turning point, payload (JSON) |

Service-enforced bookkeeping (→ 400 `VALIDATION`), not rules:

- wounds within `0..max`; `0` sets status to incapacitated
- one activation per operative per turning point; advancing clears activations
- CP never negative; no turning point past 4
- an action recorded against an operative must be one its **snapshot** lists, and
  carries the turning point it was used in (decision #23). How often each may be used
  is a rule, so it is shown, not enforced

**Tokens, markers and reserve.** Teams put tokens on operatives (Raveners: Poison,
Heightened Senses, …) and markers on the killzone (Raveners: Tunnel markers 0–4), and
some operatives start off the board (Raveners' Burrow). The tracker records these
generically: `tokens` is a list of token names on an operative, `markers` a list of
team-level markers on the game, and `status = reserve` an operative not yet on the
board. It records that they exist; the players apply what they do. No team-specific
columns, so a new team needs no migration.

**Operatives that join or change during a battle.** Two teams need the roll of
operatives to change after the game has started, and they need it in two different ways
— so the tracker gets two generic operations rather than either team's rule
(decision #18):

| Team | What the rules do | What the tracker does |
|---|---|---|
| Gellerpox Infected | Revealing the **MUTOID VERMIN** equipment adds four vermin operatives for the battle | **add** four `KTGameOperative` rows with `source = equipment`, chosen from the team's `in_battle` datacards (Cursemite, Eyestinger Swarm, Sludge-Grub) |
| Chaos Cult | **Mutation** turns a Devotee into a Mutant, and a Mutant into a Torment, during the battle | **transform** the row in place: its catalog pointer moves and its datacard is re-snapshotted, keeping its tokens, actions used and board status |

Both are recorded in the event log (`operative_added`, `operative_transformed`, payload
naming the datacards and the reason), so `undo` works on them like anything else and the
game screen can show what a model used to be.

What stays with the players, per decision #1: Mutation's per-turning-point quota (2, 2,
3, 4) and its control-range trigger, and which **Accursed Gift** is taken. The quota
needs no column — "how many transforms this turning point" is a query over the event log
— and a per-game choice like the Accursed Gift goes in a small `choices` JSON on the
game rather than a column per rule.

Validation stays bookkeeping, not rules: an added or transformed operative must be a
datacard of **this game's kill team**, and an `in_battle` datacard can only be added, not
rostered (decision #20). The converse does not hold: `roster` does not mean some list
offers it — see "A list that offers nothing" and decision #20.

**Archetypes, later.** Every page prints one ("Archetype: Seek & Destroy / Security", six
pairs across the 48 teams, plus Blades of Khaine's `*` — theirs depends on the Aspect
taken — and Inquisitorial Agent's "Any"). They gate which **Tac Ops** a team may choose,
which is a game-time decision, so they belong here rather than in the catalog and are
deliberately not stored yet. The catalog holds what a player reads about an OPERATIVE;
the archetype is something a team picks a mission objective with.

Endpoints under `/api/v1/me/kill-team/games/...`:

- `POST` (from a roster), `GET` list / detail — detail carries the game's own reference
  (decision #24), so the game screen loads once and needs no catalog call
- `PATCH /{id}` — CP, VP, markers, ploys used
- `PATCH .../operatives/{id}` — wounds, order, activated, tokens, actions used
- `POST .../operatives` — add one (decision #18): the catalog operative and a `source`
- `PATCH .../operatives/{id}` with `becomes_operative_id` — transform it (decision #19)
- `POST .../equipment` / `DELETE .../equipment/{id}` — this battle's picks
- `PATCH .../equipment/{id}` — reveal it
- `POST .../advance` — next phase / turning point; the server applies resets
- `POST .../undo` — revert the last event

## Frontend

Catalog browse → roster builder → game screen. The game screen is phone-first for
table-side use: large touch targets, one card per operative, turning point / CP / VP
always visible.

An operative's card shows both halves at once: what it **can do**, from the game's own
snapshot of the datacard — weapon profiles, abilities and unique actions, keywords — and
what is **true of it now**: wounds left, order, whether it has activated, its tokens and
the actions it has used. Nothing on that screen edits the catalog (decision #21), and it
reads the snapshot rather than the catalog API, so a game needs no catalog request once
it has started.

Two levels of detail, because both are wanted at different moments. The list of
operatives is compact enough to see the whole team at a glance — name, wounds, order,
activated — and **any card opens to the full datacard** as printed, for the moment a
player needs to check a weapon rule or an ability mid-activation. A team tab holds the
rest of the reference: the kill team's rules, its ploys with their CP costs, and the
equipment picked for this battle. Read-only, from the snapshot, with the operative's
current state shown beside the stats it started with — a card is a reference sheet and a
status sheet at once, which is what a printed datacard plus a handful of dice tokens does
on the table.

## Branching

`fire-team` was cut from `roadmap`. Merge PR #4 with **"Create a merge commit"** and
nothing further is needed. If it is squashed or rebased instead, move this branch
before opening its PR:

    git fetch origin
    git rebase --onto origin/main roadmap fire-team

## Out of scope for v1

- Rules engine (dice resolution, weapon-rule automation)
- Two-player shared games and live sync
- Killzone / terrain modelling — including **base sizes**. Every datacard prints one
  (`⌀25mm` … `⌀75x42mm`, 454 of them) and the scraper strips it out of the keywords cell
  where it trails the last keyword, deliberately: the tracker records an operative's
  state, never its place on the table, so nothing would read it. Not a gap.
- Editions other than 2024
