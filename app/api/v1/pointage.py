"""
Endpoints du module Pointage (Processus 1 du BPMN — "Pointage d'un agent") :

  POST /pointage/qr        — pointage par QR code dynamique
  POST /pointage/badge     — pointage par badge
  POST /pointage/facial    — pointage par reconnaissance faciale
  GET  /pointage/webauthn/options — challenge d'authentification biométrique (WebAuthn)
  POST /pointage/webauthn  — pointage par biométrie d'appareil (WebAuthn), vérifié cryptographiquement
  GET  /pointage           — historique paginé (filtres)
  GET  /pointage/{id}      — détail d'un pointage

Les trois endpoints de saisie sont appelés par le poste de pointage
(kiosque / dispositif dédié), pas par un utilisateur du back-office : un
agent n'a pas de compte `utilisateur` (RBAC) dans ce système — cf. schéma
de données. Ils sont donc protégés par une clé de dispositif partagée
(en-tête `X-Device-Key`, cf. `app.api.deps.verify_device_key`) plutôt que
par le JWT du back-office.

La consultation (`GET`) reste réservée aux utilisateurs authentifiés du
back-office, comme les modules Agents/Services.
"""
from datetime import date as date_
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, verify_device_key
from app.models.anomalie import Anomalie
from app.models.enums import ModePointage, StatutPointage, TypePointage
from app.models.pointage import Pointage
from app.schemas.common import Page
from app.schemas.pointage import (
    PointageFacialCreate,
    PointageOut,
    PointageQrBadgeCreate,
    PointageResultat,
    PointageWebAuthnCreate,
)
from app.services import pointage_service

router = APIRouter(prefix="/pointage", tags=["Pointage"])

_DISPOSITIF = [Depends(verify_device_key)]


def _vers_resultat(pointage: Pointage, anomalie: Optional[Anomalie]) -> PointageResultat:
    return PointageResultat(
        pointage=PointageOut.model_validate(pointage),
        anomalie_detectee=anomalie.type_anomalie.value if anomalie else None,
    )


@router.post(
    "/qr",
    response_model=PointageResultat,
    status_code=201,
    summary="Pointage par QR code dynamique",
    dependencies=_DISPOSITIF,
)
def pointer_qr(payload: PointageQrBadgeCreate, db: Session = Depends(get_db)) -> PointageResultat:
    pointage, anomalie = pointage_service.pointer_qr_badge(db, payload, ModePointage.QR)
    return _vers_resultat(pointage, anomalie)


@router.post(
    "/badge",
    response_model=PointageResultat,
    status_code=201,
    summary="Pointage par badge",
    dependencies=_DISPOSITIF,
)
def pointer_badge(payload: PointageQrBadgeCreate, db: Session = Depends(get_db)) -> PointageResultat:
    pointage, anomalie = pointage_service.pointer_qr_badge(db, payload, ModePointage.BADGE)
    return _vers_resultat(pointage, anomalie)


@router.post(
    "/facial",
    response_model=PointageResultat,
    status_code=201,
    summary="Pointage par reconnaissance faciale",
    dependencies=_DISPOSITIF,
)
def pointer_facial(payload: PointageFacialCreate, db: Session = Depends(get_db)) -> PointageResultat:
    pointage, anomalie = pointage_service.pointer_facial(db, payload)
    return _vers_resultat(pointage, anomalie)


@router.get(
    "/webauthn/options",
    summary="Générer les options d'authentification WebAuthn (à transmettre à navigator.credentials.get())",
    dependencies=_DISPOSITIF,
)
def options_webauthn_pointage(
    matricule: str = Query(description="Matricule de l'agent qui va pointer"),
    db: Session = Depends(get_db),
) -> dict:
    return pointage_service.options_webauthn(db, matricule)


@router.post(
    "/webauthn",
    response_model=PointageResultat,
    status_code=201,
    summary="Pointage biométrique via WebAuthn (Touch ID / Windows Hello / empreinte appareil)",
    dependencies=_DISPOSITIF,
)
def pointer_webauthn(payload: PointageWebAuthnCreate, db: Session = Depends(get_db)) -> PointageResultat:
    pointage, anomalie = pointage_service.pointer_webauthn(db, payload)
    return _vers_resultat(pointage, anomalie)


@router.get("", response_model=Page[PointageOut], summary="Historique des pointages (filtres)")
def lister_pointages(
    id_agent: Optional[int] = Query(default=None),
    id_service: Optional[int] = Query(default=None, description="Filtrer par service principal de l'agent"),
    type_pointage: Optional[TypePointage] = Query(default=None),
    statut: Optional[StatutPointage] = Query(default=None),
    date_debut: Optional[date_] = Query(default=None),
    date_fin: Optional[date_] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    _utilisateur=Depends(get_current_active_user),
) -> Page[PointageOut]:
    pointages, total = pointage_service.lister_pointages(
        db,
        id_agent=id_agent,
        id_service=id_service,
        type_pointage=type_pointage,
        statut=statut,
        date_debut=date_debut,
        date_fin=date_fin,
        skip=skip,
        limit=limit,
    )
    return Page(items=pointages, total=total, skip=skip, limit=limit)


@router.get("/{id_pointage}", response_model=PointageOut, summary="Détail d'un pointage")
def obtenir_pointage(
    id_pointage: int,
    db: Session = Depends(get_db),
    _utilisateur=Depends(get_current_active_user),
) -> PointageOut:
    return pointage_service.get_by_id_or_404(db, id_pointage)