"""fusion des heads

Revision ID: 72b291522a4b
Revises: 652e794f941e, caaf7ffdea1e
Create Date: 2026-07-17 19:26:04.879955

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '72b291522a4b'
down_revision: Union[str, None] = ('652e794f941e', 'caaf7ffdea1e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass