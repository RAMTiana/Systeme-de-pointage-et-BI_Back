from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ParametreSysteme(Base):
    """
    Paramètres configurables par l'administrateur : seuils de retard,
    règles de récidive, expiration des codes de vérification, etc.
    (cf. cahier des charges 3.1 — Personnalisation des seuils et règles métier).
    """
    __tablename__ = "parametre_systeme"

    id_parametre: Mapped[int] = mapped_column(primary_key=True)
    nom_parametre: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    valeur: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    def __repr__(self) -> str:
        return f"<ParametreSysteme {self.nom_parametre}={self.valeur!r}>"
