"""fusion heads

Revision ID: 21b4f14c1667
Revises: 3a7c1f9e5d21, 573f4145c06b
Create Date: 2026-08-02 14:42:45.888172

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '21b4f14c1667'
down_revision: Union[str, None] = ('3a7c1f9e5d21', '573f4145c06b')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass