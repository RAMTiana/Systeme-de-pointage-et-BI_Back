"""Schémas Pydantic — Module IA."""
from datetime import date as date_
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


class AnalyseAnomaliesRequest(BaseModel):
    id_service: Optional[int] = None
    jours: int = Field(default=30, ge=1, le=180)


class AnalyseAnomaliesResponse(BaseModel):
    periode_debut: date_
    periode_fin: date_
    nombre_anomalies_analysees: int
    analyse: dict[str, Any]


class PrevisionCommenteeRequest(BaseModel):
    id_service: Optional[int] = None
    horizon: int = Field(default=3, ge=1, le=12)


class PrevisionCommenteeResponse(BaseModel):
    prevision: dict[str, Any]
    commentaire_ia: dict[str, Any]


class RapportAutoRequest(BaseModel):
    id_service: Optional[int] = None
    periode: Literal["hebdomadaire", "mensuel"] = "hebdomadaire"


class RapportAutoResponse(BaseModel):
    periode: str
    date_debut: date_
    date_fin: date_
    id_service: Optional[int] = None
    rapport_markdown: str


class QuestionRHRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=500)
    id_service: Optional[int] = None


class QuestionRHResponse(BaseModel):
    question: str
    reponse: str
