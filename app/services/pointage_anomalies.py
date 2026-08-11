"""
Détection des anomalies horaires du module Pointage (étape 14 du BPMN).
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


def _ajouter_minutes(heure, minutes: int):
    reference = datetime.combine(date_.today(), heure) + timedelta(minutes=minutes)
    return reference.time()


def _est_premier_pointage_du_jour(db: Session, id_agent: int, pointage: Pointage) -> bool:
    """
    Indique si `pointage` est le tout premier pointage VALIDE de l'agent pour
    sa journée (aucun autre pointage validé strictement antérieur ce même
    jour). Sert à ne vérifier le retard que sur l'entrée d'ouverture de la
    journée — une entrée de retour après une sortie exceptionnelle (urgence,
    cas familial, raison médicale...) ne doit pas être comparée à l'heure
    d'ouverture du service, sous peine de faux positifs "retard" sur des
    agents rentrés à l'heure le matin.
    """
    debut_jour = datetime.combine(pointage.date_heure.date(), datetime.min.time())
    stmt = select(func.count()).select_from(Pointage).where(
        Pointage.id_agent == id_agent,
        Pointage.statut == StatutPointage.VALIDE,
        Pointage.date_heure >= debut_jour,
        Pointage.date_heure < pointage.date_heure,
        Pointage.id_pointage != pointage.id_pointage,
    )
    return db.execute(stmt).scalar_one() == 0


def _detecter_anomalie_horaire(db: Session, agent: Agent, pointage: Pointage) -> Optional[Anomalie]:
    if agent.id_service is None:
        return None  # pas de service principal -> pas d'horaire de référence exploitable

    jour = _JOURS_PAR_INDEX[pointage.date_heure.weekday()]
    horaire = horaire_service.horaire_effectif(db, agent.id_service, jour)
    if horaire is None:
        return None  # jour non travaillé (jour férié, ni horaire configuré, ni jour ouvré par défaut)
    heure_debut, heure_fin = horaire

    heure_pointage = pointage.date_heure.time()
    type_anomalie: Optional[TypeAnomalie] = None

    if pointage.type_pointage == TypePointage.ENTREE:
        # Seule la 1ère entrée de la journée (l'entrée d'ouverture) est
        # comparée à l'heure de début du service. Une entrée de retour après
        # une sortie exceptionnelle en cours de journée n'a pas à être jugée
        # par rapport à l'heure d'ouverture du matin : ce n'est pas un retard.
        if _est_premier_pointage_du_jour(db, agent.id_agent, pointage):
            seuil_minutes = parametre_service.get_int(db, "seuil_retard_minutes", default=15)
            limite = _ajouter_minutes(heure_debut, seuil_minutes)
            if heure_pointage > limite:
                type_anomalie = TypeAnomalie.RETARD
    else:  # SORTIE
        if heure_pointage < heure_fin:
            type_anomalie = TypeAnomalie.DEPART_ANTICIPE

    if type_anomalie is None:
        return None

    anomalie = Anomalie(id_agent=agent.id_agent, id_pointage=pointage.id_pointage, type_anomalie=type_anomalie)
    db.add(anomalie)
    db.commit()
    db.refresh(anomalie)

    # Sortie exceptionnelle déclarée (urgence, cas familial, raison médicale,
    # autorisation de la hiérarchie...) : le départ anticipé qui en résulte est
    # légitime et déjà expliqué par l'agent au poste de pointage. On l'auto-
    # justifie immédiatement (statut JUSTIFIEE, sans alerte ni file d'attente
    # pour la secrétaire), tout en conservant la traçabilité complète (motif +
    # commentaire éventuel dans le justificatif).
    if (
        type_anomalie == TypeAnomalie.DEPART_ANTICIPE
        and pointage.motif_sortie is not None
        and pointage.motif_sortie in _MOTIFS_SORTIE_EXCEPTIONNELS
    ):
        motif_libelle = _LIBELLES_MOTIF_SORTIE[pointage.motif_sortie]
        if pointage.commentaire:
            motif_libelle = f"{motif_libelle} — {pointage.commentaire}"
        justificatif = Justificatif(id_anomalie=anomalie.id_anomalie, motif=motif_libelle)
        db.add(justificatif)
        anomalie.statut_justification = StatutJustification.JUSTIFIEE
        db.commit()
        db.refresh(anomalie)
        journal_audit_service.log_action(
            db,
            id_utilisateur=None,
            action="sortie_exceptionnelle_auto_justifiee",
            details=(
                f"agent={agent.matricule} anomalie={anomalie.id_anomalie} "
                f"motif_sortie={pointage.motif_sortie.value}"
            ),
        )
        return anomalie

    # Matérialise la suite du Processus 3 (seuils/récidive, alerte à la
    # hiérarchie) dans la même transaction applicative, cf. anomalie_service.
    anomalie_service.qualifier_et_alerter(db, anomalie)
    return anomalie


# --------------------------------------------------------------------
# Enregistrement + journalisation communs (étapes 12-13)
# --------------------------------------------------------------------
