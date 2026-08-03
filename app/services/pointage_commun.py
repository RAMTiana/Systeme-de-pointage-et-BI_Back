"""
Constantes partagées du module Pointage (voir `pointage_service`).
Extrait de `pointage_service` pour respecter la limite de 500 lignes par fichier.
"""
import math
from datetime import date as date_
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.agent import Agent
from app.models.anomalie import Anomalie
from app.models.enums import (
    JourSemaine,
    ModePointage,
    MotifSortie,
    StatutAgent,
    StatutJustification,
    StatutPointage,
    TypeAnomalie,
    TypePointage,
)
from app.models.justificatif import Justificatif
from app.models.pointage import Pointage
from app.schemas.pointage import PointageFacialCreate, PointageQrBadgeCreate, PointageWebAuthnCreate
from app.services import (
    anomalie_service,
    empreinte_service,
    horaire_service,
    journal_audit_service,
    parametre_service,
    webauthn_service,
)

# Motifs de sortie considérés comme exceptionnels (donc pas une simple sortie
# de fin de service) : un départ avant l'heure de référence pour l'un de ces
# motifs ne doit pas rester une anomalie "en attente" à traiter par la
# secrétaire — elle est auto-justifiée à la volée, cf. `_detecter_anomalie_horaire`.
_MOTIFS_SORTIE_EXCEPTIONNELS = {
    MotifSortie.URGENCE,
    MotifSortie.RAISON_FAMILIALE,
    MotifSortie.RAISON_MEDICALE,
    MotifSortie.AUTORISATION_HIERARCHIE,
    MotifSortie.AUTRE,
}

_LIBELLES_MOTIF_SORTIE = {
    MotifSortie.URGENCE: "Sortie urgente déclarée au poste de pointage",
    MotifSortie.RAISON_FAMILIALE: "Sortie pour cas familial déclarée au poste de pointage",
    MotifSortie.RAISON_MEDICALE: "Sortie pour raison médicale déclarée au poste de pointage",
    MotifSortie.AUTORISATION_HIERARCHIE: "Sortie autorisée par la hiérarchie, déclarée au poste de pointage",
    MotifSortie.AUTRE: "Sortie exceptionnelle déclarée au poste de pointage",
}

# Écart minimal exigé, en identification 1:N, entre la distance du meilleur
# candidat et celle du deuxième meilleur, pour trancher sans ambiguïté entre
# deux agents dont les visages se ressemblent. Valeur empirique modeste :
# à affiner selon les retours terrain une fois le mode 1:N en usage réel.
_MARGE_AMBIGUITE_FACIALE = 0.05

_JOURS_PAR_INDEX = [
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
    JourSemaine.SAMEDI,
    JourSemaine.DIMANCHE,
]
