"""Schémas Pydantic — Module Congés (simple registre, sans circuit d'approbation local)."""
from datetime import date as date_
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import StatutConge, TypeConge
from app.schemas.agent import AgentOut


class CongeCreateRequest(BaseModel):
    id_agent: int
    type_conge: TypeConge
    date_debut: date_
    date_fin: date_
    motif: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _dates_coherentes(self) -> "CongeCreateRequest":
        if self.date_fin < self.date_debut:
            raise ValueError("La date de fin doit être postérieure ou égale à la date de début.")
        return self


class CongeOut(BaseModel):
    id_conge: int
    id_agent: int
    agent: Optional[AgentOut] = None
    type_conge: TypeConge
    date_debut: date_
    date_fin: date_
    motif: Optional[str] = None
    statut: StatutConge
    id_utilisateur_saisie: int
    date_creation: datetime

    model_config = ConfigDict(from_attributes=True)
