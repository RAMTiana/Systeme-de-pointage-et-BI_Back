"""ajout biometrie webauthn pour les agents

Revision ID: 652e794f941e
Revises: d9936f63267d
Create Date: 2026-07-17 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '652e794f941e'
down_revision: Union[str, None] = 'd9936f63267d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL n'autorise pas ALTER TYPE ... ADD VALUE dans une transaction
    # classique : on l'exécute dans un bloc autocommit dédié.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE mode_pointage_enum ADD VALUE IF NOT EXISTS 'webauthn'")

    op.create_table(
        'identifiant_webauthn',
        sa.Column('id_identifiant', sa.Integer(), nullable=False),
        sa.Column('id_agent', sa.Integer(), nullable=False),
        sa.Column('credential_id', sa.String(length=512), nullable=False),
        sa.Column('cle_publique', sa.LargeBinary(), nullable=False),
        sa.Column('compteur_signature', sa.Integer(), server_default='0', nullable=False),
        sa.Column('nom_appareil', sa.String(length=150), nullable=True),
        sa.Column('date_creation', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['id_agent'], ['agent.id_agent'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id_identifiant'),
        sa.UniqueConstraint('id_agent'),
        sa.UniqueConstraint('credential_id'),
    )
    op.create_index(
        op.f('ix_identifiant_webauthn_credential_id'), 'identifiant_webauthn', ['credential_id'], unique=True
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_identifiant_webauthn_credential_id'), table_name='identifiant_webauthn')
    op.drop_table('identifiant_webauthn')
    # NB : PostgreSQL ne permet pas de retirer une valeur d'un type ENUM
    # existant ; le downgrade laisse volontairement 'webauthn' dans
    # mode_pointage_enum (opération non réversible sans recréer le type).
