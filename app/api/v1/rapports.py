"""Routeur — Module Rapports (Processus 4 du BPMN "Génération de rapports")."""
import os
from datetime import date as date_
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_permission, verify_job_key
from app.core.config import settings
from app.models.enums import FormatRapport, TypePeriode
from app.models.utilisateur import Utilisateur
from app.schemas.common import Page
from app.schemas.rapport import (
    RapportContenu,
    RapportGenerateRequest,
    RapportOut,
    RapportPlanifieRequest,
)
from app.services import rapport_service

router = APIRouter(prefix="/rapports", tags=["Rapports"])

_MEDIA_TYPES = {
    FormatRapport.PDF: "application/pdf",
    FormatRapport.EXCEL: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _vers_rapport_out(rapport) -> RapportOut:
    periode_debut, periode_fin = rapport_service.bornes_depuis_rapport(rapport)
    return RapportOut(
        id_rapport=rapport.id_rapport,
        id_utilisateur=rapport.id_utilisateur,
        id_service=rapport.id_service,
        service=rapport.service,
        type_periode=rapport.type_periode,
        date_generation=rapport.date_generation,
        format=rapport.format,
        chemin_fichier=rapport.chemin_fichier,
        periode_debut=periode_debut,
        periode_fin=periode_fin,
    )


@router.get("", response_model=Page[RapportOut])
def lister_rapports(
    type_periode: Optional[TypePeriode] = None,
    format: Optional[FormatRapport] = None,
    id_service: Optional[int] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> Page[RapportOut]:
    """
    Historique paginé des rapports déjà générés, consultable par tout
    utilisateur authentifié actif (étape 11 du Processus 4 : consultation
    non bloquante par le chef de service). `date_debut`/`date_fin` filtrent
    sur la date de génération, pas sur la période couverte par le rapport.
    """
    rapports, total = rapport_service.lister_rapports(
        db,
        type_periode=type_periode,
        format_rapport=format,
        id_service=id_service,
        date_debut=date_debut,
        date_fin=date_fin,
        skip=skip,
        limit=limit,
    )
    return Page(items=[_vers_rapport_out(r) for r in rapports], total=total, skip=skip, limit=limit)


@router.get("/{id_rapport}", response_model=RapportOut)
def obtenir_rapport(
    id_rapport: int,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> RapportOut:
    return _vers_rapport_out(rapport_service.get_by_id_or_404(db, id_rapport))


@router.get("/{id_rapport}/indicateurs", response_model=RapportContenu)
def obtenir_indicateurs(
    id_rapport: int,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> RapportContenu:
    """
    Aperçu des indicateurs sans télécharger le fichier — recalculé sur la
    période exacte couverte par ce rapport (déduite du nom de fichier, cf.
    `rapport_service.bornes_depuis_rapport`), utile pour un affichage direct
    côté frontend (Angular) en complément de l'export PDF/Excel.
    """
    rapport = rapport_service.get_by_id_or_404(db, id_rapport)
    periode_debut, _ = rapport_service.bornes_depuis_rapport(rapport)
    if periode_debut is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Impossible de déterminer la période couverte par ce rapport.",
        )
    indicateurs = rapport_service.calculer_indicateurs(
        db, rapport.type_periode, rapport.id_service, date_reference=periode_debut
    )
    return RapportContenu(**indicateurs)


@router.get("/{id_rapport}/telecharger")
def telecharger_rapport(
    id_rapport: int,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> FileResponse:
    rapport = rapport_service.get_by_id_or_404(db, id_rapport)
    chemin_absolu = os.path.abspath(rapport.chemin_fichier)
    if not os.path.isfile(chemin_absolu):
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="Le fichier de ce rapport n'est plus disponible sur le serveur.",
        )
    return FileResponse(
        chemin_absolu,
        media_type=_MEDIA_TYPES[rapport.format],
        filename=os.path.basename(chemin_absolu),
    )


@router.post("/generer", response_model=RapportOut, status_code=status.HTTP_201_CREATED)
def generer_rapport(
    payload: RapportGenerateRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(require_permission("generer_rapports")),
) -> RapportOut:
    """
    Génération à la demande (None Start Event du Processus 4, lane
    Secrétaire) : calcule les indicateurs de la période demandée et produit
    le document (PDF ou Excel).
    """
    rapport = rapport_service.generer_rapport(
        db,
        type_periode=payload.type_periode,
        format_rapport=payload.format,
        id_service=payload.id_service,
        id_utilisateur=utilisateur.id_utilisateur,
        date_reference=payload.date_reference,
    )
    return _vers_rapport_out(rapport)


@router.post(
    "/generation-planifiee",
    response_model=List[RapportOut],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_job_key)],
)
def generer_rapports_planifies(
    payload: RapportPlanifieRequest,
    db: Session = Depends(get_db),
) -> List[RapportOut]:
    """
    Job planifié (Timer Start Event du Processus 4, "expression de cycle" —
    ex. exécution quotidienne) : génère, pour la période demandée, un
    rapport consolidé (tous services) et un rapport par service actif, dans
    chacun des formats demandés. Protégé par `X-Job-Key` comme
    `/anomalies/detecter-absences`, au même titre qu'un déclenchement par
    ordonnanceur externe plutôt que par une session utilisateur.
    """
    rapports = rapport_service.generer_rapports_planifies(
        db,
        type_periode=payload.type_periode,
        formats=payload.formats,
        date_reference=payload.date_reference,
    )
    return [_vers_rapport_out(r) for r in rapports]