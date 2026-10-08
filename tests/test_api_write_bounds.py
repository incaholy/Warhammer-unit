"""Every write schema's bounds — the class of 500 the default test tier cannot see.

A SQLModel `Field(max_length=128)` constrains the generated DDL, not the Pydantic
schema a router validates against. So an over-long name or an out-of-range int passed
validation, reached the database, and came back as an unhandled 500 -- on POSTGRES.
SQLite's `VARCHAR(128)` is advisory and its `INTEGER` is 64-bit, so it stored both
happily and 525 green tests said nothing about it.

That is why the bounds went into the schemas (`app/api/fields.py`) rather than into the
services: a request refused before the session opens is refused identically on both
tiers, so these tests mean the same thing in either one.

`test_every_json_write_schema_bounds_its_strings_and_ints` is the load-bearing one. It
asserts the set of unbounded fields EXACTLY, so it fails in both directions -- a new
router with a bare `str` fails it, and so does bounding a listed field without striking
it from the list. It earned that second direction immediately: it failed when the 40k
admin schemas were bounded in the following commit, which is the point.
"""

import pytest

from app.api.fields import INT32_MAX
from app.main import app

ARMIES = "/api/v1/me/armies"
ROSTERS = "/api/v1/me/kill-team/rosters"

# Fields backed by an unbounded `TEXT` column, so there is no column limit to restate.
# A description is free-form prose and deliberately has no ceiling. This is the whole
# remainder: every other string and int in every JSON request body is bounded.
UNBOUNDED_TEXT = {
    "Ability_Create.description",
    "Ability_Update.description",
    "Army_Create.description",
    "Army_Update.description",
    "KTRoster_Create.description",
    "KTRoster_Update.description",
}


def _unbounded_write_fields() -> set[str]:
    """Every `<Schema>.<field>` in a JSON request body with no ceiling on its value.

    A nullable field publishes as `anyOf: [{type: string, ...}, {type: null}]`, so each
    branch is checked rather than the property. A `format` (uuid, email, date-time) is
    its own bound and needs no length. Form-encoded bodies are skipped: `login`'s
    `username` is looked up, never stored, so no column constrains it.
    """
    doc = app.openapi()
    schemas = doc["components"]["schemas"]
    bodies = {
        op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        for spec in doc["paths"].values()
        for op in spec.values()
        if isinstance(op, dict) and "application/json" in op.get("requestBody", {}).get("content", {})
    }

    unbounded = set()
    for schema_name in bodies:
        for field, spec in schemas[schema_name].get("properties", {}).items():
            for branch in spec.get("anyOf", [spec]):
                kind = branch.get("type")
                if kind == "string" and not branch.get("format") and "maxLength" not in branch:
                    unbounded.add(f"{schema_name}.{field}")
                elif kind == "integer" and "maximum" not in branch:
                    unbounded.add(f"{schema_name}.{field}")
    return unbounded


def test_every_json_write_schema_bounds_its_strings_and_ints():
    assert _unbounded_write_fields() == UNBOUNDED_TEXT


def test_every_json_write_schema_forbids_an_unknown_key():
    """`additionalProperties: false` on every request body, published.

    Pydantic ignores unknown keys by default, so a `PATCH {"nmae": "x"}` validated
    clean, reached the service as `{}` and answered 200 having changed nothing -- a
    typo indistinguishable from success. Every write schema inherits `WriteSchema`,
    which forbids extras; this asserts it over the document so a new router that
    subclasses `SQLModel` directly fails here rather than in production.

    Note `class X(SQLModel, extra="forbid")` is accepted by SQLModel's metaclass and
    does NOTHING -- `model_config["extra"]` stays `None`. That form would pass a test
    that only read the class and not the document.
    """
    doc = app.openapi()
    schemas = doc["components"]["schemas"]
    bodies = {
        op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
        for spec in doc["paths"].values()
        for op in spec.values()
        if isinstance(op, dict) and "application/json" in op.get("requestBody", {}).get("content", {})
    }

    assert bodies, "no JSON request bodies found -- the routers are not mounted"
    permissive = {n for n in bodies if schemas[n].get("additionalProperties") is not False}
    assert permissive == set()


# - the values themselves, over the wire -----------------------------------


