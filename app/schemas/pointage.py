"""Schémas Pydantic — Module Pointage (Processus 1 du BPMN)."""
from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ModePointage, StatutPointage, TypePointage
from app.schemas.agent import AgentOut


class MotifSortie(str, Enum):
    """
    Motif associé à une sortie. Ne s'applique qu'aux pointages de type
    TypePointage.SORTIE — laissé à NULL pour toutes les entrées.

    `FIN_SERVICE` = sortie normale de fin de journée / fin de poste. Les autres
    valeurs tracent une sortie exceptionnelle demandée par l'agent au moment
    du pointage (visible ensuite dans les rapports RH).
    """
    FIN_SERVICE = "fin_service"
    URGENCE = "urgence"
    CAS_FAMILIAL = "cas_familial"
    MEDICAL = "medical"
    MISSION = "mission"
    PAUSE = "pause"
    AUTRE = "autre"


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


class _MotifSortieMixin(BaseModel):
    """
    Champs de motif partagés par tous les modes de pointage. Ils ne sont
    autorisés que pour une sortie ; pour une entrée, ils DOIVENT rester nuls
    (une entrée n'a pas de motif) — c'est ce que garantit `_verifier_motif`.
    """
    motif_sortie: Optional[MotifSortie] = Field(
        default=None,
        description="Motif de la sortie (fin_service par défaut, ou sortie exceptionnelle : "
        "urgence, cas_familial, medical, mission, pause, autre). Ignoré pour une entrée.",
    )
    commentaire_motif: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Précision libre saisie par l'agent de pointage (200 caractères max).",
    )

    @model_validator(mode="after")
    def _verifier_motif(self) -> "_MotifSortieMixin":
        type_pointage = getattr(self, "type_pointage", None)
        if type_pointage == TypePointage.ENTREE:
            if self.motif_sortie is not None or self.commentaire_motif:
                raise ValueError("motif_sortie et commentaire_motif ne s'appliquent qu'à une sortie.")
        elif type_pointage == TypePointage.SORTIE and self.motif_sortie is None:
            # Valeur par défaut explicite côté back : une sortie sans motif = fin de service.
            self.motif_sortie = MotifSortie.FIN_SERVICE
        return self


class PointageQrBadgeCreate(_MotifSortieMixin, _IdentifiantAgent):
    type_pointage: TypePointage


class PointageFacialCreate(_MotifSortieMixin, _IdentifiantAgent):
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


class PointageWebAuthnCreate(_MotifSortieMixin, _IdentifiantAgent):
    """Pointage authentifié via la biométrie de l'appareil (Touch ID / Windows Hello / empreinte téléphone)."""
    type_pointage: TypePointage
    webauthn: dict = Field(
        description="Réponse JSON brute de navigator.credentials.get() (via @simplewebauthn/browser), "
        "vérifiée cryptographiquement contre la clé publique WebAuthn enregistrée pour l'agent "
        "(cf. PUT /agents/{id}/webauthn) — voir app/services/webauthn_service.py."
    )


class PointageOut(BaseModel):
    id_pointage: int
    id_agent: int
    date_heure: datetime
    type_pointage: TypePointage
    mode_pointage: ModePointage
    statut: StatutPointage
    motif_sortie: Optional[MotifSortie] = None
    commentaire_motif: Optional[str] = None
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
    motif_sortie: Optional[MotifSortie] = None
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None