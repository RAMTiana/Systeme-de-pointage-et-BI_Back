"""fusion des têtes alembic

Revision ID: 573f4145c06b
Revises: 52f2030bd67b, 8f3a1c9d2b4e
Create Date: 2026-07-27 09:13:05.558566

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '573f4145c06b'
down_revision: Union[str, None] = ('52f2030bd67b', '8f3a1c9d2b4e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass