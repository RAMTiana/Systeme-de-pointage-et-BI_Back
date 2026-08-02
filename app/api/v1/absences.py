"""Routeur — Module Absences (simple registre, sans circuit d'approbation local)."""
from datetime import date as date_
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_permission
from app.models.enums import StatutAbsence
from app.models.utilisateur import Utilisateur
from app.schemas.absence import AbsenceCreateRequest, AbsenceOut
from app.schemas.common import Page
from app.services import absence_service

router = APIRouter(prefix="/absences", tags=["Absences"])


@router.post("", response_model=AbsenceOut, status_code=201)
def creer(
    payload: AbsenceCreateRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(require_permission("gerer_absences")),
) -> AbsenceOut:
    """
    Enregistre une absence déjà connue/justifiée par l'application nationale
    de gestion des absences de la fonction publique (statut ACTIF immédiat —
    aucune étape d'approbation locale). Régularise aussi automatiquement les
    anomalies 'absence' déjà consignées sur la période si l'absence est
    saisie après coup (cf. `absence_service`).
    """
    return absence_service.creer(
        db,
        id_agent=payload.id_agent,
        date_debut=payload.date_debut,
        date_fin=payload.date_fin,
        id_utilisateur_saisie=utilisateur.id_utilisateur,
        motif=payload.motif,
    )


@router.get("", response_model=Page[AbsenceOut])
def lister(
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    statut: Optional[StatutAbsence] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> Page[AbsenceOut]:
    """Registre paginé des absences, consultable par tout utilisateur authentifié actif."""
    absences, total = absence_service.lister(
        db,
        id_agent=id_agent,
        id_service=id_service,
        statut=statut,
        date_debut=date_debut,
        date_fin=date_fin,
        skip=skip,
        limit=limit,
    )
    return Page(items=absences, total=total, skip=skip, limit=limit)


@router.get("/{id_absence}", response_model=AbsenceOut)
def obtenir(
    id_absence: int,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> AbsenceOut:
    return absence_service.get_by_id_or_404(db, id_absence)


@router.put("/{id_absence}/annulation", response_model=AbsenceOut)
def annuler(
    id_absence: int,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(require_permission("gerer_absences")),
) -> AbsenceOut:
    """Annulation par la Secrétaire (ex. erreur de saisie)."""
    return absence_service.annuler(db, id_absence=id_absence, id_utilisateur=utilisateur.id_utilisateur)
