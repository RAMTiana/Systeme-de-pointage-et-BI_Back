from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.anomalie import Anomalie


class Justificatif(Base):
    __tablename__ = "justificatif"

    id_justificatif: Mapped[int] = mapped_column(primary_key=True)
    id_anomalie: Mapped[int] = mapped_column(
        ForeignKey("anomalie.id_anomalie", ondelete="CASCADE"), nullable=False, unique=True
    )
    motif: Mapped[str] = mapped_column(Text, nullable=False)
    piece_jointe_chemin: Mapped[Optional[str]] = mapped_column(Text)
    date_depot: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    anomalie: Mapped["Anomalie"] = relationship(back_populates="justificatif")

    def __repr__(self) -> str:
        return f"<Justificatif id={self.id_justificatif} anomalie={self.id_anomalie}>"
