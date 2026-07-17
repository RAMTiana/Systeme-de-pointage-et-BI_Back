"""
Classe de base déclarative SQLAlchemy 2.0.

Tous les modèles (app/models/*.py) héritent de `Base`. C'est cette classe
qui porte les métadonnées utilisées par Alembic pour l'autogénération
des migrations.
"""
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Classe de base pour tous les modèles ORM du projet."""
    pass
