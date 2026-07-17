from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import FormatRapport, TypePeriode, pg_enum

if TYPE_CHECKING:
    from app.models.utilisateur import Utilisateur
    from app.models.service import Service


class Rapport(Base):
    __tablename__ = "rapport"

    id_rapport: Mapped[int] = mapped_column(primary_key=True)
    id_utilisateur: Mapped[Optional[int]] = mapped_column(
        ForeignKey("utilisateur.id_utilisateur", ondelete="SET NULL"), index=True
    )
    id_service: Mapped[Optional[int]] = mapped_column(
        ForeignKey("service.id_service", ondelete="SET NULL"), index=True
    )
    type_periode: Mapped[TypePeriode] = mapped_column(
        pg_enum(TypePeriode, "type_periode_enum"), nullable=False
    )
    date_generation: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False, index=True
    )
    format: Mapped[FormatRapport] = mapped_column(
        pg_enum(FormatRapport, "format_rapport_enum"), nullable=False
    )
    chemin_fichier: Mapped[str] = mapped_column(Text, nullable=False)

    # Relations
    utilisateur: Mapped[Optional["Utilisateur"]] = relationship(back_populates="rapports")
    service: Mapped[Optional["Service"]] = relationship(back_populates="rapports")

    def __repr__(self) -> str:
        return f"<Rapport id={self.id_rapport} type_periode={self.type_periode} format={self.format}>"
