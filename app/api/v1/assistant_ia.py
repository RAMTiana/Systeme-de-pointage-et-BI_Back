"""Routeur — Module Assistant IA."""
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user, get_db
from app.models.utilisateur import Utilisateur
from app.schemas.assistant_ia import AssistantCapaciteOut, AssistantMessageRequest, AssistantMessageResponse
from app.services import assistant_ia_service

router = APIRouter(prefix="/assistant", tags=["Assistant IA"])

_CAPACITES: List[AssistantCapaciteOut] = [
    AssistantCapaciteOut(
        intention="anomalies",
        libelle="Détection d'anomalies",
        description="Résumé des retards, absences et départs anticipés récents, et des anomalies en attente de traitement.",
        exemple="Quelles sont les anomalies en attente ?",
    ),
    AssistantCapaciteOut(
        intention="prevision",
        libelle="Prévisions",
        description="Tendance estimée du taux de présence sur les prochains mois (régression linéaire simple).",
        exemple="Quelle est la prévision de présence pour les 3 prochains mois ?",
    ),
    AssistantCapaciteOut(
        intention="rapport",
        libelle="Rapport auto",
        description="Génère à la demande un rapport (jour/semaine/mois/année) au format PDF ou Excel.",
        exemple="Génère le rapport du mois en PDF",
    ),
    AssistantCapaciteOut(
        intention="question_rh",
        libelle="Question RH",
        description="Questions libres sur les effectifs, la présence et le classement des agents ou des services.",
        exemple="Combien d'agents avons-nous ?",
    ),
]


@router.get("/capacites", response_model=List[AssistantCapaciteOut])
def capacites(_utilisateur: Utilisateur = Depends(get_current_active_user)) -> List[AssistantCapaciteOut]:
    """Liste des capacités de l'assistant, utilisée par le frontend pour afficher les actions rapides / l'aide initiale."""
    return _CAPACITES


@router.post("/message", response_model=AssistantMessageResponse)
def envoyer_message(
    payload: AssistantMessageRequest,
    db: Session = Depends(get_db),
    utilisateur: Utilisateur = Depends(get_current_active_user),
) -> AssistantMessageResponse:
    """
    Point d'entrée unique du chat : détecte l'intention du message (anomalies,
    prévisions, rapport, question RH), route vers le service métier concerné,
    et journalise l'interaction (cf. `assistant_ia_service.traiter_message`).

    Chaque utilisateur actif peut interroger l'assistant ; les capacités
    protégées par le RBAC (BI, génération de rapports) renvoient une réponse
    explicite plutôt qu'une erreur si l'utilisateur n'a pas la permission
    requise, afin que l'expérience de chat reste cohérente.
    """
    resultat = assistant_ia_service.traiter_message(
        db, utilisateur, payload.message, id_service=payload.id_service
    )
    return AssistantMessageResponse(**resultat)
