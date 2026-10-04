"""API tests for kill team rosters — the only Kill Team routes a player writes.

The ones that carry weight: a stranger's roster 404s rather than 403s, a repeated
add appends rather than 409ing, and the catalog's own routes stay read-only even
though this router sits beside them.
"""

import uuid

from sqlalchemy import event

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
    """`kill_team_id` is not in `KTRoster_Update`, and an unknown key is now a 422.

    It used to be DROPPED: the request validated clean, the service was handed `{}`,
    and the route answered 200 with the roster unchanged -- which reads as "the move
    was applied" to anything that only checks the status. `WriteSchema` forbids extras,
    so the refusal is now explicit and names the field.
    """
    team = _team(make_kill_team, make_kt_faction)
    other = _team(make_kill_team, make_kt_faction, name="Gellerpox", faction="Nurgle")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()

    resp = auth_client.patch(f"{BASE}/{roster['id']}", json={"kill_team_id": str(other.id)})

    assert resp.status_code == 422
    # Same error envelope as a service-raised `CodedError` -- `app/main.py` routes a
    # `RequestValidationError` through `_error_response` too, so a client reads `field`
    # the same way whichever layer refused it.
    assert resp.json()["field"] == "kill_team_id"
    # And the roster really did not move.
    assert auth_client.get(f"{BASE}/{roster['id']}").json()["kill_team_id"] == str(team.id)


def test_a_misspelled_patch_field_is_refused_rather_than_reported_as_success(
    auth_client, make_kill_team, make_kt_faction
):
    # The general case of the above, and the reason it is worth a 422: a typo used to
    # answer 200 having changed nothing, so a client could not tell a successful rename
    # from a silently discarded one.
    team = _team(make_kill_team, make_kt_faction)
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Old"}).json()

    resp = auth_client.patch(f"{BASE}/{roster['id']}", json={"nmae": "New"})

    assert resp.status_code == 422
    assert auth_client.get(f"{BASE}/{roster['id']}").json()["name"] == "Old"


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


# --- the ownership check does not drag the bundle in (audit finding 12) -----


def test_the_write_routes_ownership_check_leaves_the_roster_unloaded(
    auth_client, session, make_kill_team, make_kt_faction, make_kt_operative
):
    """`get_owned_roster_shallow` answers "owned?" without decision #52's bundle.

    Asserted through the DEPENDENCY, not the reader under it: a mutant survived in the
    games router because that test called `get_game_shallow` directly, so it proved the
    reader was shallow and never that the dependency used it.

    `inspect(...).unloaded` rather than a query count, because the count would be a
    proxy. Both are asserted here, since they say different things -- this one says the
    dependency is shallow, and the one below says the ROUTES use it.
    """
    from sqlalchemy import inspect

    from app.api.killteam_roster import (
        get_owned_roster,
        get_owned_roster_shallow,
        get_roster_service,
    )
    from app.core.db.models import User

    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    for n in range(2):
        card = make_kt_operative(kill_team=team, name=f"Warrior {n}", position=n)
        auth_client.post(f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(card.id)})
    roster_id = uuid.UUID(roster["id"])
    # Ids before the expunge: reading off the fixture's user afterwards is a
    # `DetachedInstanceError`.
    user_id = auth_client.user.id
    session.expunge_all()

    service = get_roster_service(session)
    shallow = get_owned_roster_shallow(roster_id, session.get(User, user_id), service)

    assert {"operatives", "kill_team"} <= inspect(shallow).unloaded, inspect(shallow).unloaded
    # And the bundle dependency really does load them, so the two are different reads
    # rather than the same one spelled twice.
    session.expunge_all()
    loaded = get_owned_roster(roster_id, session.get(User, user_id), service)
    assert not ({"operatives", "kill_team"} & inspect(loaded).unloaded)


