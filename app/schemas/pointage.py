"""Schémas Pydantic — Module Pointage (Processus 1 du BPMN)."""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ModePointage, StatutPointage, TypePointage
from app.schemas.agent import AgentOut


class _IdentifiantAgent(BaseModel):
    """
    Un pointage identifie l'agent soit par matricule (cas réel du poste de
    pointage : le QR code ou le badge encode le matricule), soit par
    id_agent (utile pour des tests ou une intégration interne).
    """
    matricule: Optional[str] = Field(default=None, description="Matricule lu sur le QR code ou le badge")
    id_agent: Optional[int] = None

    @model_validator(mode="after")
    def _un_identifiant_requis(self) -> "_IdentifiantAgent":
        if not self.matricule and self.id_agent is None:
            raise ValueError("matricule ou id_agent doit être fourni.")
        return self


class PointageQrBadgeCreate(_IdentifiantAgent):
    type_pointage: TypePointage


class PointageFacialCreate(_IdentifiantAgent):
    type_pointage: TypePointage
    encodage_facial: Optional[List[float]] = Field(
        default=None,
        description="Vecteur de caractéristiques faciales déjà encodé côté client "
        "(mode nominal, comparé à l'empreinte de référence de l'agent).",
    )
    image_base64: Optional[str] = Field(
        default=None,
        description="Image JPEG/PNG capturée côté client, encodée en base64. Utilisée quand "
        "le client ne calcule pas d'embedding (WebRTC + navigateur). L'identité est alors "
        "vérifiée uniquement via le matricule + consentement — TODO : intégrer une lib de "
        "reconnaissance (face_recognition/InsightFace) pour un vrai match serveur.",
    )

    @model_validator(mode="after")
    def _preuve_faciale_requise(self) -> "PointageFacialCreate":
        if not self.encodage_facial and not self.image_base64:
            raise ValueError("encodage_facial ou image_base64 doit être fourni.")
        return self


class PointageWebAuthnCreate(_IdentifiantAgent):
    """Pointage authentifié via la biométrie de l'appareil (Touch ID / Windows Hello / empreinte téléphone)."""
    type_pointage: TypePointage
    webauthn: dict = Field(
        description="Assertion WebAuthn renvoyée par navigator.credentials.get() côté client "
        "(id, rawId, clientDataJSON, authenticatorData, signature, userHandle). "
        "TODO : vérifier cryptographiquement l'assertion contre une clé publique enregistrée."
    )


class PointageOut(BaseModel):
    id_pointage: int
    id_agent: int
    date_heure: datetime
    type_pointage: TypePointage
    mode_pointage: ModePointage
    statut: StatutPointage
    agent: Optional[AgentOut] = None

    model_config = ConfigDict(from_attributes=True)


class PointageResultat(BaseModel):
    """Réponse renvoyée au poste de pointage : le pointage enregistré + l'anomalie éventuelle."""
    pointage: PointageOut
    anomalie_detectee: Optional[str] = Field(
        default=None,
        description="Type d'anomalie détectée immédiatement au pointage (retard / depart_anticipe), le cas échéant.",
    )


class PointageFiltre(BaseModel):
    """Regroupe les filtres de consultation (usage interne, non exposé tel quel en query params)."""
    id_agent: Optional[int] = None
    id_service: Optional[int] = None
    type_pointage: Optional[TypePointage] = None
    statut: Optional[StatutPointage] = None
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
