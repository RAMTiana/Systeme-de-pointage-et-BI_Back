from datetime import time
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import JourSemaine, pg_enum

if TYPE_CHECKING:
    from app.models.service import Service


class HoraireReference(Base):
    """Horaires de référence par service et par jour, utilisés pour le calcul des retards/absences."""
    __tablename__ = "horaire_reference"
    __table_args__ = (
        UniqueConstraint("id_service", "jour_semaine", name="uq_horaire_service_jour"),
    )

    id_horaire: Mapped[int] = mapped_column(primary_key=True)
    id_service: Mapped[Optional[int]] = mapped_column(
        ForeignKey("service.id_service", ondelete="CASCADE")
    )
    heure_debut: Mapped[time] = mapped_column(Time, nullable=False)
    heure_fin: Mapped[time] = mapped_column(Time, nullable=False)
    jour_semaine: Mapped[JourSemaine] = mapped_column(
        pg_enum(JourSemaine, "jour_semaine_enum"), nullable=False
    )

    # Relations
    service: Mapped[Optional["Service"]] = relationship(back_populates="horaires_reference")

    def __repr__(self) -> str:
        return f"<HoraireReference service={self.id_service} jour={self.jour_semaine}>"
