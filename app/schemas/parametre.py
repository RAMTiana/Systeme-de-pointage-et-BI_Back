"""Schémas Pydantic — Module Paramètres système (cahier des charges 3.1,
"Personnalisation des seuils et règles métier").
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ParametreOut(BaseModel):
    id_parametre: int
    nom_parametre: str
    valeur: str
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ParametreUpdate(BaseModel):
    """PUT /parametres/{nom_parametre} : seule la valeur se modifie (nom et
    description sont des données de référence, cf. script SQL section 5)."""
    valeur: str = Field(min_length=1, max_length=2000)
