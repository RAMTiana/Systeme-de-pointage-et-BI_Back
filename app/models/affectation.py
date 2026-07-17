from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, Date, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.service import Service


class Affectation(Base):
    __tablename__ = "affectation"
    __table_args__ = (
        UniqueConstraint("id_agent", "id_service", "date_debut", name="uq_affectation_agent_service_debut"),
    )

    id_affectation: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, index=True
    )
    id_service: Mapped[int] = mapped_column(
        ForeignKey("service.id_service", ondelete="CASCADE"), nullable=False, index=True
    )
    date_debut: Mapped[date] = mapped_column(Date, server_default=func.current_date(), nullable=False)
    date_fin: Mapped[Optional[date]] = mapped_column(Date)
    service_principal: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="affectations")
    service: Mapped["Service"] = relationship(back_populates="affectations")

    def __repr__(self) -> str:
        return f"<Affectation agent={self.id_agent} service={self.id_service}>"
