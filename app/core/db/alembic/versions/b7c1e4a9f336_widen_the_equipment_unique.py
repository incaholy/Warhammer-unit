"""widen the equipment unique from the game to the side

Revision ID: b7c1e4a9f336
Revises: a6b9d3f1e825
Create Date: 2026-10-29 01:00:00.000000

K8's seventh revision (ROADMAP K8, decisions #17, #48 and #67; the chain's steps are
listed under "The revision chain" there, which is the only place their count lives).

ONE constraint swap, which is ONE operation. It is a second table's swap rather than the
second half of the previous revision, which is the split `ARCHITECTURE.md` section 4
prescribes: "a slice that changes four tables is several revisions". Its drop and its add
stay together, which is the split that section REFUSES -- between them the table has no
uniqueness rule at all, and `downgrade` can stop inside that window.

`UNIQUE(game_id, equipment_id)` becomes `UNIQUE(game_id, side, equipment_id)`. Each side
picks its own equipment, and the universal list belongs to no kill team (#48), so both
sides taking the same piece is an ordinary case. One per game was right when a game had
one side and is wrong the moment it has two.

The name is unchanged. It still names this table's uniqueness rule, so a violation
reading `uq_kt_game_equipment` is still true, and renaming would churn the one string a
reader greps for.

This swap WEAKENS the table until the last revision. Postgres treats two NULLs as
distinct, so while `side` is nullable `(game_id, NULL, equipment_id)` twice is accepted
and the constraint refuses nothing -- the property `uq_kt_game_side` relies on
deliberately (#77), here working against us. It is the same gap the previous revision
left on the operative leg, and it closes the same way.

Nothing can exploit the gap in the meantime, which is worth stating rather than hoping.
`KTGameService.add_equipment` refuses a piece already in the GAME -- `_equipment_row`
queries on `game_id` and `equipment_id` alone -- so the service is the narrower gate and
this revision changes nothing observable through the API. The capability only opens when
that check narrows to the side, which is code that lands with the last revision.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1e4a9f336"
down_revision: Union[str, Sequence[str], None] = "a6b9d3f1e825"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "uq_kt_game_equipment"
_TABLE = "kt_game_equipment"


def upgrade() -> None:
    """Swap the two-column unique for the three-column one."""
    op.drop_constraint(_NAME, _TABLE, type_="unique")
    op.create_unique_constraint(_NAME, _TABLE, ["game_id", "side", "equipment_id"])


def downgrade() -> None:
    """Narrow the unique back to the game.

    This reverse can be REFUSED by data, and it is the only one in the chain that can.
    Narrowing ADDS a restriction rather than removing one, so two rows that differ only
    by `side` -- both sides holding the same piece, which is exactly what this revision
    permits -- collide under `UNIQUE(game_id, equipment_id)` and the statement fails.

    Nothing is done about that on purpose. A reverse that silently deleted one of the two
    rows would be choosing which side loses its equipment, and no migration should make
    that choice. A loud failure names the rows, and an operator can decide.
    """
    op.drop_constraint(_NAME, _TABLE, type_="unique")
    op.create_unique_constraint(_NAME, _TABLE, ["game_id", "equipment_id"])
