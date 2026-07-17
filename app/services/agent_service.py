"""
Accès aux données et logique métier pour l'entité Agent, ainsi que ses
rattachements secondaires (Affectation).

Cf. cahier des charges — module "Gestion des agents" :
  - ajouter / modifier / désactiver un agent
  - rechercher un agent selon différents critères (nom, matricule, service)
  - consulter la fiche complète et l'historique d'un agent
  - affecter les agents à un ou plusieurs services
"""
from datetime import date
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.models.affectation import Affectation
from app.models.agent import Agent
from app.models.enums import StatutAgent
from app.schemas.agent import AffectationCreate, AgentCreate, AgentUpdate

_MATRICULE_DEJA_UTILISE = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="Un agent avec ce matricule existe déjà.",
)


def _requete_de_base() -> Select:
    return select(Agent).options(
        joinedload(Agent.service),
        joinedload(Agent.empreinte_biometrique),
        joinedload(Agent.identifiant_webauthn),
    )


def get_by_id(db: Session, id_agent: int) -> Optional[Agent]:
    stmt = _requete_de_base().where(Agent.id_agent == id_agent)
    return db.execute(stmt).unique().scalar_one_or_none()


def get_by_id_or_404(db: Session, id_agent: int) -> Agent:
    agent = get_by_id(db, id_agent)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent introuvable.")
    return agent


def get_detail_or_404(db: Session, id_agent: int) -> Agent:
    """Fiche complète : agent + service principal + historique des rattachements secondaires."""
    stmt = (
        _requete_de_base()
        .options(joinedload(Agent.affectations).joinedload(Affectation.service))
        .where(Agent.id_agent == id_agent)
    )
    agent = db.execute(stmt).unique().scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent introuvable.")
    return agent


def get_by_matricule(db: Session, matricule: str) -> Optional[Agent]:
    stmt = _requete_de_base().where(Agent.matricule == matricule)
    return db.execute(stmt).unique().scalar_one_or_none()


def list_agents(
    db: Session,
    recherche: Optional[str] = None,
    id_service: Optional[int] = None,
    statut: Optional[StatutAgent] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Agent], int]:
    """
    Recherche paginée selon nom, prénom ou matricule (recherche partielle,
    insensible à la casse) et filtrage par service / statut.
    Retourne (agents, total) — le total sert à la pagination côté client Angular.
    """
    stmt = _requete_de_base()
    conditions = []

    if recherche:
        motif = f"%{recherche}%"
        conditions.append(
            or_(Agent.nom.ilike(motif), Agent.prenom.ilike(motif), Agent.matricule.ilike(motif))
        )
    if id_service is not None:
        conditions.append(Agent.id_service == id_service)
    if statut is not None:
        conditions.append(Agent.statut == statut)

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(stmt.with_only_columns(Agent.id_agent).subquery())
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Agent.nom, Agent.prenom).offset(skip).limit(limit)
    agents = list(db.execute(stmt).unique().scalars().all())

    return agents, total


def create(db: Session, payload: AgentCreate) -> Agent:
    agent = Agent(
        matricule=payload.matricule,
        nom=payload.nom,
        prenom=payload.prenom,
        fonction=payload.fonction,
        id_service=payload.id_service,
    )
    db.add(agent)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _MATRICULE_DEJA_UTILISE
    db.refresh(agent)
    return get_by_id_or_404(db, agent.id_agent)


def update(db: Session, agent: Agent, payload: AgentUpdate) -> Agent:
    donnees = payload.model_dump(exclude_unset=True)
    for champ, valeur in donnees.items():
        setattr(agent, champ, valeur)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise _MATRICULE_DEJA_UTILISE
    db.refresh(agent)
    return get_by_id_or_404(db, agent.id_agent)


def changer_statut(db: Session, agent: Agent, statut: StatutAgent) -> Agent:
    """Active ou désactive un agent (jamais de suppression physique — traçabilité)."""
    agent.statut = statut
    db.commit()
    db.refresh(agent)
    return agent


def set_consentement_facial(db: Session, agent: Agent, consentement: bool) -> Agent:
    """
    Enregistre le consentement explicite de l'agent quant à la reconnaissance
    faciale (cf. cahier des charges, chapitre protection des données).
    Le pointage par mode 'facial' nécessite ce consentement (vérifié au
    moment du pointage, module Pointage à venir).
    """
    agent.consentement_facial = consentement
    db.commit()
    db.refresh(agent)
    return agent


def delete(db: Session, agent: Agent) -> None:
    """
    Suppression physique d'un agent — supprime en cascade ses affectations,
    empreinte biométrique, pointages et anomalies (ON DELETE CASCADE, cf.
    schéma SQL). À réserver aux erreurs de saisie : dans le cas général,
    préférer `changer_statut(..., StatutAgent.DESACTIVE)` pour conserver
    l'historique et la traçabilité des pointages passés.
    """
    db.delete(agent)
    db.commit()


# --------------------------------------------------------------------
# Affectations (rattachements secondaires à un service)
# --------------------------------------------------------------------

def ajouter_affectation(db: Session, agent: Agent, payload: AffectationCreate) -> Affectation:
    affectation = Affectation(
        id_agent=agent.id_agent,
        id_service=payload.id_service,
        date_debut=payload.date_debut or date.today(),
        service_principal=payload.service_principal,
    )
    db.add(affectation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ce rattachement existe déjà pour cet agent, ce service et cette date de début.",
        )
    db.refresh(affectation)
    return affectation


def get_affectation_or_404(db: Session, id_agent: int, id_affectation: int) -> Affectation:
    stmt = select(Affectation).where(
        Affectation.id_affectation == id_affectation, Affectation.id_agent == id_agent
    )
    affectation = db.execute(stmt).scalar_one_or_none()
    if affectation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rattachement introuvable.")
    return affectation


def terminer_affectation(db: Session, affectation: Affectation, date_fin: Optional[date] = None) -> Affectation:
    affectation.date_fin = date_fin or date.today()
    db.commit()
    db.refresh(affectation)
    return affectation
