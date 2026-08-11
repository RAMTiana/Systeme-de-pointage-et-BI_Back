"""fusion des têtes

Revision ID: d01e5aa2bb90
Revises: 21b4f14c1667, 249768be473b
Create Date: 2026-08-11 08:09:21.535046

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd01e5aa2bb90'
down_revision: Union[str, None] = ('21b4f14c1667', '249768be473b')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass