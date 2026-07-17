"""Schémas Pydantic — empreinte biométrique faciale d'un agent."""
from datetime import datetime
from typing import List

from pydantic import BaseModel, ConfigDict, Field


class EmpreinteFacialeCreate(BaseModel):
    encodage_facial: List[float] = Field(
        min_length=1,
        description="Vecteur de caractéristiques faciales de référence, déjà calculé côté "
        "client/dispositif de capture lors de l'inscription de l'agent (jamais l'image brute).",
    )


class EmpreinteFacialeOut(BaseModel):
    """
    Ne renvoie jamais `encodage_facial` : la donnée biométrique ne doit être
    exposée qu'aux fonctions internes d'identification, jamais via l'API.
    """
    id_empreinte: int
    id_agent: int
    date_creation: datetime

    model_config = ConfigDict(from_attributes=True)
