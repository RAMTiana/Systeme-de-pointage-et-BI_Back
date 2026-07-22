"""Schémas Pydantic — Module Pointage (Processus 1 du BPMN)."""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ModePointage, MotifSortie, StatutPointage, TypePointage
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


class _IdentifiantAgentOptionnel(BaseModel):
    """
    Variante sans contrainte : utilisée par le pointage facial, où
    l'identité de l'agent peut soit être présumée (matricule/id_agent
    fourni — vérification 1:1, comportement historique), soit être
    entièrement déterminée par la reconnaissance faciale elle-même
    (aucun identifiant fourni — identification 1:N sur l'ensemble des
    empreintes enregistrées, cf. `pointage_service.identifier_par_visage`).
    """
    matricule: Optional[str] = Field(
        default=None,
        description="Optionnel. Si fourni, la comparaison faciale se limite à cet agent "
        "(vérification 1:1). Si omis, le visage capté est comparé à tous les agents "
        "ayant une empreinte enregistrée pour déterminer l'identité (identification 1:N).",
    )
    id_agent: Optional[int] = None


class _SortieDeclaree(BaseModel):
    """
    Ajoute au pointage la déclaration facultative du motif de sortie : le
    poste de scan ne propose ce champ que lorsque `type_pointage = sortie`,
    pour distinguer une sortie normale (fin de service) d'une sortie
    exceptionnelle en cours de journée (urgence, cas familial, raison
    médicale, autorisation de la hiérarchie...).

    Mixin volontairement indépendant de l'identification de l'agent (voir
    `_IdentifiantAgent` / `_IdentifiantAgentOptionnel`), pour pouvoir être
    combiné avec l'une ou l'autre selon le mode de pointage.
    """
    type_pointage: TypePointage
    motif_sortie: Optional[MotifSortie] = Field(
        default=None,
        description="Motif de la sortie (uniquement pertinent si type_pointage = 'sortie'). "
        "Absent ou 'normale' pour une sortie de fin de service classique.",
    )
    commentaire: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Précision libre sur le motif de sortie (obligatoire si motif_sortie = 'autre').",
    )

    @model_validator(mode="after")
    def _coherence_motif_sortie(self) -> "_SortieDeclaree":
        if self.type_pointage == TypePointage.ENTREE:
            if self.motif_sortie is not None:
                raise ValueError("motif_sortie n'est applicable qu'à une sortie, pas à une entrée.")
            return self
        # type_pointage == SORTIE
        if self.motif_sortie == MotifSortie.AUTRE and not (self.commentaire and self.commentaire.strip()):
            raise ValueError("Un commentaire est requis lorsque le motif de sortie est 'autre'.")
        return self


class PointageQrBadgeCreate(_IdentifiantAgent, _SortieDeclaree):
    pass


class PointageFacialCreate(_IdentifiantAgentOptionnel, _SortieDeclaree):
    encodage_facial: List[float] = Field(
        description="Vecteur de caractéristiques faciales (128-D, face-api.js) calculé côté client "
        "à l'instant du pointage. Obligatoire dans tous les cas. Si matricule/id_agent est fourni, "
        "utilisé pour une vérification 1:1 contre l'empreinte de cet agent (cf. "
        "pointage_service._identite_verifiee). Si aucun identifiant n'est fourni, utilisé pour une "
        "identification 1:N contre l'ensemble des empreintes enregistrées (cf. "
        "pointage_service.identifier_par_visage) — aucune identité n'est présumée sans ce vecteur.",
    )
    image_base64: Optional[str] = Field(
        default=None,
        description="Photo JPEG/PNG capturée côté client, encodée en base64 — conservée uniquement à "
        "des fins de traçabilité/preuve, jamais utilisée comme substitut à la comparaison biométrique.",
    )


class PointageWebAuthnCreate(_IdentifiantAgent, _SortieDeclaree):
    """Pointage authentifié via la biométrie de l'appareil (Touch ID / Windows Hello / empreinte téléphone)."""
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
    commentaire: Optional[str] = None
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
