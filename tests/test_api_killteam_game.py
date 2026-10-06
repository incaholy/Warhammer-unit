"""API tests for kill team games — the second half a player writes.

The ones that carry weight: a stranger's game 404s rather than 403s, the catalog's own
routes stay read-only now that a THIRD router sits beside them, a stale `version`
answers 409, and the ownership dependency does not drag the whole battle in to answer
a yes/no.
"""

import uuid

from app.core.db.models_killteam import KTGame
from app.core.services.service_killteam_game import EQUIPMENT_LIMIT
from app.main import app

BASE = "/api/v1/me/kill-team/games"


def _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative, n=3, **kw):
    """A roster of `n` operatives, created through the API so it belongs to the caller."""
    team = make_kill_team(
        faction=make_kt_faction(name=kw.get("faction", "Tyranids")), name=kw.get("team", "Raveners")
    )
    cards = [make_kt_operative(kill_team=team, name=f"Warrior {i}", wounds=12, position=i) for i in range(n)]
    roster = auth_client.post(
        "/api/v1/me/kill-team/rosters", json={"kill_team_id": str(team.id), "name": kw.get("roster", "Mine")}
    ).json()
    for card in cards:
        auth_client.post(
            f"/api/v1/me/kill-team/rosters/{roster['id']}/operatives",
            json={"operative_id": str(card.id)},
        )
    return team, cards, roster


# --- the write surface, and the catalog's lack of one ------------------------


def test_adding_games_did_not_give_the_catalog_a_write_route():
    # Decision #21 asserted again with a THIRD router mounted: the catalog stays
    # read-only however many writing halves grow beside it.
    doc = app.openapi()
    catalog = {p: set(s) for p, s in doc["paths"].items() if p.startswith("/api/v1/kill-team")}
    games = {p: set(s) for p, s in doc["paths"].items() if "/me/kill-team/games" in p}

    assert catalog and games, "both halves should be mounted"
    for path, methods in catalog.items():
        assert methods == {"get"}, f"{path} declares {sorted(methods - {'get'})}"
    assert {"post", "patch", "delete"} & set().union(*games.values())


def test_every_game_route_needs_a_token(client):
    paths = [p for p in app.openapi()["paths"] if "/me/kill-team/games" in p]
    operations = sum(len(app.openapi()["paths"][p]) for p in paths)
    assert (len(paths), operations) == (11, 15), f"{len(paths)} paths, {operations} operations"

    assert client.get(BASE).status_code == 401
    assert client.post(BASE, json={"roster_id": str(uuid.uuid4())}).status_code == 401
    assert client.get(f"{BASE}/{uuid.uuid4()}").status_code == 401
    assert client.post(f"{BASE}/{uuid.uuid4()}/advance").status_code == 401
    assert client.post(f"{BASE}/{uuid.uuid4()}/undo").status_code == 401


# --- creating and reading ---------------------------------------------------


