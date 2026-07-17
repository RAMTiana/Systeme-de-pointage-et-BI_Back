from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.agent import Agent


class IdentifiantWebAuthn(Base):
    """
    Identifiant d'authentificateur WebAuthn/FIDO2 (Touch ID, Windows Hello,
    empreinte digitale du téléphone...) enregistré pour un agent, utilisé pour
    vérifier cryptographiquement les pointages en mode biométrique
    (cf. app/services/pointage_service.py et app/services/webauthn_service.py).

    Conformément à la norme WebAuthn, on ne stocke jamais de donnée biométrique
    brute (empreinte, visage...) : uniquement l'identifiant opaque du
    "credential" et sa clé publique, fournis par l'authentificateur et son
    navigateur lors de l'inscription. La biométrie elle-même reste toujours
    sur l'appareil de l'agent.
    """
    __tablename__ = "identifiant_webauthn"

    id_identifiant: Mapped[int] = mapped_column(primary_key=True)
    id_agent: Mapped[int] = mapped_column(
        ForeignKey("agent.id_agent", ondelete="CASCADE"), nullable=False, unique=True
    )
    credential_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    cle_publique: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    compteur_signature: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    nom_appareil: Mapped[Optional[str]] = mapped_column(String(150))
    date_creation: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    # Relations
    agent: Mapped["Agent"] = relationship(back_populates="identifiant_webauthn")

    def __repr__(self) -> str:
        return f"<IdentifiantWebAuthn agent={self.id_agent}>"
