"""Schémas Pydantic — Module BI (Processus 5 du BPMN "Consultation du tableau de bord décisionnel")."""
from datetime import date as date_
from typing import List, Literal, Optional

from pydantic import BaseModel

from app.models.enums import TypePeriode


class ServiceTempsReel(BaseModel):
    id_service: Optional[int]
    nom_service: str
    nombre_agents_attendus: int
    nombre_presents: int
    nombre_sortis: int
    nombre_absents: int
    nombre_retardataires: int
    taux_presence: Optional[float] = None


class TableauBordTempsReel(BaseModel):
    """
    Statut du jour par agent (encore présent / déjà sorti / absent si son
    service travaille aujourd'hui sans qu'aucun pointage ne soit encore
    enregistré). `detail_services` n'est renseigné que pour la vue
    consolidée (`id_service` non précisé en requête).
    """
    jour: date_
    id_service: Optional[int]
    nom_service: str
    nombre_agents_attendus: int
    nombre_presents: int
    nombre_sortis: int
    nombre_absents: int
    nombre_retardataires: int
    taux_presence: Optional[float] = None
    detail_services: List[ServiceTempsReel] = []


class IndicateursGlobaux(BaseModel):
    nombre_agents: int
    jours_ouvres: int
    jours_presents: int
    nombre_retards: int
    nombre_absences: int
    nombre_departs_anticipes: int
    heures_travaillees: float
    taux_presence: Optional[float] = None


class PointTendance(BaseModel):
    periode_debut: date_
    periode_fin: date_
    globaux: IndicateursGlobaux


class ClassementAgentOut(BaseModel):
    id_agent: int
    matricule: str
    nom: str
    prenom: str
    id_service: Optional[int]
    nom_service: str
    jours_ouvres: int
    jours_presents: int
    nombre_retards: int
    nombre_absences: int
    nombre_departs_anticipes: int
    heures_travaillees: float
    taux_presence: Optional[float] = None


class ServiceCompareOut(BaseModel):
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
    rang: int


class ComparaisonServicesOut(BaseModel):
    type_periode: TypePeriode
    periode_debut: date_
    periode_fin: date_
    globaux: IndicateursGlobaux
    services: List[ServiceCompareOut]


class PointPrevisionOut(BaseModel):
    periode_debut: date_
    periode_fin: date_
    taux_presence_estime: Optional[float] = None


class PrevisionOut(BaseModel):
    """
    Régression linéaire simple ("méthode statistique simple" au sens du
    cahier des charges) — estimation indicative, jamais un engagement.
    """
    granularite: TypePeriode
    id_service: Optional[int]
    methode: str
    historique: List[PointTendance]
    prevision: List[PointPrevisionOut]
    avertissement: str


CritereClassement = Literal["ponctualite", "retards"]


class AnomalieAgentScoreOut(BaseModel):
    """Profil d'agent enrichi d'un score d'anomalie (Isolation Forest)."""
    id_agent: int
    matricule: str
    nom: str
    prenom: str
    id_service: Optional[int] = None
    nom_service: Optional[str] = None
    jours_ouvres: int
    jours_presents: int
    nombre_retards: int
    nombre_absences: int
    nombre_departs_anticipes: int
    heures_travaillees: float
    taux_presence: Optional[float] = None
    score_anomalie: float
    est_atypique: bool


class ScoreRisqueAgentOut(BaseModel):
    """Probabilité qu'un agent connaisse une anomalie (retard/absence) sur la période à venir."""
    id_agent: int
    matricule: str
    nom: str
    prenom: str
    id_service: Optional[int]
    nom_service: str
    score_risque: float
    methode: str
