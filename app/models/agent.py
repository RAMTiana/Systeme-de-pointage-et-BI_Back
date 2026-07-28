from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import StatutAgent, pg_enum

if TYPE_CHECKING:
    from app.models.service import Service
    from app.models.affectation import Affectation
    from app.models.biometrie import EmpreinteBiometrique
    from app.models.identifiant_webauthn import IdentifiantWebAuthn
    from app.models.pointage import Pointage
    from app.models.anomalie import Anomalie
    from app.models.conge import Conge


class Agent(Base):
    __tablename__ = "agent"

    id_agent: Mapped[int] = mapped_column(primary_key=True)
    matricule: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    nom: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prenom: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    fonction: Mapped[Optional[str]] = mapped_column(String(150))
    statut: Mapped[StatutAgent] = mapped_column(
        pg_enum(StatutAgent, "statut_agent"),
        default=StatutAgent.ACTIF, server_default=StatutAgent.ACTIF.value, nullable=False
    )
    consentement_facial: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    id_service: Mapped[Optional[int]] = mapped_column(
        ForeignKey("service.id_service", ondelete="SET NULL"), index=True
    )
    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    service: Mapped[Optional["Service"]] = relationship(back_populates="agents")
    affectations: Mapped[List["Affectation"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    empreinte_biometrique: Mapped[Optional["EmpreinteBiometrique"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", uselist=False
    )
    identifiant_webauthn: Mapped[Optional["IdentifiantWebAuthn"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan", uselist=False
    )
    pointages: Mapped[List["Pointage"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    anomalies: Mapped[List["Anomalie"]] = relationship(back_populates="agent", cascade="all, delete-orphan")
    conges: Mapped[List["Conge"]] = relationship(back_populates="agent", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Agent id={self.id_agent} matricule={self.matricule!r}>"

    # ------------------------------------------------------------------
    # État des moyens d'enrôlement biométrique (exposé en lecture via
    # AgentOut pour piloter l'écran "Biométrie" du module Agents).
    # ------------------------------------------------------------------
    @property
    def empreinte_faciale_enregistree(self) -> bool:
        return self.empreinte_biometrique is not None

    @property
    def webauthn_enregistre(self) -> bool:
        return self.identifiant_webauthn is not None