def test_a_game_is_created_from_a_roster_and_carries_its_whole_reference(
    auth_client,
    make_kill_team,
    make_kt_faction,
    make_kt_operative,
    make_kt_weapon,
    make_kt_ability,
    make_kill_team_rule,
    make_kt_ploy,
):
    team, cards, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    make_kill_team_rule(kill_team=team, name="Burrow", position=0)
    make_kt_ploy(kill_team=team, name="Predatory Instincts", position=0)
    make_kt_ploy(kill_team=None, name="Command Re-roll", position=0)
    for card in cards:
        make_kt_weapon(operative=card, name="Claws", position=0)
        make_kt_ability(operative=card, name="Pounce", position=0)

    resp = auth_client.post(BASE, json={"roster_id": roster["id"], "opponent_name": "Bob"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "setup"
    assert body["turning_point"] == 1
    # NULL, not "player": nobody has rolled (#62).
    assert body["initiative"] is None
    assert body["kill_team_name"] == "Raveners" and body["faction_name"] == "Tyranids"
    # Decision #57: reported, and the list may exceed it.
    assert body["equipment_limit"] == EQUIPMENT_LIMIT
    assert len(body["operatives"]) == 3
    # The snapshot is typed in the response, not a bare object -- the frontend generates
    # its types from this document.
    first = body["operatives"][0]
    assert first["weapons"][0]["name"] == "Claws"
    assert first["abilities"][0]["description"]
    assert first["current_wounds"] == first["wounds"] == 12
    assert [r["name"] for r in body["rules"]] == ["Burrow"]
    assert sum(1 for p in body["ploys"] if p["universal"]) == 1


def test_a_game_cannot_be_started_from_a_stranger_s_roster(
    auth_client,
    client,
    make_user,
    make_kill_team,
    make_kt_faction,
    make_kt_operative,
    make_kt_roster,
):
    """404, and the SAME 404 a roster that does not exist gets.

    This asserted `in {404, 409}` and so pinned neither. The real answer was 409
    "conflict with an existing resource", from the composite foreign key refusing the
    insert (#54) -- safe, in that the row never landed, but distinguishable from the
    404 a missing id gets, which makes it an oracle for whether an id is real. The two
    are compared here rather than checked separately, because being equal is the point.
    """
    team = make_kill_team(faction=make_kt_faction(name="Nurgle"), name="Gellerpox")
    theirs = make_kt_roster(owner=make_user(username="other", email="other@test.invalid"), kill_team=team)

    stranger = auth_client.post(BASE, json={"roster_id": str(theirs.id)})
    missing = auth_client.post(BASE, json={"roster_id": str(uuid.uuid4())})

    assert stranger.status_code == 404, stranger.text
    assert stranger.json()["code"] == missing.json()["code"] == "NOT_FOUND"
    # Indistinguishable: a stranger's roster and a roster that was never there.
    assert stranger.status_code == missing.status_code


def test_a_stranger_s_game_is_a_404_not_a_403(
    auth_client, session, make_user, make_kill_team, make_kt_faction, make_kt_roster
):
    """404 rather than 403, so a game id reveals nothing about whether it exists.

    Every nested route goes through the same dependency, so all six are checked here --
    a per-route check would pass the day someone adds a seventh.
    """
    team = make_kill_team(faction=make_kt_faction(name="Nurgle"), name="Gellerpox")
    roster = make_kt_roster(owner=make_user(username="other", email="other@test.invalid"), kill_team=team)
    theirs = KTGame(owner_user_id=roster.owner_user_id, kill_team_id=team.id, roster_id=roster.id)
    session.add(theirs)
    session.commit()
    gid = theirs.id
    row, kit = uuid.uuid4(), uuid.uuid4()

    assert auth_client.get(f"{BASE}/{gid}").status_code == 404
    assert auth_client.patch(f"{BASE}/{gid}", json={"command_points": 1}).status_code == 404
    assert auth_client.delete(f"{BASE}/{gid}").status_code == 404
    assert auth_client.post(f"{BASE}/{gid}/advance").status_code == 404
    assert auth_client.post(f"{BASE}/{gid}/undo").status_code == 404
    assert auth_client.post(f"{BASE}/{gid}/operatives/{row}/activate").status_code == 404
    assert auth_client.post(f"{BASE}/{gid}/equipment", json={"equipment_id": str(kit)}).status_code == 404
    # And it is still there -- a 404 hid it, it did not delete it.
    assert session.get(KTGame, gid) is not None


def test_games_are_listed_leanly_and_only_for_the_caller(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    auth_client.post(BASE, json={"roster_id": roster["id"], "opponent_name": "Bob"})
    auth_client.post(BASE, json={"roster_id": roster["id"], "opponent_name": "Carol"})

    body = auth_client.get(BASE).json()

    assert body["total"] == 2
    row = body["items"][0]
    # The listing carries the score and the team's name, and NONE of the bundle.
    assert {"opponent_name", "status", "turning_point", "victory_points", "kill_team_name"} <= set(row)
    assert "operatives" not in row and "events" not in row and "rules" not in row


# --- the ownership check stays cheap ----------------------------------------


def test_the_ownership_dependency_leaves_the_battle_unloaded(
    auth_client, session, make_kill_team, make_kt_faction, make_kt_operative
):
    """The dependency loads the game SHALLOW — asserted through the dependency itself.

    The rosters router's equivalent eager-loads decision #52's whole bundle to answer a
    yes/no, so a 204 DELETE there costs as many queries as a GET. This asks SQLAlchemy
    which attributes are still unloaded, which is the fact rather than a proxy: a query
    count would not be, because a DELETE is expensive for an unrelated reason --
    `cascade_delete` makes the ORM read the collections in order to cascade them.

    It calls `get_owned_game` and NOT `get_game_shallow`. An earlier version of this
    test called the reader directly, and swapping the dependency over to the full read
    then changed nothing it could see -- it proved the shallow reader was shallow, never
    that the dependency used it.
    """
    from sqlalchemy import inspect

    from app.api.killteam_game import get_game_service, get_owned_game
    from app.core.db.models import User

    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game_id = uuid.UUID(auth_client.post(BASE, json={"roster_id": roster["id"]}).json()["id"])
    # Ids first, then expunge: reading an attribute off the fixture's user afterwards is
    # a `DetachedInstanceError`, which is the trap this session has hit before.
    user_id = auth_client.user.id
    session.expunge_all()

    service = get_game_service(session)
    game = get_owned_game(game_id, session.get(User, user_id), service)
    unloaded = inspect(game).unloaded

    assert {"operatives", "equipment", "events"} <= unloaded, f"the dependency loaded: {unloaded}"
    # And the full read does load them, so the two really are different reads.
    session.expunge_all()
    assert not ({"operatives", "equipment", "events"} & inspect(service.get_game(game_id)).unloaded)


# --- version: a stale write is a 409 (#9) -----------------------------------


def test_a_stale_version_answers_409(auth_client, make_kill_team, make_kt_faction, make_kt_operative):
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()
    stale = game["version"]

    assert (
        auth_client.patch(f"{BASE}/{game['id']}", json={"version": stale, "command_points": 1}).status_code
        == 200
    )
    resp = auth_client.patch(f"{BASE}/{game['id']}", json={"version": stale, "command_points": 9})

    assert resp.status_code == 409
    assert resp.json()["field"] == "version"
    assert auth_client.get(f"{BASE}/{game['id']}").json()["command_points"] == 1


def test_a_write_without_a_version_is_accepted(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    # `version` is optional: a single-tab client that never races need not track it.
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()

    resp = auth_client.patch(f"{BASE}/{game['id']}", json={"command_points": 4})

    assert resp.status_code == 200
    assert resp.json()["command_points"] == 4


def test_an_unknown_field_in_a_game_patch_is_a_422(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    # `WriteSchema` forbids extras, and `turning_point` is deliberately not updatable --
    # it moves through `advance`, which applies the resets that go with it.
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()

    assert auth_client.patch(f"{BASE}/{game['id']}", json={"turning_point": 3}).status_code == 422
    assert auth_client.patch(f"{BASE}/{game['id']}", json={"nmae": "x"}).status_code == 422


# --- the battle itself ------------------------------------------------------


def test_a_battle_runs_through_its_operatives_equipment_and_turning_points(
    auth_client,
    make_kill_team,
    make_kt_faction,
    make_kt_operative,
    make_kt_ability,
    make_kt_equipment,
):
    team, cards, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    for card in cards:
        make_kt_ability(operative=card, name="Pounce", position=0)
    kit = make_kt_equipment(kill_team=team, name="Grisly Trophy", position=0)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()
    gid, row = game["id"], game["operatives"][0]

    wounded = auth_client.patch(f"{BASE}/{gid}/operatives/{row['id']}", json={"current_wounds": 7})
    assert wounded.status_code == 200 and wounded.json()["current_wounds"] == 7

    activated = auth_client.post(f"{BASE}/{gid}/operatives/{row['id']}/activate")
    assert activated.status_code == 200 and activated.json()["activated_in_turning_point"] == 1
    assert auth_client.post(f"{BASE}/{gid}/operatives/{row['id']}/activate").status_code == 400

    taken = auth_client.post(f"{BASE}/{gid}/equipment", json={"equipment_id": str(kit.id)})
    assert taken.status_code == 201 and taken.json()["revealed"] is False
    assert auth_client.post(f"{BASE}/{gid}/equipment", json={"equipment_id": str(kit.id)}).status_code == 409
    revealed = auth_client.patch(f"{BASE}/{gid}/equipment/{taken.json()['id']}")
    assert revealed.json()["revealed"] is True

    first = auth_client.post(f"{BASE}/{gid}/advance").json()
    assert (first["phase"], first["status"]) == ("firefight", "in_progress")
    second = auth_client.post(f"{BASE}/{gid}/advance").json()
    assert (second["turning_point"], second["phase"]) == (2, "strategy")


def test_wounding_past_the_cards_maximum_is_a_400(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()
    row = game["operatives"][0]

    resp = auth_client.patch(f"{BASE}/{game['id']}/operatives/{row['id']}", json={"current_wounds": 99})

    assert resp.status_code == 400
    assert resp.json()["field"] == "current_wounds"


def test_an_operative_a_rule_grants_is_added_and_a_rosters_is_refused(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    team, cards, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    vermin = make_kt_operative(kill_team=team, name="Cursemite", availability="in_battle", position=9)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()
    gid = game["id"]

    added = auth_client.post(
        f"{BASE}/{gid}/operatives", json={"operative_id": str(vermin.id), "source": "equipment"}
    )
    assert added.status_code == 201
    assert (added.json()["source"], added.json()["added_in_turning_point"]) == ("equipment", 1)

    refused = auth_client.post(
        f"{BASE}/{gid}/operatives", json={"operative_id": str(cards[0].id), "source": "roster"}
    )
    assert refused.status_code == 400


def test_a_transform_keeps_the_row_and_moves_the_card(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative, make_kt_ability
):
    team, cards, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    torment = make_kt_operative(kill_team=team, name="Torment", wounds=18, position=9)
    make_kt_ability(operative=torment, name="Writhe", position=0)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()
    gid, row = game["id"], game["operatives"][0]
    auth_client.patch(f"{BASE}/{gid}/operatives/{row['id']}", json={"tokens": ["Poison"]})

    resp = auth_client.post(
        f"{BASE}/{gid}/operatives/{row['id']}/transform",
        json={"becomes_operative_id": str(torment.id)},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == row["id"], "the same model on the table"
    assert (body["name"], body["wounds"]) == ("Torment", 18)
    assert body["tokens"] == ["Poison"]
    assert body["abilities"][0]["name"] == "Writhe"


# --- undo through the API ---------------------------------------------------


def test_undo_reverts_the_last_change_and_returns_the_game_whole(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()
    gid = game["id"]
    auth_client.patch(f"{BASE}/{gid}", json={"command_points": 3})

    resp = auth_client.post(f"{BASE}/{gid}/undo")

    assert resp.status_code == 200
    body = resp.json()
    # The whole game, not the event: an undo can move anything, so a client that got
    # only the event would have to re-read to know what changed.
    assert body["command_points"] == 0
    assert "operatives" in body and "events" in body
    assert [e["type"] for e in body["events"]] == ["game_updated", "undone"]
    assert body["events"][0]["undone_by"] == body["events"][1]["id"]


def test_undo_with_nothing_left_is_a_400(auth_client, make_kill_team, make_kt_faction, make_kt_operative):
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    game = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()

    resp = auth_client.post(f"{BASE}/{game['id']}/undo")

    assert resp.status_code == 400
    assert "undo" in resp.json()["detail"]


def test_spending_a_ploy_end_to_end(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative, make_kt_ploy
):
    """One call, two fields moved, and one undo puts both back."""
    team, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    make_kt_ploy(kill_team=team, name="Predatory Instincts", cp_cost=1, position=0)
    gid = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()["id"]
    auth_client.patch(f"{BASE}/{gid}", json={"command_points": 3})

    resp = auth_client.post(f"{BASE}/{gid}/ploys", json={"name": "Predatory Instincts"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["command_points"] == 2, "the CP came out of the snapshot's cost"
    assert body["ploys_used"] == [{"name": "Predatory Instincts", "turning_point": 1}]

    undone = auth_client.post(f"{BASE}/{gid}/undo").json()
    assert (undone["command_points"], undone["ploys_used"]) == (3, [])


def test_a_ploy_the_team_does_not_have_is_a_400(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    _, _, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    gid = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()["id"]

    resp = auth_client.post(f"{BASE}/{gid}/ploys", json={"name": "Teleportarium"})

    assert resp.status_code == 400
    assert resp.json()["field"] == "name"


def test_spending_a_ploy_needs_a_token_and_the_caller_s_own_game(
    client, auth_client, session, make_user, make_kill_team, make_kt_faction, make_kt_roster
):
    # The new route goes through the same dependency as the other thirteen, so it is
    # covered by the 401/404 contract rather than needing its own rule.
    team = make_kill_team(faction=make_kt_faction(name="Nurgle"), name="Gellerpox")
    roster = make_kt_roster(owner=make_user(username="other2", email="other2@test.invalid"), kill_team=team)
    theirs = KTGame(owner_user_id=roster.owner_user_id, kill_team_id=team.id, roster_id=roster.id)
    session.add(theirs)
    session.commit()

    assert client.post(f"{BASE}/{theirs.id}/ploys", json={"name": "x"}).status_code == 401
    assert auth_client.post(f"{BASE}/{theirs.id}/ploys", json={"name": "x"}).status_code == 404


def test_a_write_that_changes_nothing_records_nothing_and_undo_still_works(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    """A no-op PATCH must not insert an event, or the next undo spends itself on it.

    `_apply` reports only the fields that moved, so sending a field its current value
    left `touched` empty -- and `_write` recorded the empty dict anyway. Undo walks back
    one event at a time, so it reverted the event that did nothing and the real change
    below it survived: tapping the same value twice made undo appear broken. The version
    bump was the second half, 409-ing the other tab over a write that never happened.
    """
    _team, _cards, roster = _battle(auth_client, make_kill_team, make_kt_faction, make_kt_operative)
    gid = auth_client.post(BASE, json={"roster_id": roster["id"]}).json()["id"]
    started_with = auth_client.get(f"{BASE}/{gid}").json()["command_points"]

    auth_client.patch(f"{BASE}/{gid}", json={"command_points": 3})
    real = auth_client.get(f"{BASE}/{gid}").json()
    assert real["command_points"] == 3

    # The same value again: accepted, but nothing happened, so nothing is recorded.
    again = auth_client.patch(f"{BASE}/{gid}", json={"command_points": 3})
    assert again.status_code == 200
    quiet = auth_client.get(f"{BASE}/{gid}").json()
    assert quiet["version"] == real["version"], "a no-op write moved the version"
    assert len(quiet["events"]) == len(real["events"]), "a no-op write left an event behind"

    # So the one undo reaches the one real change, rather than the empty event above it.
    assert auth_client.post(f"{BASE}/{gid}/undo", json={}).status_code == 200
    assert auth_client.get(f"{BASE}/{gid}").json()["command_points"] == started_with
