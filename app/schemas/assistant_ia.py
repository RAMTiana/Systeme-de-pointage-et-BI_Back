"""
Schémas Pydantic — Module Assistant IA.

L'assistant est une couche conversationnelle qui s'appuie sur les services
métier déjà existants (Anomalies, BI/Prévisions, Rapports) : il ne recalcule
rien lui-même, il interprète une question en langage naturel (français), la
route vers le bon service et met le résultat en forme pour un affichage de
type « chat » côté frontend.
"""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Intention = Literal["anomalies", "prevision", "risque", "rapport", "question_rh", "aide"]


class AssistantActionRapide(BaseModel):
    """Bouton d'action rapide proposé sous une réponse (rejoue une intention en un clic)."""

    libelle: str
    intention: Intention


class AssistantMessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, description="Question posée en langage naturel.")
    id_service: Optional[int] = Field(
        default=None, description="Restreint l'analyse à un service (facultatif, sinon périmètre global)."
    )


class AssistantMessageResponse(BaseModel):
    intention: Intention
    reponse: str = Field(..., description="Réponse en français, prête à afficher dans la bulle de chat.")
    donnees: Optional[Dict[str, Any]] = Field(
        default=None, description="Données structurées associées (pour un affichage enrichi côté frontend)."
    )
    actions_suggerees: List[AssistantActionRapide] = Field(default_factory=list)


class AssistantCapaciteOut(BaseModel):
    intention: Intention
    libelle: str
    description: str
    exemple: str
