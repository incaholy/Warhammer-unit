"""API tests for kill team rosters — the only Kill Team routes a player writes.

The ones that carry weight: a stranger's roster 404s rather than 403s, a repeated
add appends rather than 409ing, and the catalog's own routes stay read-only even
though this router sits beside them.
"""

import uuid

from app.main import app

BASE = "/api/v1/me/kill-team/rosters"


def _team(make_kill_team, make_kt_faction, name="Raveners", faction="Tyranids"):
    return make_kill_team(faction=make_kt_faction(name=faction), name=name)


# --- the write surface is only here -----------------------------------------


def test_adding_rosters_did_not_give_the_catalog_a_write_route():
    # Decision #21 again, asserted after a WRITING router was mounted next to the
    # read-only one: that is exactly when a write is most likely to land in the wrong
    # half, and this is the check that would catch it.
    doc = app.openapi()
    catalog = {p: set(s) for p, s in doc["paths"].items() if p.startswith("/api/v1/kill-team")}
    rosters = {p: set(s) for p, s in doc["paths"].items() if "/me/kill-team/rosters" in p}

    assert catalog and rosters, "both halves should be mounted"
    for path, methods in catalog.items():
        assert methods == {"get"}, f"{path} declares {sorted(methods - {'get'})}"
    assert {"post", "patch", "delete"} & set().union(*rosters.values())


def test_a_roster_route_needs_a_token(client):
    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={"kill_team_id": str(uuid.uuid4()), "name": "x"}).status_code == 401


# --- creating ---------------------------------------------------------------


def test_a_roster_is_created_for_the_caller(auth_client, make_kill_team, make_kt_faction):
    team = _team(make_kill_team, make_kt_faction)

    resp = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "My Raveners"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Raveners"
    assert body["kill_team_id"] == str(team.id)
    assert (body["kill_team_name"], body["faction_name"]) == ("Raveners", "Tyranids")
    assert body["operatives"] == []


def test_creating_a_roster_for_an_unknown_team_is_a_404(auth_client):
    resp = auth_client.post(BASE, json={"kill_team_id": str(uuid.uuid4()), "name": "Nowhere"})

    assert resp.status_code == 404


def test_a_second_roster_of_the_same_name_is_a_409(auth_client, make_kill_team, make_kt_faction):
    team = _team(make_kill_team, make_kt_faction)
    body = {"kill_team_id": str(team.id), "name": "Raveners"}
    auth_client.post(BASE, json=body)

    assert auth_client.post(BASE, json=body).status_code == 409


# --- the listing ------------------------------------------------------------


def test_the_listing_names_the_team_and_faction_without_the_operatives(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    # The player's own list should render "Raveners, Tyranids" without fetching the
    # catalog -- a deliberate exception to decision #46, which governs catalog reads.
    team = _team(make_kill_team, make_kt_faction)
    created = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    operative = make_kt_operative(kill_team=team, name="Prime")
    auth_client.post(f"{BASE}/{created['id']}/operatives", json={"operative_id": str(operative.id)})

    row = auth_client.get(BASE).json()["items"][0]

    assert set(row) == {
        "id",
        "name",
        "description",
        "created_at",
        "kill_team_id",
        "kill_team_name",
        "faction_name",
    }
    assert (row["kill_team_name"], row["faction_name"]) == ("Raveners", "Tyranids")


def test_the_listing_shows_only_the_callers_rosters(
    auth_client, admin_client, make_kill_team, make_kt_faction
):
    # Two different tokens, so two different owners.
    team = _team(make_kill_team, make_kt_faction)
    auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"})
    admin_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Theirs"})

    assert [r["name"] for r in auth_client.get(BASE).json()["items"]] == ["Mine"]
    assert auth_client.get(BASE).json()["total"] == 1


# --- reading one ------------------------------------------------------------


def test_a_detail_read_carries_the_datacards_and_the_reference(
    auth_client,
    make_kill_team,
    make_kt_faction,
    make_kt_operative,
    make_kt_weapon,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
):
    # Decision #52's whole list: the operatives with datacards, the team's rules, ploys
    # and equipment, and the two collections that belong to no team.
    team = _team(make_kill_team, make_kt_faction)
    make_kill_team_rule(kill_team=team, name="Burrow")
    make_kt_ploy(kill_team=team, name="TUNNEL")
    make_kt_equipment(kill_team=team, name="SPORE")
    make_kt_ploy(kill_team=None, name="COMMAND RE-ROLL")
    make_kt_equipment(kill_team=None, name="AMMO CACHE")
    operative = make_kt_operative(kill_team=team, name="Prime")
    make_kt_weapon(operative=operative, name="Tail blade")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    auth_client.post(f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(operative.id)})

    body = auth_client.get(f"{BASE}/{roster['id']}").json()

    assert [r["operative"]["name"] for r in body["operatives"]] == ["Prime"]
    assert [w["name"] for w in body["operatives"][0]["operative"]["weapons"]] == ["Tail blade"]
    assert [r["name"] for r in body["rules"]] == ["Burrow"]
    assert [p["name"] for p in body["ploys"]] == ["TUNNEL"]
    assert [e["name"] for e in body["equipment"]] == ["SPORE"]
    assert [p["name"] for p in body["universal_ploys"]] == ["COMMAND RE-ROLL"]
    assert [e["name"] for e in body["universal_equipment"]] == ["AMMO CACHE"]
    # No equipment of its own: a game chooses from those two lists (decision #17).
    assert "selected_equipment" not in body


