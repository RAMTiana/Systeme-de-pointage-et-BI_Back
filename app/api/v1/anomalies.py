"""Routeur — Module Anomalies (Processus 3 du BPMN "Traitement des anomalies")."""
from datetime import date as date_
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_active_user, get_db, require_permission, verify_job_key
from app.models.enums import StatutJustification, TypeAnomalie
from app.models.utilisateur import Utilisateur
from app.schemas.anomalie import (
    AnomalieDetailOut,
    AnomalieExamenRequest,
    DetectionAbsencesRequest,
    DetectionAbsencesResultat,
)
from app.schemas.common import Page
from app.services import anomalie_service
from sqlalchemy.orm import Session

router = APIRouter(prefix="/anomalies", tags=["Anomalies"])


@router.get("", response_model=Page[AnomalieDetailOut])
def lister_anomalies(
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    type_anomalie: Optional[TypeAnomalie] = None,
    statut_justification: Optional[StatutJustification] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> Page[AnomalieDetailOut]:
    """Historique paginé des anomalies, consultable par tout utilisateur authentifié actif."""
    anomalies, total = anomalie_service.lister_anomalies(
        db,
        id_agent=id_agent,
        id_service=id_service,
        type_anomalie=type_anomalie,
        statut_justification=statut_justification,
        date_debut=date_debut,
        date_fin=date_fin,
        skip=skip,
        limit=limit,
    )
    return Page(items=anomalies, total=total, skip=skip, limit=limit)


@router.get("/{id_anomalie}", response_model=AnomalieDetailOut)
def obtenir_anomalie(
    id_anomalie: int,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> AnomalieDetailOut:
    return anomalie_service.get_by_id_or_404(db, id_anomalie)


@router.put("/{id_anomalie}/examen", response_model=AnomalieDetailOut)
def examiner_anomalie(
    id_anomalie: int,
    payload: AnomalieExamenRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(require_permission("traiter_anomalies")),
) -> AnomalieDetailOut:
    """
    Étapes 8-10 du Processus 3 : la secrétaire examine l'anomalie et le
    dossier de l'agent, puis enregistre sa décision (justifiée + justificatif,
    ou non justifiée).
    """
    return anomalie_service.examiner_anomalie(
        db,
        id_anomalie=id_anomalie,
        id_utilisateur=utilisateur.id_utilisateur,
        anomalie_justifiee=payload.anomalie_justifiee,
        motif=payload.motif,
        piece_jointe_chemin=payload.piece_jointe_chemin,
    )


@router.post(
    "/detecter-absences",
    response_model=DetectionAbsencesResultat,
    dependencies=[Depends(verify_job_key)],
)
def detecter_absences(
    payload: DetectionAbsencesRequest,
    db: Session = Depends(get_db),
) -> DetectionAbsencesResultat:
    """
    Job planifié (à appeler quotidiennement par un ordonnanceur externe, cf.
    Timer Start Event du Processus 4) : détecte les agents sans pointage
    d'entrée sur le jour contrôlé (par défaut la veille) et déclenche la
    qualification/alerte comme pour les anomalies détectées au pointage.
    Protégé par la clé partagée `X-Job-Key` (pas de session utilisateur pour
    un appel de cron).
    """
    jour_controle = payload.jour or (date_.today() - timedelta(days=1))
    anomalies = anomalie_service.detecter_absences(db, jour=payload.jour)
    return DetectionAbsencesResultat(
        jour_controle=jour_controle,
        absences_detectees=len(anomalies),
        anomalies=anomalies,
    )