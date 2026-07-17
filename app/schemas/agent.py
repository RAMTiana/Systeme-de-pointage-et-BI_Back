"""Schémas Pydantic pour l'entité Agent et ses rattachements (Affectation)."""
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import StatutAgent
from app.schemas.service import ServiceLight


# --------------------------------------------------------------------
# Affectation (rattachement secondaire d'un agent à un service)
# --------------------------------------------------------------------

class AffectationCreate(BaseModel):
    id_service: int
    date_debut: Optional[date] = None
    service_principal: bool = False


class AffectationOut(BaseModel):
    id_affectation: int
    id_service: int
    service: ServiceLight
    date_debut: date
    date_fin: Optional[date] = None
    service_principal: bool

    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------
# Agent
# --------------------------------------------------------------------

class AgentBase(BaseModel):
    matricule: str = Field(min_length=1, max_length=30)
    nom: str = Field(min_length=1, max_length=100)
    prenom: str = Field(min_length=1, max_length=100)
    fonction: Optional[str] = Field(default=None, max_length=150)
    id_service: Optional[int] = None


class AgentCreate(AgentBase):
    """Création d'un agent. Le consentement facial se donne séparément (endpoint dédié)."""
    pass


class AgentUpdate(BaseModel):
    """PATCH sémantique : seuls les champs fournis sont modifiés."""
    matricule: Optional[str] = Field(default=None, min_length=1, max_length=30)
    nom: Optional[str] = Field(default=None, min_length=1, max_length=100)
    prenom: Optional[str] = Field(default=None, min_length=1, max_length=100)
    fonction: Optional[str] = Field(default=None, max_length=150)
    id_service: Optional[int] = None


class AgentOut(BaseModel):
    id_agent: int
    matricule: str
    nom: str
    prenom: str
    fonction: Optional[str] = None
    statut: StatutAgent
    consentement_facial: bool
    empreinte_faciale_enregistree: bool = False
    webauthn_enregistre: bool = False
    id_service: Optional[int] = None
    service: Optional[ServiceLight] = None
    date_creation: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentDetailOut(AgentOut):
    """Fiche complète d'un agent : rattachements secondaires inclus (cf. cahier des charges §68)."""
    affectations: List[AffectationOut] = []


class ConsentementFacialUpdate(BaseModel):
    consentement_facial: bool