def test_another_players_roster_is_a_404_not_a_403(
    auth_client, admin_client, make_kill_team, make_kt_faction
):
    # 404 rather than 403, so a roster id does not disclose that it exists -- the same
    # rule the armies router follows.
    team = _team(make_kill_team, make_kt_faction)
    theirs = admin_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Theirs"}).json()

    for method, path in (
        (auth_client.get, f"{BASE}/{theirs['id']}"),
        (auth_client.delete, f"{BASE}/{theirs['id']}"),
    ):
        assert method(path).status_code == 404


def test_an_unknown_roster_is_a_404(auth_client):
    assert auth_client.get(f"{BASE}/{uuid.uuid4()}").status_code == 404


# --- updating and deleting --------------------------------------------------


def test_a_roster_can_be_renamed(auth_client, make_kill_team, make_kt_faction):
    team = _team(make_kill_team, make_kt_faction)
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Old"}).json()

    body = auth_client.patch(f"{BASE}/{roster['id']}", json={"name": "New"}).json()

    assert body["name"] == "New"


def test_a_patch_cannot_move_a_roster_to_another_kill_team(auth_client, make_kill_team, make_kt_faction):
    # Not in `KTRoster_Update`, so FastAPI drops it; the service would reject it too.
    team = _team(make_kill_team, make_kt_faction)
    other = _team(make_kill_team, make_kt_faction, name="Gellerpox", faction="Nurgle")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()

    body = auth_client.patch(f"{BASE}/{roster['id']}", json={"kill_team_id": str(other.id)}).json()

    assert body["kill_team_id"] == str(team.id)


def test_a_roster_can_be_deleted(auth_client, make_kill_team, make_kt_faction):
    team = _team(make_kill_team, make_kt_faction)
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()

    assert auth_client.delete(f"{BASE}/{roster['id']}").status_code == 204
    assert auth_client.get(f"{BASE}/{roster['id']}").status_code == 404


# --- operatives on a roster -------------------------------------------------


def test_adding_the_same_operative_twice_appends(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    # The armies router 409s on a repeat because an incrementing add is not retry-safe.
    # There is no quantity here (decision #50), so a repeat is a second operative.
    team = _team(make_kill_team, make_kt_faction)
    operative = make_kt_operative(kill_team=team, name="Warrior")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    payload = {"operative_id": str(operative.id)}

    first = auth_client.post(f"{BASE}/{roster['id']}/operatives", json=payload)
    second = auth_client.post(f"{BASE}/{roster['id']}/operatives", json=payload)

    assert (first.status_code, second.status_code) == (201, 201)
    assert first.json()["id"] != second.json()["id"]
    assert [first.json()["position"], second.json()["position"]] == [0, 1]


def test_an_operative_of_another_team_cannot_be_added(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    team = _team(make_kill_team, make_kt_faction)
    stranger = make_kt_operative(
        kill_team=_team(make_kill_team, make_kt_faction, name="Other", faction="Necrons")
    )
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()

    resp = auth_client.post(f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(stranger.id)})

    assert resp.status_code == 404


def test_an_in_battle_operative_may_be_rostered(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    # Decision #51: `availability` informs a picker, it does not refuse a row.
    team = _team(make_kill_team, make_kt_faction)
    vermin = make_kt_operative(kill_team=team, name="Cursemite", availability="in_battle")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()

    resp = auth_client.post(f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(vermin.id)})

    assert resp.status_code == 201


def test_a_row_is_moved_and_removed_by_its_own_id(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    team = _team(make_kill_team, make_kt_faction)
    operative = make_kt_operative(kill_team=team, name="Warrior")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    payload = {"operative_id": str(operative.id)}
    first = auth_client.post(f"{BASE}/{roster['id']}/operatives", json=payload).json()
    second = auth_client.post(f"{BASE}/{roster['id']}/operatives", json=payload).json()

    moved = auth_client.patch(f"{BASE}/{roster['id']}/operatives/{second['id']}", json={"position": 0})
    removed = auth_client.delete(f"{BASE}/{roster['id']}/operatives/{first['id']}")

    assert (moved.status_code, moved.json()["position"]) == (200, 0)
    assert removed.status_code == 204
    body = auth_client.get(f"{BASE}/{roster['id']}").json()
    assert [r["id"] for r in body["operatives"]] == [second["id"]]


def test_a_row_from_another_roster_is_a_404(auth_client, make_kill_team, make_kt_faction, make_kt_operative):
    team = _team(make_kill_team, make_kt_faction)
    operative = make_kt_operative(kill_team=team)
    mine = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    other = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Other"}).json()
    row = auth_client.post(
        f"{BASE}/{other['id']}/operatives", json={"operative_id": str(operative.id)}
    ).json()

    assert auth_client.delete(f"{BASE}/{mine['id']}/operatives/{row['id']}").status_code == 404


def test_a_negative_position_is_a_400(auth_client, make_kill_team, make_kt_faction, make_kt_operative):
    team = _team(make_kill_team, make_kt_faction)
    operative = make_kt_operative(kill_team=team)
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    row = auth_client.post(
        f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(operative.id)}
    ).json()

    resp = auth_client.patch(f"{BASE}/{roster['id']}/operatives/{row['id']}", json={"position": -1})

    assert resp.status_code == 400
