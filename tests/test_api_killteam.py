"""API tests for the Kill Team catalog router — reads are public, writes do not exist.

The load-bearing one is `test_the_kill_team_catalog_publishes_no_writes`: decision #21
says there is no write route at all, and until it existed nothing checked that. The
likely failure is a later slice mirroring the 40k routers, which are ~80% write code,
and adding a `POST` nobody questions.
"""

import uuid

from app.main import app

BASE = "/api/v1/kill-team"


# --- the contract that there are no writes (decision #49) -------------------


def test_the_kill_team_catalog_publishes_no_writes():
    # Asserted over the published document rather than per path, so a route added later
    # cannot slip past it -- and the frontend generates its types from this same file.
    doc = app.openapi()
    kill_team_paths = {path: spec for path, spec in doc["paths"].items() if "/kill-team" in path}

    assert kill_team_paths, "the router is not mounted"
    for path, spec in kill_team_paths.items():
        assert set(spec) == {"get"}, f"{path} declares {sorted(set(spec) - {'get'})}"


def test_a_write_to_the_catalog_is_not_allowed(client):
    for method, path in (
        (client.post, f"{BASE}/teams"),
        (client.patch, f"{BASE}/teams/{uuid.uuid4()}"),
        (client.delete, f"{BASE}/teams/{uuid.uuid4()}"),
    ):
        assert method(path).status_code == 405, f"{path} accepted a write"


# --- factions ---------------------------------------------------------------


def test_listing_factions_is_public_and_paged(client, make_kt_faction):
    # Public: the catalog is reference data, and a player never needs a token to read it.
    make_kt_faction(name="Tyranids")
    make_kt_faction(name="Aeldari")

    resp = client.get(f"{BASE}/factions")

    assert resp.status_code == 200
    body = resp.json()
    assert [f["name"] for f in body["items"]] == ["Aeldari", "Tyranids"]
    assert body["total"] == 2
    assert set(body["items"][0]) == {"id", "name"}


# --- the team listing -------------------------------------------------------


def test_a_listed_team_names_its_faction_by_id_only(client, make_kt_faction, make_kill_team):
    # Decision #46: the id, never a copy of the name. The frontend resolves it from the
    # factions listing, which it needs anyway for its filter rail.
    faction = make_kt_faction(name="Tyranids")
    make_kill_team(faction=faction, name="Raveners")

    team = client.get(f"{BASE}/teams").json()["items"][0]

    assert set(team) == {"id", "name", "faction_id"}
    assert team["faction_id"] == str(faction.id)


def test_the_team_listing_can_be_narrowed_to_one_faction(client, make_kt_faction, make_kill_team):
    tyranids = make_kt_faction(name="Tyranids")
    make_kill_team(faction=tyranids, name="Raveners")
    make_kill_team(faction=make_kt_faction(name="Necrons"), name="Hierotek Circle")

    resp = client.get(f"{BASE}/teams", params={"faction_id": str(tyranids.id)})

    assert [t["name"] for t in resp.json()["items"]] == ["Raveners"]
    assert resp.json()["total"] == 1


def test_a_faction_filter_matching_nothing_is_an_empty_page(client, make_kill_team):
    # A filter naming nothing is a legitimate result; only a missing ROW asked for by id
    # is a 404.
    make_kill_team()

    resp = client.get(f"{BASE}/teams", params={"faction_id": str(uuid.uuid4())})

    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


# --- one team, whole --------------------------------------------------------


def test_a_team_detail_carries_the_whole_page(
    client,
    make_kill_team,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
    make_kt_operative,
    make_kt_weapon,
    make_kt_ability,
    make_kt_selection_rule,
):
    # Everything nested (decision #47), because operatives are reachable only this way.
    team = make_kill_team(name="Raveners")
    make_kill_team_rule(kill_team=team, name="Burrow", position=0)
    make_kt_ploy(kill_team=team, name="DEATH FROM BELOW", position=0)
    make_kt_equipment(kill_team=team, name="SPORE", position=0)
    operative = make_kt_operative(kill_team=team, name="Ravener Prime", position=0)
    make_kt_weapon(operative=operative, name="Tail blade", position=0)
    make_kt_ability(operative=operative, name="Crest", position=0)
    make_kt_selection_rule(kill_team=team, position=0, depth=0, text="1 RAVENER PRIME operative")

    body = client.get(f"{BASE}/teams/{team.id}").json()

    assert body["name"] == "Raveners"
    assert [r["name"] for r in body["rules"]] == ["Burrow"]
    assert [p["name"] for p in body["ploys"]] == ["DEATH FROM BELOW"]
    assert [e["name"] for e in body["equipment"]] == ["SPORE"]
    assert [o["name"] for o in body["operatives"]] == ["Ravener Prime"]
    assert [w["name"] for w in body["operatives"][0]["weapons"]] == ["Tail blade"]
    assert [a["name"] for a in body["operatives"][0]["abilities"]] == ["Crest"]
    assert [r["text"] for r in body["selection_rules"]] == ["1 RAVENER PRIME operative"]


