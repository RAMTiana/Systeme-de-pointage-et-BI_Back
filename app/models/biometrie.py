from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, LargeBinary, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.agent import Agent


class EmpreinteBiometrique(Base):
    """
    Empreinte faciale encodée de l'agent (vecteur de caractéristiques,
    PAS l'image brute) — utilisée pour la comparaison lors du pointage
    par reconnaissance faciale. Stockage chiffré au niveau applicatif
    recommandé (cf. cahier des charges, chapitre VII — Sécurité).
    """
    __tablename__ = "empreinte_biometrique"

    id_empreinte: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, unique=True
    )
    encodage_facial: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="empreinte_biometrique")

    def __repr__(self) -> str:
        return f"<EmpreinteBiometrique agent={self.id_agent}>"