def test_a_write_route_costs_far_less_than_the_detail_read(
    auth_client,
    session,
    make_kill_team,
    make_kt_faction,
    make_kt_operative,
    make_kt_weapon,
    make_kt_ability,
    make_kill_team_rule,
    make_kt_ploy,
    make_kt_equipment,
):
    """Audit finding 12: a 204 DELETE used to cost as much as a full GET.

    A query count IS the right assertion here, unlike for a game's DELETE: removing one
    roster row cascades nothing, so the number reflects the ownership check and the write
    rather than the ORM walking collections to delete them.

    A plain `<` is exactly the line, and the margin is not generous by accident. Measured
    on this fixture: the detail is 13 queries either way, a removal is 6 and a move 8 --
    where before the shallow dependency they were 13 and 14. So `<` fails the moment a
    write route reaches for the bundle again, and nothing looser would.

    Note what does NOT distinguish them: scaling. `selectinload` is a fixed number of
    queries whatever the collection holds, so the detail costs 13 for a one-operative
    roster and 13 for a six-operative one. An assertion about growth would pass either
    way; only the absolute count separates a shallow check from a full one.
    """
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    for n in range(3):
        make_kill_team_rule(kill_team=team, name=f"Rule {n}", position=n)
        make_kt_ploy(kill_team=team, name=f"Ploy {n}", position=n)
        make_kt_equipment(kill_team=team, name=f"Kit {n}", position=n)
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    rows = []
    for n in range(2):
        card = make_kt_operative(kill_team=team, name=f"Warrior {n}", position=n)
        make_kt_weapon(operative=card, name="Claws", position=0)
        make_kt_ability(operative=card, name="Pounce", position=0)
        rows.append(
            auth_client.post(f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(card.id)}).json()
        )

    def queries(call):
        n = 0

        def tick(*_a, **_k):
            nonlocal n
            n += 1

        bind = session.get_bind()
        event.listen(bind, "before_cursor_execute", tick)
        try:
            response = call()
        finally:
            event.remove(bind, "before_cursor_execute", tick)
        assert response.status_code in {200, 204}, response.text
        return n

    detail = queries(lambda: auth_client.get(f"{BASE}/{roster['id']}"))
    removal = queries(lambda: auth_client.delete(f"{BASE}/{roster['id']}/operatives/{rows[0]['id']}"))
    move = queries(
        lambda: auth_client.patch(f"{BASE}/{roster['id']}/operatives/{rows[1]['id']}", json={"position": 5})
    )

    assert removal < detail, f"a 204 cost {removal} queries against the detail's {detail}"
    assert move < detail, f"a one-row PATCH cost {move} against the detail's {detail}"


def test_what_a_delete_publishes_is_what_it_returns(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    """The DECLARED status matches the one the server sends.

    These routes return an explicit `Response(status_code=204)`, so changing the
    decorator's `status_code` changes `openapi.json` and nothing else -- a mutant that
    every test survived. The `openapi.json` drift gate in CI does catch it, but only
    because the committed file differs: regenerate the file in the same commit and the
    document would claim 200 while the server returned 204, with nothing to say so.

    The frontend generates its types from that document, so a wrong status there is a
    client written against a response that never arrives.
    """
    doc = app.openapi()
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    card = make_kt_operative(kill_team=team)
    roster = auth_client.post(BASE, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    row = auth_client.post(f"{BASE}/{roster['id']}/operatives", json={"operative_id": str(card.id)}).json()

    live = {
        f"{BASE}/{{roster_id}}/operatives/{{row_id}}": auth_client.delete(
            f"{BASE}/{roster['id']}/operatives/{row['id']}"
        ).status_code,
        f"{BASE}/{{roster_id}}": auth_client.delete(f"{BASE}/{roster['id']}").status_code,
    }

    for path, code in live.items():
        published = set(doc["paths"][path]["delete"]["responses"]) - {"422"}
        assert code == 204, f"{path} returned {code}"
        assert str(code) in published, f"{path} returns {code} but publishes {sorted(published)}"
