"""
Moteur SQLAlchemy et gestion des sessions.

`get_db` est une dépendance FastAPI : une session est ouverte au début
de la requête et fermée automatiquement à la fin (succès ou erreur),
via `try/finally`.
"""
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.SQLALCHEMY_DATABASE_URI,
    pool_pre_ping=True,   # évite les erreurs sur connexions PG fermées côté serveur (idle timeout)
    echo=settings.DEBUG,  # log SQL en mode debug uniquement
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """Dépendance FastAPI : `db: Session = Depends(get_db)`."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
