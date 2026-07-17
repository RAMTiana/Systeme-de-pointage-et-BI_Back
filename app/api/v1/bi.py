"""Routeur — Module BI (Processus 5 du BPMN "Consultation du tableau de bord décisionnel")."""
from datetime import date as date_
from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from sqlalchemy.orm import Session

from app.api.deps import get_db, require_permission
from app.models.enums import TypePeriode
from app.models.utilisateur import Utilisateur
from app.schemas.bi import (
    ClassementAgentOut,
    ComparaisonServicesOut,
    CritereClassement,
    PointTendance,
    PrevisionOut,
    TableauBordTempsReel,
)
from app.services import bi_service, journal_audit_service

router = APIRouter(prefix="/bi", tags=["Tableau de bord décisionnel (BI)"])

_PROTECTION = Depends(require_permission("consulter_bi"))


@router.get("/temps-reel", response_model=TableauBordTempsReel)
def temps_reel(
    id_service: Optional[int] = None,
    jour: Optional[date_] = None,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = _PROTECTION,
) -> TableauBordTempsReel:
    """
    Étapes 1-7 du Processus 5 : point d'entrée du tableau de bord (RBAC déjà
    vérifié par `require_permission`). Consigne la consultation dans le
    journal d'audit (étape 11) — les endpoints d'exploration ci-dessous
    (`/tendances`, `/classement`, `/comparaison-services`, `/prevision`),
    rappelés en boucle par le frontend au fil du filtrage (étapes 8→9→10),
    ne le font pas, pour ne pas saturer le journal d'un simple changement de
    filtre.
    """
    resultat = bi_service.tableau_de_bord_temps_reel(db, id_service=id_service, jour=jour)
    journal_audit_service.log_action(
        db,
        id_utilisateur=utilisateur.id_utilisateur,
        action="consultation_bi",
        details=f"jour={resultat['jour'].isoformat()} service={id_service if id_service is not None else 'tous'}",
    )
    return resultat


@router.get("/tendances", response_model=List[PointTendance])
def tendances(
    granularite: TypePeriode = Query(default=TypePeriode.MOIS),
    id_service: Optional[int] = None,
    date_debut: date_ = Query(..., description="Début de la plage à explorer."),
    date_fin: date_ = Query(..., description="Fin de la plage à explorer."),
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = _PROTECTION,
) -> List[PointTendance]:
    """
    Besoin "Analyser les tendances de présence sur des périodes définies" /
    "Suivre l'évolution mensuelle des indicateurs de présence" — une valeur
    par période de la granularité choisie, sur la plage `date_debut`..`date_fin`.
    """
    return bi_service.tendances(db, granularite, id_service, date_debut, date_fin)


@router.get("/classement", response_model=List[ClassementAgentOut])
def classement(
    date_debut: date_,
    date_fin: date_,
    id_service: Optional[int] = None,
    critere: CritereClassement = Query(default="ponctualite"),
    limite: int = Query(default=10, ge=1, le=50),
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = _PROTECTION,
) -> List[ClassementAgentOut]:
    """Besoin "Identifier les agents les plus ponctuels ou les plus souvent en retard"."""
    return bi_service.classement_agents(
        db, date_debut, date_fin, id_service=id_service, critere=critere, limite=limite
    )


@router.get("/comparaison-services", response_model=ComparaisonServicesOut)
def comparaison_services(
    type_periode: TypePeriode = Query(default=TypePeriode.MOIS),
    date_reference: Optional[date_] = None,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = _PROTECTION,
) -> ComparaisonServicesOut:
    """Besoin "Comparer les performances de ponctualité entre services", classés par taux de présence."""
    return bi_service.comparaison_services(db, type_periode, date_reference=date_reference)


@router.get("/prevision", response_model=PrevisionOut)
def prevision(
    granularite: TypePeriode = Query(default=TypePeriode.MOIS),
    id_service: Optional[int] = None,
    nombre_periodes_historique: int = Query(default=6, ge=2, le=24),
    horizon: int = Query(default=3, ge=1, le=12),
    date_reference: Optional[date_] = None,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = _PROTECTION,
) -> PrevisionOut:
    """
    Tableau de bord prédictif : régression linéaire simple sur l'historique
    récent, projetée sur `horizon` périodes futures.
    """
    return bi_service.prevision(
        db, granularite, id_service=id_service,
        nombre_periodes_historique=nombre_periodes_historique, horizon=horizon,
        date_reference=date_reference,
    )