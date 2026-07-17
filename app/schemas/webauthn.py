"""
Schémas Pydantic — enrôlement et vérification WebAuthn (biométrie d'appareil :
Touch ID / Windows Hello / empreinte digitale du téléphone).

Les objets `credential` transmis sont les réponses JSON brutes de
`navigator.credentials.create()` / `.get()` (sérialisées côté client par
`@simplewebauthn/browser`), transmises telles quelles à la bibliothèque
`webauthn` (py_webauthn) pour vérification cryptographique — cf.
app/services/webauthn_service.py.
"""
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class WebAuthnRegistrationCreate(BaseModel):
    """Réponse d'inscription (`navigator.credentials.create()`), transmise telle quelle."""
    credential: Dict[str, Any]
    nom_appareil: Optional[str] = Field(default=None, max_length=150, description="Ex. 'Téléphone du poste 1'")


class IdentifiantWebAuthnOut(BaseModel):
    id_identifiant: int
    id_agent: int
    nom_appareil: Optional[str] = None
    date_creation: datetime

    model_config = ConfigDict(from_attributes=True)