@pytest.mark.parametrize(
    "name",
    ["x" * 129, "", "   ", "\t\n "],
    ids=["too-long", "empty", "spaces", "whitespace"],
)
def test_a_roster_name_must_be_present_and_fit_the_column(auth_client, make_kill_team, make_kt_faction, name):
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    body = {"kill_team_id": str(team.id), "name": name}

    assert auth_client.post(ROSTERS, json=body).status_code == 422

    ok = auth_client.post(ROSTERS, json={**body, "name": "Mine"}).json()
    assert auth_client.patch(f"{ROSTERS}/{ok['id']}", json={"name": name}).status_code == 422
    # The rename was refused, not half-applied.
    assert auth_client.get(f"{ROSTERS}/{ok['id']}").json()["name"] == "Mine"


def test_a_name_exactly_at_the_column_limit_is_accepted(auth_client, make_kill_team, make_kt_faction):
    # The boundary itself, so `maxLength` cannot drift to 127 unnoticed.
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    resp = auth_client.post(ROSTERS, json={"kill_team_id": str(team.id), "name": "x" * 128})

    assert resp.status_code == 201
    assert len(resp.json()["name"]) == 128


def test_a_surrounding_space_is_kept_rather_than_trimmed(auth_client, make_kill_team, make_kt_faction):
    # `_not_blank` checks and does not rewrite: a name is stored as the client sent it,
    # so " Mine " does not silently become an attempt to reuse "Mine".
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    auth_client.post(ROSTERS, json={"kill_team_id": str(team.id), "name": "Mine"})
    resp = auth_client.post(ROSTERS, json={"kill_team_id": str(team.id), "name": " Mine "})

    assert resp.status_code == 201
    assert resp.json()["name"] == " Mine "


def test_a_position_past_the_integer_column_is_refused_not_a_500(
    auth_client, make_kill_team, make_kt_faction, make_kt_operative
):
    team = make_kill_team(faction=make_kt_faction(name="Tyranids"), name="Raveners")
    operative = make_kt_operative(kill_team=team)
    roster = auth_client.post(ROSTERS, json={"kill_team_id": str(team.id), "name": "Mine"}).json()
    row = auth_client.post(
        f"{ROSTERS}/{roster['id']}/operatives", json={"operative_id": str(operative.id)}
    ).json()
    moved = f"{ROSTERS}/{roster['id']}/operatives/{row['id']}"

    assert auth_client.patch(moved, json={"position": INT32_MAX + 1}).status_code == 422
    # The top of the range still works, and the service's own floor is untouched: this
    # schema deliberately bounds only the top, so a negative position stays a 400.
    assert auth_client.patch(moved, json={"position": INT32_MAX}).status_code == 200
    assert auth_client.patch(moved, json={"position": -1}).status_code == 400


def test_an_army_points_limit_is_bounded_at_both_ends(auth_client, make_faction):
    faction = make_faction()
    body = {"name": "A", "faction_id": str(faction.id)}

    # A negative limit used to reach `ck_army_points_limit_non_negative` and surface as a
    # 409 -- bad input reported as a conflict with another resource.
    assert auth_client.post(ARMIES, json={**body, "points_limit": -1}).status_code == 422
    assert auth_client.post(ARMIES, json={**body, "points_limit": INT32_MAX + 1}).status_code == 422
    assert auth_client.post(ARMIES, json={**body, "points_limit": 2000}).status_code == 201


def test_an_amount_past_the_integer_column_is_refused_not_a_500(auth_client, make_faction, make_unit):
    faction = make_faction()
    unit = make_unit(faction=faction)
    army = auth_client.post(ARMIES, json={"name": "A", "faction_id": str(faction.id)}).json()
    units = f"{ARMIES}/{army['id']}/units"

    assert auth_client.post(units, json={"unit_id": str(unit.id), "amount": INT32_MAX + 1}).status_code == 422
    assert auth_client.post(units, json={"unit_id": str(unit.id), "amount": 1}).status_code == 201
    # `AmountSet` bounds only the top; below 1 the service still answers 400, naming
    # `remove_unit`.
    assert auth_client.patch(f"{units}/{unit.id}", json={"amount": INT32_MAX + 1}).status_code == 422
    assert auth_client.patch(f"{units}/{unit.id}", json={"amount": 0}).status_code == 400


def test_an_inventory_amount_is_bounded_the_same_way(auth_client, make_unit):
    unit = make_unit()
    base = "/api/v1/me/inventory"

    assert auth_client.post(base, json={"unit_id": str(unit.id), "amount": INT32_MAX + 1}).status_code == 422
    assert auth_client.post(base, json={"unit_id": str(unit.id), "amount": 1}).status_code == 201
    assert auth_client.patch(f"{base}/{unit.id}", json={"amount": INT32_MAX + 1}).status_code == 422
    assert auth_client.patch(f"{base}/{unit.id}", json={"amount": 0}).status_code == 400


