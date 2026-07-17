from datetime import datetime
from typing import TYPE_CHECKING, List

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import ModePointage, StatutPointage, TypePointage, pg_enum

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.anomalie import Anomalie


class Pointage(Base):
    __tablename__ = "pointage"

    id_pointage: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, index=True
    )
    date_heure: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
    type_pointage: Mapped[TypePointage] = mapped_column(
        pg_enum(TypePointage, "type_pointage_enum"), nullable=False
    )
    mode_pointage: Mapped[ModePointage] = mapped_column(
        pg_enum(ModePointage, "mode_pointage_enum"), nullable=False
    )
    statut: Mapped[StatutPointage] = mapped_column(
        pg_enum(StatutPointage, "statut_pointage_enum"),
        default=StatutPointage.VALIDE, server_default=StatutPointage.VALIDE.value, nullable=False
    )

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="pointages")
    anomalies: Mapped[List["Anomalie"]] = relationship(back_populates="pointage")

    def __repr__(self) -> str:
        return f"<Pointage id={self.id_pointage} agent={self.id_agent} type={self.type_pointage}>"
