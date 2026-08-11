from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import AuthProvider, pg_enum

if TYPE_CHECKING:
    from app.models.rbac import Role
    from app.models.code_verification import CodeVerification
    from app.models.anomalie import Anomalie
    from app.models.rapport import Rapport
    from app.models.journal_audit import JournalAudit


class Utilisateur(Base):
    __tablename__ = "utilisateur"
    __table_args__ = (
        CheckConstraint(
            "(auth_provider = 'local' AND mot_de_passe_hash IS NOT NULL) "
            "OR (auth_provider = 'google' AND google_id IS NOT NULL)",
            name="chk_auth_provider",
        ),
    )

    id_utilisateur: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(150), nullable=False, unique=True, index=True)
    mot_de_passe_hash: Mapped[Optional[str]] = mapped_column(String(255))
    auth_provider: Mapped[AuthProvider] = mapped_column(
        pg_enum(AuthProvider, "auth_provider_enum"),
        default=AuthProvider.LOCAL, server_default=AuthProvider.LOCAL.value, nullable=False
    )
    google_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, index=True)
    email_verifie: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    nom_complet: Mapped[str] = mapped_column(String(150), nullable=False)
    actif: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", nullable=False)
    photo_profil: Mapped[Optional[str]] = mapped_column(
        Text,
        comment="Photo de profil en data URL base64 (JPEG/PNG/WebP, 2 Mo max décodé) — "
        "modifiable par l'utilisateur lui-même (PATCH /auth/me) ou par un administrateur.",
    )
    id_role: Mapped[int] = mapped_column(
        ForeignKey("role.id_role", ondelete="RESTRICT"), nullable=False, index=True
    )
    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    role: Mapped["Role"] = relationship(back_populates="utilisateurs")
    codes_verification: Mapped[List["CodeVerification"]] = relationship(
        back_populates="utilisateur", cascade="all, delete-orphan"
    )
    anomalies_traitees: Mapped[List["Anomalie"]] = relationship(back_populates="utilisateur_traitant")
    rapports: Mapped[List["Rapport"]] = relationship(back_populates="utilisateur")
    journal_audit: Mapped[List["JournalAudit"]] = relationship(back_populates="utilisateur")

    def __repr__(self) -> str:
        return f"<Utilisateur id={self.id_utilisateur} login={self.login!r}>"
