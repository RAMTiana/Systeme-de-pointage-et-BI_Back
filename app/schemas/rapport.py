"""Schémas Pydantic — Module Rapports (Processus 4 du BPMN "Génération de rapports")."""
from datetime import date as date_
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import FormatRapport, TypePeriode
from app.schemas.service import ServiceLight


class RapportGenerateRequest(BaseModel):
    """
    Génération à la demande (étapes 1-6 du Processus 4, déclenchées par le
    None Start Event de la lane Secrétaire, plutôt que par le Timer).
    """
    type_periode: TypePeriode
    format: FormatRapport
    id_service: Optional[int] = Field(
        default=None,
        description="Restreint le rapport à un service. Omis = rapport consolidé (tous services).",
    )
    date_reference: Optional[date_] = Field(
        default=None,
        description=(
            "Toute date incluse dans la période à couvrir (ex. un jour du mois pour un rapport "
            "mensuel). Par défaut : hier — la dernière journée complète disponible."
        ),
    )


class RapportPlanifieRequest(BaseModel):
    """
    Génération planifiée (Timer Start Event du Processus 4) : un rapport par
    service actif ayant des agents, plus un rapport consolidé (tous services),
    pour chaque format demandé.
    """
    type_periode: TypePeriode
    formats: List[FormatRapport] = Field(default_factory=lambda: [FormatRapport.PDF, FormatRapport.EXCEL])
    date_reference: Optional[date_] = Field(default=None, description="Par défaut : hier.")


class IndicateurAgentOut(BaseModel):
    """Détail par agent — présent uniquement quand le rapport porte sur un service précis."""
    id_agent: int
    matricule: str
    nom: str
    prenom: str
    jours_ouvres: int
    jours_presents: int
    nombre_retards: int
    nombre_absences: int
    nombre_departs_anticipes: int
    heures_travaillees: float
    taux_presence: Optional[float] = None


class IndicateurServiceOut(BaseModel):
    """Détail par service — présent uniquement quand le rapport est consolidé (tous services)."""
    id_service: Optional[int]
    nom_service: str
    nombre_agents: int
    jours_ouvres: int
    jours_presents: int
    nombre_retards: int
    nombre_absences: int
    nombre_departs_anticipes: int
    heures_travaillees: float
    taux_presence: Optional[float] = None


class IndicateursGlobaux(BaseModel):
    nombre_agents: int
    nombre_retards: int
    nombre_absences: int
    nombre_departs_anticipes: int
    heures_travaillees: float
    taux_presence: Optional[float] = None


class RapportOut(BaseModel):
    id_rapport: int
    id_utilisateur: Optional[int] = None
    id_service: Optional[int] = None
    service: Optional[ServiceLight] = None
    type_periode: TypePeriode
    date_generation: datetime
    format: FormatRapport
    chemin_fichier: str
    periode_debut: Optional[date_] = None
    periode_fin: Optional[date_] = None

    model_config = ConfigDict(from_attributes=True)


class RapportContenu(BaseModel):
    """
    Indicateurs calculés pour une période/un périmètre donné — utilisé à la
    fois pour rendre le PDF/Excel et pour un aperçu direct côté API
    (`GET /rapports/{id}/indicateurs`) sans devoir télécharger le fichier.
    """
    type_periode: TypePeriode
    periode_debut: date_
    periode_fin: date_
    id_service: Optional[int]
    nom_service: str
    globaux: IndicateursGlobaux
    detail_agents: List[IndicateurAgentOut] = []
    detail_services: List[IndicateurServiceOut] = []
