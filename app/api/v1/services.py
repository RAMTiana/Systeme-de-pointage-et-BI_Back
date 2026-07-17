"""
Endpoints de gestion des divisions (unités organisationnelles du SRB) :
  GET    /divisions       — liste (recherche par nom), accessible à tout utilisateur connecté
  GET    /divisions/{id}  — détail
  POST   /divisions       — création, protégé par la permission `gerer_services`
  PATCH  /divisions/{id}  — modification, idem
  DELETE /divisions/{id}  — suppression, idem

La lecture est ouverte à tout utilisateur actif (ex. : peupler un menu
déroulant "division" dans le formulaire de création d'un agent), tandis que
l'écriture est réservée aux profils disposant de la permission RBAC dédiée.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_permission
from app.schemas.service import ServiceCreate, ServiceOut, ServiceUpdate
from app.services import service_service

router = APIRouter(prefix="/divisions", tags=["Divisions"])
router_legacy = APIRouter(prefix="/services", tags=["Divisions"])


def _vers_out(db: Session, service) -> ServiceOut:
    return ServiceOut(
        id_service=service.id_service,
        nom_service=service.nom_service,
        description=service.description,
        nombre_agents=service_service.compter_agents(db, service.id_service),
    )


@router.get("", response_model=List[ServiceOut], summary="Lister les divisions")
@router_legacy.get("", response_model=List[ServiceOut], summary="Lister les divisions")
def lister_services(
    recherche: Optional[str] = Query(default=None, description="Filtre sur le nom de la division"),
    db: Session = Depends(get_db),
    _utilisateur=Depends(get_current_active_user),
) -> List[ServiceOut]:
    services = service_service.list_all(db, recherche=recherche)
    return [_vers_out(db, s) for s in services]


@router.get("/{id_service}", response_model=ServiceOut, summary="Détail d'une division")
@router_legacy.get("/{id_service}", response_model=ServiceOut, summary="Détail d'une division")
def obtenir_service(
    id_service: int,
    db: Session = Depends(get_db),
    _utilisateur=Depends(get_current_active_user),
) -> ServiceOut:
    service = service_service.get_by_id_or_404(db, id_service)
    return _vers_out(db, service)


@router.post(
    "",
    response_model=ServiceOut,
    status_code=201,
    summary="Créer une division",
    dependencies=[Depends(require_permission("gerer_services"))],
)
@router_legacy.post(
    "",
    response_model=ServiceOut,
    status_code=201,
    summary="Créer une division",
    dependencies=[Depends(require_permission("gerer_services"))],
)
def creer_service(payload: ServiceCreate, db: Session = Depends(get_db)) -> ServiceOut:
    service = service_service.create(db, payload)
    return _vers_out(db, service)


@router.patch(
    "/{id_service}",
    response_model=ServiceOut,
    summary="Modifier une division",
    dependencies=[Depends(require_permission("gerer_services"))],
)
@router_legacy.patch(
    "/{id_service}",
    response_model=ServiceOut,
    summary="Modifier une division",
    dependencies=[Depends(require_permission("gerer_services"))],
)
def modifier_service(id_service: int, payload: ServiceUpdate, db: Session = Depends(get_db)) -> ServiceOut:
    service = service_service.get_by_id_or_404(db, id_service)
    service = service_service.update(db, service, payload)
    return _vers_out(db, service)


@router.delete(
    "/{id_service}",
    status_code=204,
    summary="Supprimer une division",
    dependencies=[Depends(require_permission("gerer_services"))],
)
@router_legacy.delete(
    "/{id_service}",
    status_code=204,
    summary="Supprimer une division",
    dependencies=[Depends(require_permission("gerer_services"))],
)
def supprimer_service(id_service: int, db: Session = Depends(get_db)) -> None:
    service = service_service.get_by_id_or_404(db, id_service)
    service_service.delete(db, service)