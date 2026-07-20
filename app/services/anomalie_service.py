"""
Service métier — Module Anomalies (Processus 3 du BPMN "Traitement des
anomalies").

La qualification de l'anomalie (étape 2, retard / départ anticipé) et sa
consignation en base (étape 7) sont déjà réalisées par
`pointage_service._detecter_anomalie_horaire`, dans la même transaction que
le pointage — cf. note d'architecture dans ce module. Le présent service
couvre le reste du Processus 3 :

  - étape 3  : vérifier seuils et récidive
  - étape 4  : décider si une alerte est nécessaire
  - étape 5  : déclencher l'envoi de l'alerte (délégué à alerte_service)
  - étapes 8-10 : examen de l'anomalie et enregistrement du justificatif
                  par la secrétaire
  - étape 12 : journalisation de l'action d'examen

ainsi qu'une fonctionnalité complémentaire mentionnée au cahier des charges
("détection automatique des absences non justifiées") et aux prochaines
étapes du README : la détection des absences, qui alimente ce même
processus mais n'est pas déclenchée par un pointage (un agent absent ne
pointe jamais) — elle doit donc être exécutée par un job planifié
(cf. Processus 4, pattern Timer Start Event) plutôt qu'en réaction à un
événement de pointage.
"""
from datetime import date as date_
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.agent import Agent
from app.models.alerte import Alerte
from app.models.anomalie import Anomalie
from app.models.enums import JourSemaine, StatutAgent, StatutJustification, StatutPointage, TypeAnomalie, TypePointage
from app.models.horaire_reference import HoraireReference
from app.models.justificatif import Justificatif
from app.models.pointage import Pointage
from app.services import alerte_service, horaire_service, journal_audit_service, parametre_service

_JOURS_PAR_INDEX = [
    JourSemaine.LUNDI,
    JourSemaine.MARDI,
    JourSemaine.MERCREDI,
    JourSemaine.JEUDI,
    JourSemaine.VENDREDI,
    JourSemaine.SAMEDI,
    JourSemaine.DIMANCHE,
]


# --------------------------------------------------------------------
# Étapes 3-5 : seuils, récidive et déclenchement de l'alerte
# --------------------------------------------------------------------

def _recidive_atteinte(db: Session, anomalie: Anomalie) -> bool:
    """
    Compte les anomalies non justifiées du même agent sur la fenêtre glissante
    configurée par l'administrateur (`periode_glissante_jours`), anomalie
    courante incluse, et compare au seuil de récidive (`seuil_recidive`).

    Les anomalies déjà justifiées ne comptent pas dans la récidive : un agent
    ayant fourni un motif valable pour ses retards passés ne doit pas
    déclencher une alerte à cause d'eux.
    """
    periode_jours = parametre_service.get_int(db, "periode_glissante_jours", default=30)
    seuil_recidive = parametre_service.get_int(db, "seuil_recidive", default=3)

    fenetre_debut = anomalie.date_detection - timedelta(days=periode_jours)
    stmt = select(func.count()).select_from(Anomalie).where(
        Anomalie.id_agent == anomalie.id_agent,
        Anomalie.date_detection >= fenetre_debut,
        Anomalie.date_detection <= anomalie.date_detection,
        Anomalie.statut_justification != StatutJustification.JUSTIFIEE,
    )
    nb_anomalies = db.execute(stmt).scalar_one()
    return nb_anomalies >= seuil_recidive


def _alerte_necessaire(db: Session, anomalie: Anomalie) -> bool:
    """
    Étape 4 (Exclusive Gateway "Alerte nécessaire ?") : une absence est
    toujours considérée comme sérieuse et alerte immédiatement la hiérarchie ;
    un retard ou un départ anticipé isolé n'alerte qu'à partir de la récidive
    (cohérent avec le cahier des charges : "alertes automatiques ... en cas
    de retard répété, d'anomalie de pointage ou de dépassement de seuil").
    """
    if anomalie.type_anomalie == TypeAnomalie.ABSENCE:
        return True
    return _recidive_atteinte(db, anomalie)


