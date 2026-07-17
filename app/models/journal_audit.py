from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.utilisateur import Utilisateur


class JournalAudit(Base):
    """Journal d'audit détaillé : traçabilité de toutes les actions effectuées sur le système."""
    __tablename__ = "journal_audit"

    id_journal: Mapped[int] = mapped_column(primary_key=True)
    id_utilisateur: Mapped[Optional[int]] = mapped_column(
        ForeignKey("utilisateur.id_utilisateur", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(150), nullable=False)
    date_heure: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False, index=True)
    details: Mapped[Optional[str]] = mapped_column(Text)

    # Relations
    utilisateur: Mapped[Optional["Utilisateur"]] = relationship(back_populates="journal_audit")

    def __repr__(self) -> str:
        return f"<JournalAudit id={self.id_journal} action={self.action!r}>"
