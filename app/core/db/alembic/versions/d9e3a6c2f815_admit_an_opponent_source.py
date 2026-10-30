"""admit 'opponent' as an operative's source

Revision ID: d9e3a6c2f815
Revises: c8d2f5b1a749
Create Date: 2026-10-29 01:20:00.000000

K8's ninth revision (ROADMAP K8, decisions #92 and #93; the chain's steps are listed
under "The revision chain" there, which is the only place their count lives).

ONE constraint swap, which is ONE operation, and the third in this chain after the
operative leg and the equipment unique. Its drop and its add stay together, which is the
split `ARCHITECTURE.md` section 4 refuses: between them the column has no vocabulary at
all and `downgrade` can stop inside that window.

`source IN ('roster', 'equipment', 'rule')` gains `'opponent'`. #92 lets a host describe
an opponent who holds no account; #93 lets them field that opponent's models, and both of
`add_operative`'s guards refuse that case today -- `'roster'` outright, and
`operative.kill_team_id != game.kill_team_id` for every datacard of the other team, which
was correct while a game held one team.

A new value rather than reusing `'equipment'` or `'rule'`: `source` answers "why is this
row here", and "the host typed it in" is a different answer from either.

This revision WIDENS, so it cannot fail on existing data -- every row already holds one of
the three it keeps. It also changes nothing observable, because `_SOURCES` still refuses
`'opponent'` and `add_operative` reads `_SOURCES`. The schema admitting more than the
service does is the safe direction and the same shape the equipment unique took: the
service is the narrower gate until its own change lands, which is piece 6.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9e3a6c2f815"
down_revision: Union[str, Sequence[str], None] = "c8d2f5b1a749"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NAME = "ck_kt_game_operative_source"
_TABLE = "kt_game_operatives"
_THREE = "source IN ('roster', 'equipment', 'rule')"
_FOUR = "source IN ('roster', 'equipment', 'rule', 'opponent')"


def upgrade() -> None:
    """Swap the three-value vocabulary for the four-value one."""
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _FOUR)


def downgrade() -> None:
    """Narrow it back to three.

    REFUSED once any operative carries `source = 'opponent'`, which is a state this
    revision is what first permits. Narrowing a vocabulary adds a restriction rather than
    removing one, so Postgres validates the existing rows and the statement fails.

    Deliberately not softened. A reverse that rewrote those rows to another value would be
    inventing an answer to "why is this row here", and one that deleted them would destroy
    an opponent's models. A loud failure names the rows and an operator can decide -- the
    same reasoning the equipment unique's reverse carries.
    """
    op.drop_constraint(_NAME, _TABLE, type_="check")
    op.create_check_constraint(_NAME, _TABLE, _THREE)
