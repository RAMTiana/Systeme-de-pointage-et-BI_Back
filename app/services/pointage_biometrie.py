"""
Reconnaissance faciale du module Pointage (étapes 5-6 du BPMN).
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
from app.services.pointage_commun import (
    _JOURS_PAR_INDEX,
    _LIBELLES_MOTIF_SORTIE,
    _MARGE_AMBIGUITE_FACIALE,
    _MOTIFS_SORTIE_EXCEPTIONNELS,
)

# --------------------------------------------------------------------
# Reconnaissance faciale (étapes 5-6) : comparaison de vecteurs
# --------------------------------------------------------------------

def _distance_euclidienne(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le vecteur facial transmis n'a pas la même dimension que l'empreinte de référence.",
        )
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _identite_verifiee(db: Session, agent: Agent, vecteur_capture: List[float]) -> bool:
    empreinte_reference = empreinte_service.decoder_vecteur(agent.empreinte_biometrique.encodage_facial)
    seuil = parametre_service.get_float(db, "seuil_distance_faciale", default=0.6)
    distance = _distance_euclidienne(vecteur_capture, empreinte_reference)
    return distance <= seuil


def identifier_par_visage(db: Session, vecteur_capture: List[float]) -> Agent:
    """
    Identification 1:N : compare le vecteur facial capté à l'empreinte de
    TOUS les agents actifs ayant consenti à la reconnaissance faciale et
    disposant d'une empreinte enregistrée, et retourne l'agent le plus
    proche — à condition que cette distance minimale reste sous le seuil
    configuré ET qu'elle se détache clairement du deuxième meilleur candidat
    (cf. `_MARGE_AMBIGUITE_FACIALE` ci-dessous), pour éviter de trancher à
    tort entre deux agents dont les empreintes sont proches.

    Utilisée quand le poste de pointage facial n'a pas fait saisir de
    matricule au préalable (cf. PointageFacialCreate, matricule/id_agent
    optionnels) : c'est la capture elle-même qui détermine l'identité,
    contrairement à `_identite_verifiee` qui vérifie une identité déjà
    présumée (1:1).
    """
    stmt = select(Agent).where(
        Agent.statut == StatutAgent.ACTIF,
        Agent.consentement_facial.is_(True),
    ).options(joinedload(Agent.empreinte_biometrique))
    agents = db.execute(stmt).unique().scalars().all()

    seuil = parametre_service.get_float(db, "seuil_distance_faciale", default=0.6)

    candidats: List[Tuple[Agent, float]] = []
    for agent in agents:
        if agent.empreinte_biometrique is None:
            continue
        empreinte_reference = empreinte_service.decoder_vecteur(agent.empreinte_biometrique.encodage_facial)
        try:
            distance = _distance_euclidienne(vecteur_capture, empreinte_reference)
        except HTTPException:
            continue  # empreinte de dimension incompatible : on l'ignore plutôt que de planter la recherche
        candidats.append((agent, distance))

    if not candidats:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identité non vérifiée : aucun agent correspondant au visage capté.",
        )

    candidats.sort(key=lambda c: c[1])
    meilleur_agent, meilleure_distance = candidats[0]

    if meilleure_distance > seuil:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identité non vérifiée : aucun agent correspondant au visage capté.",
        )

    if len(candidats) > 1:
        _, deuxieme_distance = candidats[1]
        if deuxieme_distance - meilleure_distance < _MARGE_AMBIGUITE_FACIALE:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Identité ambiguë : plusieurs agents correspondent de façon trop proche au visage "
                "capté. Merci de réessayer (meilleur cadrage/éclairage) ou de pointer par matricule.",
            )

    return meilleur_agent


# --------------------------------------------------------------------
# Calcul d'anomalie horaire (étape 14) — retard / départ anticipé
# --------------------------------------------------------------------
