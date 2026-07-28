"""ajout registre des congés (évite les fausses absences)
 
Pas de circuit d'approbation local : la fonction publique malgache dispose
déjà d'un système national dédié aux demandes de congé/absence, commun à
tous les ministères. Cette table ne fait qu'enregistrer, côté SRB, un congé
déjà validé ailleurs (statut ACTIF dès la création), pour que la détection
automatique d'absence sache exclure l'agent concerné.
 
Revision ID: 8f3a1c9d2b4e
Revises: 52f2030bd67b
Create Date: 2026-07-27 09:00:00.000000
 
"""
from typing import Sequence, Union
 
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
 
 
# revision identifiers, used by Alembic.
revision: str = '8f3a1c9d2b4e'
down_revision: Union[str, None] = '52f2030bd67b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None
 
 
# Enum utilisé UNIQUEMENT pour la création/suppression explicite du type Postgres.
type_conge_enum = postgresql.ENUM(
    'conge_annuel',
    'maladie',
    'maternite',
    'paternite',
    'evenement_familial',
    'sans_solde',
    'autre',
    name='type_conge',
)
 
statut_conge_enum = postgresql.ENUM(
    'actif',
    'annule',
    name='statut_conge',
)
 
# Enum utilisé dans la colonne : create_type=False car le type est déjà
# créé explicitement ci-dessus (op.create_table ne fait pas de checkfirst).
type_conge_col = postgresql.ENUM(
    'conge_annuel',
    'maladie',
    'maternite',
    'paternite',
    'evenement_familial',
    'sans_solde',
    'autre',
    name='type_conge',
    create_type=False,
)
 
statut_conge_col = postgresql.ENUM(
    'actif',
    'annule',
    name='statut_conge',
    create_type=False,
)
 
 
def upgrade() -> None:
    type_conge_enum.create(op.get_bind(), checkfirst=True)
    statut_conge_enum.create(op.get_bind(), checkfirst=True)
 
    op.create_table(
        'conge',
        sa.Column('id_conge', sa.Integer(), nullable=False),
        sa.Column('id_agent', sa.Integer(), nullable=False),
        sa.Column('type_conge', type_conge_col, nullable=False),
        sa.Column('date_debut', sa.Date(), nullable=False),
        sa.Column('date_fin', sa.Date(), nullable=False),
        sa.Column('motif', sa.Text(), nullable=True),
        sa.Column('statut', statut_conge_col, server_default='actif', nullable=False),
        sa.Column('id_utilisateur_saisie', sa.Integer(), nullable=False),
        sa.Column('date_creation', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('date_fin >= date_debut', name='ck_conge_dates_coherentes'),
        sa.ForeignKeyConstraint(['id_agent'], ['agent.id_agent'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['id_utilisateur_saisie'], ['utilisateur.id_utilisateur'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id_conge'),
    )
    op.create_index(op.f('ix_conge_id_agent'), 'conge', ['id_agent'], unique=False)
    op.create_index(op.f('ix_conge_date_debut'), 'conge', ['date_debut'], unique=False)
    op.create_index(op.f('ix_conge_date_fin'), 'conge', ['date_fin'], unique=False)
 
 
def downgrade() -> None:
    op.drop_index(op.f('ix_conge_date_fin'), table_name='conge')
    op.drop_index(op.f('ix_conge_date_debut'), table_name='conge')
    op.drop_index(op.f('ix_conge_id_agent'), table_name='conge')
    op.drop_table('conge')
    statut_conge_enum.drop(op.get_bind(), checkfirst=True)
    type_conge_enum.drop(op.get_bind(), checkfirst=True)