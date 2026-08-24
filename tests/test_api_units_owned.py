"""`GET /units?owned=true` — the server-side inventory filter (frontend ROADMAP F10).

Filtering and pagination have to happen on the same side. The frontend used to
narrow the current page against the inventory, which hid owned units sitting on
other pages and made "N of M" compare a filtered page against an unfiltered total.
"""

from app.core.db.models import UserUnit


def _own(session, user, unit, amount=1):
    session.add(UserUnit(owner_user_id=user.id, unit_id=unit.id, amount=amount))
    session.commit()


def test_owned_filters_to_the_callers_inventory(auth_client, session, make_unit):
    owned, not_owned = make_unit(unit_name="Alpha"), make_unit(unit_name="Beta")
    _own(session, auth_client.user, owned)

    body = auth_client.get("/units?owned=true").json()
    assert [u["unit_name"] for u in body["items"]] == ["Alpha"]
    assert body["total"] == 1
    # ...and the unfiltered catalog still shows both.
    assert auth_client.get("/units").json()["total"] == 2
    assert not_owned.id is not None


def test_total_counts_the_filtered_set_not_the_catalog(auth_client, session, make_unit):
    # The bug this closes: a filtered page beside an unfiltered total.
    for i in range(5):
        make_unit(unit_name=f"Unit {i}")
    _own(session, auth_client.user, make_unit(unit_name="Mine"))

    body = auth_client.get("/units?owned=true").json()
    assert body["total"] == 1 and len(body["items"]) == 1


def test_an_owned_unit_on_a_later_page_is_reachable(auth_client, session, make_unit):
    # Client-side filtering could not see this: unfiltered, "Zulu" is last, so with
    # limit=2 it sits on page 3 and an owned-only view of page 1 showed nothing.
    for name in ["Alpha", "Bravo", "Charlie", "Delta"]:
        make_unit(unit_name=name)
    _own(session, auth_client.user, make_unit(unit_name="Zulu"))

    body = auth_client.get("/units?owned=true&limit=2&offset=0").json()
    assert [u["unit_name"] for u in body["items"]] == ["Zulu"]
    assert body["total"] == 1


def test_facets_respect_owned_so_the_rail_agrees_with_the_list(auth_client, session, make_unit, make_faction):
    f1, f2 = make_faction("Imperium"), make_faction("Chaos")
    make_unit(faction=f1, unit_name="Theirs")
    _own(session, auth_client.user, make_unit(faction=f2, unit_name="Mine"))

    facets = auth_client.get("/units/facets?owned=true").json()
    assert facets["total"] == 1
    assert facets["by_faction"] == {str(f2.id): 1}


def test_owned_true_without_a_token_is_401_not_the_whole_catalog(client, make_unit):
    # Silently ignoring the parameter would return every unit while the client
    # believes it is showing an owned-only view.
    make_unit()
    assert client.get("/units?owned=true").status_code == 401
    assert client.get("/units").status_code == 200  # the catalog stays public


def test_owned_composes_with_the_other_filters(auth_client, session, make_unit, make_faction):
    f1, f2 = make_faction("Imperium"), make_faction("Chaos")
    _own(session, auth_client.user, make_unit(faction=f1, unit_name="Alpha"))
    _own(session, auth_client.user, make_unit(faction=f2, unit_name="Alpha Two"))

    body = auth_client.get(f"/units?owned=true&faction_id={f1.id}").json()
    assert [u["unit_name"] for u in body["items"]] == ["Alpha"]
    assert body["total"] == 1

    body = auth_client.get("/units?owned=true&q=two").json()
    assert [u["unit_name"] for u in body["items"]] == ["Alpha Two"]
