"""
Endpoints de gestion des agents (cf. cahier des charges — "Gestion des agents") :

  GET    /agents                        — recherche paginée (nom, matricule, service, statut)
  GET    /agents/{id}                    — fiche complète (service + historique des rattachements)
  POST   /agents                        — création
  PATCH  /agents/{id}                    — modification
  POST   /agents/{id}/desactiver         — désactivation (statut -> desactive)
  POST   /agents/{id}/reactiver          — réactivation (statut -> actif)
  PUT    /agents/{id}/consentement-facial — consentement explicite à la reconnaissance faciale
  DELETE /agents/{id}                    — suppression physique (cas exceptionnel, cf. service)

  POST   /agents/{id}/affectations                       — ajouter un rattachement secondaire
  DELETE /agents/{id}/affectations/{id_affectation}       — clôturer un rattachement (date_fin)

  PUT    /agents/{id}/empreinte-faciale     — enregistrer l'empreinte faciale de référence
  DELETE /agents/{id}/empreinte-faciale     — supprimer l'empreinte faciale

  GET    /agents/{id}/webauthn/options      — options d'inscription biométrique WebAuthn
  PUT    /agents/{id}/webauthn              — vérifier et enregistrer l'identifiant WebAuthn
  DELETE /agents/{id}/webauthn              — supprimer l'identifiant WebAuthn

Lecture ouverte à tout utilisateur actif ; écriture protégée par la
permission RBAC `gerer_agents`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db, require_permission
from app.models.enums import StatutAgent
from app.schemas.agent import (
    AffectationCreate,
    AffectationOut,
    AgentCreate,
    AgentDetailOut,
    AgentOut,
    AgentUpdate,
    ConsentementFacialUpdate,
)
from app.schemas.common import Page
from app.schemas.empreinte import EmpreinteFacialeCreate, EmpreinteFacialeOut
from app.schemas.webauthn import IdentifiantWebAuthnOut, WebAuthnRegistrationCreate
from app.services import agent_service, empreinte_service, webauthn_service

router = APIRouter(prefix="/agents", tags=["Agents"])

_ECRITURE = [Depends(require_permission("gerer_agents"))]


@router.get("", response_model=Page[AgentOut], summary="Rechercher / lister les agents")
def lister_agents(
    recherche: Optional[str] = Query(default=None, description="Recherche sur nom, prénom ou matricule"),
    id_service: Optional[int] = Query(default=None, description="Filtrer par service principal"),
    statut: Optional[StatutAgent] = Query(default=None, description="Filtrer par statut"),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    _utilisateur=Depends(get_current_active_user),
) -> Page[AgentOut]:
    agents, total = agent_service.list_agents(
        db, recherche=recherche, id_service=id_service, statut=statut, skip=skip, limit=limit
    )
    return Page(items=agents, total=total, skip=skip, limit=limit)


@router.get("/{id_agent}", response_model=AgentDetailOut, summary="Fiche complète d'un agent")
def obtenir_agent(
    id_agent: int,
    db: Session = Depends(get_db),
    _utilisateur=Depends(get_current_active_user),
) -> AgentDetailOut:
    return agent_service.get_detail_or_404(db, id_agent)


@router.post("", response_model=AgentOut, status_code=201, summary="Créer un agent", dependencies=_ECRITURE)
def creer_agent(payload: AgentCreate, db: Session = Depends(get_db)) -> AgentOut:
    return agent_service.create(db, payload)


@router.patch("/{id_agent}", response_model=AgentOut, summary="Modifier un agent", dependencies=_ECRITURE)
def modifier_agent(id_agent: int, payload: AgentUpdate, db: Session = Depends(get_db)) -> AgentOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return agent_service.update(db, agent, payload)


@router.post(
    "/{id_agent}/desactiver",
    response_model=AgentOut,
    summary="Désactiver un agent (conserve l'historique)",
    dependencies=_ECRITURE,
)
def desactiver_agent(id_agent: int, db: Session = Depends(get_db)) -> AgentOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return agent_service.changer_statut(db, agent, StatutAgent.DESACTIVE)


@router.post(
    "/{id_agent}/reactiver",
    response_model=AgentOut,
    summary="Réactiver un agent précédemment désactivé",
    dependencies=_ECRITURE,
)
def reactiver_agent(id_agent: int, db: Session = Depends(get_db)) -> AgentOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return agent_service.changer_statut(db, agent, StatutAgent.ACTIF)


@router.put(
    "/{id_agent}/consentement-facial",
    response_model=AgentOut,
    summary="Enregistrer le consentement de l'agent pour la reconnaissance faciale",
    dependencies=_ECRITURE,
)
def definir_consentement_facial(
    id_agent: int, payload: ConsentementFacialUpdate, db: Session = Depends(get_db)
) -> AgentOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return agent_service.set_consentement_facial(db, agent, payload.consentement_facial)


@router.delete(
    "/{id_agent}",
    status_code=204,
    summary="Supprimer physiquement un agent (cas exceptionnel — préférer la désactivation)",
    dependencies=_ECRITURE,
)
def supprimer_agent(id_agent: int, db: Session = Depends(get_db)) -> None:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    agent_service.delete(db, agent)


# --------------------------------------------------------------------
# Affectations (rattachements secondaires à un service)
# --------------------------------------------------------------------

@router.post(
    "/{id_agent}/affectations",
    response_model=AffectationOut,
    status_code=201,
    summary="Ajouter un rattachement secondaire à un service",
    dependencies=_ECRITURE,
)
def ajouter_affectation(id_agent: int, payload: AffectationCreate, db: Session = Depends(get_db)) -> AffectationOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return agent_service.ajouter_affectation(db, agent, payload)


@router.delete(
    "/{id_agent}/affectations/{id_affectation}",
    response_model=AffectationOut,
    summary="Clôturer un rattachement secondaire (renseigne date_fin, ne supprime pas l'historique)",
    dependencies=_ECRITURE,
)
def terminer_affectation(id_agent: int, id_affectation: int, db: Session = Depends(get_db)) -> AffectationOut:
    affectation = agent_service.get_affectation_or_404(db, id_agent, id_affectation)
    return agent_service.terminer_affectation(db, affectation)


# --------------------------------------------------------------------
# Empreinte biométrique faciale (prérequis au pointage par mode 'facial')
# --------------------------------------------------------------------

@router.put(
    "/{id_agent}/empreinte-faciale",
    response_model=EmpreinteFacialeOut,
    summary="Enregistrer / remplacer l'empreinte faciale de référence d'un agent",
    dependencies=_ECRITURE,
)
def enregistrer_empreinte_faciale(
    id_agent: int, payload: EmpreinteFacialeCreate, db: Session = Depends(get_db)
) -> EmpreinteFacialeOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return empreinte_service.enregistrer(db, agent, payload.encodage_facial)


@router.delete(
    "/{id_agent}/empreinte-faciale",
    status_code=204,
    summary="Supprimer l'empreinte faciale d'un agent (désactive le pointage facial)",
    dependencies=_ECRITURE,
)
def supprimer_empreinte_faciale(id_agent: int, db: Session = Depends(get_db)) -> None:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    empreinte_service.supprimer(db, agent)


# --------------------------------------------------------------------
# Biométrie d'appareil WebAuthn (prérequis au pointage par mode 'webauthn')
# --------------------------------------------------------------------

@router.get(
    "/{id_agent}/webauthn/options",
    summary="Générer les options d'inscription WebAuthn (à transmettre à navigator.credentials.create())",
    dependencies=_ECRITURE,
)
def obtenir_options_webauthn(id_agent: int, db: Session = Depends(get_db)) -> dict:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return webauthn_service.options_enrolement(agent)


@router.put(
    "/{id_agent}/webauthn",
    response_model=IdentifiantWebAuthnOut,
    summary="Vérifier et enregistrer l'identifiant WebAuthn (empreinte / Touch ID / Windows Hello) d'un agent",
    dependencies=_ECRITURE,
)
def enregistrer_webauthn(
    id_agent: int, payload: WebAuthnRegistrationCreate, db: Session = Depends(get_db)
) -> IdentifiantWebAuthnOut:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    return webauthn_service.enregistrer_credential(db, agent, payload.credential, payload.nom_appareil)


@router.delete(
    "/{id_agent}/webauthn",
    status_code=204,
    summary="Supprimer l'identifiant WebAuthn d'un agent (désactive le pointage biométrique)",
    dependencies=_ECRITURE,
)
def supprimer_webauthn(id_agent: int, db: Session = Depends(get_db)) -> None:
    agent = agent_service.get_by_id_or_404(db, id_agent)
    webauthn_service.supprimer_credential(db, agent)