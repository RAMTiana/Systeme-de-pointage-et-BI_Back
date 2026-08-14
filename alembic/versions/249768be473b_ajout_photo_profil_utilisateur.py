"""ajout photo_profil utilisateur

Revision ID: 249768be473b
Revises: 3a7c1f9e5d21
Create Date: 2026-08-10 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '249768be473b'
down_revision: Union[str, None] = '3a7c1f9e5d21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_cols = [c["name"] for c in inspector.get_columns("utilisateur")] if "utilisateur" in inspector.get_table_names() else []
    if "photo_profil" not in existing_cols:
        try:
            op.add_column(
                'utilisateur',
                sa.Column(
                    'photo_profil',
                    sa.Text(),
                    nullable=True,
                    comment="Photo de profil en data URL base64 (JPEG/PNG/WebP, 2 Mo max décodé).",
                ),
            )
        except sa.exc.ProgrammingError:
            pass


def downgrade() -> None:
    op.drop_column('utilisateur', 'photo_profil')
