"""ajout motif de sortie (urgence, cas familial, etc.) au pointage

Revision ID: 2ec617d7c597
Revises: 652e794f941e
Create Date: 2026-07-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2ec617d7c597'
down_revision: Union[str, None] = '652e794f941e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


motif_sortie_enum = sa.Enum(
    'normale',
    'urgence',
    'raison_familiale',
    'raison_medicale',
    'autorisation_hierarchie',
    'autre',
    name='motif_sortie_enum',
)


def upgrade() -> None:
    motif_sortie_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('pointage', sa.Column('motif_sortie', motif_sortie_enum, nullable=True))
    op.add_column('pointage', sa.Column('commentaire', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('pointage', 'commentaire')
    op.drop_column('pointage', 'motif_sortie')
    motif_sortie_enum.drop(op.get_bind(), checkfirst=True)