def test_a_detail_leaves_out_the_seeds_bookkeeping(client, make_kill_team, make_kt_operative, make_kt_weapon):
    # `created_at` / `updated_at` change whenever a re-scrape rewrites a row, so a
    # response carrying them would differ for no visible reason and defeat caching. A
    # nested row's parent FK is left out too: the parent already supplies it.
    team = make_kill_team()
    make_kt_weapon(operative=make_kt_operative(kill_team=team))

    body = client.get(f"{BASE}/teams/{team.id}").json()
    weapon = body["operatives"][0]["weapons"][0]

    for payload in (body, body["operatives"][0], weapon):
        assert "created_at" not in payload and "updated_at" not in payload
    assert "kill_team_id" not in body["operatives"][0]
    assert "operative_id" not in weapon


def test_a_composition_is_served_as_printed_text(client, make_kill_team, make_kt_selection_rule):
    # The page's own words with its indent (decisions #44, #45): read in position order,
    # indenting by depth, and the composition section is back. No budget, no cap, no
    # option -- a consumer renders it, it does not compute with it.
    team = make_kill_team()
    for position, depth, kind, text in (
        (0, 0, "line", "4 HOLLOW operatives selected from the following list:"),
        (1, 1, "line", "SENTINEL with ash lash"),
        (2, 2, "line", "Ash lash; rusted glaive"),
        (3, 0, "restriction", "Other than SENTINEL operatives, once each."),
        (4, 0, "note", "A footnote the page prints."),
    ):
        make_kt_selection_rule(kill_team=team, position=position, depth=depth, kind=kind, text=text)

    rules = client.get(f"{BASE}/teams/{team.id}").json()["selection_rules"]

    assert [(r["position"], r["depth"], r["kind"]) for r in rules] == [
        (0, 0, "line"),
        (1, 1, "line"),
        (2, 2, "line"),
        (3, 0, "restriction"),
        (4, 0, "note"),
    ]
    assert set(rules[0]) == {"id", "position", "depth", "kind", "text"}


def test_an_operatives_availability_travels_with_it(client, make_kill_team, make_kt_operative):
    # What K5's "add an operative" picker is built from (decision #20).
    team = make_kill_team()
    make_kt_operative(kill_team=team, name="Cursemite", availability="in_battle", position=0)
    make_kt_operative(kill_team=team, name="Warden", position=1)

    operatives = client.get(f"{BASE}/teams/{team.id}").json()["operatives"]

    assert [(o["name"], o["availability"]) for o in operatives] == [
        ("Cursemite", "in_battle"),
        ("Warden", "roster"),
    ]


def test_an_unknown_team_is_a_404(client):
    resp = client.get(f"{BASE}/teams/{uuid.uuid4()}")

    assert resp.status_code == 404


def test_a_team_id_that_is_not_a_uuid_is_a_422(client):
    assert client.get(f"{BASE}/teams/not-a-uuid").status_code == 422


# --- the rows no team owns --------------------------------------------------


def test_the_universal_route_carries_both_collections(
    client, make_kt_ploy, make_kt_equipment, make_kill_team
):
    # One route for both (decision #48): they are one concept -- the rows with
    # `kill_team_id` NULL -- and one thing a client wants, fetched once per session.
    team = make_kill_team()
    make_kt_ploy(kill_team=team, name="A TEAM PLOY")
    make_kt_equipment(kill_team=team, name="TEAM KIT")
    make_kt_ploy(kill_team=None, name="COMMAND RE-ROLL")
    make_kt_equipment(kill_team=None, name="AMMO CACHE")

    body = client.get(f"{BASE}/universal").json()

    assert [p["name"] for p in body["ploys"]] == ["COMMAND RE-ROLL"]
    assert [e["name"] for e in body["equipment"]] == ["AMMO CACHE"]


def test_the_universal_route_is_not_paged(client, make_kt_ploy):
    # One document, not a collection: one ploy and eleven pieces of equipment, static
    # until a re-scrape. A client fetches it once and keeps it.
    make_kt_ploy(kill_team=None, name="COMMAND RE-ROLL")

    body = client.get(f"{BASE}/universal").json()

    assert set(body) == {"ploys", "equipment"}
    assert "total" not in body and "limit" not in body
