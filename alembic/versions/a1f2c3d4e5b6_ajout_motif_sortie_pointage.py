"""ajout motif_sortie / commentaire_motif sur pointage

Revision ID: a1f2c3d4e5b6
Revises: 652e794f941e
Create Date: 2026-07-17 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1f2c3d4e5b6"
down_revision: Union[str, None] = "652e794f941e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Deux colonnes nullables : elles restent NULL pour toutes les entrées et
    # pour l'historique existant (aucun backfill nécessaire).
    op.add_column(
        "pointage",
        sa.Column("motif_sortie", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "pointage",
        sa.Column("commentaire_motif", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("pointage", "commentaire_motif")
    op.drop_column("pointage", "motif_sortie")
