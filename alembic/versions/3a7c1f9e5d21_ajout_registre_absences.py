"""ajout registre des absences (registre local, comme les congés)

L'administration centrale dispose déjà de sa propre application de gestion
des absences de tous les agents publics, commune à tous les ministères.
Cette table ne fait qu'enregistrer, côté SRB, qu'une absence est déjà
connue/justifiée ailleurs (statut ACTIF dès la création), pour que la
détection automatique d'absence sache exclure l'agent concerné — même
principe que la table `conge` (cf. `8f3a1c9d2b4e_ajout_gestion_conges.py`).

Revision ID: 3a7c1f9e5d21
Revises: 8f3a1c9d2b4e
Create Date: 2026-07-31 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3a7c1f9e5d21'
down_revision: Union[str, None] = '8f3a1c9d2b4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


statut_absence_enum = sa.Enum(
    'actif',
    'annule',
    name='statut_absence',
)


def upgrade() -> None:
    op.create_table(
        'absence',
        sa.Column('id_absence', sa.Integer(), nullable=False),
        sa.Column('id_agent', sa.Integer(), nullable=False),
        sa.Column('date_debut', sa.Date(), nullable=False),
        sa.Column('date_fin', sa.Date(), nullable=False),
        sa.Column('motif', sa.Text(), nullable=True),
        sa.Column('statut', statut_absence_enum, server_default='actif', nullable=False),
        sa.Column('id_utilisateur_saisie', sa.Integer(), nullable=False),
        sa.Column('date_creation', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('date_fin >= date_debut', name='ck_absence_dates_coherentes'),
        sa.ForeignKeyConstraint(['id_agent'], ['agent.id_agent'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['id_utilisateur_saisie'], ['utilisateur.id_utilisateur'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id_absence'),
    )
    op.create_index(op.f('ix_absence_id_agent'), 'absence', ['id_agent'], unique=False)
    op.create_index(op.f('ix_absence_date_debut'), 'absence', ['date_debut'], unique=False)
    op.create_index(op.f('ix_absence_date_fin'), 'absence', ['date_fin'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_absence_date_fin'), table_name='absence')
    op.drop_index(op.f('ix_absence_date_debut'), table_name='absence')
    op.drop_index(op.f('ix_absence_id_agent'), table_name='absence')
    op.drop_table('absence')
    statut_absence_enum.drop(op.get_bind(), checkfirst=True)
