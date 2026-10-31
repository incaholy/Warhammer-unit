"""Every mutation takes the game's optimistic lock, which `sequence` depends on.

ROADMAP K9 states this as a correctness requirement rather than a convention, and says
why: `KTGameEvent.sequence` is assigned in Python as `max(sequence) + 1`, and its
`UNIQUE(game_id, sequence)` is the only guard against two writers choosing the same
number. Two concurrent writers cannot collide on it ONLY because both must also
`UPDATE kt_games ... WHERE version = N` in the same transaction, so the loser rolls back
first. A write that bumped nothing on `kt_games` would take no lock and could duplicate a
sequence.

K8 added three side-scoped writes -- `establish_side`, `update_side` and a side-scoped
`use_ploy` -- any of which could have written only to `kt_game_sides`. They did not, and
this is what keeps the next one honest.
"""

import re
from pathlib import Path

import pytest
from sqlalchemy import event

from app.core.services.service_killteam_game import KTGameService

#: Public methods that change a game and so MUST reach `_write`. Listed rather than
#: guessed, because "is this a mutation" is not derivable from a signature -- and paired
#: with `_READS` below so a NEW method has to be classified rather than quietly omitted.
_MUTATIONS = frozenset(
    {
        "update_game",
        "establish_side",
        "update_side",
        "add_operative",
        "transform_operative",
        "update_operative",
        "activate_operative",
        "use_ploy",
        "add_equipment",
        "reveal_equipment",
        "remove_equipment",
        "advance",
        "undo",
    }
)

#: The rest, each for a stated reason:
#:   `create_game`   writes the game ITSELF, so there is no prior version to lock and no
#:                   earlier event to collide with. #81 makes creation an event in K9,
#:                   which is when it joins `_MUTATIONS`.
#:   `delete_game`   removes the row the lock lives on.
#:   the reads       change nothing.
_READS = frozenset(
    {
        "create_game",
        "delete_game",
        "get_game",
        "get_game_shallow",
        "list_games",
        "count_games",
        "player_side",
        "opponent_side",
    }
)


def _public_methods():
    source = Path("app/core/services/service_killteam_game.py").read_text(encoding="utf-8")
    return {n for n in re.findall(r"^    def ([a-z][a-z_0-9]*)\(", source, re.M)}


def test_every_public_method_is_classified():
    """A method added later must be put in one list or the other.

    This is the forcing function. Without it a new mutation is simply absent from
    `_MUTATIONS`, and the test below passes while the new write takes no lock.
    """
    unclassified = _public_methods() - _MUTATIONS - _READS
    assert unclassified == set(), f"classify these as a mutation or a read: {sorted(unclassified)}"

    stale = (_MUTATIONS | _READS) - _public_methods()
    assert stale == set(), f"these no longer exist: {sorted(stale)}"


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_every_mutation_reaches_write(name):
    """`_write` is the one place that bumps `version`, so reaching it IS taking the lock."""
    source = Path("app/core/services/service_killteam_game.py").read_text(encoding="utf-8")
    body = re.search(rf"    def {name}\(.*?(?=\n    def )", source, re.S).group(0)
    assert "self._write(" in body, f"{name} changes a game without writing an event"


def test_write_is_the_only_place_version_moves():
    """Because the claim above is only true while that stays so."""
    source = Path("app/core/services/service_killteam_game.py").read_text(encoding="utf-8")
    bumps = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"\bversion\s*\+=|\bversion\s*=\s*\w+\.version\s*\+", line)
    ]
    assert bumps == ["game.version += 1"], f"version moves in more than one place: {bumps}"


def test_a_side_scoped_write_emits_the_lock(
    session, make_kt_roster, make_kt_operative, make_kt_roster_operative
):
    """The behavioural half: a write that touches only a side still locks the GAME.

    Asserted against the SQL, because this is the property the structural tests above
    stand in for -- they check that `_write` is called, and this checks what calling it
    does.
    """
    roster = make_kt_roster()
    make_kt_roster_operative(roster=roster, operative=make_kt_operative(kill_team=roster.kill_team))
    svc = KTGameService(session)
    game = svc.create_game(roster.owner_user_id, roster.id)
    session.commit()

    seen: list[str] = []

    @event.listens_for(session.get_bind(), "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    try:
        svc.update_side(game.id, "player", None, command_points=3)
        session.flush()
    finally:
        event.remove(session.get_bind(), "before_cursor_execute", _record)

    locks = [s for s in seen if s.startswith("UPDATE kt_games") and "version = ?" in s]
    assert locks, f"no optimistic lock on kt_games; statements were {seen}"
