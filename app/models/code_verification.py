from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import TypeCode, pg_enum

if TYPE_CHECKING:
    from app.models.utilisateur import Utilisateur


class CodeVerification(Base):
    """
    Codes à usage unique (réinitialisation de mot de passe, vérification
    d'email). Le code envoyé par email n'est JAMAIS stocké en clair :
    seul son hash (code_hash) est conservé, comparable au hash du mot de passe.
    """
    __tablename__ = "code_verification"

    id_code: Mapped[int] = mapped_column(primary_key=True)
    id_utilisateur: Mapped[int] = mapped_column(
        ForeignKey("utilisateur.id_utilisateur", ondelete="CASCADE"), nullable=False, index=True
    )
    type_code: Mapped[TypeCode] = mapped_column(pg_enum(TypeCode, "type_code_enum"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    date_expiration: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    utilise: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    tentatives: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0", nullable=False)

    # Relations
    utilisateur: Mapped["Utilisateur"] = relationship(back_populates="codes_verification")

    def __repr__(self) -> str:
        return f"<CodeVerification id={self.id_code} type={self.type_code}>"
