"""ajout motif de sortie sur pointage (urgence, cas familial, medical, ...)

Revision ID: a1f2c3d4e5b6
Revises: 652e794f941e
Create Date: 2026-07-19 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1f2c3d4e5b6"
down_revision: Union[str, None] = "652e794f941e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MOTIF_VALUES = (
    "fin_service",
    "pause",
    "urgence",
    "cas_familial",
    "medical",
    "mission",
    "autre",
)


def upgrade() -> None:
    motif_enum = sa.Enum(*MOTIF_VALUES, name="motif_sortie_enum")
    motif_enum.create(op.get_bind(), checkfirst=True)

    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing_cols = [c["name"] for c in insp.get_columns("pointage")] if "pointage" in insp.get_table_names() else []

    if "motif_sortie" not in existing_cols:
        try:
            op.add_column(
                "pointage",
                sa.Column("motif_sortie", motif_enum, nullable=True),
            )
        except sa.exc.ProgrammingError:
            pass

    if "commentaire_motif" not in existing_cols:
        try:
            op.add_column(
                "pointage",
                sa.Column("commentaire_motif", sa.String(length=255), nullable=True),
            )
        except sa.exc.ProgrammingError:
            pass

    # Rétro-remplissage : toute sortie existante devient "fin_service".
    op.execute(
        "UPDATE pointage SET motif_sortie = 'fin_service'::motif_sortie_enum "
        "WHERE type_pointage = 'sortie' AND motif_sortie IS NULL"
    )

    # Contrainte d'intégrité : motif uniquement sur les sorties.
    op.create_check_constraint(
        "ck_pointage_motif_sortie_coherent",
        "pointage",
        "(type_pointage = 'sortie' AND motif_sortie IS NOT NULL) "
        "OR (type_pointage = 'entree' AND motif_sortie IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_pointage_motif_sortie_coherent", "pointage", type_="check")
    op.drop_column("pointage", "commentaire_motif")
    op.drop_column("pointage", "motif_sortie")
    sa.Enum(name="motif_sortie_enum").drop(op.get_bind(), checkfirst=True)