def qualifier_et_alerter(db: Session, anomalie: Anomalie) -> List[Alerte]:
    """
    Point d'entrée appelé juste après la consignation d'une anomalie en base
    (par pointage_service ou par detecter_absences ci-dessous) : réalise les
    étapes 3 à 5 du Processus 3. Ne fait rien de plus si aucune alerte n'est
    nécessaire.
    """
    if not _alerte_necessaire(db, anomalie):
        return []

    alertes = alerte_service.envoyer_alertes(db, anomalie)
    journal_audit_service.log_action(
        db,
        id_utilisateur=None,
        action="alerte_anomalie",
        details=(
            f"anomalie={anomalie.id_anomalie} agent={anomalie.id_agent} "
            f"type={anomalie.type_anomalie.value} destinataires={len(alertes)}"
        ),
    )
    return alertes


# --------------------------------------------------------------------
# Étapes 8-10 : examen de l'anomalie par la secrétaire
# --------------------------------------------------------------------

def get_by_id_or_404(db: Session, id_anomalie: int) -> Anomalie:
    stmt = (
        select(Anomalie)
        .options(
            joinedload(Anomalie.agent),
            joinedload(Anomalie.justificatif),
            joinedload(Anomalie.alertes),
        )
        .where(Anomalie.id_anomalie == id_anomalie)
    )
    anomalie = db.execute(stmt).unique().scalar_one_or_none()
    if anomalie is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Anomalie introuvable.")
    return anomalie


def examiner_anomalie(
    db: Session,
    id_anomalie: int,
    id_utilisateur: int,
    anomalie_justifiee: bool,
    motif: Optional[str] = None,
    piece_jointe_chemin: Optional[str] = None,
) -> Anomalie:
    """
    Étapes 8-9-10a/10b : la secrétaire examine l'anomalie et, selon le cas,
    enregistre un justificatif (10a) ou maintient l'anomalie non justifiée
    (10b). Journalise l'action (étape 12).
    """
    anomalie = get_by_id_or_404(db, id_anomalie)

    if anomalie.statut_justification != StatutJustification.EN_ATTENTE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cette anomalie a déjà été examinée.",
        )

    if anomalie_justifiee:
        if not motif:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Un motif est requis pour justifier une anomalie.",
            )
        justificatif = Justificatif(
            id_anomalie=anomalie.id_anomalie,
            motif=motif,
            piece_jointe_chemin=piece_jointe_chemin,
        )
        db.add(justificatif)
        anomalie.statut_justification = StatutJustification.JUSTIFIEE
    else:
        anomalie.statut_justification = StatutJustification.NON_JUSTIFIEE

    anomalie.id_utilisateur_traitant = id_utilisateur
    db.commit()
    db.refresh(anomalie)

    journal_audit_service.log_action(
        db,
        id_utilisateur=id_utilisateur,
        action="traitement_anomalie",
        details=(
            f"anomalie={anomalie.id_anomalie} agent={anomalie.id_agent} "
            f"resultat={'justifiee' if anomalie_justifiee else 'non_justifiee'}"
        ),
    )
    return get_by_id_or_404(db, anomalie.id_anomalie)


# --------------------------------------------------------------------
# Consultation (back-office)
# --------------------------------------------------------------------

def lister_anomalies(
    db: Session,
    id_agent: Optional[int] = None,
    id_service: Optional[int] = None,
    type_anomalie: Optional[TypeAnomalie] = None,
    statut_justification: Optional[StatutJustification] = None,
    date_debut: Optional[date_] = None,
    date_fin: Optional[date_] = None,
    skip: int = 0,
    limit: int = 50,
) -> Tuple[List[Anomalie], int]:
    stmt = select(Anomalie).options(joinedload(Anomalie.agent), joinedload(Anomalie.justificatif))
    if id_service is not None:
        stmt = stmt.join(Agent, Anomalie.id_agent == Agent.id_agent).where(Agent.id_service == id_service)

    conditions = []
    if id_agent is not None:
        conditions.append(Anomalie.id_agent == id_agent)
    if type_anomalie is not None:
        conditions.append(Anomalie.type_anomalie == type_anomalie)
    if statut_justification is not None:
        conditions.append(Anomalie.statut_justification == statut_justification)
    if date_debut is not None:
        conditions.append(Anomalie.date_detection >= datetime.combine(date_debut, datetime.min.time()))
    if date_fin is not None:
        conditions.append(Anomalie.date_detection <= datetime.combine(date_fin, datetime.max.time()))

    for condition in conditions:
        stmt = stmt.where(condition)

    total_stmt = select(func.count()).select_from(stmt.with_only_columns(Anomalie.id_anomalie).subquery())
    total = db.execute(total_stmt).scalar_one()

    stmt = stmt.order_by(Anomalie.date_detection.desc()).offset(skip).limit(limit)
    anomalies = list(db.execute(stmt).unique().scalars().all())

    return anomalies, total


