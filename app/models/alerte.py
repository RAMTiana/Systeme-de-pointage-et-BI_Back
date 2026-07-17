from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import CanalAlerte, StatutAlerte, pg_enum

if TYPE_CHECKING:
    from app.models.anomalie import Anomalie


class Alerte(Base):
    __tablename__ = "alerte"

    id_alerte: Mapped[int] = mapped_column(primary_key=True)
    id_anomalie: Mapped[int] = mapped_column(
        ForeignKey("anomalie.id_anomalie", ondelete="CASCADE"), nullable=False, index=True
    )
    canal: Mapped[CanalAlerte] = mapped_column(pg_enum(CanalAlerte, "canal_alerte_enum"), nullable=False)
    date_envoi: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    statut: Mapped[StatutAlerte] = mapped_column(
        pg_enum(StatutAlerte, "statut_alerte_enum"),
        default=StatutAlerte.ENVOYEE, server_default=StatutAlerte.ENVOYEE.value, nullable=False
    )
    destinataire: Mapped[str] = mapped_column(String(150), nullable=False)

    # Relations
    anomalie: Mapped["Anomalie"] = relationship(back_populates="alertes")

    def __repr__(self) -> str:
        return f"<Alerte id={self.id_alerte} canal={self.canal} statut={self.statut}>"
