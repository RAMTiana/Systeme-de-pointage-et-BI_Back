"""Schémas Pydantic pour l'entité Service (unité organisationnelle du SRB)."""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ServiceBase(BaseModel):
    nom_service: str = Field(min_length=1, max_length=150)
    description: Optional[str] = None


class ServiceCreate(ServiceBase):
    pass


class ServiceUpdate(BaseModel):
    """Tous les champs sont optionnels : seuls ceux fournis sont modifiés (PATCH sémantique)."""
    nom_service: Optional[str] = Field(default=None, min_length=1, max_length=150)
    description: Optional[str] = None


class ServiceOut(ServiceBase):
    id_service: int
    nombre_agents: int = 0

    model_config = ConfigDict(from_attributes=True)


class ServiceLight(BaseModel):
    """Version allégée utilisée en imbrication (ex. dans AgentOut) — évite le calcul de nombre_agents."""
    id_service: int
    nom_service: str

    model_config = ConfigDict(from_attributes=True)