# --------------------------------------------------------------------
# Détection des absences (job planifié, complète le Processus 1/3)
# --------------------------------------------------------------------

def _absence_deja_consignee(db: Session, id_agent: int, jour: date_) -> bool:
    stmt = select(func.count()).select_from(Anomalie).where(
        Anomalie.id_agent == id_agent,
        Anomalie.type_anomalie == TypeAnomalie.ABSENCE,
        Anomalie.date_detection >= datetime.combine(jour, datetime.min.time()),
        Anomalie.date_detection <= datetime.combine(jour, datetime.max.time()),
    )
    return db.execute(stmt).scalar_one() > 0


def _a_pointe_entree(db: Session, id_agent: int, jour: date_) -> bool:
    stmt = select(func.count()).select_from(Pointage).where(
        Pointage.id_agent == id_agent,
        Pointage.type_pointage == TypePointage.ENTREE,
        Pointage.statut == StatutPointage.VALIDE,
        Pointage.date_heure >= datetime.combine(jour, datetime.min.time()),
        Pointage.date_heure <= datetime.combine(jour, datetime.max.time()),
    )
    return db.execute(stmt).scalar_one() > 0


def detecter_absences(db: Session, jour: Optional[date_] = None) -> List[Anomalie]:
    """
    Job planifié (à exécuter en fin de journée ou tôt le lendemain, via un
    ordonnanceur externe — cf. pattern Timer Start Event du Processus 4) :
    consigne une anomalie 'absence' pour tout agent actif n'ayant enregistré
    aucun pointage d'entrée valide ce jour-là, dans les services contrôlés
    ce jour-là.

    Un service est contrôlé un jour donné si un `HoraireReference` explicite
    y est défini, ou — à défaut — si ce jour fait partie des jours ouvrés
    par défaut (lundi-vendredi), auquel cas l'horaire standard 8h-17h
    s'applique (cf. `horaire_service.horaire_effectif`). Un service sans
    horaire de référence un jour de week-end est considéré comme non
    travaillé et n'est pas contrôlé — même règle que la détection du retard
    au pointage.
    """
    jour = jour or (date_.today() - timedelta(days=1))
    jour_semaine = _JOURS_PAR_INDEX[jour.weekday()]

    if jour_semaine in horaire_service.JOURS_OUVRES_PAR_DEFAUT:
        # Jour ouvré par défaut (lundi-vendredi) : tous les services sont
        # contrôlés, ceux sans `HoraireReference` explicite héritant de
        # l'horaire standard 8h-17h (cf. horaire_service.horaire_effectif),
        # cohérent avec la détection de retard au pointage.
        stmt_agents = select(Agent).where(
            Agent.statut == StatutAgent.ACTIF,
            Agent.id_service.is_not(None),
        )
    else:
        # Week-end : seuls les services ayant explicitement défini un
        # horaire de référence ce jour-là sont contrôlés.
        stmt_services = select(HoraireReference.id_service).where(
            HoraireReference.jour_semaine == jour_semaine,
            HoraireReference.id_service.is_not(None),
        ).distinct()
        ids_services = [row for row in db.execute(stmt_services).scalars().all()]

        if not ids_services:
            return []

        stmt_agents = select(Agent).where(
            Agent.id_service.in_(ids_services),
            Agent.statut == StatutAgent.ACTIF,
        )

    agents = list(db.execute(stmt_agents).scalars().all())

    anomalies_creees: List[Anomalie] = []
    for agent in agents:
        if _a_pointe_entree(db, agent.id_agent, jour):
            continue
        if _absence_deja_consignee(db, agent.id_agent, jour):
            continue

        anomalie = Anomalie(
            id_agent=agent.id_agent,
            id_pointage=None,
            type_anomalie=TypeAnomalie.ABSENCE,
            date_detection=datetime.combine(jour, datetime.min.time()) + timedelta(hours=23, minutes=59),
        )
        db.add(anomalie)
        db.commit()
        db.refresh(anomalie)
        anomalies_creees.append(anomalie)
        qualifier_et_alerter(db, anomalie)

    journal_audit_service.log_action(
        db,
        id_utilisateur=None,
        action="detection_absences",
        details=f"jour={jour.isoformat()} agents_controles={len(agents)} absences_detectees={len(anomalies_creees)}",
    )
    return anomalies_creees
