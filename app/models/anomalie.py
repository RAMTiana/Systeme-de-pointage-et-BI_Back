from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import StatutJustification, TypeAnomalie, pg_enum

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.pointage import Pointage
    from app.models.utilisateur import Utilisateur
    from app.models.justificatif import Justificatif
    from app.models.alerte import Alerte


class Anomalie(Base):
    __tablename__ = "anomalie"

    id_anomalie: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, index=True
    )
    id_pointage: Mapped[Optional[int]] = mapped_column(
        ForeignKey("pointage.id_pointage", ondelete="SET NULL")
    )
    type_anomalie: Mapped[TypeAnomalie] = mapped_column(
        pg_enum(TypeAnomalie, "type_anomalie_enum"), nullable=False
    )
    statut_justification: Mapped[StatutJustification] = mapped_column(
        pg_enum(StatutJustification, "statut_justification_enum"),
        default=StatutJustification.EN_ATTENTE,
        server_default=StatutJustification.EN_ATTENTE.value,
        nullable=False,
        index=True,
    )
    date_detection: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    id_utilisateur_traitant: Mapped[Optional[int]] = mapped_column(
        ForeignKey("utilisateur.id_utilisateur", ondelete="SET NULL")
    )

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="anomalies")
    pointage: Mapped[Optional["Pointage"]] = relationship(back_populates="anomalies")
    utilisateur_traitant: Mapped[Optional["Utilisateur"]] = relationship(back_populates="anomalies_traitees")
    justificatif: Mapped[Optional["Justificatif"]] = relationship(
        back_populates="anomalie", cascade="all, delete-orphan", uselist=False
    )
    alertes: Mapped[list["Alerte"]] = relationship(back_populates="anomalie", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Anomalie id={self.id_anomalie} agent={self.id_agent} type={self.type_anomalie}>"
