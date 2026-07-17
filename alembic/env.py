"""
Configuration de l'environnement Alembic.

Ce fichier relie Alembic à :
  1. l'URL de connexion réelle (via app.core.config.settings, donc .env)
  2. les métadonnées de tous les modèles ORM (via app.models),
     ce qui permet l'autogénération des migrations (`alembic revision --autogenerate`).
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# --- Import de l'application ---
from app.core.config import settings
from app.models import Base  # importe aussi tous les modèles (voir app/models/__init__.py)

# Objet de configuration Alembic, qui donne accès aux valeurs de alembic.ini
config = context.config

# Injecte l'URL réelle (issue de .env) à la place du placeholder d'alembic.ini
config.set_main_option("sqlalchemy.url", settings.SQLALCHEMY_DATABASE_URI)

# Configuration du logging Python à partir du fichier .ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata cible utilisée pour l'autogénération des migrations
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Génère le SQL des migrations sans se connecter à la base (mode 'offline')."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Applique les migrations en se connectant réellement à la base (mode 'online')."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,        # détecte les changements de type de colonne
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()