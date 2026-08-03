"""
Helpers et constantes partagés du module BI (voir `bi_service`).
Extrait de `bi_service` pour respecter la limite de 500 lignes par fichier.
"""
from datetime import date as date_
from datetime import datetime, timedelta
from typing import Dict, List, Literal, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import jours_feries
from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import JourSemaine, StatutAgent, StatutPointage, TypeAnomalie, TypePeriode, TypePointage
from app.models.horaire_reference import HoraireReference
from app.models.pointage import Pointage
from app.models.service import Service
from app.services import conge_service, horaire_service, rapport_service
from app.ml import anomalies_ml, risque_agents
# Alias explicite : le service expose lui aussi une fonction `prevision_ml`,
# qui masquerait le module importé sous le même nom (AttributeError -> 500).
from app.ml import prevision_ml as prevision_ml_module


# Dupliqué depuis anomalie_service/rapport_service (même convention) plutôt
# qu'une dépendance croisée sur une constante privée d'un autre module.
_JOURS_PAR_INDEX = [
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
    JourSemaine.SAMEDI,
    JourSemaine.DIMANCHE,
]

_MAX_BUCKETS = 60  # garde-fou contre une plage de dates trop large en granularité fine

# Bornes de l'historique fourni aux modèles ML par le module BI.
# Trop peu de points -> le modèle retombe systématiquement sur le repli
# statistique ; trop de points -> l'historique ancien (organisation, effectifs
# différents) dilue le signal récent et alourdit inutilement chaque appel.
_ML_MIN_POINTS = 8       # objectif minimal de périodes exploitables pour la prévision
_ML_MAX_PERIODES = 24    # plafond absolu de périodes explorées pour la prévision
_ML_MIN_ECHANTILLONS = 30   # objectif d'exemples (agent x mois) pour le score de risque
_ML_MAX_MOIS_RISQUE = 18    # plafond de mois d'historique pour le score de risque
_ML_MAX_CALCULS_RISQUE = 600  # plafond agents x mois, pour garder un temps de réponse raisonnable


# Tableau de bord opérationnel (temps réel)
# --------------------------------------------------------------------

def _agents_du_perimetre(db: Session, id_service: Optional[int]) -> List[Agent]:
    stmt = select(Agent).where(Agent.statut == StatutAgent.ACTIF)
    if id_service is not None:
        stmt = stmt.where(Agent.id_service == id_service)
    return list(db.execute(stmt).scalars().all())



def _buckets_dans_plage(granularite: TypePeriode, date_debut: date_, date_fin: date_) -> List[Tuple[date_, date_]]:
    debut, fin = rapport_service.bornes_periode(granularite, date_debut)
    buckets: List[Tuple[date_, date_]] = []
    while debut <= date_fin:
        buckets.append((debut, fin))
        if len(buckets) > _MAX_BUCKETS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Plage trop large pour cette granularité (plus de {_MAX_BUCKETS} périodes). Réduisez la plage ou choisissez une granularité plus large.",
            )
        debut, fin = rapport_service.bornes_periode(granularite, fin + timedelta(days=1))
    return buckets


def _buckets_recents(granularite: TypePeriode, date_reference: date_, nombre: int) -> List[Tuple[date_, date_]]:
    """Les `nombre` dernières périodes complètes se terminant dans la période contenant `date_reference`."""
    _, fin = rapport_service.bornes_periode(granularite, date_reference)
    buckets: List[Tuple[date_, date_]] = []
    for _ in range(nombre):
        debut, f = rapport_service.bornes_periode(granularite, fin)
        buckets.append((debut, f))
        fin = debut - timedelta(days=1)
    buckets.reverse()
    return buckets

