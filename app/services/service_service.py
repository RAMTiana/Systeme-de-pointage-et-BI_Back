"""
Accès aux données pour l'entité Service (unité organisationnelle du SRB).

Toute violation de contrainte d'unicité (nom_service) est traduite en
HTTPException 409 ici, pour que les routeurs n'aient jamais à connaître
les détails de l'IntegrityError SQLAlchemy/psycopg2.
"""
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent import Agent
from app.models.service import Service
from app.schemas.service import ServiceCreate, ServiceUpdate

_NOM_SERVICE_DEJA_UTILISE = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="Un service portant ce nom existe déjà.",
)


def get_by_id(db: Session, id_service: int) -> Optional[Service]:
    return db.get(Service, id_service)


def get_by_id_or_404(db: Session, id_service: int) -> Service:
    service = get_by_id(db, id_service)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service introuvable.")
    return service


def list_all(db: Session, recherche: Optional[str] = None) -> List[Service]:
    stmt = select(Service).order_by(Service.nom_service)
    if recherche:
        stmt = stmt.where(Service.nom_service.ilike(f"%{recherche}%"))
    return list(db.execute(stmt).scalars().all())


def compter_agents(db: Session, id_service: int) -> int:
    stmt = select(func.count(Agent.id_agent)).where(Agent.id_service == id_service)
    return db.execute(stmt).scalar_one()


def create(db: Session, payload: ServiceCreate) -> Service:
    service = Service(nom_service=payload.nom_service, description=payload.description)
    db.add(service)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _NOM_SERVICE_DEJA_UTILISE
    db.refresh(service)
    return service


def update(db: Session, service: Service, payload: ServiceUpdate) -> Service:
    donnees = payload.model_dump(exclude_unset=True)
    for champ, valeur in donnees.items():
        setattr(service, champ, valeur)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _NOM_SERVICE_DEJA_UTILISE
    db.refresh(service)
    return service


def delete(db: Session, service: Service) -> None:
    """
    Supprime un service. Les agents rattachés ne sont pas supprimés :
    `agent.id_service` passe à NULL (ON DELETE SET NULL, cf. schéma SQL) —
    ils doivent être réaffectés explicitement ensuite.
    """
    db.delete(service)
    db.commit()
