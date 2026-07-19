"""Fusion des branches divergentes

Revision ID: 2db6db8a20b9
Revises: 2ec617d7c597, 7f23065a53f7
Create Date: 2026-07-19 20:17:20.428191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2db6db8a20b9'
down_revision: Union[str, None] = ('2ec617d7c597', '7f23065a53f7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass