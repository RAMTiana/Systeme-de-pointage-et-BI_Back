"""Routeur — Module Congés (simple registre, sans circuit d'approbation local)."""
from datetime import date as date_
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_permission
from app.models.enums import StatutConge, TypeConge
from app.models.utilisateur import Utilisateur
from app.schemas.common import Page
from app.schemas.conge import CongeCreateRequest, CongeOut
from app.services import conge_service

router = APIRouter(prefix="/conges", tags=["Congés"])


@router.post("", response_model=CongeOut, status_code=201)
def creer(
    payload: CongeCreateRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(require_permission("gerer_conges")),
) -> CongeOut:
    """
    Enregistre un congé déjà approuvé par le système national de gestion des
    congés de la fonction publique (statut ACTIF immédiat — aucune étape
    d'approbation locale). Régularise aussi automatiquement les anomalies
    'absence' déjà consignées sur la période si le congé est saisi après
    coup (cf. `conge_service`).
    """
    return conge_service.creer(
        db,
        id_agent=payload.id_agent,
        type_conge=payload.type_conge,
        date_debut=payload.date_debut,
        date_fin=payload.date_fin,
        id_utilisateur_saisie=utilisateur.id_utilisateur,
        motif=payload.motif,
    )


@router.get("", response_model=Page[CongeOut])
def lister(
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    statut: Optional[StatutConge] = None,
    type_conge: Optional[TypeConge] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> Page[CongeOut]:
    """Registre paginé des congés, consultable par tout utilisateur authentifié actif."""
    conges, total = conge_service.lister(
        db,
        id_agent=id_agent,
        id_service=id_service,
        statut=statut,
        type_conge=type_conge,
        date_debut=date_debut,
        date_fin=date_fin,
        skip=skip,
        limit=limit,
    )
    return Page(items=conges, total=total, skip=skip, limit=limit)


@router.get("/{id_conge}", response_model=CongeOut)
def obtenir(
    id_conge: int,
    db: Session = Depends(get_db),
    _utilisateur: Utilisateur = Depends(get_current_active_user),
) -> CongeOut:
    return conge_service.get_by_id_or_404(db, id_conge)


@router.put("/{id_conge}/annulation", response_model=CongeOut)
def annuler(
    id_conge: int,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(require_permission("gerer_conges")),
) -> CongeOut:
    """Annulation par la Secrétaire (ex. erreur de saisie, congé finalement non pris)."""
    return conge_service.annuler(db, id_conge=id_conge, id_utilisateur=utilisateur.id_utilisateur)