@pytest.mark.parametrize(
    "username",
    ["u" * 65, "   ", "ab"],
    ids=["too-long", "spaces", "too-short"],
)
def test_a_username_must_be_present_and_fit_the_column(client, username):
    # `min_length=3` alone accepted "   ": three spaces are three characters, and the
    # account was created. Registration is the one unauthenticated write, so it is the
    # one anybody can reach.
    resp = client.post(
        "/api/v1/auth/register",
        json={"username": username, "email": "someone@test.invalid", "password": "Str0ngPassw0rd!"},
    )

    assert resp.status_code == 422


# --- the admin half of the catalog, bounded the same way --------------------


@pytest.mark.parametrize("value", ["x" * 129, "", "   "], ids=["too-long", "empty", "spaces"])
def test_a_unit_name_must_be_present_and_fit_the_column(admin_client, make_faction, value):
    faction = make_faction()
    body = {
        "faction_id": str(faction.id),
        "unit_name": value,
        "movement": 6,
        "toughness": 4,
        "armor_save": 3,
        "wounds": 2,
        "leadership": 6,
        "objective_control": 2,
        "points": 80,
    }

    assert admin_client.post("/api/v1/units", json=body).status_code == 422


@pytest.mark.parametrize(
    "stat",
    ["movement", "toughness", "armor_save", "wounds", "leadership", "objective_control", "points"],
)
def test_a_unit_stat_is_bounded_at_both_ends(admin_client, make_faction, stat):
    faction = make_faction()
    body = {
        "faction_id": str(faction.id),
        "unit_name": "Intercessor",
        "movement": 6,
        "toughness": 4,
        "armor_save": 3,
        "wounds": 2,
        "leadership": 6,
        "objective_control": 2,
        "points": 80,
    }

    # A negative stat reached `ck_unit_stats_non_negative` and surfaced as a 409.
    assert admin_client.post("/api/v1/units", json={**body, stat: -1}).status_code == 422
    assert admin_client.post("/api/v1/units", json={**body, stat: INT32_MAX + 1}).status_code == 422


def test_a_weapon_stat_and_its_dice_notation_are_bounded(admin_client):
    body = {
        "name": "Bolt rifle",
        "category": "range",
        "attacks": "2",
        "weapon_skill": 3,
        "strength": 4,
        "armor_piercing": 1,
        "damage": "1",
        "range_inches": 24,
    }

    assert admin_client.post("/api/v1/weapons", json=body).status_code == 201
    assert admin_client.post("/api/v1/weapons", json={**body, "strength": -1}).status_code == 422
    assert admin_client.post("/api/v1/weapons", json={**body, "damage": "D" * 17}).status_code == 422
    assert admin_client.post("/api/v1/weapons", json={**body, "attacks": "  "}).status_code == 422
    assert admin_client.post("/api/v1/weapons", json={**body, "category": "x" * 9}).status_code == 422
    # `category` keeps its SERVICE-raised 400 for a wrong-but-short value: the schema
    # bounds the length, the service validates the value, and that status is pinned by
    # `test_create_weapon_invalid_category_returns_400`.
    assert admin_client.post("/api/v1/weapons", json={**body, "category": "thrown"}).status_code == 400


def test_every_list_route_bounds_its_offset_at_both_ends(auth_client):
    """`offset` is bounded above on every paginated route, derived from the spec.

    Read-side rather than write-side, but the same class of bug and the same reason it
    belongs here: an unbounded incoming int is a 500. A Python int has no width, so
    `?offset=10**21` passed validation and reached the driver as an `OverflowError`
    (SQLite) or `NumericValueOutOfRange` (Postgres) -- unhandled on BOTH tiers, which is
    how it survived 689 green tests.

    Derived from `app.openapi()` rather than listed, because the risk is not the bound
    itself -- that lives in one shared `PageParams` -- but a route that declares its own
    `limit`/`offset` instead of depending on it. A listing would pass the day someone
    adds the thirteenth route; this fails.
    """
    routes = [
        path
        for path, operations in app.openapi()["paths"].items()
        for operation in operations.values()
        if any(p.get("name") == "offset" for p in operation.get("parameters", []))
    ]
    assert len(routes) >= 12, routes  # every list route the project serves

    for path in routes:
        assert auth_client.get(path, params={"offset": INT32_MAX + 1}).status_code == 422, path
        assert auth_client.get(path, params={"offset": -1}).status_code == 422, path
        assert auth_client.get(path, params={"offset": INT32_MAX}).status_code == 200, path
