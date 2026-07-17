"""Fusion des deux branches

Revision ID: 0cfc0c431e8c
Revises: 72b291522a4b, a1f2c3d4e5b6
Create Date: 2026-07-17 21:30:25.249654

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0cfc0c431e8c'
down_revision: Union[str, None] = ('72b291522a4b', 'a1f2c3d4e5b6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass